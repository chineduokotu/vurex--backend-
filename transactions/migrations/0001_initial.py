import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("full_name", models.CharField(max_length=100)),
                ("email", models.EmailField(max_length=150, unique=True)),
                ("phone", models.CharField(blank=True, max_length=20, null=True)),
                ("password_hash", models.CharField(max_length=255)),
                ("role", models.CharField(choices=[("vendor", "Vendor"), ("buyer", "Buyer")], max_length=20)),
                ("subaccount_code", models.CharField(blank=True, max_length=100, null=True)),
                ("created_at", models.DateTimeField(default=timezone.now)),
            ],
            options={"db_table": "users"},
        ),
        migrations.CreateModel(
            name="Transaction",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("status", models.CharField(choices=[("created", "Created"), ("funded", "Funded"), ("in_transit", "In transit"), ("delivered", "Delivered"), ("disputed", "Disputed"), ("resolved", "Resolved")], default="created", max_length=20)),
                ("payment_ref", models.CharField(blank=True, max_length=100, null=True)),
                ("description", models.TextField(blank=True, null=True)),
                ("auto_release_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("updated_at", models.DateTimeField(default=timezone.now)),
                ("buyer", models.ForeignKey(blank=True, db_column="buyer_id", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="buyer_transactions", to="transactions.user")),
                ("vendor", models.ForeignKey(blank=True, db_column="vendor_id", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="vendor_transactions", to="transactions.user")),
            ],
            options={"db_table": "transactions"},
        ),
        migrations.CreateModel(
            name="Dispute",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("reason", models.TextField()),
                ("evidence_url", models.TextField(blank=True, null=True)),
                ("outcome", models.CharField(blank=True, choices=[("refund_buyer", "Refund buyer"), ("release_vendor", "Release vendor")], max_length=20, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("raised_by", models.ForeignKey(blank=True, db_column="raised_by", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="disputes", to="transactions.user")),
                ("transaction", models.ForeignKey(blank=True, db_column="transaction_id", null=True, on_delete=django.db.models.deletion.CASCADE, related_name="disputes", to="transactions.transaction")),
            ],
            options={"db_table": "disputes"},
        ),
        migrations.CreateModel(
            name="Shipment",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("track_type", models.CharField(choices=[("formal", "Formal"), ("informal", "Informal")], max_length=20)),
                ("origin_state", models.CharField(blank=True, max_length=50, null=True)),
                ("dest_state", models.CharField(blank=True, max_length=50, null=True)),
                ("est_delivery_days", models.IntegerField(blank=True, null=True)),
                ("tracking_ref", models.CharField(blank=True, max_length=100, null=True)),
                ("created_at", models.DateTimeField(default=timezone.now)),
                ("transaction", models.ForeignKey(blank=True, db_column="transaction_id", null=True, on_delete=django.db.models.deletion.CASCADE, related_name="shipments", to="transactions.transaction")),
            ],
            options={"db_table": "shipments"},
        ),
    ]
