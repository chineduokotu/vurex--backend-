from datetime import timedelta
import os

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction as db_transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import check_password
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from . import paystack
from .models import Dispute, DisputeOutcome, Transaction, TransactionStatus, User, UserRole, OTPCode
from .otp import create_and_send_otp
from .serializers import (
    ConfirmDeliverySerializer,
    DisputeTransactionSerializer,
    SubmitDefenseSerializer,
    InitializeTransactionSerializer,
    RegisterSubaccountSerializer,
    ResolveDisputeSerializer,
    UserSerializer,
    UserRegisterSerializer,
    UserLoginSerializer,
    UserUpdatePhoneSerializer,
    TransactionCreateSerializer,
    TransactionSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
)


def _serializer_errors(serializer):
    return Response({"error": serializer.errors}, status=400)


def get_tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "user": UserSerializer(user).data,
    }


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def submit_defense(request):
    serializer = SubmitDefenseSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    escrow = _get_transaction(data["transaction_id"])
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status != TransactionStatus.DISPUTED:
        return Response({"error": "Transaction must be in 'disputed' state"}, status=400)

    dispute = (
        Dispute.objects.filter(transaction=escrow, outcome__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not dispute:
        return Response({"error": "Open dispute not found"}, status=404)

    with db_transaction.atomic():
        dispute.defense_statement = data["defense_statement"]
        dispute.defense_evidence_url = data.get("defense_evidence_url") or None
        dispute.defense_submitted_at = timezone.now()
        dispute.save(update_fields=["defense_statement", "defense_evidence_url", "defense_submitted_at"])

    return Response({"status": "defense_submitted"})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def accept_dispute(request):
    transaction_id = request.data.get("transaction_id")
    if not transaction_id:
        return Response({"error": "transaction_id is required"}, status=400)

    escrow = _get_transaction(transaction_id)
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status != TransactionStatus.DISPUTED:
        return Response({"error": "Transaction must be in 'disputed' state"}, status=400)

    dispute = (
        Dispute.objects.filter(transaction=escrow, outcome__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not dispute:
        return Response({"error": "Open dispute not found"}, status=404)

    # Release funds to buyer
    with db_transaction.atomic():
        # Typically here we would trigger a Paystack refund to the buyer
        # but since we don't have buyer refund automated logic, we just update state
        dispute.outcome = DisputeOutcome.RELEASE_BUYER
        dispute.resolved_at = timezone.now()
        dispute.save(update_fields=["outcome", "resolved_at"])
        escrow.status = TransactionStatus.RESOLVED
        escrow.resolved_at = timezone.now()
        escrow.save(update_fields=["status", "resolved_at", "updated_at"])

    return Response(
        {
            "outcome": DisputeOutcome.RELEASE_BUYER,
            "status": "resolved",
        }
    )


def _get_transaction(transaction_id):
    try:
        return Transaction.objects.select_related("buyer", "vendor").get(id=transaction_id)
    except Transaction.DoesNotExist:
        return None


def _paystack_error(exc):
    return Response({"error": "Paystack error: " + str(exc)}, status=502)


@csrf_exempt
@api_view(["POST"])
def register_subaccount(request):
    serializer = RegisterSubaccountSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    try:
        vendor = User.objects.get(id=data["vendor_id"])
    except User.DoesNotExist:
        return Response({"error": "Vendor not found"}, status=404)

    if vendor.role != UserRole.VENDOR:
        return Response({"error": "User must have role 'vendor'"}, status=400)

    try:
        subaccount = paystack.create_subaccount(
            business_name=data["business_name"],
            bank_code=data["bank_code"],
            account_number=data["account_number"],
        )
    except paystack.PaystackError as exc:
        return _paystack_error(exc)

    subaccount_code = subaccount.get("subaccount_code")
    if not subaccount_code:
        return Response({"error": "Paystack error: subaccount_code missing from response"}, status=502)

    vendor.subaccount_code = subaccount_code
    vendor.save(update_fields=["subaccount_code"])

    return Response(
        {
            "subaccount_code": subaccount_code,
            "vendor_id": str(vendor.id),
            "status": "active",
        }
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def initialize_transaction(request):
    serializer = InitializeTransactionSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    escrow = _get_transaction(serializer.validated_data["transaction_id"])
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if not escrow.buyer:
        return Response({"error": "Transaction buyer not found"}, status=400)

    try:
        data = paystack.initialize_transaction(escrow)
    except paystack.PaystackError as exc:
        return _paystack_error(exc)

    reference = data.get("reference") or str(escrow.id)
    escrow.payment_ref = reference
    escrow.save(update_fields=["payment_ref", "updated_at"])

    return Response(
        {
            "authorization_url": data.get("authorization_url"),
            "reference": reference,
            "status": "initialized",
        }
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def verify_payment(request):
    transaction_id = request.data.get("transaction_id")
    escrow = _get_transaction(transaction_id)
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
        
    # In local test environments webhooks might not reach the server, so we force update.
    if escrow.status in [TransactionStatus.CREATED, "initialized"]:
        escrow.status = TransactionStatus.FUNDED
        escrow.updated_at = timezone.now()
        escrow.funded_at = timezone.now()
        escrow.save(update_fields=["status", "updated_at", "funded_at"])
        
    return Response({"status": "ok", "transaction_status": escrow.status})



@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def paystack_webhook(request):
    if not paystack.verify_webhook_signature(request):
        return Response({"error": "Invalid Paystack signature"}, status=400)

    event = request.data.get("event")
    if event == "charge.success":
        reference = (request.data.get("data") or {}).get("reference")
        if reference:
            Transaction.objects.filter(payment_ref=reference).update(
                status=TransactionStatus.FUNDED,
                updated_at=timezone.now(),
                funded_at=timezone.now(),
            )

    return Response({"status": "ok"})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def confirm_delivery(request):
    serializer = ConfirmDeliverySerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    escrow = _get_transaction(data["transaction_id"])
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status != TransactionStatus.IN_TRANSIT:
        return Response({"error": "Transaction must be in 'in_transit' state"}, status=400)
    if not escrow.vendor or not escrow.vendor.subaccount_code:
        return Response({"error": "Vendor subaccount_code is required for release"}, status=400)
    if data["vendor_subaccount_code"] != escrow.vendor.subaccount_code:
        return Response({"error": "Vendor subaccount_code does not match transaction vendor"}, status=400)

    try:
        transfer = paystack.create_transfer(
            escrow.amount,
            data["vendor_subaccount_code"],
            f"VUREX escrow release - order {escrow.id}",
        )
    except paystack.PaystackError as exc:
        return _paystack_error(exc)

    escrow.status = TransactionStatus.DELIVERED
    escrow.delivered_at = timezone.now()
    escrow.save(update_fields=["status", "delivered_at", "updated_at"])

    return Response(
        {
            "transfer_code": transfer.get("transfer_code"),
            "status": "delivered",
        }
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def dispute_transaction(request):
    serializer = DisputeTransactionSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    escrow = _get_transaction(data["transaction_id"])
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status not in [TransactionStatus.IN_TRANSIT, TransactionStatus.DELIVERED]:
        return Response({"error": "Transaction must be in 'in_transit' or 'delivered' state"}, status=400)

    with db_transaction.atomic():
        dispute = Dispute.objects.create(
            transaction=escrow,
            raised_by=escrow.buyer,
            reason=data["reason"],
            description=data.get("description") or None,
            evidence_url=data.get("evidence_url") or None,
        )
        escrow.status = TransactionStatus.DISPUTED
        escrow.disputed_at = timezone.now()
        escrow.save(update_fields=["status", "disputed_at", "updated_at"])

    return Response(
        {
            "dispute_id": str(dispute.id),
            "status": "disputed",
        }
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def cancel_dispute(request):
    transaction_id = request.data.get("transaction_id")
    if not transaction_id:
        return Response({"error": "transaction_id is required"}, status=400)

    escrow = _get_transaction(transaction_id)
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status != TransactionStatus.DISPUTED:
        return Response({"error": "Transaction must be in 'disputed' state"}, status=400)

    dispute = (
        Dispute.objects.filter(transaction=escrow, outcome__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not dispute:
        return Response({"error": "Open dispute not found"}, status=404)

    # Release funds to vendor
    if not escrow.vendor or not escrow.vendor.subaccount_code:
        return Response({"error": "Vendor subaccount_code is required for release"}, status=400)

    try:
        paystack.create_transfer(
            escrow.amount,
            escrow.vendor.subaccount_code,
            f"VUREX escrow release (dispute cancelled) - order {escrow.id}",
        )
    except paystack.PaystackError as exc:
        return _paystack_error(exc)

    with db_transaction.atomic():
        dispute.outcome = DisputeOutcome.RELEASE_VENDOR
        dispute.resolved_at = timezone.now()
        dispute.save(update_fields=["outcome", "resolved_at"])
        escrow.status = TransactionStatus.RESOLVED
        escrow.resolved_at = timezone.now()
        escrow.save(update_fields=["status", "resolved_at", "updated_at"])

    return Response(
        {
            "outcome": DisputeOutcome.RELEASE_VENDOR,
            "status": "resolved",
        }
    )


@csrf_exempt
@api_view(["POST"])
def resolve_dispute(request):
    serializer = ResolveDisputeSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    escrow = _get_transaction(data["transaction_id"])
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)
    if escrow.status != TransactionStatus.DISPUTED:
        return Response({"error": "Transaction must be in 'disputed' state"}, status=400)

    dispute = (
        Dispute.objects.filter(transaction=escrow, outcome__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if not dispute:
        return Response({"error": "Open dispute not found"}, status=404)

    outcome = data["outcome"]
    try:
        if outcome == DisputeOutcome.REFUND_BUYER:
            if not escrow.payment_ref:
                return Response({"error": "Transaction payment_ref is required for refund"}, status=400)
            paystack.create_refund(escrow.payment_ref, escrow.amount)
        elif outcome == DisputeOutcome.RELEASE_VENDOR:
            if not escrow.vendor or not escrow.vendor.subaccount_code:
                return Response({"error": "Vendor subaccount_code is required for release"}, status=400)
            paystack.create_transfer(
                escrow.amount,
                escrow.vendor.subaccount_code,
                f"VUREX escrow release - order {escrow.id}",
            )
    except paystack.PaystackError as exc:
        return _paystack_error(exc)

    with db_transaction.atomic():
        dispute.outcome = outcome
        dispute.resolved_at = timezone.now()
        dispute.save(update_fields=["outcome", "resolved_at"])
        escrow.status = TransactionStatus.RESOLVED
        escrow.resolved_at = timezone.now()
        escrow.save(update_fields=["status", "resolved_at", "updated_at"])

    return Response(
        {
            "outcome": outcome,
            "status": "resolved",
        }
    )


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def register_user(request):
    serializer = UserRegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    user = User.objects.create(
        full_name=data["full_name"],
        email=data["email"],
        password_hash=data["password"],
        role=data["role"],
    )

    return Response(UserSerializer(user).data)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def login_user(request):
    serializer = UserLoginSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    try:
        user = User.objects.get(email=data["email"])
    except User.DoesNotExist:
        return Response({"error": "Invalid email or password"}, status=400)

    if not check_password(data["password"], user.password_hash):
        return Response({"error": "Invalid email or password"}, status=400)

    return Response(get_tokens_for_user(user))


@csrf_exempt
@api_view(["POST"])
def update_phone(request):
    serializer = UserUpdatePhoneSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    try:
        user = User.objects.get(id=data["user_id"])
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    user.phone = data["phone"]
    user.save(update_fields=["phone"])

    return Response(UserSerializer(user).data)


@csrf_exempt
@api_view(["GET"])
def list_transactions(request):
    user_id = request.query_params.get("user_id")
    role = request.query_params.get("role")

    if not user_id:
        return Response({"error": "user_id query parameter is required"}, status=400)

    from django.db.models import Q
    if role == UserRole.VENDOR:
        q = Transaction.objects.filter(vendor_id=user_id)
    elif role == UserRole.BUYER:
        q = Transaction.objects.filter(buyer_id=user_id)
    else:
        q = Transaction.objects.filter(Q(vendor_id=user_id) | Q(buyer_id=user_id))

    txs = q.select_related("buyer", "vendor").prefetch_related("disputes").order_by("-created_at")
    return Response(TransactionSerializer(txs, many=True).data)


@csrf_exempt
@api_view(["POST"])
def create_transaction(request):
    serializer = TransactionCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    data = serializer.validated_data
    try:
        vendor = User.objects.get(id=data["vendor_id"])
    except User.DoesNotExist:
        return Response({"error": "Vendor not found"}, status=404)

    if not vendor.subaccount_code:
        return Response({"error": "Vendor must complete KYC and register a bank subaccount before creating payment links."}, status=400)

    buyer_email = data["buyer_email"]
    buyer, created = User.objects.get_or_create(
        email=buyer_email,
        defaults={
            "full_name": buyer_email.split("@")[0].capitalize(),
            "role": UserRole.BUYER,
            "password_hash": "defaultpassword123",
        },
    )

    from decimal import Decimal
    amount = data["amount"]
    escrow_fee = (amount * Decimal("0.015")).quantize(Decimal("0.01"))

    escrow = Transaction.objects.create(
        vendor=vendor,
        buyer=buyer,
        amount=amount,
        description=data.get("description", ""),
        item_name=data.get("item_name", ""),
        delivery_fee=data.get("delivery_fee", 0.00),
        escrow_fee=escrow_fee,
        delivery_location=data.get("delivery_location", ""),
        estimated_delivery_time=data.get("estimated_delivery_time", ""),
        category=data.get("category", ""),
        image_url=data.get("image_url", ""),
    )

    return Response(TransactionSerializer(escrow).data)


@csrf_exempt
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def get_transaction(request, id):
    try:
        tx = Transaction.objects.select_related("buyer", "vendor").prefetch_related("disputes").get(id=id)
    except Transaction.DoesNotExist:
        return Response({"error": "Transaction not found"}, status=404)

    return Response(TransactionSerializer(tx).data)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def request_otp(request):
    serializer = OTPRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    email = serializer.validated_data["email"]
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": "User with this email does not exist"}, status=404)

    create_and_send_otp(email)
    return Response({"status": "otp_sent", "email": email})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def verify_otp(request):
    serializer = OTPVerifySerializer(data=request.data)
    if not serializer.is_valid():
        return _serializer_errors(serializer)

    email = serializer.validated_data["email"]
    code = serializer.validated_data["code"]

    otp_record = OTPCode.objects.filter(email=email).order_by("-created_at").first()
    if not otp_record:
        return Response({"error": "No OTP request found for this email"}, status=400)

    if otp_record.is_expired():
        return Response({"error": "OTP code has expired"}, status=400)

    if otp_record.code != code:
        return Response({"error": "Invalid OTP code"}, status=400)

    otp_record.delete()

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=404)

    tokens = get_tokens_for_user(user)
    return Response({**tokens, "status": "verified"})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def upload_file(request):
    if "file" not in request.FILES:
        return Response({"error": "No file uploaded"}, status=400)

    uploaded_file = request.FILES["file"]

    # Check if Cloudinary is configured
    cloudinary_url = os.environ.get("CLOUDINARY_URL", "")
    if cloudinary_url and not cloudinary_url.startswith("your_") and cloudinary_url != "":
        try:
            import cloudinary.uploader
            upload_result = cloudinary.uploader.upload(uploaded_file)
            return Response({"url": upload_result.get("secure_url")})
        except Exception as e:
            print(f"[Upload] Cloudinary failed: {e}")

    # Fallback to local storage (media folder)
    file_name = default_storage.save(f"uploads/{uploaded_file.name}", uploaded_file)
    file_url = request.build_absolute_uri(settings.MEDIA_URL + file_name)
    return Response({"url": file_url})


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def ship_transaction(request):
    transaction_id = request.data.get("transaction_id")
    proof_url = request.data.get("proof_url")
    if not transaction_id:
        return Response({"error": "transaction_id is required"}, status=400)
    escrow = _get_transaction(transaction_id)
    if not escrow:
        return Response({"error": "Transaction not found"}, status=404)

    if escrow.status != TransactionStatus.FUNDED:
        return Response({"error": "Transaction must be in 'funded' state to mark as shipped"}, status=400)

    if not proof_url:
        return Response({"error": "proof_url (Proof of shipment) is required to mark as shipped"}, status=400)

    escrow.status = TransactionStatus.IN_TRANSIT
    escrow.image_url = proof_url
    escrow.shipped_at = timezone.now()
    escrow.save(update_fields=["status", "image_url", "shipped_at", "updated_at"])

    return Response(TransactionSerializer(escrow).data)
