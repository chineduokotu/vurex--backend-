import uuid

from django.contrib.auth.hashers import identify_hasher, make_password
from django.db import models
from django.utils import timezone


class UserRole(models.TextChoices):
    VENDOR = "vendor", "Vendor"
    BUYER = "buyer", "Buyer"


class TransactionStatus(models.TextChoices):
    CREATED = "created", "Created"
    FUNDED = "funded", "Funded"
    IN_TRANSIT = "in_transit", "In transit"
    DELIVERED = "delivered", "Delivered"
    DISPUTED = "disputed", "Disputed"
    RESOLVED = "resolved", "Resolved"


class DisputeOutcome(models.TextChoices):
    REFUND_BUYER = "refund_buyer", "Refund buyer"
    RELEASE_VENDOR = "release_vendor", "Release vendor"


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=100)
    email = models.EmailField(max_length=150, unique=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=20, choices=UserRole.choices)
    subaccount_code = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "users"

    def save(self, *args, **kwargs):
        try:
            identify_hasher(self.password_hash)
        except ValueError:
            self.password_hash = make_password(self.password_hash)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.role})"

    @property
    def is_authenticated(self):
        return True


import random

def generate_tx_id():
    return f"VRX-{random.randint(1000, 9999)}"


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tx_id = models.CharField(max_length=20, default=generate_tx_id, unique=True)
    vendor = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="vendor_transactions",
        db_column="vendor_id",
        blank=True,
        null=True,
    )
    buyer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="buyer_transactions",
        db_column="buyer_id",
        blank=True,
        null=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(
        max_length=20,
        choices=TransactionStatus.choices,
        default=TransactionStatus.CREATED,
    )
    payment_ref = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    item_name = models.CharField(max_length=200, blank=True, null=True)
    delivery_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    escrow_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    delivery_location = models.TextField(blank=True, null=True)
    estimated_delivery_time = models.CharField(max_length=100, blank=True, null=True)
    category = models.CharField(max_length=100, blank=True, null=True)
    image_url = models.TextField(blank=True, null=True)
    
    # Timestamps for states
    auto_release_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    funded_at = models.DateTimeField(blank=True, null=True)
    shipped_at = models.DateTimeField(blank=True, null=True)
    delivered_at = models.DateTimeField(blank=True, null=True)
    disputed_at = models.DateTimeField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "transactions"

    def save(self, *args, **kwargs):
        if self.pk:
            self.updated_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tx_id} ({self.id}) - {self.status}"


class OTPCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=150)
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "otp_codes"

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.email} - {self.code}"


class Dispute(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="disputes",
        db_column="transaction_id",
        blank=True,
        null=True,
    )
    raised_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="disputes",
        db_column="raised_by",
        blank=True,
        null=True,
    )
    reason = models.TextField()
    description = models.TextField(blank=True, null=True)
    evidence_url = models.TextField(blank=True, null=True)
    defense_statement = models.TextField(blank=True, null=True)
    defense_evidence_url = models.TextField(blank=True, null=True)
    defense_submitted_at = models.DateTimeField(blank=True, null=True)
    outcome = models.CharField(
        max_length=20,
        choices=DisputeOutcome.choices,
        blank=True,
        null=True,
    )
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "disputes"

    def __str__(self):
        return f"{self.transaction_id} - {self.outcome or 'pending'}"
