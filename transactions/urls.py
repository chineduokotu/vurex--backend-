from django.urls import path

from . import views


urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────
    path("auth/register/", views.register_user, name="auth-register"),
    path("auth/login/", views.login_user, name="auth-login"),
    path("auth/update-phone/", views.update_phone, name="auth-update-phone"),
    path("auth/request-otp/", views.request_otp, name="auth-request-otp"),
    path("auth/verify-otp/", views.verify_otp, name="auth-verify-otp"),

    # ── Transactions (CRUD) ───────────────────────────────────────────────
    path("transactions/", views.list_transactions, name="list-transactions"),
    path("transactions/create/", views.create_transaction, name="create-transaction"),
    path("transactions/<uuid:id>/", views.get_transaction, name="get-transaction"),

    # ── Escrow flow ───────────────────────────────────────────────────────
    path("vendors/register-subaccount/", views.register_subaccount, name="register-subaccount"),
    path("transactions/initialize/", views.initialize_transaction, name="initialize-transaction"),
    path("transactions/verify-payment/", views.verify_payment, name="verify-payment"),
    path("webhooks/paystack/", views.paystack_webhook, name="paystack-webhook"),
    path("transactions/ship/", views.ship_transaction, name="ship-transaction"),
    path("transactions/confirm-delivery/", views.confirm_delivery, name="confirm-delivery"),
    path("transactions/dispute/", views.dispute_transaction, name="dispute-transaction"),
    path("transactions/cancel-dispute/", views.cancel_dispute, name="cancel-dispute"),
    path("transactions/submit-defense/", views.submit_defense, name="submit-defense"),
    path("transactions/accept-dispute/", views.accept_dispute, name="accept-dispute"),
    path("transactions/resolve/", views.resolve_dispute, name="resolve-dispute"),
    
    # ── File Upload ───────────────────────────────────────────────────────
    path("upload/", views.upload_file, name="upload-file"),
]

