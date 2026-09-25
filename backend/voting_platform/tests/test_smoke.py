"""
Smoke tests: the project boots and its basic wiring works.

These deliberately do NOT assert anything about voting, authorization or
other application behaviour; that gets designed and tested in Phase 2.
"""

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection


def test_app_boots():
    assert apps.is_installed("api")
    call_command("check")


@pytest.mark.django_db
def test_migrations_apply_and_are_complete():
    # pytest-django builds the test DB by running every migration; if that
    # succeeded the core tables exist.
    tables = connection.introspection.table_names()
    for expected in ("api_user", "api_category", "api_nominees", "api_votes"):
        assert expected in tables
    # No model changes without a migration. api/models/__init__.py only imports
    # User; the other models register when the URLconf imports the views, which
    # `manage.py` triggers through the system checks. Do the same here.
    call_command("check")
    call_command("makemigrations", "--check", "--dry-run")


@pytest.mark.django_db
def test_admin_login_page_loads(client):
    response = client.get("/admin/login/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_categories_endpoint_returns_200(client):
    response = client.get("/api/categories/")
    assert response.status_code == 200
