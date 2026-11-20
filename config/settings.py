"""
Django settings for the moving_bins project.

Configuration comes from environment variables so the same code runs locally
and in production. For local development nothing needs to be set: you get
DEBUG on and a SQLite file database.

    DJANGO_SECRET_KEY   required in production
    DJANGO_DEBUG        "1" to enable debug (default "1" locally)
    DJANGO_ALLOWED_HOSTS comma-separated, e.g. "example.com,www.example.com"
    DATABASE_URL        e.g. postgres://user:pass@host:5432/dbname
"""

import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DEBUG is off.")
    SECRET_KEY = "dev-only-insecure-key"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rentals",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "rentals.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "rentals.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Business integrations
# ---------------------------------------------------------------------------

# Maps: "google" in production (needs GOOGLE_MAPS_API_KEY); "fake" locally,
# which estimates distances from a small table of ZIP codes (see rentals/maps.py).
MAPS_BACKEND = os.environ.get("MAPS_BACKEND", "fake" if DEBUG else "google")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

# Email: printed to the terminal locally; a real provider is set up at deploy.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "Sprout Tote Co. <bookings@localhost>")

# Who gets "new request" emails. Comma-separated.
OWNER_EMAILS = [e.strip() for e in os.environ.get("OWNER_EMAILS", "").split(",") if e.strip()]

# Used to build links in emails, e.g. https://example.com (no trailing slash).
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000")

# How long the private booking link in an email keeps working.
BOOKING_LINK_MAX_AGE_DAYS = 120

# What the QR code on each tote label points to. Upper case keeps QR codes
# small (QR "alphanumeric" mode). sprouttote.com redirects to the main site,
# keeping the /Q/<code> part.
QR_BASE_URL = os.environ.get("QR_BASE_URL", "HTTPS://SPROUTTOTE.COM/Q/")
LOGIN_URL = "admin:login"

# ---------------------------------------------------------------------------
# Stripe
# ---------------------------------------------------------------------------
# Never put keys in code. Locally and in production they come from the
# environment (in production: the host's secrets settings). With no secret
# key set, the site runs without the payment step (useful for development).
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
# Publishable keys are public (they go in the web page).
STRIPE_PUBLISHABLE_KEY = os.environ.get(
    "STRIPE_PUBLISHABLE_KEY",
    "pk_test_51UdsQ1ItEfnAO89A5N6UxIeXmfeU64l8qkjCWptBSE2273tXP8dpaB0JPzTNJVGrQXYmwCd15yfsPJjlDTF40gfr00VoRQv6gh",
)
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
# Optional: a payment method configuration limited to cards (and Link), since
# holds and later charges need a reusable card. Set up in the Stripe Dashboard.
STRIPE_PAYMENT_METHOD_CONFIGURATION = os.environ.get("STRIPE_PAYMENT_METHOD_CONFIGURATION", "")
# Only for sandboxes whose network proxy re-signs HTTPS (like the build
# environment). Leave unset in production.
STRIPE_CA_BUNDLE = os.environ.get("STRIPE_CA_BUNDLE", "")

# Behind the production host's proxy, the visitor's real IP address arrives in
# X-Forwarded-For. Turn this on there so signatures record the right address.
TRUST_X_FORWARDED_FOR = os.environ.get("TRUST_X_FORWARDED_FOR", "") == "1"
