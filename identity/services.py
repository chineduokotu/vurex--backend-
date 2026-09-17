import hashlib
import json
import math
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from integrations.termii import TermiiClient, TermiiError, validate_configuration
from notifications.services import enqueue_job, notify_phone_verified, reserve_sms_budget
from transactions.models import User

from .errors import IdentityError
from .models import AuditEvent, PhoneChallenge
from .rate_limits import reserve_limits
from .security import client_ip, fingerprint, masked_phone, normalize_phone, require_recent_password, session_binding


def audit(action, outcome, user=None, subject_id="", request=None):
    AuditEvent.objects.create(
        action=action, outcome=outcome, user=user, subject_id=str(subject_id),
        ip_fingerprint=fingerprint(client_ip(request)) if request is not None else "",
    )


def challenge_data(challenge):
    now = timezone.now()
    status = challenge.status
    if status in ("sent", "verifying") and challenge.expires_at and now >= challenge.expires_at:
        status = "expired"
    if status == "queued" and now >= challenge.dispatch_deadline:
        status = "expired"
    if status == "verifying" and challenge.verifying_at and (now - challenge.verifying_at).total_seconds() > settings.NOTIFICATION_LEASE_SECONDS:
        status = "unknown"
    return {
        "id": str(challenge.id), "status": status,
        "purpose": challenge.purpose, "masked_phone": masked_phone(challenge.phone),
        "expires_at": challenge.expires_at.isoformat() if challenge.expires_at else None,
        "resend_available_at": challenge.resend_available_at.isoformat(),
        "attempts_remaining": max(0, settings.OTP_MAX_ATTEMPTS - challenge.attempts),
        "delivery_status": challenge.delivery_status,
        "server_time": now.isoformat(),
    }


def own_challenge(request, user, challenge_id, *, lock=False):
    query = PhoneChallenge.objects.select_for_update() if lock else PhoneChallenge.objects
    challenge = query.filter(id=challenge_id, user=user, session_binding=session_binding(request)).first()
    if challenge is None:
        raise IdentityError("challenge_not_found", "This verification request is unavailable. Please sign in again.", 404)
    return challenge


def request_challenge(request, user, phone=None, resend_id=None):
    key = request.headers.get("Idempotency-Key", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
        raise IdentityError("idempotency_key_required", "A valid request identifier is required.")
    binding = session_binding(request)
    if resend_id:
        phone = own_challenge(request, user, resend_id).phone
    phone = normalize_phone(phone)
    signature = hashlib.sha256(json.dumps([phone, binding, str(resend_id or "")]).encode()).hexdigest()
    existing = PhoneChallenge.objects.filter(user=user, idempotency_key=key).first()
    if existing:
        if existing.request_fingerprint != signature:
            raise IdentityError("idempotency_conflict", "This request identifier was already used for a different request.", 409)
        return existing
    require_recent_password(request, user)
    if not settings.SMS_ENABLED:
        raise IdentityError("sms_unavailable", "Phone verification is temporarily unavailable.", 503)
    try:
        validate_configuration(require_webhook=False)
    except TermiiError:
        raise IdentityError("sms_unavailable", "Phone verification is temporarily unavailable.", 503) from None
    # Count invalid repeated requests as well, independently of business rollback.
    reserve_limits([
        ("otp-send-user", user.id, 3600, settings.OTP_SENDS_PER_HOUR),
        ("otp-send-user", user.id, 86400, settings.OTP_SENDS_PER_DAY),
        ("otp-send-phone", phone, 3600, settings.OTP_SENDS_PER_HOUR),
        ("otp-send-phone", phone, 86400, settings.OTP_SENDS_PER_DAY),
        ("otp-send-ip", client_ip(request), 3600, settings.OTP_IP_SENDS_PER_HOUR),
    ])
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(id=user.id)
        if not locked_user.is_active:
            raise IdentityError("account_unavailable", "This account is unavailable.", 403)
        existing = PhoneChallenge.objects.filter(user=user, idempotency_key=key).first()
        if existing:
            if existing.request_fingerprint != signature:
                raise IdentityError("idempotency_conflict", "Request identifier conflict.", 409)
            return existing
        if locked_user.phone_verified_at and locked_user.phone == phone:
            raise IdentityError("phone_already_verified", "This phone number is already verified.", 409)
        if User.objects.filter(phone=phone, phone_verified_at__isnull=False).exclude(id=user.id).exists():
            raise IdentityError("phone_unavailable", "This number cannot be used. Contact support if you need help.", 409)
        current = PhoneChallenge.objects.filter(user=user, active=True).first()
        now = timezone.now()
        if current:
            if current.resend_available_at > now:
                raise IdentityError("resend_too_soon", "Please wait before requesting another code.", 429,
                                    math.ceil((current.resend_available_at - now).total_seconds()))
            if resend_id and current.id != resend_id:
                raise IdentityError("challenge_superseded", "Use your most recent verification request.", 409)
            current.active = False
            current.status = PhoneChallenge.Status.SUPERSEDED
            current.save(update_fields=["active", "status", "updated_at"])
        elif resend_id:
            raise IdentityError("challenge_inactive", "Start a new phone verification request.", 409)
        challenge = PhoneChallenge.objects.create(
            user=locked_user, phone=phone, session_binding=binding,
            idempotency_key=key, request_fingerprint=signature,
            dispatch_deadline=now + timedelta(seconds=settings.OTP_DISPATCH_DEADLINE_SECONDS),
            resend_available_at=now + timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS),
        )
        enqueue_job("phone_otp", challenge.id, f"phone_otp:{challenge.id}")
        audit("phone.challenge_requested", "queued", locked_user, challenge.id, request)
        return challenge


