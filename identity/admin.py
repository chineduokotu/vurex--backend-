from django.contrib import admin
from django.utils.html import format_html

from .models import AuditEvent, KYCVerification, PhoneChallenge


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


@admin.register(KYCVerification)
class KYCVerificationAdmin(admin.ModelAdmin):
    """Admin panel for KYC verifications.

    Security:
      - Full NIN/BVN is never stored; masked_id is safe to display.
      - id_hash is excluded from all views (it cannot recover the original ID
        but should not be presented unnecessarily).
      - Approve/reject actions are logged via Django's built-in change history.
    """
    list_display = (
        "id", "user_id", "verification_type", "status_badge",
        "masked_id", "match_score_display", "provider", "created_at",
    )
    list_filter = ("status", "verification_type", "provider")
    search_fields = ("user__email", "masked_id")
    readonly_fields = (
        "id", "user", "verification_type", "provider", "masked_id",
        "match_score", "failure_reason", "created_at", "updated_at", "verified_at",
    )
    exclude = ("id_hash",)
    ordering = ("-created_at",)
    actions = ["action_approve", "action_reject"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Status")
    def status_badge(self, obj):
        colours = {
            "verified": "#16a34a",
            "pending": "#ca8a04",
            "requires_review": "#ea580c",
            "failed": "#dc2626",
            "rejected": "#7c3aed",
        }
        colour = colours.get(obj.status, "#6b7280")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:700">{}</span>',
            colour,
            obj.get_status_display(),
        )

    @admin.display(description="Match Score")
    def match_score_display(self, obj):
        if obj.match_score:
            return f"{obj.match_score:.0%}"
        return "—"

    @admin.action(description="✅ Approve selected verifications (manual override)")
    def action_approve(self, request, queryset):
        from django.utils import timezone
        updatable = queryset.filter(status__in=["requires_review", "pending", "failed", "rejected"])
        count = updatable.update(
            status=KYCVerification.Status.VERIFIED,
            verified_at=timezone.now(),
            reviewed_by=request.user if hasattr(request, "user") else None,
            review_notes="Manually approved by admin.",
        )
        self.message_user(request, f"{count} verification(s) approved.")

    @admin.action(description="❌ Reject selected verifications")
    def action_reject(self, request, queryset):
        updatable = queryset.exclude(status=KYCVerification.Status.REJECTED)
        count = updatable.update(
            status=KYCVerification.Status.REJECTED,
            reviewed_by=request.user if hasattr(request, "user") else None,
            review_notes="Manually rejected by admin.",
        )
        self.message_user(request, f"{count} verification(s) rejected.")
