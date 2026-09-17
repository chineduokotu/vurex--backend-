from django.conf import settings
from django.core.checks import Error, Tags, Warning, register

from integrations.termii import TermiiError, validate_configuration


@register(Tags.security, deploy=True)
def production_identity_checks(app_configs, **kwargs):
    errors = []
    if len(settings.SECRET_KEY) < 50 or len(set(settings.SECRET_KEY)) < 5 or settings.SECRET_KEY.startswith(("django-insecure-", "unsafe-", "your_")):
        errors.append(Error("Set a strong DJANGO_SECRET_KEY of at least 50 characters before production deployment.", id="identity.E009"))
    if settings.DEBUG:
        errors.append(Error("DEBUG must be False for live phone verification.", id="identity.E001"))
    if settings.ALLOW_ALL_HOSTS_AND_ORIGINS:
        errors.append(Warning(
            "Temporary all-host/all-origin access is enabled, including credentialed identity requests.",
            hint="Set ALLOW_ALL_HOSTS_AND_ORIGINS=False and configure explicit hosts and origins when ready.",
            id="identity.W001",
        ))
    elif settings.CORS_ALLOW_ALL_ORIGINS or not settings.CORS_ALLOWED_ORIGINS:
        errors.append(Error("Configure explicit CORS_ALLOWED_ORIGINS and disable CORS_ALLOW_ALL_ORIGINS.", id="identity.E002"))
    if not settings.ALLOW_ALL_HOSTS_AND_ORIGINS and not settings.CSRF_TRUSTED_ORIGINS:
        errors.append(Error("Configure CSRF_TRUSTED_ORIGINS for the frontend HTTPS origin.", id="identity.E003"))
    if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
        errors.append(Error("Onboarding and CSRF cookies must be Secure in production.", id="identity.E004"))
    if settings.SESSION_COOKIE_SAMESITE not in ("Lax", "Strict", "None"):
        errors.append(Error("SESSION_COOKIE_SAMESITE must be Lax, Strict, or None.", id="identity.E005"))
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        errors.append(Error("Phone verification and messaging workers require PostgreSQL row locks.", id="identity.E006"))
    if settings.SMS_ENABLED or settings.TERMII_WEBHOOKS_ENABLED:
        try:
            validate_configuration(require_webhook=settings.TERMII_WEBHOOKS_ENABLED)
        except TermiiError as exc:
            errors.append(Error("Termii configuration is incomplete or invalid: " + exc.code, id="identity.E007"))
    if settings.NOTIFICATION_LEASE_SECONDS <= settings.TERMII_CONNECT_TIMEOUT_SECONDS + settings.TERMII_READ_TIMEOUT_SECONDS + 30:
        errors.append(Error("The worker lease must exceed provider timeouts by at least 30 seconds.", id="identity.E008"))
    limits = [settings.SMS_DAILY_SEND_LIMIT, settings.OTP_SENDS_PER_HOUR, settings.OTP_SENDS_PER_DAY,
              settings.OTP_IP_SENDS_PER_HOUR, settings.OTP_VERIFY_ATTEMPTS_PER_HOUR, settings.OTP_VERIFY_IP_ATTEMPTS_PER_HOUR]
    if any(value < 1 for value in limits):
        errors.append(Error("SMS and OTP limits must be positive; use SMS_ENABLED=False to pause sending.", id="identity.E010"))
    return errors
