"""Audit trail: append-only guarantees, safe metadata, read API (AUDIT F-13 area, dispute resolution)."""

from datetime import timedelta

import pytest
import time_machine
from django.db import DatabaseError, connection, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from audit import services as audit
from audit.models import AppendOnlyError, AuditAction, AuditLog
from voting.authentication import VoterPrincipal

from .helpers import (
    make_event_admin,
    make_moderator,
    make_user,
    make_voter,
    staff_client,
    voter_client,
)

pytestmark = pytest.mark.django_db

URL = "/api/v1/audit-logs/"


def entry(**kwargs):
    kwargs.setdefault("action", AuditAction.VOTE_CAST)
    return AuditLog.objects.create(**kwargs)


# --- append-only ------------------------------------------------------------------------


def test_entries_cannot_be_modified_or_deleted_through_the_orm():
    row = entry()
    row.action = AuditAction.VOTE_VOIDED
    with pytest.raises(AppendOnlyError):
        row.save()
    with pytest.raises(AppendOnlyError):
        row.delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().update(action="x")
    assert AuditLog.objects.get(pk=row.pk).action == AuditAction.VOTE_CAST


def test_entries_are_immutable_in_postgres_even_for_raw_sql():
    row = entry()
    with connection.cursor() as cursor:
        for sql in (
            "UPDATE audit_auditlog SET action = 'x' WHERE id = %s",
            "UPDATE audit_auditlog SET metadata = '{}' WHERE id = %s",
            "DELETE FROM audit_auditlog WHERE id = %s",
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(sql, [row.pk])
    with pytest.raises(DatabaseError), transaction.atomic():
        AuditLog.objects.filter(pk=row.pk)._raw_delete(using="default")
    assert AuditLog.objects.filter(pk=row.pk, action=AuditAction.VOTE_CAST).exists()


def test_users_and_voters_referenced_by_the_log_cannot_be_deleted():
    """The table is append-only, so a referenced user must be deactivated, never deleted."""
    user, voter = make_user(), make_voter()
    entry(actor_user=user)
    entry(actor_voter=voter)
    with pytest.raises(ProtectedError):
        user.delete()
    with pytest.raises(ProtectedError):
        voter.delete()


def test_entry_string_and_ordering():
    now = timezone.now()
    with time_machine.travel(now, tick=False):
        first = entry(target_type="x.y", target_id="1")
    with time_machine.travel(now + timedelta(seconds=5), tick=False):
        second = entry(action=AuditAction.VOTE_VOIDED)
    assert list(AuditLog.objects.all()) == [second, first]  # newest first
    assert "vote.cast" in str(first) and "x.y:1" in str(first)


# --- writing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["code", "otp", "password", "phone", "phone_e164", "token", "secret", "PASSWORD"]
)
def test_sensitive_metadata_keys_are_refused(key):
    with pytest.raises(ValueError):
        audit.log(AuditAction.OTP_REQUESTED, metadata={key: "value"})
    assert AuditLog.objects.count() == 0


def test_masked_phone_metadata_is_allowed():
    row = audit.log(AuditAction.OTP_REQUESTED, metadata={"phone_masked": "+2547******12"})
    assert row.metadata == {"phone_masked": "+2547******12"}


def test_log_takes_ip_user_agent_and_actor_from_the_request():
    user = make_user()
    request = RequestFactory().get("/x", REMOTE_ADDR="203.0.113.9", HTTP_USER_AGENT="U" * 500)
    request.user = user
    row = audit.log(AuditAction.STAFF_LOGIN, request=request)
    assert row.actor_user == user and row.actor_voter is None
    assert row.ip_address == "203.0.113.9" and len(row.user_agent) == 300


def test_log_recognises_voter_principals_and_explicit_actors_win():
    voter, user = make_voter(), make_user()
    request = RequestFactory().get("/x")
    request.user = VoterPrincipal(voter)
    assert audit.log(AuditAction.VOTE_CAST, request=request).actor_voter == voter
    assert audit.log(AuditAction.VOTE_CAST, request=request, actor_user=user).actor_user == user


def test_log_without_a_request_and_with_a_target():
    user = make_user()
    row = audit.log(AuditAction.EVENT_STATUS_CHANGED, target=user, metadata={"a": 1})
    assert row.target_type == "accounts.user" and row.target_id == str(user.pk)
    assert row.ip_address is None


# --- read API ---------------------------------------------------------------------------------


def test_only_event_admins_can_read_the_audit_log():
    row = entry()
    assert APIClient().get(URL).status_code == 401
    assert voter_client(make_voter()).get(URL).status_code == 401
    assert staff_client(make_moderator()).get(URL).status_code == 403
    admin = staff_client(make_event_admin())
    assert admin.get(URL).status_code == 200
    assert admin.get(f"{URL}{row.id}/").status_code == 200
    assert staff_client(make_user(superuser=True)).get(URL).status_code == 200


def test_the_audit_api_is_read_only():
    row = entry()
    admin = staff_client(make_event_admin())
    assert admin.post(URL, {"action": "vote.cast"}).status_code == 405
    for method in (admin.put, admin.patch):
        assert method(f"{URL}{row.id}/", {"action": "x"}).status_code == 405
    assert admin.delete(f"{URL}{row.id}/").status_code == 405
    assert AuditLog.objects.count() >= 1


def test_audit_entries_expose_masked_voters_and_no_raw_phone_numbers():
    voter = make_voter("+254712345612")
    entry(actor_voter=voter, metadata={"phone_masked": "+2547******12"})
    admin_user = make_event_admin()
    entry(actor_user=admin_user)
    body = staff_client(admin_user).get(URL)
    text = body.content.decode()
    assert "+254712345612" not in text and "+2547******12" in text
    users = {r["actor_user"] for r in body.data["results"]}
    assert admin_user.email in users


def test_audit_log_filters():
    admin_user, other = make_event_admin(), make_user()
    voter = make_voter()
    old = entry(action=AuditAction.STAFF_LOGIN, actor_user=other, target_type="a.b", target_id="7")
    recent = entry(
        action=AuditAction.VOTE_CAST, actor_voter=voter, target_type="voting.vote", target_id="9"
    )
    client = staff_client(admin_user)

    def ids(query):
        return {r["id"] for r in client.get(URL + query).data["results"]}

    assert ids("?action=vote.cast") == {str(recent.id)}
    assert ids("?target_type=a.b") == {str(old.id)}
    assert ids("?target_id=9") == {str(recent.id)}
    assert ids(f"?actor_user={other.id}") == {str(old.id)}
    assert ids(f"?actor_voter={voter.id}") == {str(recent.id)}
    future = (timezone.now() + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert ids(f"?created_after={future}") == set()
    assert ids(f"?created_before={future}") >= {str(old.id), str(recent.id)}


def test_audit_log_is_paginated():
    for _ in range(27):
        entry()
    admin = staff_client(make_event_admin())
    first = admin.get(URL).data
    assert first["count"] >= 27 and len(first["results"]) == 25 and first["next"]
