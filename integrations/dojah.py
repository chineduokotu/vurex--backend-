"""Dojah KYC API client — NIN and BVN identity verification for Nigeria.

References:
    https://docs.dojah.io/docs/kyc-bvn
    https://docs.dojah.io/docs/kyc-nin

Design principles (mirrors integrations/termii.py):
  - Stateless HTTP boundary class. Callers own persistence, limits, and recovery.
  - DojahError carries only a safe error code. Never logs or re-raises raw responses.
  - @sensitive_variables() on every function that touches credentials or raw API data.
  - Explicit connect + read timeouts; redirects rejected; non-2xx handled predictably.
  - Callers must treat an ambiguous=True error as potentially having mutated remote state.
"""

import hashlib
import hmac
import math
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.views.decorators.debug import sensitive_variables


class DojahError(Exception):
    """Safe error metadata — never exposes request/response bodies or secrets."""

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
class NINResult:
    """Verified data returned from Dojah NIN lookup."""
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    middle_name: str = field(repr=False, default="")
    date_of_birth: str = field(repr=False, default="")
    phone: str = field(repr=False, default="")
    gender: str = ""


@dataclass(frozen=True)
class BVNResult:
    """Verified data returned from Dojah BVN lookup."""
    first_name: str = field(repr=False)
    last_name: str = field(repr=False)
    middle_name: str = field(repr=False, default="")
    date_of_birth: str = field(repr=False, default="")
    phone: str = field(repr=False, default="")
    bank_name: str = ""


@dataclass(frozen=True)
class _Configuration:
    app_id: str = field(repr=False)
    secret_key: str = field(repr=False)
    base_url: str
    connect_timeout: float
    read_timeout: float


def _setting_text(name: str) -> str:
    value = getattr(settings, name, "")
    if not isinstance(value, str) or not value.strip():
        raise DojahError("configuration_missing_" + name.lower())
    return value.strip()


def _timeout(name: str, default: float) -> float:
    value = getattr(settings, name, default)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise DojahError("configuration_invalid_" + name.lower()) from None
    if isinstance(value, bool) or not math.isfinite(number) or not 0 < number <= 60:
        raise DojahError("configuration_invalid_" + name.lower())
    return number


@sensitive_variables()
def _configuration() -> _Configuration:
    app_id = _setting_text("DOJAH_APP_ID")
    secret_key = _setting_text("DOJAH_SECRET_KEY")
    if any(c.isspace() for c in secret_key) or any(c.isspace() for c in app_id):
        raise DojahError("configuration_invalid_dojah_credentials")
    base_url = _setting_text("DOJAH_BASE_URL")
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname or ""
        valid_url = (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and (hostname.endswith(".dojah.io") or hostname == "dojah.io")
            and re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", hostname)
            and not parsed.query
            and not parsed.fragment
            and not any(c.isspace() for c in base_url)
            and "\\" not in base_url
        )
    except ValueError:
        raise DojahError("configuration_invalid_dojah_base_url") from None
    if not valid_url:
        raise DojahError("configuration_invalid_dojah_base_url")
    base_url = "https://" + parsed.netloc.lower()
    return _Configuration(
        app_id=app_id,
        secret_key=secret_key,
        base_url=base_url,
        connect_timeout=_timeout("DOJAH_CONNECT_TIMEOUT_SECONDS", 5),
        read_timeout=_timeout("DOJAH_READ_TIMEOUT_SECONDS", 20),
    )


@sensitive_variables()
def validate_configuration() -> None:
    """Validate Dojah credentials. Raises DojahError if misconfigured."""
    _configuration()


def _safe_str(value) -> str:
    """Return a clean string from an API field, empty string if absent."""
    if isinstance(value, str):
        return value.strip()
    return ""


def _retry_after(response: requests.Response) -> int | None:
    value = response.headers.get("Retry-After", "")
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,8}", value):
        return min(int(value), 86400)
    return None


