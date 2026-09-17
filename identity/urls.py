from django.urls import path

from . import views

urlpatterns = [
    path("me/onboarding/", views.onboarding, name="onboarding"),
    path("auth/register/", views.register, name="auth-register"),
    path("auth/login/", views.login, name="auth-login"),
    path("auth/logout/", views.logout, name="auth-logout"),
    path("auth/phone/challenges/", views.create_challenge, name="phone-challenge-create"),
    path("auth/phone/challenges/<uuid:challenge_id>/", views.get_challenge, name="phone-challenge-detail"),
    path("auth/phone/challenges/<uuid:challenge_id>/verify/", views.verify_phone, name="phone-challenge-verify"),
    path("auth/phone/challenges/<uuid:challenge_id>/resend/", views.resend_challenge, name="phone-challenge-resend"),
    path("auth/request-otp/", views.retired_otp),
    path("auth/verify-otp/", views.retired_otp),
    path("auth/update-phone/", views.retired_otp),
]
