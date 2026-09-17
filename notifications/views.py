import hashlib
import json

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.db import transaction
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from integrations.termii import verify_webhook_signature

from .models import WebhookReceipt
from .services import enqueue_job

MAX_WEBHOOK_BYTES = 64 * 1024
DELIVERY_STATUSES = {
    "delivered": "delivered",
    "message sent": "sent",
    "sent": "sent",
    "received": "sent",
    "message failed": "failed",
    "failed": "failed",
    "rejected": "failed",
    "expired": "failed",
    "dnd active on phone number": "failed",
}


def _identifier(value):
    return value if isinstance(value, str) and 0 < len(value) <= 160 else ""


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def termii_webhook(request):
    # Optional delivery reporting cannot block OTP sends or accept unsigned data.
    if not settings.TERMII_WEBHOOKS_ENABLED:
        return Response({"detail": "Delivery webhooks are disabled."}, status=404)
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return Response({"detail": "Invalid content length."}, status=400)
    if content_length < 0 or content_length > MAX_WEBHOOK_BYTES:
        return Response({"detail": "Payload is too large."}, status=413)
    try:
        raw_body = request.body
    except RequestDataTooBig:
        return Response({"detail": "Payload is too large."}, status=413)
    if len(raw_body) > MAX_WEBHOOK_BYTES:
        return Response({"detail": "Payload is too large."}, status=413)
    if not verify_webhook_signature(raw_body, request.headers.get("X-Termii-Signature", "")):
        return Response({"detail": "Invalid signature."}, status=401)
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return Response({"detail": "Invalid JSON."}, status=400)
    if not isinstance(payload, dict):
        return Response({"detail": "Invalid event."}, status=400)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    provider_status = data.get("status")
    status = DELIVERY_STATUSES.get(provider_status.strip().lower(), "") if isinstance(provider_status, str) else ""
    # Allowlisted metadata only: payload.message may contain the actual OTP.
    with transaction.atomic():
        receipt, _ = WebhookReceipt.objects.get_or_create(
            payload_hash=hashlib.sha256(raw_body).hexdigest(),
            defaults={
                "provider_event_id": _identifier(data.get("id")),
                "provider_message_id": _identifier(data.get("message_id") or data.get("message_id_str")),
                "delivery_status": status,
            },
        )
        enqueue_job("termii_receipt", receipt.pk, f"termii_receipt:{receipt.pk}")
    return Response({"received": True})
