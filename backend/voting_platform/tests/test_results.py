"""Results and dashboard stats: computed, ranked, hidden until published (AUDIT F-02, F-04, F-09)."""

from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from events.models import EventStatus
from nominations.models import NominationStatus
from voting import results

from .helpers import (
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_nominee,
    make_voter,
    staff_client,
    voter_client,
)
from .vote_helpers import make_paid_vote, make_vote

pytestmark = pytest.mark.django_db


def results_url(event):
    return f"/api/v1/events/{event.slug}/results/"


def build_event(status=EventStatus.RESULTS_PUBLISHED):
    """One event, two categories, awards with a known vote distribution."""
    event = make_event(status=status)
    music, comedy = make_category(event, name="Music"), make_category(event, name="Comedy")
    award_a, award_b = make_award(music, name="Album"), make_award(comedy, name="Standup")
    alice = make_nomination(award_a, nominee=make_nominee(name="Alice"))
    bob = make_nomination(award_a, nominee=make_nominee(name="Bob"))
    cara = make_nomination(award_b, nominee=make_nominee(name="Cara"))
    for _ in range(3):
        make_vote(make_voter(), alice)
    for _ in range(1):
        make_vote(make_voter(), bob)
    return event, {"alice": alice, "bob": bob, "cara": cara, "album": award_a, "standup": award_b}


def test_results_are_hidden_from_the_public_before_publication(api):
    for status in (
        EventStatus.NOMINATIONS_OPEN,
        EventStatus.VOTING_OPEN,
        EventStatus.CLOSED,
    ):
        event, _ = build_event(status)
        assert api.get(results_url(event)).status_code == 404, status
        assert voter_client(make_voter()).get(results_url(event)).status_code in (401, 404)
        assert staff_client(make_moderator()).get(results_url(event)).status_code == 404


def test_draft_events_have_no_public_results_at_all(api):
    event, _ = build_event(EventStatus.DRAFT)
    assert api.get(results_url(event)).status_code == 404


def test_published_results_are_public_and_ranked(api):
    event, n = build_event()
    response = api.get(results_url(event))
    assert response.status_code == 200
    body = response.data
    assert body["event"]["slug"] == event.slug and body["event"]["status"] == "results_published"
    categories = {c["name"]: c for c in body["categories"]}
    album = categories["Music"]["awards"][0]
    assert album["name"] == "Album" and album["total_votes"] == 4
    ranked = [(e["nominee"]["name"], e["votes"], e["rank"]) for e in album["nominations"]]
    assert ranked == [("Alice", 3, 1), ("Bob", 1, 2)]
    standup = categories["Comedy"]["awards"][0]
    assert [(e["nominee"]["name"], e["votes"]) for e in standup["nominations"]] == [("Cara", 0)]
    assert n["cara"].id  # nominees with zero votes still appear


def test_event_admins_can_read_results_at_any_time_but_moderators_cannot():
    event, _ = build_event(EventStatus.VOTING_OPEN)
    assert staff_client(make_event_admin()).get(results_url(event)).status_code == 200
    assert staff_client(make_moderator()).get(results_url(event)).status_code == 404


def test_results_exclude_voided_votes_and_reflect_paid_votes(api):
    event, n = build_event()
    voided = make_vote(make_voter(), n["bob"])
    voided.void(make_event_admin(), "duplicate person")
    make_paid_vote(make_voter(), n["bob"], quantity=10)
    album = api.get(results_url(event)).data["categories"][0]["awards"][0]
    if album["name"] != "Album":
        album = api.get(results_url(event)).data["categories"][1]["awards"][0]
    votes = {e["nominee"]["name"]: e["votes"] for e in album["nominations"]}
    assert votes == {"Alice": 3, "Bob": 11}  # 1 free + 10 paid; the voided vote is ignored
    assert album["nominations"][0]["nominee"]["name"] == "Bob"  # ranking follows the tally