def deliver_challenge(challenge_id):
    """Worker entry point. Reserve state before HTTP; never hold a DB lock over HTTP."""
    with transaction.atomic():
        challenge = PhoneChallenge.objects.select_for_update().get(id=challenge_id)
        if not challenge.active or challenge.status != PhoneChallenge.Status.QUEUED:
            return {"status": "completed"}
        now = timezone.now()
        if now >= challenge.dispatch_deadline or not User.objects.filter(id=challenge.user_id, is_active=True).exists():
            challenge.status = PhoneChallenge.Status.EXPIRED
            challenge.save(update_fields=["status", "updated_at"])
            return {"status": "failed", "code": "challenge_expired"}
        if not reserve_sms_budget():
            challenge.status = PhoneChallenge.Status.FAILED
            challenge.error_code = "sms_daily_limit"
            challenge.save(update_fields=["status", "error_code", "updated_at"])
            audit("phone.otp_send", "sms_daily_limit", challenge.user, challenge.id)
            return {"status": "failed", "code": "sms_daily_limit"}
        challenge.status = PhoneChallenge.Status.SENDING
        challenge.expires_at = now + timedelta(seconds=settings.OTP_TTL_SECONDS)
        challenge.resend_available_at = now + timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS)
        challenge.save(update_fields=["status", "expires_at", "resend_available_at", "updated_at"])
    try:
        delivery = TermiiClient().send_otp(
            challenge.phone, ttl_seconds=settings.OTP_TTL_SECONDS,
            length=settings.OTP_LENGTH, max_attempts=settings.OTP_MAX_ATTEMPTS,
        )
    except TermiiError as exc:
        state = "unknown" if exc.ambiguous else ("queued" if exc.retryable else "failed")
        with transaction.atomic():
            PhoneChallenge.objects.filter(id=challenge_id, status="sending").update(status=state, error_code=exc.code, updated_at=timezone.now())
            audit("phone.otp_send", state, challenge.user, challenge.id)
        return {"status": "unknown" if exc.ambiguous else ("retry" if exc.retryable else "failed"),
                "code": exc.code, "retry_after": exc.retry_after or 5}
    with transaction.atomic():
        updated = PhoneChallenge.objects.select_for_update().get(id=challenge_id)
        updated.provider_pin_id = delivery.pin_id
        updated.provider_message_id = delivery.message_id
        if updated.status == PhoneChallenge.Status.SENDING:
            updated.status = PhoneChallenge.Status.SENT
        updated.save(update_fields=["provider_pin_id", "provider_message_id", "status", "updated_at"])
        audit("phone.otp_send", "accepted", challenge.user, challenge.id)
    return {"status": "completed"}


def mark_delivery_unknown(challenge_id):
    PhoneChallenge.objects.filter(id=challenge_id, status__in=["sending", "queued"]).update(
        status="unknown", error_code="worker_interrupted", updated_at=timezone.now(),
    )


def record_delivery(message_id, status):
    with transaction.atomic():
        matches = list(PhoneChallenge.objects.select_for_update().filter(provider_message_id=message_id))
        priority = {"": 0, "sent": 1, "failed": 2, "delivered": 3}
        for challenge in matches:
            if priority.get(status, 0) > priority.get(challenge.delivery_status, 0):
                challenge.delivery_status = status
                challenge.save(update_fields=["delivery_status", "updated_at"])
        return bool(matches)


