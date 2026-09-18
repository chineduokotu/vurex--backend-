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


class KYCVerification(models.Model):
    """One verification attempt for a user against a Dojah KYC service.

    Security constraints:
      - id_hash: SHA-256(DOJAH_HASH_SALT + ":" + raw_id_number). Used to detect
        duplicate submissions across users without storing the raw number.
      - masked_id: Display-safe version such as "223****1890". Stored for admin
        visibility and user history only.
      - Full NIN/BVN numbers are NEVER persisted. The application discards them
        immediately after the Dojah API call returns.
      - provider_reference: Dojah's internal reference, not the NIN/BVN itself.
    """

    class VerificationType(models.TextChoices):
        NIN = "nin", "NIN Verification"
        BVN = "bvn", "BVN Verification"
        CAC = "cac", "CAC Verification"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"
        REJECTED = "rejected", "Rejected"
        REQUIRES_REVIEW = "requires_review", "Requires Review"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "transactions.User",
        on_delete=models.CASCADE,
        related_name="kyc_verifications",
    )
    verification_type = models.CharField(max_length=16, choices=VerificationType.choices)
    provider = models.CharField(max_length=32, default="dojah")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)

    # id_hash: deterministic HMAC of the raw ID number, never reversible.
    id_hash = models.CharField(max_length=64, db_index=True)
    # masked_id: safe for display and admin logs, e.g. "223****1890".
    masked_id = models.CharField(max_length=24)

    provider_reference = models.CharField(max_length=256, blank=True)
    match_score = models.FloatField(default=0.0)
    failure_reason = models.CharField(max_length=128, blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        "transactions.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="kyc_reviews",
    )

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "-created_at"], name="kyc_user_created_idx"),
            models.Index(fields=["status", "created_at"], name="kyc_status_created_idx"),
        ]

    def __str__(self):
        return f"KYC {self.verification_type} for user {self.user_id} ({self.status})"