def test_ties_share_a_rank_and_are_ordered_by_name():
    award = make_award(make_category(make_event()))
    zed = make_nomination(award, nominee=make_nominee(name="Zed"))
    amy = make_nomination(award, nominee=make_nominee(name="Amy"))
    top = make_nomination(award, nominee=make_nominee(name="Top"))
    for nomination, votes in ((zed, 2), (amy, 2), (top, 5)):
        for _ in range(votes):
            make_vote(make_voter(), nomination)
    ranking = results.award_tally(award)
    assert [(e["nominee"]["name"], e["votes"], e["rank"]) for e in ranking] == [
        ("Top", 5, 1),
        ("Amy", 2, 2),
        ("Zed", 2, 2),
    ]


def test_only_approved_nominations_appear_in_results(api):
    event, n = build_event()
    make_nomination(
        n["album"], status=NominationStatus.PENDING, nominee=make_nominee(name="Pending")
    )
    names = {
        e["nominee"]["name"]
        for c in api.get(results_url(event)).data["categories"]
        for a in c["awards"]
        for e in a["nominations"]
    }
    assert "Pending" not in names and {"Alice", "Bob", "Cara"} <= names


def test_results_use_a_constant_number_of_queries(api, django_assert_max_num_queries):
    event, n = build_event()
    for i in range(10):
        nomination = make_nomination(n["album"], nominee=make_nominee(name=f"Extra {i}"))
        make_vote(make_voter(), nomination)
    with django_assert_max_num_queries(12):
        assert api.get(results_url(event)).status_code == 200


def test_unknown_event_results_are_404(api):
    assert api.get("/api/v1/events/nope/results/").status_code == 404


# --- stats ----------------------------------------------------------------------------


def test_stats_require_event_admin(api):
    event, _ = build_event()
    url = f"/api/v1/events/{event.slug}/stats/"
    assert api.get(url).status_code == 401
    assert voter_client(make_voter()).get(url).status_code == 401
    assert staff_client(make_moderator()).get(url).status_code == 403
    assert staff_client(make_event_admin()).get(url).status_code == 200


def test_stats_report_totals_over_time_and_per_category():
    event, n = build_event(EventStatus.VOTING_OPEN)
    make_vote(make_voter(), n["cara"])
    voided = make_vote(make_voter(), n["cara"])
    voided.void(make_event_admin(), "bot")
    make_nomination(n["standup"], status=NominationStatus.PENDING)
    make_paid_vote(make_voter(), n["alice"], quantity=6)
    body = staff_client(make_event_admin()).get(f"/api/v1/events/{event.slug}/stats/").data
    totals = body["totals"]
    assert totals["votes"] == 3 + 1 + 1 + 6  # alice 3, bob 1, cara 1 (voided one excluded), paid 6
    assert totals["voided_votes"] == 1
    assert totals["pending_nominations"] == 1
    assert totals["approved_nominations"] == 3
    assert totals["voters"] == 3 + 1 + 1 + 1
    per_category = {c["category"]: c["votes"] for c in body["per_category"]}
    assert per_category == {"Music": 3 + 1 + 6, "Comedy": 1}
    assert sum(day["votes"] for day in body["votes_over_time"]) == totals["votes"]


def test_stats_bucket_votes_by_local_day():
    event = make_event()
    award = make_award(make_category(event))
    nomination = make_nomination(award)
    # 21:30 UTC on the 1st is 00:30 on the 2nd in Nairobi (UTC+3).
    with time_machine.travel(
        timezone.now().replace(hour=21, minute=30) - timedelta(days=3), tick=False
    ):
        vote = make_vote(make_voter(), nomination)
    local_day = timezone.localtime(vote.created_at).date()
    body = staff_client(make_event_admin()).get(f"/api/v1/events/{event.slug}/stats/").data
    assert [str(d["date"]) for d in body["votes_over_time"]] == [str(local_day)]
    assert local_day != vote.created_at.date()


def test_stats_of_a_draft_event_are_admin_only_and_empty_is_fine():
    event = make_event(status=EventStatus.DRAFT)
    admin = staff_client(make_event_admin())
    body = admin.get(f"/api/v1/events/{event.slug}/stats/").data
    assert (
        body["totals"]["votes"] == 0
        and body["votes_over_time"] == []
        and body["per_category"] == []
    )
