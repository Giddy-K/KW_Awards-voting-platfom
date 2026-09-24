"""Events, categories, awards: visibility, staff CRUD, status transitions (AUDIT F-03, F-09, F-18, F-25)."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditAction, AuditLog
from events.models import Event, EventStatus

from .helpers import (
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_voter,
    staff_client,
)

pytestmark = pytest.mark.django_db


def event_payload(**overrides):
    now = timezone.now()
    payload = {
        "name": "KW Awards 2027",
        "slug": "kw-awards-2027",
        "year": 2027,
        "nominations_open_at": (now + timedelta(days=1)).isoformat(),
        "nominations_close_at": (now + timedelta(days=10)).isoformat(),
        "voting_opens_at": (now + timedelta(days=11)).isoformat(),
        "voting_closes_at": (now + timedelta(days=20)).isoformat(),
    }
    payload.update(overrides)
    return payload


# --- public read ------------------------------------------------------------


def test_public_list_hides_draft_events_and_nests_categories_and_awards(api):
    draft = make_event(status=EventStatus.DRAFT)
    live = make_event()
    cat = make_category(live, name="Music", display_order=1)
    award = make_award(cat, name="Best Album")
    make_award(cat, name="Hidden Award", is_active=False)
    response = api.get("/api/v1/events/")
    assert response.status_code == 200
    slugs = [e["slug"] for e in response.data["results"]]
    assert live.slug in slugs and draft.slug not in slugs
    body = next(e for e in response.data["results"] if e["slug"] == live.slug)
    assert [c["name"] for c in body["categories"]] == ["Music"]
    assert [a["name"] for a in body["categories"][0]["awards"]] == ["Best Album"]
    assert body["categories"][0]["awards"][0]["id"] == str(award.id)


def test_public_detail_by_slug_and_draft_is_404_for_public_but_visible_to_admin(api):
    draft = make_event(status=EventStatus.DRAFT)
    assert api.get(f"/api/v1/events/{draft.slug}/").status_code == 404
    assert staff_client(make_moderator()).get(f"/api/v1/events/{draft.slug}/").status_code == 404
    assert staff_client(make_event_admin()).get(f"/api/v1/events/{draft.slug}/").status_code == 200
    live = make_event()
    assert api.get(f"/api/v1/events/{live.slug}/").data["id"] == str(live.id)


def test_public_read_never_exposes_inactive_awards_but_staff_see_them(api):
    event = make_event()
    cat = make_category(event)
    make_award(cat, name="Visible")
    make_award(cat, name="Inactive", is_active=False)
    public = api.get(f"/api/v1/events/{event.slug}/").data
    assert [a["name"] for a in public["categories"][0]["awards"]] == ["Visible"]
    staff = staff_client(make_event_admin()).get(f"/api/v1/events/{event.slug}/").data
    assert {a["name"] for a in staff["categories"][0]["awards"]} == {"Visible", "Inactive"}


def test_event_reports_open_flags_from_status_and_window(api):
    event = make_event()  # voting_open and inside the window
    body = api.get(f"/api/v1/events/{event.slug}/").data
    assert body["voting_is_open"] is True and body["nominations_are_open"] is False
    outside = make_event(voting_open_now=False)  # status voting_open but window in the future
    assert api.get(f"/api/v1/events/{outside.slug}/").data["voting_is_open"] is False


def test_filters_and_search_on_awards_and_categories(api):
    event = make_event()
    music, comedy = make_category(event, name="Music"), make_category(event, name="Comedy")
    make_award(music, name="Best Album")
    make_award(music, name="Best Song")
    make_award(comedy, name="Best Stand-up")
    other = make_award(make_category(make_event(), name="Other"), name="Elsewhere")
    by_cat = api.get(f"/api/v1/awards/?category={music.id}").data["results"]
    assert {a["name"] for a in by_cat} == {"Best Album", "Best Song"}
    by_event = api.get(f"/api/v1/awards/?event={event.slug}").data["results"]
    assert other.name not in {a["name"] for a in by_event} and len(by_event) == 3
    found = api.get(f"/api/v1/awards/?event={event.slug}&search=stand").data["results"]
    assert [a["name"] for a in found] == ["Best Stand-up"]
    cats = api.get(f"/api/v1/categories/?event={event.slug}").data["results"]
    assert {c["name"] for c in cats} == {"Music", "Comedy"}


def test_draft_event_awards_and_categories_are_not_public(api):
    draft = make_event(status=EventStatus.DRAFT)
    award = make_award(make_category(draft))
    assert api.get("/api/v1/awards/").data["count"] == 0
    assert api.get(f"/api/v1/awards/{award.id}/").status_code == 404
    assert api.get("/api/v1/categories/").data["count"] == 0


def test_f25_lists_are_paginated(api):
    """The old list endpoints returned every row (PAGE_SIZE was never set)."""
    for _ in range(27):
        make_event()
    first = api.get("/api/v1/events/").data
    assert first["count"] == 27 and len(first["results"]) == 25 and first["next"]
    second = api.get("/api/v1/events/?page=2").data
    assert len(second["results"]) == 2 and second["next"] is None


# --- staff write ------------------------------------------------------------


def test_f03_only_event_admins_can_write(api):
    payload = event_payload()
    assert api.post("/api/v1/events/", payload).status_code == 401
    assert staff_client(make_moderator()).post("/api/v1/events/", payload).status_code == 403
    response = staff_client(make_event_admin()).post("/api/v1/events/", payload)
    assert response.status_code == 201, response.data
    assert response.data["status"] == "draft"
    assert (
        staff_client(make_user_superuser())
        .post("/api/v1/events/", event_payload(slug="another", name="Another"))
        .status_code
        == 201
    )


def make_user_superuser():
    from .helpers import make_user

    return make_user(superuser=True)


def test_permission_denials_are_audited():
    moderator = make_moderator()
    staff_client(moderator).post("/api/v1/events/", event_payload())
    entry = AuditLog.objects.get(action=AuditAction.PERMISSION_DENIED)
    assert entry.actor_user == moderator
    assert entry.metadata["method"] == "POST" and entry.metadata["path"] == "/api/v1/events/"


def test_status_cannot_be_set_through_create_or_update():
    admin = staff_client(make_event_admin())
    created = admin.post("/api/v1/events/", event_payload(status="voting_open"))
    assert created.status_code == 201 and created.data["status"] == "draft"
    patched = admin.patch(
        f"/api/v1/events/{created.data['slug']}/", {"status": "results_published"}
    )
    assert patched.status_code == 200 and patched.data["status"] == "draft"


def test_window_ordering_is_validated_by_the_api_and_the_database():
    admin = staff_client(make_event_admin())
    now = timezone.now()
    bad = event_payload(
        voting_opens_at=(now + timedelta(days=5)).isoformat(),
        voting_closes_at=(now + timedelta(days=4)).isoformat(),
    )
    assert admin.post("/api/v1/events/", bad).status_code == 400
    overlap = event_payload(nominations_close_at=(now + timedelta(days=15)).isoformat())
    assert admin.post("/api/v1/events/", overlap).status_code == 400
    event = make_event()
    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.filter(pk=event.pk).update(voting_closes_at=event.voting_opens_at)
    with pytest.raises(IntegrityError), transaction.atomic():
        Event.objects.filter(pk=event.pk).update(
            status="results_published", results_published_at=None
        )


def test_event_year_and_slug_validation():
    admin = staff_client(make_event_admin())
    assert admin.post("/api/v1/events/", event_payload(year=1999)).status_code == 400
    assert admin.post("/api/v1/events/", event_payload(slug="Bad Slug!")).status_code == 400
    make_event(slug="taken")
    assert admin.post("/api/v1/events/", event_payload(slug="taken")).status_code == 400


def test_html_is_stripped_from_names_and_descriptions():
    admin = staff_client(make_event_admin())
    event = make_event()
    response = admin.post(
        "/api/v1/categories/",
        {
            "event": str(event.id),
            "name": "<b>Music</b><script>alert(1)</script>",
            "slug": "music",
            "description": "<img src=x onerror=alert(1)>Loud",
        },
    )
    assert response.status_code == 201, response.data
    assert "<" not in response.data["name"] and "<" not in response.data["description"]


def test_category_and_award_crud_and_uniqueness():
    admin = staff_client(make_event_admin())
    event = make_event()
    cat = admin.post(
        "/api/v1/categories/",
        {"event": str(event.id), "name": "Music", "slug": "music", "display_order": 1},
    )
    assert cat.status_code == 201
    dup = admin.post(
        "/api/v1/categories/", {"event": str(event.id), "name": "Music 2", "slug": "music"}
    )
    assert dup.status_code == 400
    award = admin.post(
        "/api/v1/awards/", {"category": cat.data["id"], "name": "Best Album", "slug": "best-album"}
    )
    assert award.status_code == 201
    assert (
        admin.patch(f"/api/v1/awards/{award.data['id']}/", {"is_active": False}).status_code == 200
    )
    assert admin.delete(f"/api/v1/awards/{award.data['id']}/").status_code == 204
    assert admin.delete(f"/api/v1/categories/{cat.data['id']}/").status_code == 204


def test_moderator_cannot_write_categories_or_awards():
    mod = staff_client(make_moderator())
    category = make_category()
    award = make_award(category)
    assert (
        mod.post(
            "/api/v1/categories/", {"event": str(category.event_id), "name": "x", "slug": "x"}
        ).status_code
        == 403
    )
    assert mod.patch(f"/api/v1/awards/{award.id}/", {"name": "New"}).status_code == 403
    assert mod.delete(f"/api/v1/awards/{award.id}/").status_code == 403


def test_f18_only_standard_rest_routes_exist(api):
    """The old API used /delete/<id> and /put/<id> paths."""
    award = make_award()
    admin = staff_client(make_event_admin())
    for path in (
        f"/api/v1/awards/delete/{award.id}",
        f"/api/v1/awards/put/{award.id}",
        f"/api/v1/nominees/delete/{award.id}",
        f"/api/v1/nominees/put/{award.id}",
    ):
        assert admin.delete(path).status_code == 404, path
        assert admin.put(path, {}).status_code == 404, path
    assert (
        admin.put(
            f"/api/v1/awards/{award.id}/",
            {"category": str(award.category_id), "name": "Renamed", "slug": "renamed"},
        ).status_code
        == 200
    )


def test_deleting_an_event_with_votes_is_refused():
    from voting.models import Vote

    nomination = make_nomination()
    event = nomination.award.category.event
    Vote.objects.create(
        event=event,
        award=nomination.award,
        nomination=nomination,
        voter=make_voter(),
        source="free",
    )
    response = staff_client(make_event_admin()).delete(f"/api/v1/events/{event.slug}/")
    assert response.status_code == 409 and response.data["code"] == "conflict"
    assert Event.objects.filter(pk=event.pk).exists()


def test_deleting_an_event_without_votes_works():
    event = make_event()
    assert (
        staff_client(make_event_admin()).delete(f"/api/v1/events/{event.slug}/").status_code == 204
    )


# --- status transitions -----------------------------------------------------


def set_status(client, event, status):
    return client.post(f"/api/v1/events/{event.slug}/set-status/", {"status": status})


def test_valid_transition_path_and_audit_trail():
    admin_user = make_event_admin()
    admin = staff_client(admin_user)
    event = make_event(status=EventStatus.DRAFT)
    for target in ("nominations_open", "voting_open", "closed", "results_published"):
        response = set_status(admin, event, target)
        assert response.status_code == 200, (target, response.data)
        assert response.data["status"] == target
    event.refresh_from_db()
    assert event.results_published_at is not None
    trail = list(
        AuditLog.objects.filter(action=AuditAction.EVENT_STATUS_CHANGED).order_by("created_at")
    )
    assert [(e.metadata["from"], e.metadata["to"]) for e in trail] == [
        ("draft", "nominations_open"),
        ("nominations_open", "voting_open"),
        ("voting_open", "closed"),
        ("closed", "results_published"),
    ]
    assert all(e.actor_user == admin_user and e.target_id == str(event.id) for e in trail)


def test_invalid_transitions_are_rejected_with_conflict():
    admin = staff_client(make_event_admin())
    event = make_event(status=EventStatus.DRAFT)
    for bad in ("voting_open", "closed", "results_published", "draft"):
        response = set_status(admin, event, bad)
        assert response.status_code == 409 and response.data["code"] == "invalid_transition", bad
    assert set_status(admin, event, "bogus").status_code == 400
    assert not AuditLog.objects.filter(action=AuditAction.EVENT_STATUS_CHANGED).exists()


def test_unpublishing_results_clears_the_timestamp():
    admin = staff_client(make_event_admin())
    event = make_event(status=EventStatus.RESULTS_PUBLISHED)
    assert set_status(admin, event, "closed").status_code == 200
    event.refresh_from_db()
    assert event.results_published_at is None


def test_opening_voting_or_nominations_requires_windows():
    admin = staff_client(make_event_admin())
    event = Event.objects.create(name="No windows", slug="no-windows", year=2026)
    assert set_status(admin, event, "nominations_open").status_code == 400
    Event.objects.filter(pk=event.pk).update(status="nominations_open")
    assert set_status(admin, event, "voting_open").status_code == 400


def test_only_event_admins_can_change_status():
    event = make_event(status=EventStatus.DRAFT)
    assert set_status(staff_client(make_moderator()), event, "nominations_open").status_code == 403


# --- model helpers ----------------------------------------------------------


def test_voting_and_nomination_windows_are_open_inclusive_closed_exclusive():
    event = make_event()
    assert event.voting_is_open(event.voting_opens_at)
    assert not event.voting_is_open(event.voting_opens_at - timedelta(microseconds=1))
    assert event.voting_is_open(event.voting_closes_at - timedelta(microseconds=1))
    assert not event.voting_is_open(event.voting_closes_at)
    event.status = EventStatus.CLOSED
    assert not event.voting_is_open(event.voting_opens_at + timedelta(hours=1))
    nomination_event = make_event(status=EventStatus.NOMINATIONS_OPEN)
    at = nomination_event.nominations_open_at + timedelta(hours=1)
    assert nomination_event.nominations_are_open(at)
    assert not nomination_event.nominations_are_open(nomination_event.nominations_close_at)
    assert str(nomination_event).startswith("KW Awards")
