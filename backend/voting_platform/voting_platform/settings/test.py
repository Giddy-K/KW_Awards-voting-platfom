"""Settings used by pytest. The database comes from the DB_* / DATABASE_URL vars."""

import tempfile

from .dev import *  # noqa: F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SMS_BACKEND = "locmem"
CAPTCHA_BACKEND = "dummy"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MEDIA_ROOT = tempfile.mkdtemp(prefix="voting-platform-test-media-")  # per-test fixture overrides

# High limits so ordinary tests never trip throttles; throttle tests override these.
APP_THROTTLE_RATES = {name: "100000/1d" for name in APP_THROTTLE_RATES}  # noqa: F405
