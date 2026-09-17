import uuid

from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """A business-authorized message. Text is resolved from a fixed template."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("transactions.User", on_delete=models.PROTECT)
    recipient = models.CharField(max_length=20)
    template = models.CharField(max_length=64)
    deduplication_key = models.CharField(max_length=160, unique=True)
    status = models.CharField(max_length=20, default="queued")
    provider_message_id = models.CharField(max_length=160, null=True, blank=True, unique=True)
    last_error = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"], name="notif_status_created_idx")]

    def __str__(self):
        return f"{self.id}: {self.template} ({self.status})"


class NotificationAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="attempts")
    attempt = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=20, default="sending")
    provider_message_id = models.CharField(max_length=160, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["notification", "attempt"], name="notif_unique_attempt")]


class DispatchJob(models.Model):
    """Transactional outbox and durable queue; running external sends are never replayed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    topic = models.CharField(max_length=32)
    object_id = models.UUIDField()
    deduplication_key = models.CharField(max_length=160, unique=True)
    status = models.CharField(max_length=20, default="pending")
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "available_at"], name="dispatch_due_idx"),
            models.Index(fields=["status", "locked_at"], name="dispatch_lease_idx"),
        ]


class WebhookReceipt(models.Model):
    """Only the allowlisted delivery metadata is retained, never raw SMS content."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payload_hash = models.CharField(max_length=64, unique=True)
    provider_event_id = models.CharField(max_length=160, blank=True)
    provider_message_id = models.CharField(max_length=160, blank=True, db_index=True)
    delivery_status = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, default="pending")
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "received_at"], name="receipt_status_received_idx")]


class DailySmsBudget(models.Model):
    """Counts attempts conservatively, including sends with an unknown outcome."""

    day = models.DateField(primary_key=True)
    attempts = models.PositiveIntegerField(default=0)

