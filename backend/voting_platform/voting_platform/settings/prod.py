"""
Production settings.

Every sensitive or environment-specific value must come from the environment
(see ``.env.example``); there are no insecure defaults here. The app is
assumed to run behind a TLS-terminating reverse proxy that sets
``X-Forwarded-Proto``.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    AFRICASTALKING_API_KEY,
    AFRICASTALKING_USERNAME,
    ALLOWED_HOSTS,
    CACHE_URL,
    CAPTCHA_BACKEND,
    CORS_ALLOWED_ORIGINS,
    DATABASES,
    FRONTEND_URL,
    MIDDLEWARE,
    SMS_BACKEND,
    TURNSTILE_SECRET_KEY,
    env,
)

# --- Fail fast on blank required values ------------------------------------
# (base.py already fails when a variable is missing entirely; a blank value,
# e.g. copied from .env.example, must not slip through either.)

_required = {
    "ALLOWED_HOSTS": ALLOWED_HOSTS,
    "FRONTEND_URL": FRONTEND_URL,
    "database name (DATABASE_URL or DB_NAME)": DATABASES["default"].get("NAME"),
    "database user (DATABASE_URL or DB_USER)": DATABASES["default"].get("USER"),
    # CORS_ALLOW_CREDENTIALS is always True (base.py), so the staff refresh cookie is only
    # ever sent to origins named here; an empty list would silently allow no frontend at all.
    "CORS_ALLOWED_ORIGINS": CORS_ALLOWED_ORIGINS,
}
_missing = [name for name, value in _required.items() if not value]
if _missing:
    raise ImproperlyConfigured("Empty required production setting(s): " + ", ".join(_missing))

# --- Refuse insecure backends in production --------------------------------
# The dev-only fallbacks (console SMS prints OTP codes; the dummy CAPTCHA always passes;
# a per-process cache makes throttles per-worker) must never run in production.

_problems = []
if CACHE_URL.startswith("locmem") or not CACHE_URL:
    _problems.append("CACHE_URL must be a shared cache (redis:// or db://) so throttles work")
_sms = SMS_BACKEND
if _sms == "auto":
    _sms = "africastalking" if (AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY) else "console"
if _sms in ("console", "locmem", "e2e"):
    _problems.append(
        "SMS backend is dev/e2e-only; set AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY"
    )
if _sms == "africastalking" and not (AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY):
    _problems.append("AFRICASTALKING_USERNAME and AFRICASTALKING_API_KEY are required")
_captcha = CAPTCHA_BACKEND
if _captcha == "auto":
    _captcha = "turnstile" if TURNSTILE_SECRET_KEY else "dummy"
if _captcha == "dummy":
    _problems.append("CAPTCHA backend is dev-only; set TURNSTILE_SECRET_KEY")
if _captcha == "turnstile" and not TURNSTILE_SECRET_KEY:
    _problems.append("TURNSTILE_SECRET_KEY is required")
if _problems:
    raise ImproperlyConfigured("Unsafe production configuration: " + "; ".join(_problems))

# --- HTTPS / transport security --------------------------------------------

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
# Lower this (e.g. 3600) when first enabling HTTPS, then raise it to a year.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preload is opt-in: it is effectively irreversible and applies to every subdomain, so it
# must be a deliberate decision. Django's check for it (W021) is silenced for that reason.
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
REFRESH_COOKIE_SECURE = True
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
