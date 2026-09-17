"""Termii's live messaging/token API with explicit, non-retrying failures.

References:
https://developers.termii.com/send-token
https://developers.termii.com/verify-token
https://developers.termii.com/messaging-api
https://developers.termii.com/events-and-reports

A successful send means provider acceptance, not handset delivery. In particular,
an ambiguous failure must not cause a caller to automatically send another OTP/SMS.
"""

import base64
import binascii
import hashlib
import hmac
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class TermiiError(Exception):
    """Safe error metadata; never includes request/response bodies or secrets."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        ambiguous: bool = False,
        retry_after: int | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


@dataclass(frozen=True)
class OTPDelivery:
    pin_id: str = field(repr=False)
    message_id: str = field(repr=False)


@dataclass(frozen=True)
class _Configuration:
    api_key: str = field(repr=False)
    base_url: str
    sender_id: str
    channel: str
    connect_timeout: float
    read_timeout: float


def _setting_text(name: str) -> str:
    value = getattr(settings, name, "")
    if not isinstance(value, str) or not value.strip():
        raise TermiiError("configuration_missing_" + name.lower())
    return value.strip()


def _timeout(name: str, default: float) -> float:
    value = getattr(settings, name, default)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise TermiiError("configuration_invalid_" + name.lower()) from None
    if isinstance(value, bool) or not math.isfinite(number) or not 0 < number <= 60:
        raise TermiiError("configuration_invalid_" + name.lower())
    return number


@sensitive_variables()
def _configuration() -> _Configuration:
    api_key = _setting_text("TERMII_API_KEY")
    if any(character.isspace() for character in api_key):
        raise TermiiError("configuration_invalid_termii_api_key")
    base_url = _setting_text("TERMII_BASE_URL")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname or ""
        valid_url = (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and hostname.endswith(".termii.com")
            and re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", hostname)
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/", "/api", "/api/")
            and not any(character.isspace() for character in base_url)
            and "\\" not in base_url
        )
    except ValueError:
        raise TermiiError("configuration_invalid_termii_base_url") from None
    if not valid_url:
        raise TermiiError("configuration_invalid_termii_base_url")
    # Dashboard URLs with or without /api refer to the same configured origin.
    base_url = "https://" + parsed.netloc.lower()
    sender_id = _setting_text("TERMII_SENDER_ID")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ]{1,9}[A-Za-z0-9]", sender_id):
        raise TermiiError("configuration_invalid_termii_sender_id")
    channel = getattr(settings, "TERMII_CHANNEL", "dnd")
    if channel != "dnd":
        raise TermiiError("configuration_requires_termii_transactional_channel")
    return _Configuration(
        api_key=api_key,
        base_url=base_url,
        sender_id=sender_id,
        channel=channel,
        connect_timeout=_timeout("TERMII_CONNECT_TIMEOUT_SECONDS", 3),
        read_timeout=_timeout("TERMII_READ_TIMEOUT_SECONDS", 10),
    )


@sensitive_variables()
def validate_configuration(*, require_webhook: bool = False) -> None:
    """Validate send/verify settings; optionally validate delivery-receipt settings."""
    _configuration()
    if require_webhook:
        _setting_text("TERMII_WEBHOOK_SECRET")
        if getattr(settings, "TERMII_WEBHOOK_SIGNATURE_ENCODING", "hex") not in (
            "hex",
            "base64",
        ):
            raise TermiiError("configuration_invalid_termii_webhook_signature_encoding")


def _phone_digits(phone: str) -> str:
    if not isinstance(phone, str) or not re.fullmatch(r"\+?[1-9][0-9]{6,14}", phone):
        raise TermiiError("invalid_phone")
    return phone.removeprefix("+")


def _identifier(body: dict, *keys: str) -> str:
    values = []
    for key in keys:
        value = body.get(key)
        if value is None:
            continue
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            value = str(value)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", value):
            raise TermiiError("provider_protocol_error", ambiguous=True)
        values.append(value)
    if not values or any(value != values[0] for value in values):
        raise TermiiError("provider_protocol_error", ambiguous=True)
    return values[0]


def _retry_after(response: requests.Response) -> int | None:
    value = response.headers.get("Retry-After", "")
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value):
        return min(int(value), 86400)
    return None


class TermiiClient:
    """Stateless HTTP boundary; callers own limits, persistence, and recovery."""

    def __init__(self):
        self._config = _configuration()

    @sensitive_variables()
    def _post(self, path: str, payload: dict) -> dict:
        request_body = {**payload, "api_key": self._config.api_key}
        try:
            response = requests.post(
                self._config.base_url + path,
                json=request_body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=(self._config.connect_timeout, self._config.read_timeout),
                allow_redirects=False,
            )
        except requests.ConnectTimeout:
            # Connection setup timed out before the HTTP request could be sent.
            raise TermiiError("provider_connect_timeout", retryable=True) from None
        except requests.RequestException:
            # A read timeout/reset can happen after Termii accepted the request.
            raise TermiiError("provider_transport_unknown", ambiguous=True) from None

        try:
            status = response.status_code
            if 300 <= status < 400:
                raise TermiiError("provider_redirect_rejected", ambiguous=True)
            if status in (401, 403):
                raise TermiiError("provider_access_rejected")
            if status == 429:
                # Termii does not document an idempotency/acceptance guarantee for
                # rate-limited sends. Preserve timing without authorizing replay.
                raise TermiiError(
                    "provider_rate_limited", ambiguous=True, retry_after=_retry_after(response)
                )
            if status >= 500 or status == 408:
                raise TermiiError("provider_unavailable_unknown", ambiguous=True)
            if 400 <= status < 500:
                raise TermiiError("provider_request_rejected")
            if not 200 <= status < 300:
                raise TermiiError("provider_protocol_error", ambiguous=True)
            try:
                body = response.json()
            except ValueError:
                raise TermiiError("provider_protocol_error", ambiguous=True) from None
            if not isinstance(body, dict):
                raise TermiiError("provider_protocol_error", ambiguous=True)
            return body
        finally:
            response.close()

    @sensitive_variables()
    def send_otp(
        self,
        phone: str,
        *,
        ttl_seconds: int = 300,
        length: int = 6,
        max_attempts: int = 3,
    ) -> OTPDelivery:
        digits = _phone_digits(phone)
        if (
            type(ttl_seconds) is not int
            or not 60 <= ttl_seconds <= 3600
            or ttl_seconds % 60
            or type(length) is not int
            or not 4 <= length <= 8
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 10
        ):
            raise TermiiError("invalid_otp_policy")
        minutes = ttl_seconds // 60
        minute_label = "minute" if minutes == 1 else "minutes"
        body = self._post(
            "/api/sms/otp/send",
            {
                # Current reference examples include both fields; pin_type is
                # the required field listed in the endpoint's parameter table.
                "message_type": "NUMERIC",
                "pin_type": "NUMERIC",
                "to": digits,
                "from": self._config.sender_id,
                "channel": self._config.channel,
                "pin_attempts": max_attempts,
                "pin_time_to_live": minutes,
                "pin_length": length,
                "pin_placeholder": "<OTP>",
                "message_text": (
                    "Your Vurex verification code is <OTP>. "
                    f"It expires in {minutes} {minute_label}. Do not share it."
                ),
            },
        )
        status = body.get("status")
        if status is not None and str(status) != "200":
            raise TermiiError("provider_protocol_error", ambiguous=True)
        if body.get("smsStatus") != "Message Sent":
            raise TermiiError("provider_protocol_error", ambiguous=True)
        for key in ("phone_number", "to"):
            if key in body and body[key] != digits:
                raise TermiiError("provider_protocol_error", ambiguous=True)
        return OTPDelivery(
            pin_id=_identifier(body, "pin_id", "pinId"),
            message_id=_identifier(body, "message_id_str", "message_id"),
        )

    @sensitive_variables()
    def verify_otp(self, pin_id: str, code: str, expected_phone: str) -> bool:
        digits = _phone_digits(expected_phone)
        if not isinstance(pin_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,255}", pin_id):
            raise TermiiError("invalid_pin_reference")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{4,8}", code):
            return False
        body = self._post("/api/sms/otp/verify", {"pin_id": pin_id, "pin": code})
        verified = body.get("verified")
        if verified is False or (isinstance(verified, str) and verified.lower() == "false"):
            return False
        if not (verified is True or (isinstance(verified, str) and verified.lower() == "true")):
            raise TermiiError("provider_protocol_error", ambiguous=True)
        returned_pin_id = _identifier(body, "pinId", "pin_id")
        msisdn = body.get("msisdn")
        if not isinstance(msisdn, str) or not re.fullmatch(r"\+?[1-9][0-9]{6,14}", msisdn):
            raise TermiiError("provider_protocol_error", ambiguous=True)
        return hmac.compare_digest(returned_pin_id, pin_id) and hmac.compare_digest(
            msisdn.removeprefix("+"), digits
        )

    @sensitive_variables()
    def send_sms(self, phone: str, text: str) -> str:
        digits = _phone_digits(phone)
        if not isinstance(text, str) or not text.strip() or len(text) > 1600:
            raise TermiiError("invalid_message")
        body = self._post(
            "/api/sms/send",
            {
                "to": digits,
                "from": self._config.sender_id,
                "channel": self._config.channel,
                "sms": text,
                "type": "plain",
            },
        )
        if body.get("code") != "ok":
            raise TermiiError("provider_protocol_error", ambiguous=True)
        return _identifier(body, "message_id_str", "message_id")

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
        return verify_webhook_signature(raw_body, signature)


@sensitive_variables()
def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Authenticate exact payload bytes; encoding must match the account setup.

    Termii documents HMAC-SHA512 and X-Termii-Signature but does not specify the
    signature encoding. This accepts only the explicitly configured encoding.
    """
    if not getattr(settings, "TERMII_WEBHOOKS_ENABLED", False):
        return False
    secret = getattr(settings, "TERMII_WEBHOOK_SECRET", "")
    encoding = getattr(settings, "TERMII_WEBHOOK_SIGNATURE_ENCODING", "hex")
    if (
        not isinstance(secret, str)
        or not secret.strip()
        or not isinstance(raw_body, bytes)
        or not isinstance(signature, str)
    ):
        return False
    if encoding == "hex":
        if not re.fullmatch(r"[0-9a-fA-F]{128}", signature):
            return False
        supplied_digest = bytes.fromhex(signature)
    elif encoding == "base64":
        try:
            supplied_digest = base64.b64decode(signature, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(supplied_digest) != 64:
            return False
    else:
        return False
    expected_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).digest()
    return hmac.compare_digest(expected_digest, supplied_digest)
