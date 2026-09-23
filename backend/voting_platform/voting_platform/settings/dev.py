"""
Local development settings.

Supplies safe local defaults for the variables that ``base`` requires, then
imports everything from ``base``. Real environment variables (or a ``.env``
file) still take precedence over these defaults. NEVER use in production.
"""

import os

_DEV_DEFAULTS = {
    "SECRET_KEY": "django-insecure-dev-only-do-not-use-in-production",
    "DEBUG": "True",
    "ALLOWED_HOSTS": "localhost,127.0.0.1,[::1],testserver",
    "FRONTEND_URL": "http://localhost:4500",
    "CORS_ALLOWED_ORIGINS": ("http://localhost:3000,http://localhost:4500,http://localhost:5173"),
    # Emails are printed to the console instead of being sent.
    "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend",
    "DB_NAME": "vote_app",
    "DB_USER": "postgres",
    "DB_PASSWORD": "",
    "DB_HOST": "localhost",
}
for _key, _value in _DEV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)

# Must come after the defaults above.
from .base import *  # noqa: E402,F403
