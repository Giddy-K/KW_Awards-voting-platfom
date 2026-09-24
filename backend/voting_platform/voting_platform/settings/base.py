"""
Base settings shared by every environment.

Anything sensitive or environment-specific is read from environment variables
(optionally loaded from a ``.env`` file in the project directory). Required
variables have no default here, so a missing value fails loudly at startup.
``dev.py`` supplies safe local defaults; ``prod.py`` supplies hardening.

Use ``DJANGO_SETTINGS_MODULE=voting_platform.settings.dev`` (default for
``manage.py``), ``...settings.prod`` (default for wsgi/asgi) or
``...settings.test`` (pytest).

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

from datetime import timedelta
from pathlib import Path

import environ

# Project directory (the one containing manage.py).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
# Real environment variables win over values in the .env file.
if (BASE_DIR / ".env").exists():
    env.read_env(BASE_DIR / ".env")

# --- Core -------------------------------------------------------------------

# Required (no default). Generate one with:
#   python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
SECRET_KEY = env("SECRET_KEY")

DEBUG = env.bool("DEBUG", default=False)

# Comma-separated, e.g. "vote.example.com,api.example.com". Required.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# Public URL of the frontend (used for CORS defaults, links, etc.).
FRONTEND_URL = env("FRONTEND_URL")

# Comma-separated origins, e.g. "https://vote.example.com".
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# The reverse-proxy chain in front of the app: IPs or CIDRs, one per hop, outermost first and
# ending with the proxy that connects to this server. Empty means X-Forwarded-For is ignored
# and the socket peer address is the client IP. See common/ip.py.
TRUSTED_PROXIES = env.list("TRUSTED_PROXIES", default=[])


# --- Application definition -------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    # Project apps
    "common",
    "accounts",
    "events",
    "nominations",
    "voting",
    "payments",
    "audit",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "voting_platform.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "voting_platform.wsgi.application"


# --- Database ---------------------------------------------------------------
# Either set DATABASE_URL (e.g. postgres://user:pass@host:5432/dbname) or the
# individual DB_* variables below.

if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("DB_NAME"),
            "USER": env("DB_USER"),
            "PASSWORD": env("DB_PASSWORD"),
            "HOST": env("DB_HOST"),
            "PORT": env.int("DB_PORT", default=5432),
        }
    }


# --- Cache (also backs the throttles) ---------------------------------------
# CACHE_URL: redis://host:6379/1 | db://cache_table (run `createcachetable`) | locmem://
# Throttle counters must live in a cache shared by all worker processes, so prod
# settings refuse to start with the per-process locmem cache.


def _caches_from_url(url):
    if url.startswith(("redis://", "rediss://")):
        return {
            "default": {
                "BACKEND": "django.core.cache.backends.redis.RedisCache",
                "LOCATION": url,
            }
        }
    if url.startswith("db://"):
        return {
            "default": {
                "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                "LOCATION": url.removeprefix("db://") or "cache_table",
            }
        }
    return {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "voting-platform",
        }
    }


CACHE_URL = env("CACHE_URL", default="locmem://")
CACHES = _caches_from_url(CACHE_URL)


# --- Password validation ----------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# --- Internationalization ---------------------------------------------------

LANGUAGE_CODE = "en-us"
# Timestamps are stored in UTC (USE_TZ); this is only the display/default zone.
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True


# --- Static and media files -------------------------------------------------

STATIC_URL = "static/"
# Target of `collectstatic`.
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Local file storage. S3-compatible storage is configured in the deployment phase.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Uploads (nominee photos): 5 MB, JPEG/PNG/WebP only, re-encoded and resized.
MAX_UPLOAD_BYTES = env.int("MAX_UPLOAD_BYTES", default=5 * 1024 * 1024)
IMAGE_MAX_DIMENSION = env.int("IMAGE_MAX_DIMENSION", default=1600)
IMAGE_MAX_PIXELS = env.int("IMAGE_MAX_PIXELS", default=40_000_000)  # decompression-bomb guard
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # non-file request body limit


# --- Models -----------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- REST framework ---------------------------------------------------------

REST_FRAMEWORK = {
    # Deny by default: every public endpoint opts in with AllowAny explicitly.
    "DEFAULT_AUTHENTICATION_CLASSES": ["accounts.authentication.PublicOrStaffAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": env.int("PAGE_SIZE", default=25),
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [],
    "TEST_REQUEST_DEFAULT_FORMAT": "json",
}

# --- Auth tokens ------------------------------------------------------------

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=env.int("JWT_REFRESH_HOURS", default=24)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    # Staff endpoints accept ACCESS tokens only. Voter tokens have token_type="voter"
    # and are therefore rejected here (and staff tokens are rejected by voter auth).
    "AUTH_TOKEN_CLASSES": ("rest_framework_simplejwt.tokens.AccessToken",),
}

# Short-lived voter token issued after OTP verification (scope "vote").
VOTER_TOKEN_LIFETIME = timedelta(minutes=env.int("VOTER_TOKEN_MINUTES", default=30))

# One-time codes
OTP_LENGTH = 6
OTP_TTL_SECONDS = env.int("OTP_TTL_SECONDS", default=300)  # 5 minutes
OTP_MAX_ATTEMPTS = env.int("OTP_MAX_ATTEMPTS", default=5)


# --- Abuse controls ---------------------------------------------------------
# Rates look like "3/15m" or "10/1d" (units s, m, h, d). Override any with the
# environment variable THROTTLE_<NAME>, e.g. THROTTLE_OTP_REQUEST_PHONE_SHORT=3/15m.
# An empty value disables that limit.

_THROTTLE_DEFAULTS = {
    "otp_request_phone_short": "3/15m",
    "otp_request_phone_day": "10/1d",
    "otp_request_ip": "20/1h",
    "otp_verify_phone": "10/15m",
    "otp_verify_ip": "40/1h",
    "vote_voter": "30/1h",
    "vote_ip": "100/1h",
    "nomination_ip": "5/1h",
    "staff_login_ip": "10/15m",
}
APP_THROTTLE_RATES = {
    name: env(f"THROTTLE_{name.upper()}", default=default)
    for name, default in _THROTTLE_DEFAULTS.items()
}

# CAPTCHA (Cloudflare Turnstile) and SMS (Africa's Talking). "auto" picks the real
# provider when its credentials are set, otherwise the dev-only fallback.
CAPTCHA_BACKEND = env("CAPTCHA_BACKEND", default="auto")  # auto | dummy | turnstile
TURNSTILE_SECRET_KEY = env("TURNSTILE_SECRET_KEY", default="")
SMS_BACKEND = env("SMS_BACKEND", default="auto")  # auto | console | locmem | africastalking
AFRICASTALKING_USERNAME = env("AFRICASTALKING_USERNAME", default="")
AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", default="")
AFRICASTALKING_SENDER_ID = env("AFRICASTALKING_SENDER_ID", default="")
AFRICASTALKING_SANDBOX = env.bool("AFRICASTALKING_SANDBOX", default=False)


# --- API schema (drf-spectacular) -------------------------------------------

SPECTACULAR_SETTINGS = {
    "TITLE": "KW Awards Voting Platform API",
    "DESCRIPTION": (
        "Public read endpoints, phone-verified voting and staff administration for the "
        "KW Awards. Staff authenticate with a JWT (`/api/v1/auth/token/`); voters with a "
        "short-lived voter token issued by `/api/v1/voters/otp/verify/`."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "SORT_OPERATIONS": True,
    "ENUM_NAME_OVERRIDES": {
        "EventStatusEnum": "events.models.EventStatus",
        "NominationStatusEnum": "nominations.models.NominationStatus",
        "AuditActionEnum": "audit.models.AuditAction",
    },
    # Only staff (Django session) may see the schema and Swagger UI.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
    "SERVE_AUTHENTICATION": ["rest_framework.authentication.SessionAuthentication"],
}


# --- Email (Django admin password resets etc.) ------------------------------

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_FROM = env("EMAIL_FROM", default=None)
EMAIL_BCC = env("EMAIL_BCC", default=None)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=465)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=True)


# --- Logging ----------------------------------------------------------------
# Phone numbers are masked in every log line by PhoneMaskFilter.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"mask_phones": {"()": "common.log_filters.PhoneMaskFilter"}},
    "formatters": {
        "default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["mask_phones"],
        },
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
