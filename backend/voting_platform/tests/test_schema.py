"""OpenAPI schema: warning-free, committed, staff-only (AUDIT F-26)."""

from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.urls import reverse
from rest_framework.test import APIClient

from .helpers import make_event_admin, make_user, make_voter, staff_client, voter_client

pytestmark = pytest.mark.django_db

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema.yml"


def generate(tmp_path, *extra):
    target = tmp_path / "schema.yml"
    call_command("spectacular", "--file", str(target), *extra)
    return target.read_text(encoding="utf-8")


def test_schema_generates_and_validates_without_warnings(tmp_path):
    generate(tmp_path, "--validate", "--fail-on-warn")


def test_the_committed_schema_matches_the_code(tmp_path):
    """Regenerate with: python manage.py spectacular --file schema.yml --validate --fail-on-warn"""
    fresh = generate(tmp_path).replace("\r\n", "\n")
    committed = SCHEMA_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == fresh, "schema.yml is stale; regenerate it and commit the result"


def load_committed():
    return yaml.safe_load(SCHEMA_FILE.read_text(encoding="utf-8"))


def test_schema_documents_every_endpoint_and_the_auth_schemes():
    spec = load_committed()
    paths = spec["paths"]
    for expected in (
        "/api/v1/auth/token/",
        "/api/v1/events/",
        "/api/v1/events/{slug}/results/",
        "/api/v1/events/{slug}/stats/",
        "/api/v1/nominations/",
        "/api/v1/nominations/{id}/approve/",
        "/api/v1/voters/otp/request/",
        "/api/v1/voters/otp/verify/",
        "/api/v1/votes/",
        "/api/v1/votes/mine/",
        "/api/v1/votes/{id}/void/",
        "/api/v1/audit-logs/",
    ):
        assert expected in paths, expected
    schemes = spec["components"]["securitySchemes"]
    assert {"staffAuth", "voterOrStaffAuth"} <= set(schemes)
    assert "voter token" in schemes["voterOrStaffAuth"]["description"]
    assert "Voter tokens are not accepted" in schemes["staffAuth"]["description"]
    assert all(p.startswith("/api/v1/") for p in paths)  # everything lives under /api/v1/


def test_schema_reflects_the_security_design():
    spec = load_committed()
    schemas = spec["components"]["schemas"]
    cast_request = schemas["VoteCastRequest"]["properties"]
    assert set(cast_request) == {"nomination_id"}  # award/event are never client input
    nominee = schemas["PublicNominee"]["properties"]
    assert not {"contact_phone", "contact_email", "owner"} & set(nominee)
    submit = spec["paths"]["/api/v1/nominations/"]["post"]["requestBody"]["content"]
    assert "multipart/form-data" in submit
    assert "votes" not in schemas.get("PublicNominee", {}).get("properties", {})


def test_schema_and_docs_are_staff_only(client):
    for name in ("schema", "swagger-ui", "redoc"):
        url = reverse(name)
        assert client.get(url).status_code == 403, name  # anonymous
    # API tokens do not open the docs; only a Django admin session does.
    for api in (staff_client(make_event_admin()), voter_client(make_voter()), APIClient()):
        assert api.get(reverse("schema")).status_code == 403
    outsider = make_user(is_staff=False)
    client.force_login(outsider)
    assert client.get(reverse("schema")).status_code == 403
    client.force_login(make_user())
    for name in ("schema", "swagger-ui", "redoc"):
        assert client.get(reverse(name)).status_code == 200, name
