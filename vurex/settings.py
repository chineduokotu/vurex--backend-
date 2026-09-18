from pathlib import Path

import dj_database_url
from decouple import Csv, config
from corsheaders.defaults import default_headers


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("DJANGO_SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOW_ALL_HOSTS_AND_ORIGINS = config("ALLOW_ALL_HOSTS_AND_ORIGINS", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="127.0.0.1,localhost", cast=Csv())
if ALLOW_ALL_HOSTS_AND_ORIGINS:
    ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "transactions",
    "identity",
    "notifications",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "vurex.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "vurex.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=600,
    )
}

# Transaction-pooling deployments must not keep server-side cursors across requests.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "transactions.authentication.CustomJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

from datetime import timedelta
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": False,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": False,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
}

CORS_ALLOW_ALL_ORIGINS = config("CORS_ALLOW_ALL_ORIGINS", default=False, cast=bool)
if ALLOW_ALL_HOSTS_AND_ORIGINS:
    CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    default="http://127.0.0.1:5500,http://localhost:5500,http://localhost:5173",
    cast=Csv(),
)

PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")
PAYSTACK_BASE_URL = "https://api.paystack.co"
SENDGRID_API_KEY = config("SENDGRID_API_KEY", default="")
BREVO_API_KEY = config("BREVO_API_KEY", default="")
BREVO_SENDER_EMAIL = config("BREVO_SENDER_EMAIL", default="noreply@vurex.io")

# Django Standard SMTP (Gmail or custom SMTP fallback)
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "noreply@vurex.io")

# The browser receives a scoped onboarding session, never a Termii credential.
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = (*default_headers, "idempotency-key")
CORS_EXPOSE_HEADERS = ["Retry-After"]
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
SESSION_COOKIE_NAME = "vurex_session"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = config("SESSION_COOKIE_SAMESITE", default="Lax")
if ALLOW_ALL_HOSTS_AND_ORIGINS:
    # Allow credentialed browser requests from unrelated frontend sites.
    SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=not DEBUG, cast=bool)
# Enable only behind a proxy that overwrites X-Forwarded-Proto.
if config("TRUST_PROXY_SSL_HEADER", default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=3600, cast=int)
DEFAULT_EXCEPTION_REPORTER_FILTER = "identity.security.PrivateExceptionReporterFilter"
ONBOARDING_SESSION_TTL_SECONDS = 1800
PHONE_CHANGE_REAUTH_SECONDS = 300
IDENTITY_TRUSTED_PROXY_IPS = config("IDENTITY_TRUSTED_PROXY_IPS", default="127.0.0.1,::1", cast=Csv())
REGISTRATION_IPS_PER_HOUR = config("REGISTRATION_IPS_PER_HOUR", default=10, cast=int)
LOGIN_IP_ATTEMPTS_PER_HOUR = config("LOGIN_IP_ATTEMPTS_PER_HOUR", default=60, cast=int)
LOGIN_ACCOUNT_ATTEMPTS_PER_HOUR = config("LOGIN_ACCOUNT_ATTEMPTS_PER_HOUR", default=20, cast=int)

# Production Termii integration. Missing credentials never enable simulated sends.
TERMII_API_KEY = config("TERMII_API_KEY", default="")
TERMII_BASE_URL = config("TERMII_BASE_URL", default="")
TERMII_SENDER_ID = config("TERMII_SENDER_ID", default="")
TERMII_CHANNEL = "dnd"
# Delivery receipts are optional; Token send/verify and SMS need no webhook secret.
TERMII_WEBHOOKS_ENABLED = config("TERMII_WEBHOOKS_ENABLED", default=False, cast=bool)
TERMII_WEBHOOK_SECRET = config("TERMII_WEBHOOK_SECRET", default="")
TERMII_WEBHOOK_SIGNATURE_ENCODING = config("TERMII_WEBHOOK_SIGNATURE_ENCODING", default="hex")
TERMII_CONNECT_TIMEOUT_SECONDS = config("TERMII_CONNECT_TIMEOUT_SECONDS", default=3, cast=float)
TERMII_READ_TIMEOUT_SECONDS = config("TERMII_READ_TIMEOUT_SECONDS", default=10, cast=float)
SMS_ENABLED = config("SMS_ENABLED", default=True, cast=bool)
SMS_DAILY_SEND_LIMIT = config("SMS_DAILY_SEND_LIMIT", default=500, cast=int)
OTP_LENGTH = 6
OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 3
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_DISPATCH_DEADLINE_SECONDS = 120
OTP_SENDS_PER_HOUR = config("OTP_SENDS_PER_HOUR", default=5, cast=int)
OTP_SENDS_PER_DAY = config("OTP_SENDS_PER_DAY", default=10, cast=int)
OTP_IP_SENDS_PER_HOUR = config("OTP_IP_SENDS_PER_HOUR", default=20, cast=int)
OTP_VERIFY_ATTEMPTS_PER_HOUR = config("OTP_VERIFY_ATTEMPTS_PER_HOUR", default=20, cast=int)
OTP_VERIFY_IP_ATTEMPTS_PER_HOUR = config("OTP_VERIFY_IP_ATTEMPTS_PER_HOUR", default=100, cast=int)
# Dojah KYC Integration settings. Missing credentials disable all KYC verification.
DOJAH_APP_ID = config("DOJAH_APP_ID", default="")
DOJAH_SECRET_KEY = config("DOJAH_SECRET_KEY", default="")
DOJAH_BASE_URL = config("DOJAH_BASE_URL", default="https://sandbox.dojah.io")
DOJAH_WEBHOOK_SECRET = config("DOJAH_WEBHOOK_SECRET", default="")
DOJAH_HASH_SALT = config("DOJAH_HASH_SALT", default="")
DOJAH_CONNECT_TIMEOUT_SECONDS = config("DOJAH_CONNECT_TIMEOUT_SECONDS", default=5, cast=float)
DOJAH_READ_TIMEOUT_SECONDS = config("DOJAH_READ_TIMEOUT_SECONDS", default=20, cast=float)
# KYC rate limits
KYC_SUBMISSIONS_PER_USER_PER_DAY = config("KYC_SUBMISSIONS_PER_USER_PER_DAY", default=3, cast=int)
KYC_SUBMISSIONS_PER_IP_PER_HOUR = config("KYC_SUBMISSIONS_PER_IP_PER_HOUR", default=10, cast=int)
# Minimum fuzzy name match score (0.0–1.0) to auto-approve. Below this goes to requires_review.
KYC_NAME_MATCH_THRESHOLD = config("KYC_NAME_MATCH_THRESHOLD", default=0.85, cast=float)

NOTIFICATION_MAX_ATTEMPTS = 3
NOTIFICATION_LEASE_SECONDS = 120
PUBLIC_API_BASE_URL = config("PUBLIC_API_BASE_URL", default="")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "identity": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "notifications": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
