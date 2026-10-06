import hmac
import hashlib
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings


class PaystackError(Exception):
    pass


def _headers():
    if (
        not settings.PAYSTACK_SECRET_KEY
        or "xxxx" in settings.PAYSTACK_SECRET_KEY
        or (not settings.DEBUG and settings.PAYSTACK_SECRET_KEY.startswith("sk_test_"))
    ):
        raise PaystackError("PAYSTACK_SECRET_KEY is not configured")
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _post(path, payload):
    url = f"{settings.PAYSTACK_BASE_URL}{path}"
    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=20)
    except requests.RequestException as exc:
        raise PaystackError(str(exc)) from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise PaystackError(f"Invalid response from Paystack ({response.status_code})") from exc

    if response.status_code >= 400 or not body.get("status", False):
        raise PaystackError(body.get("message", "Paystack request failed"))
    return body.get("data") or {}


def naira_to_kobo(amount):
    return int((Decimal(amount) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def create_subaccount(business_name, bank_code, account_number):
    return _post(
        "/subaccount",
        {
            "business_name": business_name,
            "settlement_bank": bank_code,
            "account_number": account_number,
            "percentage_charge": 1.5,
        },
    )


def initialize_transaction(transaction):
    total_due = (
        Decimal(transaction.amount)
        + Decimal(transaction.escrow_fee or 0)
        + Decimal(transaction.delivery_fee or 0)
    )
    return _post(
        "/transaction/initialize",
        {
            "email": transaction.buyer.email,
            "amount": naira_to_kobo(total_due),
            "reference": str(transaction.id),
            "metadata": {
                "transaction_id": str(transaction.id),
                "vendor_id": str(transaction.vendor_id),
            },
        },
    )


def create_transfer(amount, recipient, reason):
    return _post(
        "/transfer",
        {
            "source": "balance",
            "amount": naira_to_kobo(amount),
            "recipient": recipient,
            "reason": reason,
        },
    )


def create_refund(payment_ref, amount):
    return _post(
        "/refund",
        {
            "transaction": payment_ref,
            "amount": naira_to_kobo(amount),
        },
    )


def verify_webhook_signature(request):
    paystack_signature = request.headers.get("x-paystack-signature")
    if (
        not paystack_signature
        or not settings.PAYSTACK_SECRET_KEY
        or "xxxx" in settings.PAYSTACK_SECRET_KEY
        or (not settings.DEBUG and settings.PAYSTACK_SECRET_KEY.startswith("sk_test_"))
    ):
        return False
    raw_body = getattr(request, "body", None)
    if raw_body is None and hasattr(request, "_request"):
        raw_body = request._request.body
    if raw_body is None:
        return False
    computed = hmac.new(
        settings.PAYSTACK_SECRET_KEY.encode("utf-8"),
        raw_body,
        hashlib.sha512,
    ).hexdigest()
    return hmac.compare_digest(computed, paystack_signature)
