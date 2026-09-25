"""E2E test support: settings.e2e, the database-backed SMS backend, last_otp (Phase 2.1 item 3)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from common.models import E2ESentMessage
from common.sms import E2EDatabaseSmsBackend, get_sms_backend

pytestmark = pytest.mark.django_db

PROJECT_DIR = Path(__file__).resolve().parent.parent


# --- the backend and model ---------------------------------------------------------------


@override_settings(SMS_BACKEND="e2e")
def test_get_sms_backend_resolves_to_the_database_backend():
    assert isinstance(get_sms_backend(), E2EDatabaseSmsBackend)


def test_e2e_backend_persists_to_the_database_not_memory():
    E2EDatabaseSmsBackend().send("+254712345678", "code is 123456")
    message = E2ESentMessage.objects.get()
    assert message.phone_e164 == "+254712345678" and "123456" in message.body


def test_e2e_backend_keeps_a_history_ordered_newest_first():
    backend = E2EDatabaseSmsBackend()
    backend.send("+254712345678", "first code is 111111")
    backend.send("+254712345678", "second code is 222222")
    messages = list(E2ESentMessage.objects.filter(phone_e164="+254712345678"))
    assert [m.body for m in messages] == ["second code is 222222", "first code is 111111"]


# --- last_otp ------------------------------------------------------------------------------


@override_settings(SMS_BACKEND="console")
def test_last_otp_refuses_under_any_backend_other_than_e2e(capsys):
    with pytest.raises(CommandError, match="only works with SMS_BACKEND=e2e"):
        call_command("last_otp", "0712345678")


@override_settings(SMS_BACKEND="e2e")
def test_last_otp_refuses_when_nothing_was_sent():
    with pytest.raises(CommandError, match="No SMS has been sent"):
        call_command("last_otp", "0799999999")


@override_settings(SMS_BACKEND="e2e")
def test_last_otp_prints_the_most_recent_code(capsys):
    backend = E2EDatabaseSmsBackend()
    backend.send(
        "+254712345678", "Your KW Awards verification code is 111111. It expires in 5 minutes."
    )
    backend.send(
        "+254712345678", "Your KW Awards verification code is 222222. It expires in 5 minutes."
    )
    call_command("last_otp", "0712345678")  # any format common.phone accepts
    out = capsys.readouterr().out.strip()
    assert out == "222222"


@override_settings(SMS_BACKEND="e2e")
def test_last_otp_only_looks_at_the_requested_phone():
    backend = E2EDatabaseSmsBackend()
    backend.send(
        "+254711111111", "Your KW Awards verification code is 111111. It expires in 5 minutes."
    )
    backend.send(
        "+254722222222", "Your KW Awards verification code is 222222. It expires in 5 minutes."
    )
    call_command("last_otp", "+254711111111")


@override_settings(SMS_BACKEND="e2e")
def test_last_otp_rejects_an_invalid_phone_number():
    with pytest.raises(CommandError, match="valid mobile phone number"):
        call_command("last_otp", "not-a-phone")


@override_settings(SMS_BACKEND="e2e")
def test_last_otp_errors_when_the_latest_message_has_no_code():
    E2EDatabaseSmsBackend().send("+254712345678", "no code in this message")
    with pytest.raises(CommandError, match="no 6-digit code"):
        call_command("last_otp", "0712345678")


# --- dev/prod refuse the e2e backend (fresh interpreters) -----------------------------------

PROD_ENV = {
    "SECRET_KEY": "x7Qm-4vB!pZ2rLk9#tWc8Yn3Ds6HgJf0aEu1oIiXyVbN5MqTz_R-long-enough-key",
    "AUDIT_PHONE_HASH_KEY": "j2Nb-9wA!qX4sMp7#uVe1Zr6Ct8FgKh3dRy5oLiWbQ0MnTz_S-a-different-key",
    "ALLOWED_HOSTS": "vote.example.com",
    "FRONTEND_URL": "https://vote.example.com",
    "CORS_ALLOWED_ORIGINS": "https://vote.example.com",
    "CSRF_TRUSTED_ORIGINS": "https://vote.example.com",
    "CACHE_URL": "redis://localhost:6379/1",
    "AFRICASTALKING_USERNAME": "kwawards",
    "AFRICASTALKING_API_KEY": "dummy-key",
    "TURNSTILE_SECRET_KEY": "dummy-secret",
    "DB_NAME": "x",
    "DB_USER": "x",
    "DB_PASSWORD": "x",
    "DB_HOST": "localhost",
}
BOOT = "import django; django.setup(); print('booted')"


def run_settings(module, code, **overrides):
    keep = {
        "PATH",
        "SYSTEMROOT",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
    }
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["DJANGO_SETTINGS_MODULE"] = f"voting_platform.settings.{module}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(overrides)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        env=env,
        timeout=120,
    )


def test_dev_boots_normally_without_sms_backend_set():
    result = run_settings(
        "dev", BOOT, DB_NAME="x", DB_USER="x", DB_PASSWORD="", DB_HOST="localhost"
    )
    assert result.returncode == 0 and "booted" in result.stdout, result.stderr[-500:]


def test_dev_refuses_sms_backend_e2e():
    result = run_settings(
        "dev",
        BOOT,
        SMS_BACKEND="e2e",
        DB_NAME="x",
        DB_USER="x",
        DB_PASSWORD="",
        DB_HOST="localhost",
    )
    assert result.returncode != 0
    assert "only valid under voting_platform.settings.e2e" in result.stderr


def test_prod_refuses_sms_backend_e2e():
    result = run_settings("prod", BOOT, SMS_BACKEND="e2e", **PROD_ENV)
    assert result.returncode != 0
    assert "dev/e2e-only" in result.stderr


def test_e2e_settings_boot_with_sms_backend_e2e_and_dummy_captcha():
    result = run_settings(
        "e2e", BOOT, DB_NAME="x", DB_USER="x", DB_PASSWORD="", DB_HOST="localhost"
    )
    assert result.returncode == 0 and "booted" in result.stdout, result.stderr[-500:]


def test_e2e_settings_actually_set_the_expected_backends_and_relaxed_throttles():
    result = run_settings(
        "e2e",
        "import django; django.setup(); from django.conf import settings;"
        "print(settings.SMS_BACKEND, settings.CAPTCHA_BACKEND, "
        "settings.APP_THROTTLE_RATES['vote_voter'])",
        DB_NAME="x",
        DB_USER="x",
        DB_PASSWORD="",
        DB_HOST="localhost",
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert result.stdout.strip() == "e2e dummy 1000/1m"


def test_e2e_settings_are_otherwise_dev_like_debug_true_console_email():
    result = run_settings(
        "e2e",
        "import django; django.setup(); from django.conf import settings;"
        "print(settings.DEBUG, settings.EMAIL_BACKEND)",
        DB_NAME="x",
        DB_USER="x",
        DB_PASSWORD="",
        DB_HOST="localhost",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "True django.core.mail.backends.console.EmailBackend"
