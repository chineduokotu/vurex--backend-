import ipaddress
import re
import secrets
import time

from django.conf import settings
from django.middleware.csrf import rotate_token
from django.utils.crypto import salted_hmac
from django.views.debug import SafeExceptionReporterFilter

from .errors import IdentityError


def fingerprint(value):
    return salted_hmac("vurex.identity", str(value), algorithm="sha256").hexdigest()


def normalize_phone(value):
    # This release accepts Nigerian mobile numbers only; no silent country guessing.
    if not isinstance(value, str) or len(value) > 40:
        raise IdentityError("invalid_phone", "Enter a valid Nigerian mobile number.")
    compact = re.sub(r"[\s()\-]", "", value)
    match = re.fullmatch(r"(?:\+234|234|0)?([789][0-9]{9})", compact, flags=re.ASCII)
    if not match:
        raise IdentityError("invalid_phone", "Use a Nigerian mobile number, such as 08012345678 or +2348012345678.")
    return "+234" + match.group(1)


def masked_phone(phone):
    return "+234 ••• ••• " + phone[-4:]


def client_ip(request):
    remote = request.META.get("REMOTE_ADDR", "")
    trusted = set(settings.IDENTITY_TRUSTED_PROXY_IPS)
    candidates = [remote]
    if remote in trusted:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if len(forwarded) <= 1024:
            candidates = [part.strip() for part in forwarded.split(",")] + [remote]
    for candidate in reversed(candidates):
        try:
            address = str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
        if address not in trusted or len(candidates) == 1:
            return address
    return remote or "unknown"


def start_onboarding_session(request, user):
    request.session.flush()
    request.session["identity_user_id"] = str(user.id)
    request.session["identity_binding"] = secrets.token_urlsafe(32)
    request.session["identity_auth_version"] = user.auth_version
    request.session["identity_password_at"] = int(time.time())
    request.session.set_expiry(settings.ONBOARDING_SESSION_TTL_SECONDS)
    rotate_token(request)


def session_binding(request):
    binding = request.session.get("identity_binding")
    if not binding:
        raise IdentityError("sign_in_required", "Please sign in again to verify your phone.", 401)
    return fingerprint(binding)


def require_recent_password(request, user):
    if not user.phone_verified_at:
        return
    authenticated_at = request.session.get("identity_password_at", 0)
    if int(time.time()) - authenticated_at > settings.PHONE_CHANGE_REAUTH_SECONDS:
        raise IdentityError("reauthentication_required", "Please sign in again before changing your phone number.", 403)


class PrivateExceptionReporterFilter(SafeExceptionReporterFilter):
    """Error reports must not capture OTPs, provider credentials, or session locals."""

    def get_traceback_frame_variables(self, request, tb_frame):
        return []

    def get_post_parameters(self, request):
        return {}
