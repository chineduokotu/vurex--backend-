from functools import wraps

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.tokens import RefreshToken

from transactions.authentication import CustomJWTAuthentication
from transactions.models import User
from transactions.serializers import UserRegisterSerializer, UserLoginSerializer, UserSerializer

from .csrf import enforce_identity_csrf
from .errors import IdentityError
from .models import PhoneChallenge
from .rate_limits import reserve_limits
from .security import client_ip, session_binding, start_onboarding_session
from .services import (
    audit, challenge_data, get_kyc_status, own_challenge, request_challenge,
    submit_bvn_verification, submit_nin_verification, verify_challenge,
)


def identity_api(methods):
    def decorator(view):
        @api_view(methods)
        @authentication_classes([])
        @permission_classes([AllowAny])
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            try:
                if not request.is_secure():
                    raise IdentityError("https_required", "Use the secure HTTPS application URL to continue.", 400)
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    enforce_identity_csrf(request)
                    if not isinstance(request.data, dict):
                        raise IdentityError("invalid_request", "Send a JSON object with the required fields.")
                response = view(request, *args, **kwargs)
            except IdentityError as exc:
                payload = {"error": exc.message, "code": exc.code}
                if exc.retry_after is not None:
                    payload["retry_after"] = exc.retry_after
                response = Response(payload, status=exc.status)
                if exc.retry_after is not None:
                    response["Retry-After"] = str(exc.retry_after)
            response["Cache-Control"] = "no-store"
            return response
        return wrapped
    return decorator


def resolve_identity(request):
    user_id = request.session.get("identity_user_id")
    if user_id:
        user = User.objects.filter(id=user_id, is_active=True).first()
        if user and user.auth_version == request.session.get("identity_auth_version"):
            return user
    # A stale bearer must not prevent a fresh onboarding session or CSRF bootstrap.
    try:
        result = CustomJWTAuthentication().authenticate(request)
        if result:
            return result[0]
    except (AuthenticationFailed, InvalidToken):
        pass
    return None


def require_identity(request):
    user = resolve_identity(request)
    if user is None:
        raise IdentityError("sign_in_required", "Please sign in to continue phone verification.", 401)
    if request.session.get("identity_user_id") != str(user.id):
        raise IdentityError("sign_in_required", "Please sign in again to start a secure phone verification session.", 401)
    return user


def user_data(user):
    return {**UserSerializer(user).data, "phone_verified_at": user.phone_verified_at,
            "phone_verified": bool(user.phone_verified_at)}


def token_response(user):
    refresh = RefreshToken.for_user(user)
    refresh["auth_version"] = user.auth_version
    return {"access": str(refresh.access_token), "refresh": str(refresh),
            "user": user_data(user), "onboarding_required": False}


def onboarding_data(request, user):
    challenge = None
    if user and request.session.get("identity_binding"):
        challenge = PhoneChallenge.objects.filter(
            user=user, session_binding=session_binding(request),
        ).order_by("-created_at").first()
    return {
        "authenticated": user is not None,
        "user": user_data(user) if user else None,
        "phone_verified": bool(user and user.phone_verified_at),
        "onboarding_required": bool(user and not user.phone_verified_at),
        "challenge": challenge_data(challenge) if challenge else None,
        "csrf_token": get_token(request),
    }


@identity_api(["GET"])
def onboarding(request):
    return Response(onboarding_data(request, resolve_identity(request)))


@identity_api(["POST"])
def register(request):
    reserve_limits([("register-ip", client_ip(request), 3600, settings.REGISTRATION_IPS_PER_HOUR)])
    serializer = UserRegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"error": serializer.errors, "code": "invalid_registration"}, status=400)
    data = serializer.validated_data
    candidate = User(full_name=data["full_name"], email=data["email"].strip().lower(), role=data["role"])
    try:
        validate_password(data["password"], candidate)
    except ValidationError as exc:
        return Response({"error": {"password": exc.messages}, "code": "invalid_password"}, status=400)
    try:
        with transaction.atomic():
            # Serialize case-insensitive registration for an address without a destructive email migration.
            reserve_limits([("registration-address-lock", candidate.email, 86400, 1000000)])
            if User.objects.filter(email__iexact=candidate.email).exists():
                raise IdentityError("account_exists", "Unable to register this email. Sign in if you already have an account.", 409)
            candidate.password_hash = make_password(data["password"])
            candidate.save()
            audit("account.registered", "pending_phone", candidate, request=request)
    except IntegrityError:
        raise IdentityError("account_exists", "Unable to register this email. Sign in if you already have an account.", 409) from None
    start_onboarding_session(request, candidate)
    return Response(onboarding_data(request, candidate), status=201)


