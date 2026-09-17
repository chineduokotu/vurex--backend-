from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings
from rest_framework.exceptions import AuthenticationFailed
from django.core.exceptions import ValidationError
from transactions.models import User as CustomUser

class CustomJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        try:
            user_id = validated_token[api_settings.USER_ID_CLAIM]
            user = CustomUser.objects.get(id=user_id)
            if not user.is_active or not user.phone_verified_at:
                raise AuthenticationFailed("Please sign in and verify your phone.", code="phone_verification_required")
            if validated_token.get("auth_version", -1) != user.auth_version:
                raise AuthenticationFailed("Please sign in again.", code="session_expired")
            return user
        except CustomUser.DoesNotExist:
            raise AuthenticationFailed("User not found", code="user_not_found")
        except AuthenticationFailed:
            raise
        except (KeyError, ValueError, TypeError, ValidationError):
            raise AuthenticationFailed("Invalid authentication token.") from None
