"""
End-to-end test settings.

For running a real server process against a real database that an external test runner
(Playwright, Cypress, ...) drives over HTTP, while still being able to complete the phone-OTP
and CAPTCHA-gated flows without a real SMS provider or a solved CAPTCHA:

* SMS is written to the database (``common.models.E2ESentMessage``) instead of actually sent.
  A same-process outbox (``LocMemSmsBackend``) can't be read back by the test runner's own
  process, so ``python manage.py last_otp <phone>`` (this app only) queries the database
  instead.
* CAPTCHA always passes, exactly like ``dev``.
* Throttle rates are relaxed (not disabled) so a realistic e2e suite doesn't need to
  hand-tune rate limits per test, while still exercising them if a test deliberately does.

NEVER use in production. ``dev`` and ``prod`` both explicitly refuse ``SMS_BACKEND=e2e`` at
startup (see ``settings/dev.py`` and ``settings/prod.py``), so this module is the only way to
reach ``E2EDatabaseSmsBackend``.
"""

from .dev import *  # noqa: F403

SMS_BACKEND = "e2e"
CAPTCHA_BACKEND = "dummy"  # always-pass Turnstile stand-in, same as dev

# Relaxed, not disabled: generous enough that a normal e2e run (a handful of OTP requests and
# votes) never trips a limit, while a test that deliberately hammers an endpoint still can.
APP_THROTTLE_RATES = dict.fromkeys(APP_THROTTLE_RATES, "1000/1m")  # noqa: F405
