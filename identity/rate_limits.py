from datetime import datetime, timezone as datetime_timezone

from django.db import transaction
from django.utils import timezone

from .errors import IdentityError
from .models import RateLimitBucket
from .security import fingerprint


def reserve_limits(limits):
    """Atomically reserve (scope, subject, window_seconds, maximum) across workers.

    Fixed UTC windows are shared in PostgreSQL. Call outside an enclosing business
    transaction when failed business attempts must still count against the limit.
    """
    epoch = int(timezone.now().timestamp())
    entries = sorted(
        (fingerprint(f"{scope}:{subject}:{seconds}"), seconds, maximum)
        for scope, subject, seconds, maximum in limits
    )
    with transaction.atomic():
        buckets = []
        for key, seconds, maximum in entries:
            start = epoch - epoch % seconds
            bucket, _ = RateLimitBucket.objects.get_or_create(
                key=key, window_start=start,
                defaults={"expires_at": datetime.fromtimestamp(start + seconds, datetime_timezone.utc)},
            )
            bucket = RateLimitBucket.objects.select_for_update().get(pk=bucket.pk)
            if bucket.count >= maximum:
                raise IdentityError("rate_limited", "Too many attempts. Please try again later.", 429, start + seconds - epoch)
            buckets.append(bucket)
        for bucket in buckets:
            bucket.count += 1
            bucket.save(update_fields=["count"])