def verify_challenge(request, user, challenge_id, code):
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        raise IdentityError("invalid_code", "Enter the six-digit code.")
    reserve_limits([
        ("otp-verify-user", user.id, 3600, settings.OTP_VERIFY_ATTEMPTS_PER_HOUR),
        ("otp-verify-ip", client_ip(request), 3600, settings.OTP_VERIFY_IP_ATTEMPTS_PER_HOUR),
    ])
    now = timezone.now()
    with transaction.atomic():
        # Same lock ordering as request_challenge and final verification.
        locked_user = User.objects.select_for_update().get(id=user.id)
        challenge = own_challenge(request, locked_user, challenge_id, lock=True)
        if not locked_user.is_active:
            raise IdentityError("account_unavailable", "This account is unavailable.", 403)
        if challenge.status == "verified" and locked_user.phone == challenge.phone and locked_user.phone_verified_at:
            return locked_user
        if not challenge.active or challenge.status == "superseded":
            raise IdentityError("challenge_superseded", "Use the most recent code you requested.", 409)
        if challenge.expires_at and now >= challenge.expires_at:
            raise IdentityError("challenge_expired", "This code has expired. Request another code.")
        if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise IdentityError("challenge_locked", "Too many incorrect attempts. Request another code.", 429)
        if challenge.status == "verifying":
            if challenge.verifying_at and now - challenge.verifying_at > timedelta(seconds=settings.NOTIFICATION_LEASE_SECONDS):
                raise IdentityError("verification_unknown", "The previous verification could not be confirmed. Request another code.", 409)
            raise IdentityError("verification_in_progress", "Verification is still in progress. Please wait.", 409)
        if challenge.status != "sent" or not challenge.provider_pin_id:
            raise IdentityError("challenge_not_ready", "The code is not ready to verify. Check its status or request another code.", 409)
        lease = uuid.uuid4()
        challenge.status = PhoneChallenge.Status.VERIFYING
        challenge.verification_lease = lease
        challenge.verifying_at = now
        challenge.attempts += 1
        challenge.save(update_fields=["status", "verification_lease", "verifying_at", "attempts", "updated_at"])
        audit("phone.otp_verification", "started", locked_user, challenge.id, request)
    try:
        verified = TermiiClient().verify_otp(challenge.provider_pin_id, code, challenge.phone)
    except TermiiError as exc:
        PhoneChallenge.objects.filter(id=challenge_id, status="verifying", verification_lease=lease).update(
            status="unknown" if exc.ambiguous else "sent", error_code=exc.code, updated_at=timezone.now(),
        )
        audit("phone.otp_verification", "provider_unavailable", user, challenge.id, request)
        raise IdentityError("verification_unavailable", "Verification could not be confirmed. Please check the request status before continuing.", 503) from None
    if not verified:
        with transaction.atomic():
            PhoneChallenge.objects.filter(id=challenge_id, status="verifying", verification_lease=lease).update(
                status="locked" if challenge.attempts >= settings.OTP_MAX_ATTEMPTS else "sent",
                error_code="invalid_code", updated_at=timezone.now(),
            )
            audit("phone.otp_verification", "invalid_code", user, challenge.id, request)
        raise IdentityError("invalid_code", "The code is incorrect or no longer valid.")
    try:
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(id=user.id)
            current = own_challenge(request, locked_user, challenge_id, lock=True)
            if not locked_user.is_active or not current.active or current.status != "verifying" or current.verification_lease != lease:
                raise IdentityError("challenge_superseded", "This verification request is no longer active.", 409)
            if current.expires_at <= timezone.now():
                raise IdentityError("challenge_expired", "This code has expired. Request another code.")
            if User.objects.filter(phone=current.phone, phone_verified_at__isnull=False).exclude(id=user.id).exists():
                raise IdentityError("phone_unavailable", "This number cannot be used. Please contact support.", 409)
            locked_user.phone = current.phone
            locked_user.phone_verified_at = timezone.now()
            locked_user.auth_version += 1
            locked_user.save(update_fields=["phone", "phone_verified_at", "auth_version"])
            current.status = PhoneChallenge.Status.VERIFIED
            current.verified_at = locked_user.phone_verified_at
            current.active = False
            current.save(update_fields=["status", "verified_at", "active", "updated_at"])
            audit("phone.verified", "success", locked_user, current.id, request)
            notify_phone_verified(locked_user, current.id)
        request.session["identity_auth_version"] = locked_user.auth_version
        return locked_user
    except IntegrityError:
        raise IdentityError("phone_unavailable", "This number cannot be used. Please contact support.", 409) from None
