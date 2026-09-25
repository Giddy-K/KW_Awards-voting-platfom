"""
Local development settings.

Supplies safe local defaults for the variables that ``base`` requires, then
imports everything from ``base``. Precedence, highest first: real environment
variables, the ``.env`` file, then the defaults below (a blank value counts
as unset). NEVER use in production.
"""

import os
from pathlib import Path

import environ

# Load .env first so its values win over the defaults below.
environ.Env.read_env(Path(__file__).resolve().parent.parent.parent / ".env")

_DEV_DEFAULTS = {
    "SECRET_KEY": "django-insecure-dev-only-do-not-use-in-production",
    "DEBUG": "True",
    "ALLOWED_HOSTS": "localhost,127.0.0.1,[::1],testserver",
    "FRONTEND_URL": "http://localhost:4500",
    "CORS_ALLOWED_ORIGINS": "http://localhost:3000,http://localhost:4500,http://localhost:5173",
    # Emails are printed to the console instead of being sent.
    "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend",
    "DB_NAME": "vote_app",
    "DB_USER": "postgres",
    "DB_HOST": "localhost",
}
for _key, _value in _DEV_DEFAULTS.items():
    if not os.environ.get(_key):
        os.environ[_key] = _value
# The local Postgres may legitimately have an empty password.
os.environ.setdefault("DB_PASSWORD", "")

# Must come after the defaults above.
from .base import *  # noqa: E402,F403
from .base import SMS_BACKEND  # noqa: E402

# settings.e2e (SMS_BACKEND="e2e") is for driving a real server from an external end-to-end
# test runner; it must never be reachable by asking dev.py for it directly (e.g. a stray
# SMS_BACKEND=e2e environment variable left over from an e2e run). Only fires when dev.py is
# the actual settings module in use, not when settings.e2e imports from it.
if os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith(".dev") and SMS_BACKEND == "e2e":
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "SMS_BACKEND=e2e is only valid under voting_platform.settings.e2e, not .dev."
    )
