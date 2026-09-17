import logging
import re
from datetime import datetime, timezone as datetime_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F
from django.utils import timezone

from integrations.termii import TermiiClient, TermiiError

from .models import DailySmsBudget, DispatchJob, Notification, NotificationAttempt, WebhookReceipt

logger = logging.getLogger(__name__)

TEMPLATES = {
    "phone_verified_v1": "Your phone number has been verified on Vurex. Keep your verification codes private.",
}


def safe_error_code(value):
    # Error text and provider payloads must never enter operational logs or records.
    return value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) else "provider_error"


def enqueue_job(topic, object_id, deduplication_key):
    if topic not in {"phone_otp", "notification_sms", "termii_receipt"}:
        raise ValueError("Unsupported notification job topic")
    job, _ = DispatchJob.objects.get_or_create(
        deduplication_key=deduplication_key,
        defaults={"topic": topic, "object_id": object_id},
    )
    if job.topic != topic or str(job.object_id) != str(object_id):
        raise ValueError("Dispatch idempotency key was reused for a different operation")
    return job


@transaction.atomic
def notify_phone_verified(user, challenge_id):
    if not user.phone or not user.phone_verified_at:
        raise ValueError("A verified phone is required for this notification")
    key = f"phone_verified:{challenge_id}"
    notification, _ = Notification.objects.get_or_create(
        deduplication_key=key,
        defaults={"user": user, "recipient": user.phone, "template": "phone_verified_v1"},
    )
    if notification.user_id != user.pk:
        raise ValueError("Notification idempotency key belongs to another user")
    enqueue_job("notification_sms", notification.pk, f"sms:{notification.pk}")
    return notification


@transaction.atomic
def reserve_sms_budget():
    """Reserve one real send attempt under a row lock; never release ambiguous attempts."""
    if not getattr(settings, "SMS_ENABLED", False):
        return False
    limit = max(0, int(getattr(settings, "SMS_DAILY_SEND_LIMIT", 500)))
    day = datetime.now(datetime_timezone.utc).date()
    DailySmsBudget.objects.get_or_create(day=day)
    budget = DailySmsBudget.objects.select_for_update().get(day=day)
    if budget.attempts >= limit:
        return False
    budget.attempts = F("attempts") + 1
    budget.save(update_fields=["attempts"])
    return True


def _finish_attempt(attempt_id, status, code="", message_id=""):
    NotificationAttempt.objects.filter(pk=attempt_id, status="sending").update(
        status=status,
        error_code=code,
        provider_message_id=message_id,
        finished_at=timezone.now(),
    )


def deliver_notification(notification_id):
    """No database lock is held over the external HTTP request."""
    with transaction.atomic():
        notification = Notification.objects.select_for_update().select_related("user").get(pk=notification_id)
        if notification.status in {"sent", "delivered"}:
            return {"status": "completed"}
        if notification.status == "failed":
            return {"status": "failed", "code": notification.last_error}
        if notification.status in {"sending", "unknown"}:
            return {"status": "unknown", "code": "send_outcome_unknown"}
        text = TEMPLATES.get(notification.template)
        if not text or not notification.user.is_active or notification.recipient != notification.user.phone or not notification.user.phone_verified_at:
            notification.status = "failed"
            notification.last_error = "recipient_no_longer_eligible" if text else "unsupported_template"
            notification.updated_at = timezone.now()
            notification.save(update_fields=["status", "last_error", "updated_at"])
            return {"status": "failed", "code": notification.last_error}
        if not reserve_sms_budget():
            notification.status = "failed"
            notification.last_error = "sms_sending_disabled_or_capped"
            notification.updated_at = timezone.now()
            notification.save(update_fields=["status", "last_error", "updated_at"])
            return {"status": "failed", "code": notification.last_error}
        attempt = NotificationAttempt.objects.create(
            notification=notification, attempt=notification.attempts.count() + 1,
        )
        notification.status = "sending"
        notification.updated_at = timezone.now()
        notification.save(update_fields=["status", "updated_at"])

    try:
        message_id = TermiiClient().send_sms(notification.recipient, text)
    except TermiiError as exc:
        code = safe_error_code(exc.code)
        status = "unknown" if exc.ambiguous else ("queued" if exc.retryable else "failed")
        with transaction.atomic():
            _finish_attempt(attempt.pk, "unknown" if exc.ambiguous else "failed", code)
            Notification.objects.filter(pk=notification_id, status="sending").update(
                status=status, last_error=code, updated_at=timezone.now(),
            )
        logger.warning("sms_dispatch_result notification_id=%s status=%s code=%s", notification_id, status, code)
        result = {"status": "retry" if status == "queued" else status, "code": code}
        if exc.retry_after is not None:
            result["retry_after"] = exc.retry_after
        return result

    with transaction.atomic():
        _finish_attempt(attempt.pk, "accepted", message_id=message_id)
        # A late successful response can resolve an unknown send without sending again.
        Notification.objects.filter(pk=notification_id, status__in=["sending", "unknown"]).update(
            status="sent", provider_message_id=message_id, last_error="", updated_at=timezone.now(),
        )
    logger.info("sms_dispatch_result notification_id=%s status=sent", notification_id)
    return {"status": "completed"}


