"""Small factory helpers shared by the tests (no external factory library needed)."""

import itertools
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.roles import EVENT_ADMIN, MODERATOR, ensure_roles
from events.models import Award, Category, Event, EventStatus
from nominations.models import Nomination, NominationStatus, Nominee
from voting.authentication import issue_voter_token
from voting.models import Voter

User = get_user_model()
PASSWORD = "Str0ng-test-password-123"
_counter = itertools.count(1)


def make_user(role=None, *, superuser=False, email=None, is_active=True, is_staff=True):
    ensure_roles()
    n = next(_counter)
    email = email or f"user{n}@example.com"
    if superuser:
        return User.objects.create_superuser(email=email, password=PASSWORD, full_name="Super User")
    user = User.objects.create_user(
        email=email,
        password=PASSWORD,
        full_name=f"Test {role or 'User'}",
        is_active=is_active,
        is_staff=is_staff,
    )
    if role:
        from django.contrib.auth.models import Group

        user.groups.add(Group.objects.get(name=role))
    return user


def make_moderator():
    return make_user(MODERATOR)


def make_event_admin():
    return make_user(EVENT_ADMIN)


def staff_client(user):
    client = APIClient()
    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def voter_client(voter):
    client = APIClient()
    token, _ = issue_voter_token(voter)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def make_event(
    *, status=EventStatus.VOTING_OPEN, slug=None, year=2026, voting_open_now=True, name=None
):
    """Event with sensible windows. ``voting_open_now`` places 'now' inside the voting window."""
    n = next(_counter)
    now = timezone.now()
    return Event.objects.create(
        name=name or f"KW Awards {n}",
        slug=slug or f"kw-awards-{n}",
        year=year,
        status=status,
        nominations_open_at=now - timedelta(days=30),
        nominations_close_at=now - timedelta(days=10),
        voting_opens_at=now - timedelta(days=1) if voting_open_now else now + timedelta(days=5),
        voting_closes_at=now + timedelta(days=10) if voting_open_now else now + timedelta(days=20),
        results_published_at=now if status == EventStatus.RESULTS_PUBLISHED else None,
    )


def make_category(event=None, **kwargs):
    event = event or make_event()
    n = next(_counter)
    return Category.objects.create(
        event=event,
        name=kwargs.pop("name", f"Category {n}"),
        slug=kwargs.pop("slug", f"cat-{n}"),
        **kwargs,
    )


def make_award(category=None, **kwargs):
    category = category or make_category()
    n = next(_counter)
    return Award.objects.create(
        category=category,
        name=kwargs.pop("name", f"Award {n}"),
        slug=kwargs.pop("slug", f"award-{n}"),
        **kwargs,
    )


def make_nominee(**kwargs):
    n = next(_counter)
    kwargs.setdefault("name", f"Nominee {n}")
    kwargs.setdefault("contact_email", f"nominee{n}@example.com")
    kwargs.setdefault("contact_phone", "+254700000001")
    return Nominee.objects.create(**kwargs)


def make_nomination(award=None, status=NominationStatus.APPROVED, nominee=None, **kwargs):
    award = award or make_award()
    nominee = nominee or make_nominee()
    if status in (NominationStatus.APPROVED, NominationStatus.REJECTED):
        kwargs.setdefault("reviewed_at", timezone.now())
    if status == NominationStatus.REJECTED:
        kwargs.setdefault("rejection_reason", "Not eligible")
    return Nomination.objects.create(nominee=nominee, award=award, status=status, **kwargs)


def make_voter(phone=None, *, verified=True, blocked=False):
    n = next(_counter)
    phone = phone or f"+2547{10000000 + n:08d}"
    return Voter.objects.create(
        phone_e164=phone,
        verified_at=timezone.now() if verified else None,
        is_blocked=blocked,
    )
