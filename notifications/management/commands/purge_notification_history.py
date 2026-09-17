from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from identity.models import AuditEvent, PhoneChallenge, RateLimitBucket
from notifications.models import DispatchJob, Notification, WebhookReceipt
from transactions.models import OTPCode


class Command(BaseCommand):
    help = "Purge bounded batches of expired verification/delivery history; retain unresolved sends."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)
        parser.add_argument("--audit-days", type=int, default=90)
        parser.add_argument("--batch-size", type=int, default=1000)

    def handle(self, *args, **options):
        if options["days"] < 7 or options["audit_days"] < options["days"] or not 1 <= options["batch_size"] <= 10000:
            raise CommandError("Use at least seven history days, audit retention >= history retention, and a batch size from 1 to 10000.")
        cutoff = timezone.now() - timedelta(days=options["days"])
        queries = [
            ("phone_challenges", PhoneChallenge.objects.filter(created_at__lt=cutoff).exclude(status__in=["unknown", "sending", "verifying"])),
            ("notifications", Notification.objects.filter(created_at__lt=cutoff, status__in=["sent", "delivered", "failed"])),
            ("dispatch_jobs", DispatchJob.objects.filter(created_at__lt=cutoff, status__in=["completed", "failed"])),
            ("webhook_receipts", WebhookReceipt.objects.filter(received_at__lt=cutoff, status__in=["processed", "ignored"])),
            ("rate_windows", RateLimitBucket.objects.filter(expires_at__lt=timezone.now() - timedelta(days=1))),
            ("legacy_email_codes", OTPCode.objects.all()),
            ("audit_events", AuditEvent.objects.filter(created_at__lt=timezone.now() - timedelta(days=options["audit_days"]))),
        ]
        for label, query in queries:
            ids = list(query.order_by("pk").values_list("pk", flat=True)[:options["batch_size"]])
            count, _ = query.filter(pk__in=ids).delete()
            self.stdout.write(f"{label}: {count} records removed")
