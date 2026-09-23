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

# Public URL of the frontend (used for links in emails, CORS defaults, etc.).
FRONTEND_URL = env("FRONTEND_URL")

# Comma-separated origins, e.g. "https://vote.example.com".
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])


# --- Application definition -------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "api",
    "rest_framework",
    "authemail",
    "rest_framework.authtoken",
    "corsheaders",
    "drf_yasg",
]

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


# --- Password validation ----------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
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
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# --- Static files -----------------------------------------------------------

STATIC_URL = "static/"
# Target of `collectstatic`.
STATIC_ROOT = BASE_DIR / "staticfiles"


# --- Models -----------------------------------------------------------------

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "api.User"

# TEMPORARY (remove in Phase 2): Django 5.2 reports that the reverse query name
# of Votes.nominee ("votes") clashes with the Nominees.votes counter field.
# Fixing it means changing the vote models, which Phase 2 redesigns (AUDIT
# F-02/F-08). The clash is harmless at runtime as long as nothing queries the
# reverse relation, which nothing does today.
SILENCED_SYSTEM_CHECKS = ["fields.E303"]


# --- Email ------------------------------------------------------------------

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_FROM = env("EMAIL_FROM", default=None)
EMAIL_BCC = env("EMAIL_BCC", default=None)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=465)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=True)
