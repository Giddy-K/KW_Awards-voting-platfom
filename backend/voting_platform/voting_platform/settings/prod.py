"""
Production settings.

Every sensitive or environment-specific value must come from the environment
(see ``.env.example``); there are no insecure defaults here. The app is
assumed to run behind a TLS-terminating reverse proxy that sets
``X-Forwarded-Proto``.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import ALLOWED_HOSTS, DATABASES, FRONTEND_URL, MIDDLEWARE, env

# --- Fail fast on blank required values ------------------------------------
# (base.py already fails when a variable is missing entirely; a blank value,
# e.g. copied from .env.example, must not slip through either.)

_required = {
    "ALLOWED_HOSTS": ALLOWED_HOSTS,
    "FRONTEND_URL": FRONTEND_URL,
    "database name (DATABASE_URL or DB_NAME)": DATABASES["default"].get("NAME"),
    "database user (DATABASE_URL or DB_USER)": DATABASES["default"].get("USER"),
}
_missing = [name for name, value in _required.items() if not value]
if _missing:
    raise ImproperlyConfigured("Empty required production setting(s): " + ", ".join(_missing))

# --- HTTPS / transport security --------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Lower this (e.g. 3600) when first enabling HTTPS, then raise it to a year.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# --- Static files (WhiteNoise) ---------------------------------------------

MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "whitenoise.middleware.WhiteNoiseMiddleware",
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Logging (stdout, collected by the platform) ----------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"default": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "default"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
