import logging
import random
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import DispatchJob, WebhookReceipt
from .services import deliver_notification, mark_notification_failed, mark_notification_unknown, process_receipt, safe_error_code

logger = logging.getLogger(__name__)


def recover_expired_leases():
    from identity.services import mark_delivery_unknown

    cutoff = timezone.now() - timedelta(seconds=settings.NOTIFICATION_LEASE_SECONDS)
    with transaction.atomic():
        jobs = list(DispatchJob.objects.select_for_update(skip_locked=True).filter(status="running", locked_at__lt=cutoff)[:100])
        for job in jobs:
            if job.topic == "termii_receipt":
                # Local receipt processing is idempotent and makes no external send.
                job.status = "pending"
                job.available_at = timezone.now()
            else:
                job.status = "unknown"
                job.finished_at = timezone.now()
                if job.topic == "phone_otp":
                    mark_delivery_unknown(job.object_id)
                else:
                    mark_notification_unknown(job.object_id)
            job.last_error = "worker_lease_expired"
            job.lease_token = None
            job.save()
    return len(jobs)


def claim_job():
    with transaction.atomic():
        due = DispatchJob.objects.select_for_update(skip_locked=True).filter(status="pending", available_at__lte=timezone.now())
        # OTPs get priority over ordinary messages and delivery-report retries.
        job = due.filter(topic="phone_otp").order_by("available_at").first() or due.order_by("available_at").first()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = timezone.now()
        job.lease_token = uuid.uuid4()
        job.save(update_fields=["status", "attempts", "locked_at", "lease_token"])
        return job


def process_one():
    from identity.models import PhoneChallenge
    from identity.services import deliver_challenge, mark_delivery_unknown

    job = claim_job()
    if job is None:
        return False
    try:
        if job.topic == "phone_otp":
            result = deliver_challenge(job.object_id)
        elif job.topic == "notification_sms":
            result = deliver_notification(job.object_id)
        elif job.topic == "termii_receipt":
            result = process_receipt(job.object_id)
        else:
            result = {"status": "failed", "code": "unsupported_topic"}
    except Exception:
        # No exception locals, payloads, or secrets enter worker logs. A crash may
        # follow provider acceptance, so external work must never be blindly replayed.
        result = {"status": "retry" if job.topic == "termii_receipt" else "unknown", "code": "worker_processing_error"}
        if job.topic == "phone_otp":
            mark_delivery_unknown(job.object_id)
        elif job.topic == "notification_sms":
            mark_notification_unknown(job.object_id)
    state = result["status"]
    code = safe_error_code(result.get("code", "")) if result.get("code") else ""
    with transaction.atomic():
        current = DispatchJob.objects.select_for_update().get(id=job.id)
        if current.status != "running" or current.lease_token != job.lease_token:
            return True
        maximum = 10 if job.topic == "termii_receipt" else settings.NOTIFICATION_MAX_ATTEMPTS
        if state == "retry" and current.attempts < maximum:
            current.status = "pending"
            delay = min(3600, max(int(result.get("retry_after") or 0), 5 * 2 ** (current.attempts - 1))) + random.randint(0, 3)
            current.available_at = timezone.now() + timedelta(seconds=delay)
        else:
            current.status = "failed" if state == "retry" else state
            current.finished_at = timezone.now()
            if state == "retry":
                if job.topic == "phone_otp":
                    PhoneChallenge.objects.filter(id=job.object_id, status="queued").update(status="failed", error_code="send_retries_exhausted")
                elif job.topic == "notification_sms":
                    mark_notification_failed(job.object_id, "send_retries_exhausted")
                else:
                    WebhookReceipt.objects.filter(id=job.object_id).update(status="unmatched")
        current.last_error = code
        current.lease_token = None
        current.save()
    logger.info("dispatch job_id=%s topic=%s status=%s code=%s", job.id, job.topic, current.status, code)
    return True