class DojahClient:
    """Stateless HTTP boundary to the Dojah KYC API.

    Each public method makes a single request and returns a typed result or
    raises DojahError. Callers are responsible for rate limiting, persistence,
    and retry logic.
    """

    def __init__(self):
        self._config = _configuration()

    @sensitive_variables()
    def _get(self, path: str, params: dict) -> dict:
        """Execute an authenticated GET request to the Dojah API."""
        try:
            response = requests.get(
                self._config.base_url + path,
                params=params,
                headers={
                    "Accept": "application/json",
                    "AppId": self._config.app_id,
                    "Authorization": self._config.secret_key,
                },
                timeout=(self._config.connect_timeout, self._config.read_timeout),
                allow_redirects=False,
            )
        except requests.ConnectTimeout:
            raise DojahError("provider_connect_timeout", retryable=True) from None
        except requests.RequestException:
            # A read timeout or reset can happen after Dojah accepted the request.
            raise DojahError("provider_transport_unknown", ambiguous=True) from None

        try:
            status = response.status_code
            if 300 <= status < 400:
                raise DojahError("provider_redirect_rejected", ambiguous=True)
            if status in (401, 403):
                raise DojahError("provider_access_rejected")
            if status == 404:
                # 404 means the NIN/BVN does not exist in the Dojah records.
                raise DojahError("id_not_found")
            if status == 429:
                raise DojahError(
                    "provider_rate_limited",
                    ambiguous=True,
                    retry_after=_retry_after(response),
                )
            if status >= 500 or status == 408:
                raise DojahError("provider_unavailable", retryable=True)
            if 400 <= status < 500:
                # Parse error body for a user-intelligible reason.
                try:
                    body = response.json()
                    msg = body.get("error", {})
                    if isinstance(msg, dict):
                        msg = msg.get("message", "")
                    if isinstance(msg, str) and "invalid" in msg.lower():
                        raise DojahError("id_invalid_format")
                except (ValueError, AttributeError):
                    pass
                raise DojahError("provider_request_rejected")
            if not 200 <= status < 300:
                raise DojahError("provider_protocol_error", ambiguous=True)
            try:
                body = response.json()
            except ValueError:
                raise DojahError("provider_protocol_error", ambiguous=True) from None
            if not isinstance(body, dict):
                raise DojahError("provider_protocol_error", ambiguous=True)
            return body
        finally:
            response.close()

    @sensitive_variables()
    def verify_nin(self, nin: str) -> NINResult:
        """Look up a National Identification Number via Dojah.

        Args:
            nin: 11-digit Nigerian NIN.

        Returns:
            NINResult with official name and demographic data.

        Raises:
            DojahError: on any provider or input error.
        """
        if not isinstance(nin, str) or not re.fullmatch(r"[0-9]{11}", nin):
            raise DojahError("id_invalid_format")
        body = self._get("/api/v1/kyc/nin", {"nin": nin})
        entity = body.get("entity")
        if not isinstance(entity, dict):
            raise DojahError("provider_protocol_error", ambiguous=True)
        first_name = _safe_str(entity.get("firstname") or entity.get("first_name"))
        last_name = _safe_str(entity.get("surname") or entity.get("lastname") or entity.get("last_name"))
        if not first_name and not last_name:
            raise DojahError("provider_protocol_error", ambiguous=True)
        return NINResult(
            first_name=first_name,
            last_name=last_name,
            middle_name=_safe_str(entity.get("middlename") or entity.get("middle_name")),
            date_of_birth=_safe_str(entity.get("birthdate") or entity.get("date_of_birth")),
            phone=_safe_str(entity.get("phone") or entity.get("phone_number")),
            gender=_safe_str(entity.get("gender")),
        )

    @sensitive_variables()
    def verify_bvn(self, bvn: str) -> BVNResult:
        """Look up a Bank Verification Number via Dojah.

        Args:
            bvn: 11-digit Nigerian BVN.

        Returns:
            BVNResult with official name and banking data.

        Raises:
            DojahError: on any provider or input error.
        """
        if not isinstance(bvn, str) or not re.fullmatch(r"[0-9]{11}", bvn):
            raise DojahError("id_invalid_format")
        body = self._get("/api/v1/kyc/bvn/full", {"bvn": bvn})
        entity = body.get("entity")
        if not isinstance(entity, dict):
            raise DojahError("provider_protocol_error", ambiguous=True)
        first_name = _safe_str(entity.get("first_name") or entity.get("firstName"))
        last_name = _safe_str(entity.get("last_name") or entity.get("lastName"))
        if not first_name and not last_name:
            raise DojahError("provider_protocol_error", ambiguous=True)
        return BVNResult(
            first_name=first_name,
            last_name=last_name,
            middle_name=_safe_str(entity.get("middle_name") or entity.get("middleName")),
            date_of_birth=_safe_str(entity.get("date_of_birth") or entity.get("dateOfBirth")),
            phone=_safe_str(entity.get("phone_number") or entity.get("phone")),
            bank_name=_safe_str(entity.get("bank_name", "")),
        )


@sensitive_variables()
def verify_webhook_signature(raw_body: bytes, signature: str | None) -> bool:
    """Authenticate an incoming Dojah webhook payload using HMAC-SHA512.

    Returns False (never raises) if the webhook secret is not configured or
    the signature is absent/invalid. Callers must return 401 on False.
    """
    webhook_secret = getattr(settings, "DOJAH_WEBHOOK_SECRET", "")
    if (
        not isinstance(webhook_secret, str)
        or not webhook_secret.strip()
        or not isinstance(raw_body, bytes)
        or not isinstance(signature, str)
    ):
        return False
    if not re.fullmatch(r"[0-9a-fA-F]{128}", signature):
        return False
    expected = hmac.new(webhook_secret.strip().encode("utf-8"), raw_body, hashlib.sha512).digest()
    try:
        supplied = bytes.fromhex(signature)
    except ValueError:
        return False
    return hmac.compare_digest(expected, supplied)
