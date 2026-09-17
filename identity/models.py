import uuid

from django.db import models
from django.utils import timezone


class PhoneChallenge(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        SENDING = "sending"
        SENT = "sent"
        VERIFYING = "verifying"
        VERIFIED = "verified"
        EXPIRED = "expired"
        FAILED = "failed"
        UNKNOWN = "unknown"
        SUPERSEDED = "superseded"
        LOCKED = "locked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("transactions.User", on_delete=models.CASCADE, related_name="phone_challenges")
    phone = models.CharField(max_length=20)
    purpose = models.CharField(max_length=32, default="phone_verification")
    session_binding = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    request_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    active = models.BooleanField(default=True)
    provider_pin_id = models.CharField(max_length=255, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    delivery_status = models.CharField(max_length=24, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    verification_lease = models.UUIDField(null=True, blank=True)
    verifying_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    dispatch_deadline = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    resend_available_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="unique_phone_request_key"),
            models.UniqueConstraint(fields=["user", "purpose"], condition=models.Q(active=True), name="one_active_phone_challenge"),
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="phone_user_created_idx"),
            models.Index(fields=["status", "created_at"], name="phone_status_created_idx"),
        ]

    def __str__(self):
        return f"Phone challenge {self.id} ({self.status})"


class RateLimitBucket(models.Model):
    key = models.CharField(max_length=64)
    window_start = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "window_start"], name="unique_identity_rate_window")]


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("transactions.User", on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=64)
    outcome = models.CharField(max_length=64)
    subject_id = models.CharField(max_length=64, blank=True)
    ip_fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"], name="audit_user_created_idx")]

    def __str__(self):
        return f"{self.action}: {self.outcome}"
