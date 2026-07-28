from django.contrib import admin

from .models import Dispute, Transaction, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "email", "role", "subaccount_code", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("full_name", "email", "phone", "subaccount_code")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("id", "vendor", "buyer", "amount", "status", "payment_ref", "auto_release_at")
    list_filter = ("status", "created_at", "updated_at")
    search_fields = ("id", "payment_ref", "description")


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("id", "transaction", "raised_by", "outcome", "resolved_at", "created_at")
    list_filter = ("outcome", "created_at", "resolved_at")
    search_fields = ("id", "reason", "evidence_url")
