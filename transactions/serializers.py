from rest_framework import serializers
from .models import User, Transaction, Dispute, UserRole, TransactionStatus, DisputeOutcome


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "full_name", "email", "phone", "role", "subaccount_code", "created_at"]


class UserRegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=100)
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True)
    role = serializers.ChoiceField(choices=UserRole.choices)

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class UserLoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=150)
    password = serializers.CharField(max_length=128, write_only=True)


class UserUpdatePhoneSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    phone = serializers.CharField(max_length=20)


class RegisterSubaccountSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    business_name = serializers.CharField(max_length=100)
    bank_code = serializers.CharField(max_length=10)
    account_number = serializers.CharField(max_length=20)


class InitializeTransactionSerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()


class ConfirmDeliverySerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()
    vendor_subaccount_code = serializers.CharField(max_length=100)


class SubmitDefenseSerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()
    defense_statement = serializers.CharField()
    defense_evidence_url = serializers.URLField(required=False, allow_blank=True)


class DisputeTransactionSerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()
    reason = serializers.CharField()
    description = serializers.CharField(required=False, allow_blank=True)
    evidence_url = serializers.URLField(required=False, allow_blank=True)


class ResolveDisputeSerializer(serializers.Serializer):
    transaction_id = serializers.UUIDField()
    outcome = serializers.ChoiceField(choices=DisputeOutcome.choices)


class TransactionCreateSerializer(serializers.Serializer):
    vendor_id = serializers.UUIDField()
    buyer_email = serializers.EmailField(required=False, default="buyer-pending@vurex.io")
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)
    item_name = serializers.CharField(required=False, allow_blank=True)
    delivery_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    escrow_fee = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    delivery_location = serializers.CharField(required=False, allow_blank=True)
    estimated_delivery_time = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    image_url = serializers.CharField(required=False, allow_blank=True)


class DisputeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dispute
        fields = [
            "id",
            "reason",
            "description",
            "evidence_url",
            "defense_statement",
            "defense_evidence_url",
            "defense_submitted_at",
            "outcome",
            "resolved_at",
            "created_at",
        ]


class TransactionSerializer(serializers.ModelSerializer):
    vendor = UserSerializer(read_only=True)
    buyer = UserSerializer(read_only=True)
    disputes = DisputeSerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "tx_id",
            "vendor",
            "buyer",
            "amount",
            "status",
            "payment_ref",
            "description",
            "item_name",
            "delivery_fee",
            "escrow_fee",
            "delivery_location",
            "estimated_delivery_time",
            "category",
            "image_url",
            "auto_release_at",
            "created_at",
            "updated_at",
            "funded_at",
            "shipped_at",
            "delivered_at",
            "disputed_at",
            "resolved_at",
            "disputes",
        ]


class OTPRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=150)


class OTPVerifySerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=150)
    code = serializers.CharField(max_length=6)
