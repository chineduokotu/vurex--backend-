from django.contrib import admin

from .models import DispatchJob, Notification, NotificationAttempt, WebhookReceipt


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ("id", "user_id", "template", "status", "last_error", "created_at")
    list_filter = ("status", "template")
    exclude = ("recipient",)


@admin.register(DispatchJob)
class DispatchJobAdmin(ReadOnlyAdmin):
    list_display = ("id", "topic", "status", "attempts", "last_error", "available_at")
    list_filter = ("topic", "status")


@admin.register(WebhookReceipt)
class WebhookReceiptAdmin(ReadOnlyAdmin):
    list_display = ("id", "provider_message_id", "delivery_status", "status", "received_at")
    list_filter = ("status", "delivery_status")


@admin.register(NotificationAttempt)
class NotificationAttemptAdmin(ReadOnlyAdmin):
    list_display = ("id", "notification_id", "status", "error_code", "started_at", "finished_at")
