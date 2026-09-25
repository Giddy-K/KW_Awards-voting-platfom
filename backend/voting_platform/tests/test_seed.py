"""The seed_demo management command (local development only)."""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

from events.models import Award, Category, Event
from nominations.models import Nomination, NominationStatus, Nominee

pytestmark = pytest.mark.django_db
User = get_user_model()


def seed(settings, **kwargs):
    settings.DEBUG = True
    call_command("seed_demo", verbosity=0, **kwargs)


def test_seed_refuses_to_run_when_debug_is_false(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG"):
        call_command("seed_demo", verbosity=0)
    assert not Event.objects.exists() and not User.objects.exists()


def test_seed_creates_the_documented_demo_data(settings):
    seed(settings, no_photos=True)
    assert Event.objects.count() == 1
    event = Event.objects.get()
    assert event.status == "voting_open" and event.voting_is_open()
    assert Category.objects.count() == 3 and Award.objects.count() == 6
    assert Nominee.objects.count() == 15
    assert Nomination.objects.filter(status=NominationStatus.APPROVED).count() == 15
    assert set(Group.objects.values_list("name", flat=True)) >= {"Moderator", "EventAdmin"}


def test_seed_creates_one_user_per_role_that_can_log_in(settings):
    seed(settings, no_photos=True, password="a-Strong-demo-pass-1")
    api = APIClient()
    expected = {
        "admin@example.com": ["superuser"],
        "moderator@example.com": ["Moderator"],
        "eventadmin@example.com": ["EventAdmin"],
    }
    for email, roles in expected.items():
        token = api.post(
            "/api/v1/auth/token/",
            {"email": email, "password": "a-Strong-demo-pass-1"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        assert token.status_code == 200, email
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {token.data['access']}")
        assert api.get("/api/v1/auth/me/").data["roles"] == roles
        api.credentials()


def test_seed_is_idempotent(settings):
    seed(settings, no_photos=True)
    seed(settings, no_photos=True)
    assert Event.objects.count() == 1 and Nominee.objects.count() == 15
    assert Nomination.objects.count() == 15 and User.objects.count() == 3


def test_seeded_data_is_visible_through_the_public_api(settings):
    seed(settings, no_photos=True)
    api = APIClient()
    body = api.get("/api/v1/events/").data["results"][0]
    assert [c["name"] for c in body["categories"]] == ["Music", "Film & TV", "Comedy"]
    assert sum(len(c["awards"]) for c in body["categories"]) == 6
    award_id = body["categories"][0]["awards"][0]["id"]
    assert api.get(f"/api/v1/awards/{award_id}/nominations/").data["count"] >= 2


def test_seed_generates_placeholder_photos_unless_disabled(settings):
    seed(settings)
    assert all(bool(n.photo) for n in Nominee.objects.all())
    first = Nominee.objects.first().photo.name
    seed(settings)
    assert Nominee.objects.get(pk=Nominee.objects.first().pk).photo.name == first  # not regenerated