def mark_notification_unknown(notification_id):
    now = timezone.now()
    Notification.objects.filter(pk=notification_id, status__in=["queued", "sending"]).update(
        status="unknown", last_error="worker_outcome_unknown", updated_at=now,
    )
    NotificationAttempt.objects.filter(notification_id=notification_id, status="sending").update(
        status="unknown", error_code="worker_outcome_unknown", finished_at=now,
    )


def mark_notification_failed(notification_id, code):
    Notification.objects.filter(pk=notification_id, status="queued").update(
        status="failed", last_error=safe_error_code(code), updated_at=timezone.now(),
    )


def process_receipt(receipt_id):
    from identity.services import record_delivery

    receipt = WebhookReceipt.objects.get(pk=receipt_id)
    if receipt.status in {"processed", "ignored"}:
        return {"status": "completed"}
    if not receipt.provider_message_id or not receipt.delivery_status:
        WebhookReceipt.objects.filter(pk=receipt_id).update(status="ignored", processed_at=timezone.now())
        return {"status": "completed"}

    with transaction.atomic():
        notification = Notification.objects.select_for_update().filter(
            provider_message_id=receipt.provider_message_id,
        ).first()
        if notification:
            status = receipt.delivery_status
            # Provider sent_at denotes the send time, not a reliable event sequence.
            # Delivered is terminal; delayed sent reports cannot erase a failure.
            if notification.status != "delivered" and (status != "sent" or notification.status not in {"failed", "unknown"}):
                notification.status = status
                notification.last_error = "delivery_failed" if status == "failed" else ""
                notification.updated_at = timezone.now()
                notification.save(update_fields=["status", "last_error", "updated_at"])
        matched_challenge = record_delivery(receipt.provider_message_id, receipt.delivery_status)
        if notification or matched_challenge:
            WebhookReceipt.objects.filter(pk=receipt_id).update(status="processed", processed_at=timezone.now())
            return {"status": "completed"}
    # The webhook can arrive before the API response records the provider message ID.
    return {"status": "retry", "code": "delivery_receipt_unmatched", "retry_after": 15}


def status_summary():
    now = timezone.now()
    oldest = DispatchJob.objects.filter(status="pending").order_by("created_at").values_list("created_at", flat=True).first()
    return {
        "oldest_pending_age_seconds": int((now - oldest).total_seconds()) if oldest else 0,
        "jobs": list(DispatchJob.objects.values("topic", "status").annotate(count=Count("id"))),
        "notifications": list(Notification.objects.values("status").annotate(count=Count("id"))),
        "receipts": list(WebhookReceipt.objects.values("status").annotate(count=Count("id"))),
        "daily_send_attempts": DailySmsBudget.objects.filter(
            day=datetime.now(datetime_timezone.utc).date(),
        ).values_list("attempts", flat=True).first() or 0,
    }
