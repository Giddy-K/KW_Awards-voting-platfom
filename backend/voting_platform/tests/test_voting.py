"""Casting votes: integrity, races, windows, tokens, voiding (AUDIT F-01, F-02, F-04, F-08, F-09)."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import time_machine
from django.db import DatabaseError, IntegrityError, connection, connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from audit.models import AuditAction, AuditLog
from events.models import EventStatus
from nominations.models import NominationStatus, Nominee
from voting.authentication import VoterToken, issue_voter_token
from voting.models import AppendOnlyError, Vote

from .helpers import (
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_voter,
    staff_client,
    voter_client,
)
from .vote_helpers import VOTES_URL, cast, make_paid_vote, make_vote

pytestmark = pytest.mark.django_db


def open_nomination(**kwargs):
    return make_nomination(make_award(make_category(make_event())), **kwargs)


# --- the happy path and the one-vote rule (F-01) -------------------------------


def test_verified_voter_can_cast_a_vote_and_gets_a_receipt_without_tallies():
    nomination = open_nomination()
    voter = make_voter()
    response = voter_client(voter).post(
        VOTES_URL, {"nomination_id": str(nomination.id)}, HTTP_USER_AGENT="Agent/1"
    )
    assert response.status_code == 201, response.data
    assert set(response.data) == {"id", "award", "nomination", "created_at"}
    assert str(response.data["nomination"]) == str(nomination.id)
    vote = Vote.objects.get()
    assert vote.voter == voter and vote.source == "free" and vote.quantity == 1
    assert vote.nomination == nomination and vote.award == nomination.award
    assert vote.event == nomination.award.category.event
    assert vote.ip_address == "127.0.0.1" and vote.user_agent == "Agent/1"
    entry = AuditLog.objects.get(action=AuditAction.VOTE_CAST)
    assert entry.actor_voter == voter and entry.target_id == str(vote.id)


def test_f01_second_free_vote_in_the_same_award_is_a_conflict_not_a_second_row():
    """The old API accepted unlimited anonymous votes and stored no identity."""
    award = make_award(make_category(make_event()))
    first, second = make_nomination(award), make_nomination(award)
    voter = make_voter()
    client = voter_client(voter)
    assert cast(client, first).status_code == 201
    again = cast(client, second)
    assert again.status_code == 409 and again.data["code"] == "already_voted"
    assert cast(client, first).status_code == 409  # same nomination too
    assert Vote.objects.count() == 1
    assert (
        AuditLog.objects.filter(action=AuditAction.VOTE_DUPLICATE, actor_voter=voter).count() == 2
    )


def test_a_voter_can_vote_once_in_each_award():
    category = make_category(make_event())
    nominations = [make_nomination(make_award(category)) for _ in range(3)]
    client = voter_client(make_voter())
    assert [cast(client, n).status_code for n in nominations] == [201, 201, 201]


def test_different_voters_can_vote_for_the_same_nomination():
    nomination = open_nomination()
    for _ in range(3):
        assert cast(voter_client(make_voter()), nomination).status_code == 201
    assert Vote.objects.count() == 3


def test_anonymous_requests_cannot_vote():
    nomination = open_nomination()
    assert cast(APIClient(), nomination).status_code == 401
    assert Vote.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_f01_concurrent_duplicate_votes_only_one_succeeds():
    """Real threads on separate connections: the DB constraint decides, not a pre-check."""
    nomination = open_nomination()
    token, _ = issue_voter_token(make_voter())
    workers = 12
    barrier = threading.Barrier(workers)

    def attempt(_):
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            barrier.wait(timeout=15)
            return cast(client, nomination).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(workers) as pool:
        statuses = list(pool.map(attempt, range(workers)))
    assert sorted(statuses) == [201] + [409] * (workers - 1), statuses
    assert Vote.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_f01_concurrent_votes_for_different_nominations_of_one_award_only_one_succeeds():
    award = make_award(make_category(make_event()))
    nominations = [make_nomination(award) for _ in range(4)]
    token, _ = issue_voter_token(make_voter())
    barrier = threading.Barrier(len(nominations))

    def attempt(nomination):
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            barrier.wait(timeout=15)
            return cast(client, nomination).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(len(nominations)) as pool:
        statuses = list(pool.map(attempt, nominations))
    assert sorted(statuses) == [201, 409, 409, 409]
    assert Vote.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_f02_concurrent_votes_from_many_voters_are_all_counted():
    """No read-modify-write counter: N concurrent voters produce exactly N rows and tally N."""
    from voting import results

    nomination = open_nomination()
    tokens = [issue_voter_token(make_voter())[0] for _ in range(15)]
    barrier = threading.Barrier(len(tokens))

    def attempt(token):
        try:
            client = APIClient()
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            barrier.wait(timeout=15)
            return cast(client, nomination).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(len(tokens)) as pool:
        statuses = list(pool.map(attempt, tokens))
    assert statuses.count(201) == 15
    assert results.award_tally(nomination.award)[0]["votes"] == 15


# --- derived fields and nomination checks (F-08) --------------------------------


def test_f08_award_and_event_come_from_the_nomination_never_from_the_client():
    """The old API trusted a client-supplied sub_category, so votes could be filed anywhere."""
    nomination = open_nomination()
    other_award = make_award(make_category(make_event()))
    response = cast(
        voter_client(make_voter()),
        nomination,
        award=str(other_award.id),
        event=str(other_award.category.event_id),
        sub_category=str(other_award.id),
    )
    assert response.status_code == 201
    vote = Vote.objects.get()
    assert vote.award == nomination.award and vote.award != other_award
    assert vote.event == nomination.award.category.event


@pytest.mark.parametrize(
    "status",
    [NominationStatus.PENDING, NominationStatus.REJECTED, NominationStatus.WITHDRAWN],
)
def test_only_approved_nominations_are_votable(status):
    nomination = open_nomination(status=status)
    assert cast(voter_client(make_voter()), nomination).status_code == 404
    assert Vote.objects.count() == 0


def test_unknown_inactive_and_draft_nominations_look_the_same_as_missing():
    client = voter_client(make_voter())
    assert cast(client, uuid.uuid4()).status_code == 404
    inactive = make_nomination(make_award(make_category(make_event()), is_active=False))
    assert cast(client, inactive).status_code == 404
    draft = make_nomination(make_award(make_category(make_event(status=EventStatus.DRAFT))))
    assert cast(client, draft).status_code == 404


def test_malformed_requests_are_validation_errors():
    client = voter_client(make_voter())
    assert client.post(VOTES_URL, {}).status_code == 400
    assert client.post(VOTES_URL, {"nomination_id": "not-a-uuid"}).status_code == 400
    assert client.post(VOTES_URL, {"nomination_id": ""}).status_code == 400


def test_f02_f04_there_is_no_client_writable_counter():
    """Vote totals are derived from rows; nominees have no votes field to overwrite."""
    assert "votes" not in {f.name for f in Nominee._meta.get_fields() if f.concrete}
    nomination = open_nomination()
    client = staff_client(make_event_admin())
    assert client.patch(
        f"/api/v1/nominees/{nomination.nominee_id}/", {"votes": 999}
    ).status_code in (404, 405)
    assert client.put(f"/api/v1/nominees/{nomination.nominee_id}/", {"votes": 999}).status_code in (
        404,
        405,
    )


# --- voting window (F-09) ---------------------------------------------------------


def test_f09_voting_before_the_window_opens_is_forbidden():
    nomination = make_nomination(make_award(make_category(make_event(voting_open_now=False))))
    response = cast(voter_client(make_voter()), nomination)
    assert response.status_code == 403 and response.data["code"] == "voting_closed"
    assert Vote.objects.count() == 0


def test_f09_window_boundaries_open_inclusive_close_exclusive():
    event = make_event()
    nomination = make_nomination(make_award(make_category(event)))
    opens, closes = event.voting_opens_at, event.voting_closes_at
    cases = [
        (opens - timedelta(microseconds=1), 403),
        (opens, 201),
        (closes - timedelta(microseconds=1), 201),
        (closes, 403),
        (closes + timedelta(days=1), 403),
    ]
    for when, expected in cases:
        with time_machine.travel(when, tick=False):
            client = voter_client(make_voter())  # issued inside the frozen clock
            assert cast(client, nomination).status_code == expected, when


@pytest.mark.parametrize(
    "status",
    [
        EventStatus.DRAFT,
        EventStatus.NOMINATIONS_OPEN,
        EventStatus.CLOSED,
        EventStatus.RESULTS_PUBLISHED,
    ],
)
def test_f09_status_must_be_voting_open_even_inside_the_window(status):
    """Both conditions are checked: an admin closing voting stops votes immediately."""
    event = make_event(status=status)
    nomination = make_nomination(make_award(make_category(event)))
    response = cast(voter_client(make_voter()), nomination)
    expected = 404 if status == EventStatus.DRAFT else 403
    assert response.status_code == expected
    assert Vote.objects.count() == 0


def test_closing_voting_takes_effect_on_the_next_request():
    event = make_event()
    nomination = make_nomination(make_award(make_category(event)))
    a, b = voter_client(make_voter()), voter_client(make_voter())
    assert cast(a, nomination).status_code == 201
    admin = staff_client(make_event_admin())
    assert (
        admin.post(f"/api/v1/events/{event.slug}/set-status/", {"status": "closed"}).status_code
        == 200
    )
    assert cast(b, nomination).status_code == 403


# --- token separation -----------------------------------------------------------


def test_staff_tokens_cannot_cast_votes():
    nomination = open_nomination()
    for user in (make_event_admin(), make_moderator()):
        response = cast(staff_client(user), nomination)
        assert response.status_code == 403
    assert Vote.objects.count() == 0


def test_voter_tokens_are_rejected_on_staff_endpoints():
    client = voter_client(make_voter())
    assert client.get("/api/v1/auth/me/").status_code == 401
    assert client.post("/api/v1/events/", {"name": "x"}).status_code == 401
    assert client.get(VOTES_URL).status_code == 403  # dual endpoint: authenticated, but not staff
    assert client.get("/api/v1/audit-logs/").status_code == 401


def test_invalid_voter_tokens_are_unauthorized():
    nomination = open_nomination()
    voter = make_voter()
    for header in (
        "Bearer garbage",
        "Bearer",
        "Bearer a b",
        "Token abc",
        "Basic Zm9vOmJhcg==",
    ):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=header)
        assert cast(client, nomination).status_code == 401, header

    # tampered signature
    token, _ = issue_voter_token(voter)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token[:-3]}abc")
    assert cast(client, nomination).status_code == 401

    # wrong scope
    wrong_scope = VoterToken()
    wrong_scope["voter_id"] = str(voter.id)
    wrong_scope["scope"] = "admin"
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {wrong_scope}")
    assert cast(client, nomination).status_code == 401

    # a staff access token forged with the voter's id is still the wrong token type
    access = AccessToken()
    access["voter_id"] = str(voter.id)
    access["scope"] = "vote"
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    assert cast(client, nomination).status_code in (401, 403)

    # unknown / malformed voter id in a correctly signed token
    for bad_id in (str(uuid.uuid4()), "not-a-uuid"):
        forged = VoterToken()
        forged["voter_id"], forged["scope"] = bad_id, "vote"
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {forged}")
        assert cast(client, nomination).status_code == 401
    assert Vote.objects.count() == 0


def test_voter_tokens_expire_after_thirty_minutes():
    nomination = open_nomination()
    client = voter_client(make_voter())
    issued = timezone.now()
    with time_machine.travel(issued + timedelta(minutes=29), tick=False):
        assert client.get("/api/v1/votes/mine/").status_code == 200
    with time_machine.travel(issued + timedelta(minutes=31), tick=False):
        assert cast(client, nomination).status_code == 401


def test_blocked_or_unverified_voters_lose_access_immediately():
    nomination = open_nomination()
    voter = make_voter()
    client = voter_client(voter)
    voter.is_blocked = True
    voter.save()
    assert cast(client, nomination).status_code == 401
    unverified = make_voter(verified=False)
    assert cast(voter_client(unverified), nomination).status_code == 401


# --- votes/mine ---------------------------------------------------------------------


def test_mine_lists_only_my_votes_and_no_tallies():
    category = make_category(make_event())
    a1, a2 = make_award(category), make_award(category)
    n1, n2 = make_nomination(a1), make_nomination(a2)
    me, other = make_voter(), make_voter()
    make_vote(me, n1)
    make_vote(other, n1)
    make_vote(other, n2)
    response = voter_client(me).get("/api/v1/votes/mine/")
    assert response.status_code == 200
    rows = response.data["results"] if "results" in response.data else response.data
    assert len(rows) == 1
    row = rows[0]
    assert row["award"]["id"] == str(a1.id) and row["nomination"]["id"] == str(n1.id)
    assert row["voided"] is False
    for forbidden in ("total", "votes", "count", "tally"):
        assert forbidden not in row
    assert APIClient().get("/api/v1/votes/mine/").status_code == 401
    assert staff_client(make_event_admin()).get("/api/v1/votes/mine/").status_code == 403


# --- staff review and voiding ----------------------------------------------------


def test_only_event_admins_can_list_and_read_votes_and_phones_are_masked():
    nomination = open_nomination()
    voter = make_voter("+254712345612")
    vote = make_vote(voter, nomination)
    assert staff_client(make_moderator()).get(VOTES_URL).status_code == 403
    assert APIClient().get(VOTES_URL).status_code == 401
    admin = staff_client(make_event_admin())
    listing = admin.get(VOTES_URL)
    assert listing.status_code == 200 and listing.data["count"] == 1
    body = listing.content.decode()
    assert "+254712345612" not in body and "+2547******12" in body
    detail = admin.get(f"{VOTES_URL}{vote.id}/")
    assert detail.status_code == 200 and detail.data["voter_phone"] == "+2547******12"
    assert detail.data["ip_address"] == vote.ip_address


def test_staff_vote_list_filters_by_event_award_nomination_voided_and_phone():
    category = make_category(make_event())
    a1, a2 = make_award(category), make_award(category)
    n1, n2 = make_nomination(a1), make_nomination(a2)
    v1, v2 = make_voter("+254712000001"), make_voter("+254712000002")
    keep, voided = make_vote(v1, n1), make_vote(v2, n2)
    voided.void(make_event_admin(), "fraud")
    admin = staff_client(make_event_admin())

    def ids(query):
        return {r["id"] for r in admin.get(VOTES_URL + query).data["results"]}

    assert ids(f"?award={a1.id}") == {str(keep.id)}
    assert ids(f"?nomination={n2.id}") == {str(voided.id)}
    assert ids(f"?event={category.event.slug}") == {str(keep.id), str(voided.id)}
    assert ids("?voided=true") == {str(voided.id)}
    assert ids("?voided=false") == {str(keep.id)}
    assert ids("?phone=0712000001") == {str(keep.id)}
    assert admin.get(VOTES_URL + "?phone=garbage").status_code == 400


def test_voiding_requires_event_admin_and_a_reason_and_is_audited():
    nomination = open_nomination()
    vote = make_vote(make_voter(), nomination)
    url = f"{VOTES_URL}{vote.id}/void/"
    assert APIClient().post(url, {"reason": "x"}).status_code == 401
    assert staff_client(make_moderator()).post(url, {"reason": "x"}).status_code == 403
    admin_user = make_event_admin()
    admin = staff_client(admin_user)
    assert admin.post(url, {}).status_code == 400
    assert admin.post(url, {"reason": "   "}).status_code == 400
    assert admin.post(url, {"reason": "x" * 501}).status_code == 400
    response = admin.post(url, {"reason": "<b>Bot</b> activity"})
    assert response.status_code == 200 and response.data["voided_at"]
    vote.refresh_from_db()
    assert vote.voided_at and vote.voided_by == admin_user and vote.void_reason == "Bot activity"
    entry = AuditLog.objects.get(action=AuditAction.VOTE_VOIDED)
    assert entry.actor_user == admin_user and entry.target_id == str(vote.id)
    assert entry.metadata["reason"] == "Bot activity"
    again = admin.post(url, {"reason": "again"})
    assert again.status_code == 409 and again.data["code"] == "already_voided"


def test_voided_votes_are_excluded_from_tallies_but_the_row_remains():
    from voting import results

    award = make_award(make_category(make_event()))
    a, b = make_nomination(award), make_nomination(award)
    votes = [make_vote(make_voter(), a) for _ in range(5)] + [
        make_vote(make_voter(), b) for _ in range(3)
    ]
    totals = {r["nomination_id"]: r["votes"] for r in results.award_tally(award)}
    assert totals == {a.id: 5, b.id: 3}
    votes[0].void(make_event_admin(), "duplicate person")
    votes[1].void(make_event_admin(), "bot")
    totals = {r["nomination_id"]: r["votes"] for r in results.award_tally(award)}
    assert totals == {a.id: 3, b.id: 3}
    assert Vote.objects.count() == 8 and Vote.objects.active().count() == 6


def test_voiding_a_free_vote_frees_the_slot_so_the_voter_can_vote_again():
    """Voiding a free vote gives the voter their free vote back in that award (Phase 2.1)."""
    award = make_award(make_category(make_event()))
    first, second = make_nomination(award), make_nomination(award)
    voter = make_voter()
    client = voter_client(voter)
    original_response = cast(client, first)
    assert original_response.status_code == 201
    original = Vote.objects.get(pk=original_response.data["id"])
    admin_user = make_event_admin()
    original.void(admin_user, "fraud")

    response = cast(client, second)
    assert response.status_code == 201, response.data
    new_vote = Vote.objects.get(pk=response.data["id"])
    assert new_vote.voter == voter and new_vote.nomination == second and new_vote.voided_at is None

    entries = list(AuditLog.objects.filter(action=AuditAction.VOTE_CAST).order_by("created_at"))
    assert len(entries) == 2
    assert [e.target_id for e in entries] == [str(original.id), str(new_vote.id)]


def test_a_second_active_free_vote_is_still_rejected_after_a_void_elsewhere():
    """Voiding one vote does not loosen the one-active-free-vote-per-award rule generally."""
    award = make_award(make_category(make_event()))
    first, second, third = (
        make_nomination(award),
        make_nomination(award),
        make_nomination(award),
    )
    voter = make_voter()
    client = voter_client(voter)
    assert cast(client, first).status_code == 201
    # A different, unrelated voided vote in the same award changes nothing about this voter's
    # still-active vote: a duplicate is still rejected.
    make_vote(make_voter(), second).void(make_event_admin(), "unrelated")
    again = cast(client, third)
    assert again.status_code == 409 and again.data["code"] == "already_voted"


def test_re_voting_after_a_void_keeps_the_voided_row_and_only_the_new_vote_is_active():
    award = make_award(make_category(make_event()))
    first, second = make_nomination(award), make_nomination(award)
    voter = make_voter()
    original = make_vote(voter, first)
    original.void(make_event_admin(), "fraud")
    client = voter_client(voter)
    response = cast(client, second)
    assert response.status_code == 201
    rows = Vote.objects.filter(voter=voter, award=award)
    assert rows.count() == 2
    assert rows.get(pk=original.pk).voided_at is not None
    assert rows.exclude(pk=original.pk).get().voided_at is None
    # database truth, not just the ORM guard: exactly one *active* free vote for this pair
    active = Vote.objects.active().filter(voter=voter, award=award, source="free")
    assert active.count() == 1


# --- database-level guarantees ----------------------------------------------------


def test_database_rejects_a_second_free_vote_for_the_same_voter_and_award():
    nomination = open_nomination()
    voter = make_voter()
    make_vote(voter, nomination)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, nomination)


def test_database_allows_a_re_vote_after_void_but_still_rejects_two_active_ones():
    """The partial unique index only covers non-voided free votes (Phase 2.1)."""
    award = make_award(make_category(make_event()))
    a, b, c = make_nomination(award), make_nomination(award), make_nomination(award)
    voter = make_voter()
    first = make_vote(voter, a)
    first.void(make_event_admin(), "fraud")
    make_vote(voter, b)  # allowed: only one voided row exists, no active conflict
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, c)  # still rejected: an active free vote already exists (b)


def test_database_check_constraints_on_source_quantity_and_payment():
    nomination = open_nomination()
    voter = make_voter()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, nomination, quantity=2)  # free votes are exactly 1
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, nomination, source="paid", quantity=3)  # paid needs a payment
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, nomination, quantity=0)
    payment_vote = make_paid_vote(voter, nomination, quantity=4)
    with pytest.raises(IntegrityError), transaction.atomic():
        make_vote(voter, nomination, payment=payment_vote.payment)  # free vote with a payment
    with pytest.raises(IntegrityError), transaction.atomic():
        Vote.objects.filter(pk=payment_vote.pk).update(voided_at=timezone.now())  # no reason


def test_paid_votes_count_by_quantity_and_a_voter_may_buy_repeatedly():
    from voting import results

    award = make_award(make_category(make_event()))
    nomination = make_nomination(award)
    voter = make_voter()
    make_vote(voter, nomination)  # one free vote
    make_paid_vote(voter, nomination, quantity=5)
    make_paid_vote(voter, nomination, quantity=10)
    assert results.award_tally(award)[0]["votes"] == 16


def test_votes_are_append_only_in_the_orm():
    nomination = open_nomination()
    vote = make_vote(make_voter(), nomination)
    vote.quantity = 50
    with pytest.raises(AppendOnlyError):
        vote.save()
    with pytest.raises(AppendOnlyError):
        vote.save(update_fields=["quantity"])
    with pytest.raises(AppendOnlyError):
        vote.delete()
    with pytest.raises(AppendOnlyError):
        Vote.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        Vote.objects.filter(pk=vote.pk).update(quantity=99)
    with pytest.raises(AppendOnlyError):
        Vote.objects.filter(pk=vote.pk).update(nomination=make_nomination())
    Vote.objects.get(pk=vote.pk)  # still there, unchanged
    assert Vote.objects.get(pk=vote.pk).quantity == 1


def test_votes_are_append_only_in_postgres_even_for_raw_sql():
    nomination = open_nomination()
    vote = make_vote(make_voter(), nomination)
    other = make_nomination(nomination.award)
    with connection.cursor() as cursor:
        for sql, params in (
            ("UPDATE voting_vote SET quantity = 9 WHERE id = %s", [vote.pk]),
            ("UPDATE voting_vote SET nomination_id = %s WHERE id = %s", [other.pk, vote.pk]),
            (
                "UPDATE voting_vote SET voter_id = voter_id, created_at = now() WHERE id = %s",
                [vote.pk],
            ),
            ("UPDATE voting_vote SET seq = 999999 WHERE id = %s", [vote.pk]),  # Phase 2.2
            ("DELETE FROM voting_vote WHERE id = %s", [vote.pk]),
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(sql, params)
    with pytest.raises(DatabaseError), transaction.atomic():
        Vote.objects.filter(pk=vote.pk)._raw_delete(using="default")
    assert Vote.objects.filter(pk=vote.pk, quantity=1).exists()


def test_a_void_cannot_be_undone_or_rewritten():
    nomination = open_nomination()
    vote = make_vote(make_voter(), nomination)
    vote.void(make_event_admin(), "first reason")
    with connection.cursor() as cursor:
        for sql in (
            "UPDATE voting_vote SET voided_at = NULL, void_reason = '' WHERE id = %s",
            "UPDATE voting_vote SET void_reason = 'rewritten' WHERE id = %s",
        ):
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute(sql, [vote.pk])
    vote.refresh_from_db()
    assert vote.void_reason == "first reason" and vote.voided_at is not None


def test_voided_by_users_cannot_be_deleted_only_deactivated():
    from django.db.models import ProtectedError

    admin_user = make_event_admin()
    vote = make_vote(make_voter(), open_nomination())
    vote.void(admin_user, "fraud")
    with pytest.raises(ProtectedError):
        admin_user.delete()


def test_str_helpers():
    vote = make_vote(make_voter(), open_nomination())
    assert "free" in str(vote) and vote.is_voided is False
