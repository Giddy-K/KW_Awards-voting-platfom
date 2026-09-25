"""Boot checks and production-settings guards (AUDIT F-07)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps
from django.core.management import call_command
from django.urls import reverse

PROJECT_DIR = Path(__file__).resolve().parent.parent

PROD_ENV = {
    "DJANGO_SETTINGS_MODULE": "voting_platform.settings.prod",
    "SECRET_KEY": "x7Qm-4vB!pZ2rLk9#tWc8Yn3Ds6HgJf0aEu1oIiXyVbN5MqTz_R-long-enough-key-value",
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


def run_prod(code, **overrides):
    """Run ``code`` in a fresh interpreter with the production settings and a dummy env."""
    # Start from a minimal environment: the pytest process itself carries dev defaults
    # (DEBUG=True, console e-mail, ...) that must not leak into the "production" interpreter.
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
    env.update(PROD_ENV)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=PROJECT_DIR,
        env=env,
        timeout=120,
    )


BOOT = "import django; django.setup(); print('booted')"


# --- boot ---------------------------------------------------------------------------------


def test_the_project_boots_with_the_expected_apps():
    labels = {c.label for c in apps.get_app_configs()}
    assert {"accounts", "events", "nominations", "voting", "payments", "audit", "common"} <= labels
    assert "authemail" not in labels and "drf_yasg" not in labels and "api" not in labels
    assert apps.get_model("accounts", "User")._meta.pk.name == "id"


@pytest.mark.django_db
def test_system_check_and_migrations_are_clean():
    call_command("check")
    call_command("makemigrations", "--check", "--dry-run")


def test_every_domain_model_uses_uuid_ids_and_timestamps():
    for label in (
        "accounts.User",
        "events.Event",
        "events.Category",
        "events.Award",
        "nominations.Nominee",
        "nominations.Nomination",
        "voting.Voter",
        "voting.OTPChallenge",
        "voting.Vote",
        "payments.Payment",
        "audit.AuditLog",
    ):
        model = apps.get_model(label)
        names = {f.name for f in model._meta.get_fields()}
        assert {"id", "created_at", "updated_at"} <= names, label
        assert model._meta.pk.get_internal_type() == "UUIDField", label


def test_timezone_is_nairobi_for_display_and_utc_for_storage(settings):
    assert settings.TIME_ZONE == "Africa/Nairobi" and settings.USE_TZ is True


@pytest.mark.django_db
def test_public_landing_points_respond(client):
    assert client.get(reverse("admin:login")).status_code == 200
    assert client.get("/api/v1/events/").status_code == 200
    assert client.get("/api/categories/").status_code == 404  # legacy routes are gone
    assert client.get("/swagger/").status_code == 404


def test_drf_denies_by_default_and_paginates(settings):
    rest = settings.REST_FRAMEWORK
    assert rest["DEFAULT_PERMISSION_CLASSES"] == ["rest_framework.permissions.IsAuthenticated"]
    assert rest["PAGE_SIZE"] > 0
    assert rest["DEFAULT_RENDERER_CLASSES"] == ["rest_framework.renderers.JSONRenderer"]


def test_no_hardcoded_secrets_or_insecure_defaults_in_settings_files():
    text = "".join(
        p.read_text(encoding="utf-8")
        for p in (PROJECT_DIR / "voting_platform" / "settings").glob("*.py")
    )
    assert "django-insecure-@1hm9" not in text  # the key leaked in the old repo history
    assert "ALLOWED_HOSTS = []" not in text


# --- production guards (fresh interpreters) ---------------------------------------------------


def test_production_settings_boot_with_a_valid_environment():
    result = run_prod(BOOT)
    assert result.returncode == 0 and "booted" in result.stdout, result.stderr[-800:]


def test_check_deploy_has_zero_warnings_and_hsts_preload_is_opt_in():
    result = run_prod(
        "import django; django.setup(); from django.core.management import call_command;"
        "from django.conf import settings;"
        "call_command('check', '--deploy', '--fail-level', 'WARNING');"
        "print('preload', settings.SECURE_HSTS_PRELOAD, 'debug', settings.DEBUG)"
    )
    assert result.returncode == 0, result.stdout + result.stderr[-800:]
    assert "preload False debug False" in result.stdout
    assert "W021" not in result.stderr


@pytest.mark.parametrize(
    "override,message",
    [
        ({"SECRET_KEY": None}, "SECRET_KEY"),
        ({"AUDIT_PHONE_HASH_KEY": None}, "AUDIT_PHONE_HASH_KEY"),
        ({"AUDIT_PHONE_HASH_KEY": PROD_ENV["SECRET_KEY"]}, "AUDIT_PHONE_HASH_KEY"),
        ({"ALLOWED_HOSTS": None}, "ALLOWED_HOSTS"),
        ({"ALLOWED_HOSTS": ""}, "Empty required production setting"),
        ({"FRONTEND_URL": None}, "FRONTEND_URL"),
        ({"CACHE_URL": None}, "CACHE_URL"),
        ({"CACHE_URL": "locmem://"}, "CACHE_URL"),
        ({"AFRICASTALKING_USERNAME": None, "AFRICASTALKING_API_KEY": None}, "SMS"),
        ({"SMS_BACKEND": "console"}, "SMS"),
        ({"TURNSTILE_SECRET_KEY": None}, "CAPTCHA"),
        ({"CAPTCHA_BACKEND": "dummy"}, "CAPTCHA"),
    ],
)
def test_production_refuses_to_start_when_unsafe_or_incomplete(override, message):
    result = run_prod(BOOT, **override)
    assert result.returncode != 0 and message in result.stderr, result.stderr[-600:]


def test_production_requires_a_database_name_and_user():
    result = run_prod(BOOT, DB_NAME="", DB_USER="", DB_PASSWORD="x", DB_HOST="localhost")
    assert result.returncode != 0 and "database name" in result.stderr


def test_malformed_trusted_proxies_break_the_production_system_check():
    result = run_prod(
        "import django; django.setup(); from django.core.management import call_command;"
        "call_command('check')",
        TRUSTED_PROXIES="10.0.0.0/8,not-an-ip",
    )
    assert result.returncode != 0 and "common.E001" in (result.stdout + result.stderr)
