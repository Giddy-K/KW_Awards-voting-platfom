"""Permission matrix: every API endpoint x {anonymous, voter, moderator, event admin, superuser}.

Each row states the expected HTTP status per principal. A voter token counts as "not staff":
on public endpoints it behaves like anonymous, on staff endpoints it is simply not accepted.
(AUDIT F-03, F-05, F-09: deny by default, server-side authorization on every endpoint.)
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from events.models import EventStatus
from nominations.models import NominationStatus

from .helpers import (
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_user,
    make_voter,
    staff_client,
    voter_client,
)
from .media_helpers import photo_upload
from .vote_helpers import make_vote

pytestmark = pytest.mark.django_db

PRINCIPALS = ["anonymous", "voter", "moderator", "event_admin", "superuser"]


@dataclass
class World:
    event: Any
    category: Any
    award: Any
    approved: Any
    pending: Any
    vote: Any
    spare_event: Any
    spare_category: Any
    spare_award: Any
    published_event: Any
    open_award: Any
    audit_entry: Any


@pytest.fixture
def world():
    event = make_event()
    category = make_category(event)
    award = make_award(category)
    approved = make_nomination(award)
    pending = make_nomination(award, status=NominationStatus.PENDING)
    vote = make_vote(make_voter(), approved)
    spare_event = make_event(status=EventStatus.DRAFT)
    spare_category = make_category(event)
    spare_award = make_award(category)
    published = make_event(status=EventStatus.RESULTS_PUBLISHED)
    now = timezone.now()
    nominating = make_event(status=EventStatus.NOMINATIONS_OPEN)
    nominating.nominations_open_at = now - timedelta(days=1)
    nominating.nominations_close_at = now + timedelta(days=3)
    nominating.voting_opens_at = now + timedelta(days=4)
    nominating.voting_closes_at = now + timedelta(days=9)
    nominating.save()
    open_award = make_award(make_category(nominating))
    from audit.models import AuditAction, AuditLog

    audit_entry = AuditLog.objects.create(action=AuditAction.VOTE_CAST)
    return World(
        event,
        category,
        award,
        approved,
        pending,
        vote,
        spare_event,
        spare_category,
        spare_award,
        published,
        open_award,
        audit_entry,
    )


def client_for(principal):
    if principal == "anonymous":
        return APIClient()
    if principal == "voter":
        return voter_client(make_voter())
    if principal == "moderator":
        return staff_client(make_moderator())
    if principal == "event_admin":
        return staff_client(make_event_admin())
    return staff_client(make_user(superuser=True))


def expect(anonymous, voter, moderator, event_admin, superuser):
    return {
        "anonymous": anonymous,
        "voter": voter,
        "moderator": moderator,
        "event_admin": event_admin,
        "superuser": superuser,
    }


def everyone(status):
    return expect(status, status, status, status, status)


PUBLIC = everyone(200)
STAFF_ONLY_EVENT_ADMIN = expect(401, 401, 403, 200, 200)


@dataclass
class Case:
    name: str
    method: str
    url: Callable[[World], str]
    expected: dict
    body: Callable[[World], dict] = field(default=lambda w: {})
    fmt: str = "json"


def uid():
    return uuid.uuid4().hex[:10]


CASES = [
    # --- auth
    Case("auth-me", "get", lambda w: "/api/v1/auth/me/", expect(401, 401, 200, 200, 200)),
    Case(
        "auth-logout",
        "post",
        lambda w: "/api/v1/auth/logout/",
        expect(401, 401, 400, 400, 400),
        lambda w: {"refresh": "not-a-token"},
    ),
    Case(
        "auth-refresh",
        "post",
        lambda w: "/api/v1/auth/refresh/",
        everyone(401),
        lambda w: {"refresh": "not-a-token"},
    ),
    Case(
        "auth-token",
        "post",
        lambda w: "/api/v1/auth/token/",
        everyone(401),
        lambda w: {"email": "nobody@example.com", "password": "wrong"},
    ),
    # --- events
    Case("events-list", "get", lambda w: "/api/v1/events/", PUBLIC),
    Case("events-detail", "get", lambda w: f"/api/v1/events/{w.event.slug}/", PUBLIC),
    Case(
        "events-create",
        "post",
        lambda w: "/api/v1/events/",
        expect(401, 401, 403, 201, 201),
        lambda w: {"name": "New", "slug": f"new-{uid()}", "year": 2030},
    ),
    Case(
        "events-update",
        "patch",
        lambda w: f"/api/v1/events/{w.event.slug}/",
        STAFF_ONLY_EVENT_ADMIN,
        lambda w: {"name": "Renamed"},
    ),
    Case(
        "events-delete",
        "delete",
        lambda w: f"/api/v1/events/{w.spare_event.slug}/",
        expect(401, 401, 403, 204, 204),
    ),
    Case(
        "events-set-status",
        "post",
        lambda w: f"/api/v1/events/{w.event.slug}/set-status/",
        STAFF_ONLY_EVENT_ADMIN,
        lambda w: {"status": "closed"},
    ),
    Case(
        "events-results-unpublished",
        "get",
        lambda w: f"/api/v1/events/{w.event.slug}/results/",
        expect(404, 404, 404, 200, 200),
    ),
    Case(
        "events-results-published",
        "get",
        lambda w: f"/api/v1/events/{w.published_event.slug}/results/",
        PUBLIC,
    ),
    Case(
        "events-stats",
        "get",
        lambda w: f"/api/v1/events/{w.event.slug}/stats/",
        STAFF_ONLY_EVENT_ADMIN,
    ),
    # --- categories / awards
    Case("categories-list", "get", lambda w: "/api/v1/categories/", PUBLIC),
    Case("categories-detail", "get", lambda w: f"/api/v1/categories/{w.category.id}/", PUBLIC),
    Case(
        "categories-create",
        "post",
        lambda w: "/api/v1/categories/",
        expect(401, 401, 403, 201, 201),
        lambda w: {"event": str(w.event.id), "name": "C", "slug": f"c-{uid()}"},
    ),
    Case(
        "categories-update",
        "patch",
        lambda w: f"/api/v1/categories/{w.category.id}/",
        STAFF_ONLY_EVENT_ADMIN,
        lambda w: {"name": "Renamed"},
    ),
    Case(
        "categories-delete",
        "delete",
        lambda w: f"/api/v1/categories/{w.spare_category.id}/",
        expect(401, 401, 403, 204, 204),
    ),
    Case("awards-list", "get", lambda w: "/api/v1/awards/", PUBLIC),
    Case("awards-detail", "get", lambda w: f"/api/v1/awards/{w.award.id}/", PUBLIC),
    Case(
        "awards-create",
        "post",
        lambda w: "/api/v1/awards/",
        expect(401, 401, 403, 201, 201),
        lambda w: {"category": str(w.category.id), "name": "A", "slug": f"a-{uid()}"},
    ),
    Case(
        "awards-update",
        "patch",
        lambda w: f"/api/v1/awards/{w.award.id}/",
        STAFF_ONLY_EVENT_ADMIN,
        lambda w: {"name": "Renamed"},
    ),
    Case(
        "awards-delete",
        "delete",
        lambda w: f"/api/v1/awards/{w.spare_award.id}/",
        expect(401, 401, 403, 204, 204),
    ),
    Case("award-nominations", "get", lambda w: f"/api/v1/awards/{w.award.id}/nominations/", PUBLIC),
    # --- nominations
    Case("nominations-list", "get", lambda w: "/api/v1/nominations/", PUBLIC),
    Case(
        "nominations-detail-approved",
        "get",
        lambda w: f"/api/v1/nominations/{w.approved.id}/",
        PUBLIC,
    ),
    Case(
        "nominations-detail-pending",
        "get",
        lambda w: f"/api/v1/nominations/{w.pending.id}/",
        expect(404, 404, 200, 404, 200),
    ),
    Case(
        "nominations-submit",
        "post",
        lambda w: "/api/v1/nominations/",
        everyone(201),
        lambda w: {
            "award": str(w.open_award.id),
            "name": f"Nominee {uid()}",
            "contact_email": "n@example.com",
            "captcha_token": "t",
            "photo": photo_upload(),
        },
        "multipart",
    ),
    Case(
        "nominations-approve",
        "post",
        lambda w: f"/api/v1/nominations/{w.pending.id}/approve/",
        expect(401, 401, 200, 403, 200),
    ),
    Case(
        "nominations-reject",
        "post",
        lambda w: f"/api/v1/nominations/{w.pending.id}/reject/",
        expect(401, 401, 200, 403, 200),
        lambda w: {"reason": "No"},
    ),
    Case(
        "nominations-patch-not-allowed",
        "patch",
        lambda w: f"/api/v1/nominations/{w.approved.id}/",
        everyone(405),
        lambda w: {"status": "approved"},
    ),
    Case(
        "nominations-delete-not-allowed",
        "delete",
        lambda w: f"/api/v1/nominations/{w.approved.id}/",
        everyone(405),
    ),
    Case("nominee-profile", "get", lambda w: f"/api/v1/nominees/{w.approved.nominee_id}/", PUBLIC),
    # --- voter flow
    Case(
        "otp-request",
        "post",
        lambda w: "/api/v1/voters/otp/request/",
        everyone(202),
        lambda w: {"phone": "0712000000", "captcha_token": "t"},
    ),
    Case(
        "otp-verify",
        "post",
        lambda w: "/api/v1/voters/otp/verify/",
        everyone(400),
        lambda w: {"phone": "0712000000", "code": "123456"},
    ),
    Case(
        "votes-cast",
        "post",
        lambda w: "/api/v1/votes/",
        expect(401, 201, 403, 403, 403),
        lambda w: {"nomination_id": str(w.approved.id)},
    ),
    Case("votes-mine", "get", lambda w: "/api/v1/votes/mine/", expect(401, 200, 403, 403, 403)),
    Case("votes-list", "get", lambda w: "/api/v1/votes/", expect(401, 403, 403, 200, 200)),
    Case(
        "votes-detail",
        "get",
        lambda w: f"/api/v1/votes/{w.vote.id}/",
        expect(401, 403, 403, 200, 200),
    ),
    Case(
        "votes-void",
        "post",
        lambda w: f"/api/v1/votes/{w.vote.id}/void/",
        expect(401, 403, 403, 200, 200),
        lambda w: {"reason": "fraud"},
    ),
    Case(
        "votes-patch-not-allowed",
        "patch",
        lambda w: f"/api/v1/votes/{w.vote.id}/",
        expect(401, 403, 403, 405, 405),
        lambda w: {"quantity": 50},
    ),
    Case(
        "votes-delete-not-allowed",
        "delete",
        lambda w: f"/api/v1/votes/{w.vote.id}/",
        expect(401, 403, 403, 405, 405),
    ),
    # --- audit
    Case("audit-list", "get", lambda w: "/api/v1/audit-logs/", STAFF_ONLY_EVENT_ADMIN),
    Case(
        "audit-detail",
        "get",
        lambda w: f"/api/v1/audit-logs/{w.audit_entry.id}/",
        STAFF_ONLY_EVENT_ADMIN,
    ),
    Case(
        "audit-post-not-allowed",
        "post",
        lambda w: "/api/v1/audit-logs/",
        expect(401, 401, 403, 405, 405),
    ),
]


@pytest.mark.parametrize("principal", PRINCIPALS)
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_endpoint_authorization_matrix(world, case, principal):
    client = client_for(principal)
    response = getattr(client, case.method)(case.url(world), case.body(world), format=case.fmt)
    expected = case.expected[principal]
    assert response.status_code == expected, (
        f"{case.method.upper()} {case.name} as {principal}: "
        f"expected {expected}, got {response.status_code} {getattr(response, 'data', '')}"
    )


def _normalise(path):
    import re

    return re.sub(r"\{[^}]+\}", "{x}", path)


def test_every_registered_api_route_is_covered_by_the_matrix():
    """Adding an endpoint without deciding its permissions must fail this test."""
    from drf_spectacular.generators import EndpointEnumerator

    routes = {
        (_normalise(path), "patch" if method.lower() == "put" else method.lower())
        for path, _regex, method, _callback in EndpointEnumerator().get_api_endpoints()
        if path.startswith("/api/v1/")
    }
    placeholder = _Placeholder()
    covered = {(_normalise(c.url(placeholder)), c.method) for c in CASES}
    uncovered = routes - covered
    assert not uncovered, f"routes without a permission-matrix case: {sorted(uncovered)}"
    assert len(routes) >= 35, "the enumerator found suspiciously few routes"


class _Placeholder:
    """Stands in for ``World`` so URLs render with ``{x}`` markers."""

    id = "{x}"
    slug = "{x}"
    nominee_id = "{x}"

    def __getattr__(self, name):
        return _Placeholder()
