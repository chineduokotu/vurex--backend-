from django.contrib import admin

from .models import AuditEvent, PhoneChallenge


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PhoneChallenge)
class PhoneChallengeAdmin(ReadOnlyAdmin):
    list_display = ("id", "user_id", "status", "delivery_status", "attempts", "error_code", "created_at")
    list_filter = ("status", "delivery_status")
    exclude = ("phone", "provider_pin_id", "session_binding", "request_fingerprint", "idempotency_key")
    search_fields = ("id", "provider_message_id")


@admin.register(AuditEvent)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ("id", "user_id", "action", "outcome", "created_at")
    list_filter = ("action", "outcome")
    search_fields = ("subject_id",)