@identity_api(["POST"])
def login(request):
    serializer = UserLoginSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"error": serializer.errors, "code": "invalid_login"}, status=400)
    data = serializer.validated_data
    email = data["email"].strip().lower()
    reserve_limits([
        ("login-ip", client_ip(request), 3600, settings.LOGIN_IP_ATTEMPTS_PER_HOUR),
        ("login-email", email, 3600, settings.LOGIN_ACCOUNT_ATTEMPTS_PER_HOUR),
    ])
    candidates = list(User.objects.filter(email__iexact=email)[:2])
    user = candidates[0] if len(candidates) == 1 else None
    # Unknown addresses still incur one password-hash operation.
    if user:
        valid = check_password(data["password"], user.password_hash)
    else:
        make_password(data["password"])
        valid = False
    # Older automatically created buyers had this shared placeholder password.
    # Such records require a separate account-claim flow, never SMS attachment.
    if data["password"] == "defaultpassword123":
        valid = False
    if not valid or not user.is_active:
        audit("account.login", "rejected", request=request)
        raise IdentityError("invalid_credentials", "Invalid email or password.", 401)
    start_onboarding_session(request, user)
    audit("account.login", "success", user, request=request)
    if not user.phone_verified_at:
        return Response(onboarding_data(request, user))
    return Response(token_response(user))


@identity_api(["POST"])
def logout(request):
    user = resolve_identity(request)
    if user:
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(id=user.id)
            locked_user.auth_version += 1
            locked_user.save(update_fields=["auth_version"])
            audit("account.logout", "sessions_revoked", locked_user, request=request)
    request.session.flush()
    return Response({"status": "signed_out"})


@identity_api(["POST"])
def create_challenge(request):
    user = require_identity(request)
    challenge = request_challenge(request, user, phone=request.data.get("phone"))
    return Response({"challenge": challenge_data(challenge), "phone_verified": bool(user.phone_verified_at)}, status=202)


@identity_api(["GET"])
def get_challenge(request, challenge_id):
    user = require_identity(request)
    challenge = own_challenge(request, user, challenge_id)
    return Response({"challenge": challenge_data(challenge), "phone_verified": bool(user.phone_verified_at)})


@identity_api(["POST"])
def resend_challenge(request, challenge_id):
    user = require_identity(request)
    challenge = request_challenge(request, user, resend_id=challenge_id)
    return Response({"challenge": challenge_data(challenge), "phone_verified": bool(user.phone_verified_at)}, status=202)


@identity_api(["POST"])
def verify_phone(request, challenge_id):
    user = require_identity(request)
    verified_user = verify_challenge(request, user, challenge_id, request.data.get("code"))
    return Response({"status": "verified", **token_response(verified_user)})


@identity_api(["POST"])
def retired_otp(request):
    return Response({"error": "This verification flow has been replaced. Refresh the application and sign in.",
                     "code": "verification_flow_replaced"}, status=410)


# ─── KYC / Dojah Views ───────────────────────────────────────────────────────

def _require_authenticated_user(request):
    """Return the authenticated User or raise IdentityError. KYC requires a full JWT session."""
    from transactions.authentication import CustomJWTAuthentication
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework_simplejwt.exceptions import InvalidToken
    try:
        result = CustomJWTAuthentication().authenticate(request)
        if result:
            user, _ = result
            if user.is_active:
                return user
    except (AuthenticationFailed, InvalidToken):
        pass
    raise IdentityError("sign_in_required", "Please sign in to verify your identity.", 401)


@identity_api(["GET"])
def kyc_status(request):
    """GET /api/me/kyc/status/ — Return the authenticated user's KYC status summary."""
    user = _require_authenticated_user(request)
    return Response(get_kyc_status(user))


@identity_api(["POST"])
def kyc_verify_nin(request):
    """POST /api/me/kyc/verify-nin/ — Submit NIN for Dojah verification.

    Body: { "nin": "12345678901" }
    """
    user = _require_authenticated_user(request)
    nin = request.data.get("nin", "")
    record = submit_nin_verification(user, nin, request)
    status_map = {
        "verified": 200,
        "requires_review": 202,
        "failed": 422,
        "rejected": 422,
    }
    http_status = status_map.get(record.status, 422)
    failure_messages = {
        "name_mismatch": "The name on your NIN does not match your account name. Please check your account name and try again.",
        "name_partial_match": "Your identity is under review. You will be notified of the outcome.",
        "nin_not_found": "This NIN was not found. Please check the number and try again.",
        "nin_invalid_format": "The NIN format is not valid. Please enter all 11 digits.",
    }
    return Response({
        "status": record.status,
        "masked_id": record.masked_id,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "message": failure_messages.get(record.failure_reason, "") if record.status != "verified" else "NIN verified successfully.",
    }, status=http_status)


@identity_api(["POST"])
def kyc_verify_bvn(request):
    """POST /api/me/kyc/verify-bvn/ — Submit BVN for Dojah verification.

    Body: { "bvn": "12345678901" }
    """
    user = _require_authenticated_user(request)
    bvn = request.data.get("bvn", "")
    record = submit_bvn_verification(user, bvn, request)
    status_map = {
        "verified": 200,
        "requires_review": 202,
        "failed": 422,
        "rejected": 422,
    }
    http_status = status_map.get(record.status, 422)
    failure_messages = {
        "name_mismatch": "The name on your BVN does not match your account name. Please check your account name and try again.",
        "name_partial_match": "Your identity is under review. You will be notified of the outcome.",
        "bvn_not_found": "This BVN was not found. Please check the number and try again.",
        "bvn_invalid_format": "The BVN format is not valid. Please enter all 11 digits.",
    }
    return Response({
        "status": record.status,
        "masked_id": record.masked_id,
        "verified_at": record.verified_at.isoformat() if record.verified_at else None,
        "message": failure_messages.get(record.failure_reason, "") if record.status != "verified" else "BVN verified successfully.",
    }, status=http_status)
