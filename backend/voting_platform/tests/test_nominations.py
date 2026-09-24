"""Nominations: public submission, moderation, public visibility (AUDIT F-12, F-27, F-28)."""

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditAction, AuditLog
from events.models import EventStatus
from nominations.models import Nomination, NominationStatus, Nominee

from .helpers import (
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_nominee,
    staff_client,
)
from .media_helpers import photo_upload

pytestmark = pytest.mark.django_db

URL = "/api/v1/nominations/"


def open_award():
    """An award in an event whose nomination window is open right now."""
    event = make_event(status=EventStatus.NOMINATIONS_OPEN)
    now = timezone.now()
    event.nominations_open_at = now - timedelta(days=1)
    event.nominations_close_at = now + timedelta(days=5)
    event.voting_opens_at = now + timedelta(days=6)
    event.voting_closes_at = now + timedelta(days=20)
    event.save()
    return make_award(make_category(event))


def payload(award_id, **overrides):
    data = {
        "award": str(award_id),
        "name": "Amani Wanjiku",
        "stage_name": "Amani W",
        "bio": "Singer-songwriter from Nairobi.",
        "contact_email": "amani@example.com",
        "captcha_token": "token",
        "photo": photo_upload(),
    }
    data.update(overrides)
    return data


def submit(api, award, **overrides):
    award_id = getattr(award, "id", award)
    return api.post(URL, payload(award_id, **overrides), format="multipart")


# --- submission --------------------------------------------------------------


def test_public_can_submit_a_nomination_which_starts_pending(api):
    award = open_award()
    response = submit(api, award, website_url="https://amani.example.com")
    assert response.status_code == 201, response.data
    assert response.data["status"] == "pending"
    body = str(response.data)
    assert "amani@example.com" not in body and "contact" not in body  # private fields
    nomination = Nomination.objects.get()
    assert nomination.status == NominationStatus.PENDING
    assert nomination.nominee.name == "Amani Wanjiku"
    assert nomination.submitted_by_ip == "127.0.0.1"
    assert nomination.nominee.photo.name.startswith("nominees/")
    entry = AuditLog.objects.get(action=AuditAction.NOMINATION_SUBMITTED)
    assert entry.target_id == str(nomination.id) and entry.ip_address == "127.0.0.1"


def test_f12_pending_nominations_are_not_public(api):
    award = open_award()
    submit(api, award)
    assert api.get(URL).data["count"] == 0
    nomination = Nomination.objects.get()
    assert api.get(f"{URL}{nomination.id}/").status_code == 404
    assert api.get(f"/api/v1/nominees/{nomination.nominee_id}/").status_code == 404


def test_submission_is_refused_when_nominations_are_closed(api):
    voting = make_event()  # voting_open, not nominations
    award = make_award(make_category(voting))
    response = submit(api, award)
    assert response.status_code == 403 and response.data["code"] == "nominations_closed"
    # status says open but the window has passed
    closed_window = make_event(status=EventStatus.NOMINATIONS_OPEN)  # window ended 10 days ago
    award = make_award(make_category(closed_window))
    assert submit(api, award).status_code == 403
    assert Nomination.objects.count() == 0


def test_submission_to_unknown_inactive_or_draft_award_is_a_validation_error(api):
    assert submit(api, uuid.uuid4()).status_code == 400
    inactive = open_award()
    inactive.is_active = False
    inactive.save()
    assert submit(api, inactive).status_code == 400
    draft_award = make_award(make_category(make_event(status=EventStatus.DRAFT)))
    assert submit(api, draft_award).status_code == 400


def test_submission_needs_a_contact_and_normalises_phone_numbers(api):
    award = open_award()
    assert submit(api, award, contact_email="").status_code == 400
    assert submit(api, award, contact_email="", contact_phone="0712345678").status_code == 201
    assert Nominee.objects.get().contact_phone == "+254712345678"
    assert submit(api, open_award(), contact_email="", contact_phone="12345").status_code == 400


def test_submission_requires_a_photo_and_name(api):
    award = open_award()
    data = payload(award.id)
    del data["photo"]
    assert api.post(URL, data, format="multipart").status_code == 400
    assert submit(api, award, name="   ").status_code == 400


def test_captcha_is_verified_server_side(api, settings):
    settings.CAPTCHA_BACKEND = "tests.media_helpers.RejectingCaptcha"
    award = open_award()
    response = submit(api, award)
    assert response.status_code == 400 and response.data["code"] == "captcha_failed"
    assert Nomination.objects.count() == 0


def test_f10_nomination_submissions_are_throttled_per_ip(api, settings):
    settings.APP_THROTTLE_RATES = {**settings.APP_THROTTLE_RATES, "nomination_ip": "2/1h"}
    award = open_award()
    assert submit(api, award, name="One").status_code == 201
    assert submit(api, award, name="Two").status_code == 201
    third = submit(api, award, name="Three")
    assert third.status_code == 429 and "Retry-After" in third.headers


def test_f28_text_is_stored_as_plain_text_with_length_limits(api):
    award = open_award()
    response = submit(
        api,
        award,
        name="<script>alert(1)</script>Amani",
        bio="<img src=x onerror=alert(1)>Hello <b>world</b>",
    )
    assert response.status_code == 201, response.data
    nominee = Nominee.objects.get()
    assert nominee.name == "alert(1)Amani" and "<" not in nominee.name
    assert nominee.bio == "Hello world"
    assert submit(api, open_award(), bio="x" * 2001).status_code == 400
    assert submit(api, open_award(), name="n" * 121).status_code == 400


@pytest.mark.parametrize(
    "bad",
    ["javascript:alert(1)", "ftp://example.com/x", "NA", "not a url", "data:text/html,hi"],
)
def test_f27_social_links_must_be_http_urls_never_placeholders(api, bad):
    award = open_award()
    assert submit(api, award, instagram_url=bad).status_code == 400


def test_f27_missing_links_are_null_not_na(api):
    award = open_award()
    assert submit(api, award).status_code == 201
    nominee = Nominee.objects.get()
    for field in (
        "website_url",
        "instagram_url",
        "facebook_url",
        "x_url",
        "youtube_url",
        "tiktok_url",
    ):
        assert getattr(nominee, field) is None


def test_duplicate_pending_or_approved_nomination_for_same_award_is_a_conflict(api):
    award = open_award()
    assert submit(api, award, name="Amani").status_code == 201
    duplicate = submit(api, award, name="amani")
    assert duplicate.status_code == 409 and duplicate.data["code"] == "duplicate_nomination"
    assert submit(api, open_award(), name="Amani").status_code == 201  # other award: fine


# --- public reads ------------------------------------------------------------


def test_public_list_only_shows_approved_nominations_without_private_fields(api):
    award = make_award(make_category(make_event()))
    approved = make_nomination(award, nominee=make_nominee(name="Zed", stage_name="Zed Z"))
    make_nomination(award, status=NominationStatus.PENDING)
    make_nomination(award, status=NominationStatus.REJECTED)
    make_nomination(award, status=NominationStatus.WITHDRAWN)
    draft = make_award(make_category(make_event(status=EventStatus.DRAFT)))
    make_nomination(draft)
    body = api.get(URL).data
    assert body["count"] == 1
    item = body["results"][0]
    assert item["id"] == str(approved.id) and item["nominee"]["stage_name"] == "Zed Z"
    for private in ("contact_phone", "contact_email", "owner", "submitted_by_ip", "status"):
        assert private not in item and private not in item["nominee"], private


def test_public_filters_and_search(api):
    event = make_event()
    music, comedy = make_category(event, name="Music"), make_category(event, name="Comedy")
    a1, a2 = make_award(music), make_award(comedy)
    n1 = make_nomination(a1, nominee=make_nominee(name="Alice", stage_name="Ally"))
    n2 = make_nomination(a2, nominee=make_nominee(name="Bob", stage_name="Bobby B"))
    make_nomination(make_award(make_category(make_event())))

    def ids(query):
        return {r["id"] for r in api.get(URL + query).data["results"]}

    assert ids(f"?award={a1.id}") == {str(n1.id)}
    assert ids(f"?category={comedy.id}") == {str(n2.id)}
    assert ids(f"?event={event.slug}") == {str(n1.id), str(n2.id)}
    assert ids("?search=bobby") == {str(n2.id)}
    assert ids("?search=alice") == {str(n1.id)}
    assert ids("?status=pending") == set()  # public callers can never see pending ones


def test_awards_nominations_endpoint_lists_approved_only(api):
    award = make_award(make_category(make_event()))
    approved = make_nomination(award)
    make_nomination(award, status=NominationStatus.PENDING)
    response = api.get(f"/api/v1/awards/{award.id}/nominations/")
    assert response.status_code == 200
    assert [r["id"] for r in response.data["results"]] == [str(approved.id)]
    inactive = make_award(make_category(make_event()), is_active=False)
    assert api.get(f"/api/v1/awards/{inactive.id}/nominations/").status_code == 404
    draft = make_award(make_category(make_event(status=EventStatus.DRAFT)))
    assert api.get(f"/api/v1/awards/{draft.id}/nominations/").status_code == 404


def test_awards_nominations_endpoint_supports_search(api):
    award = make_award(make_category(make_event()))
    make_nomination(award, nominee=make_nominee(name="Findme"))
    make_nomination(award, nominee=make_nominee(name="Other"))
    found = api.get(f"/api/v1/awards/{award.id}/nominations/?search=findme").data["results"]
    assert len(found) == 1


def test_nominee_profile_shows_only_approved_nominations_and_no_contact_data(api):
    nominee = make_nominee(name="Multi", bio="Bio text")
    a1 = make_award(make_category(make_event()))
    a2 = make_award(make_category(make_event()))
    make_nomination(a1, nominee=nominee)
    make_nomination(a2, nominee=nominee, status=NominationStatus.PENDING)
    response = api.get(f"/api/v1/nominees/{nominee.id}/")
    assert response.status_code == 200
    assert response.data["name"] == "Multi" and len(response.data["nominations"]) == 1
    assert response.data["nominations"][0]["award"]["id"] == str(a1.id)
    assert "contact" not in str(response.data) and "example.com" not in str(response.data)


def test_nominee_in_draft_event_or_without_approved_nomination_is_hidden(api):
    only_draft = make_nominee()
    draft_award = make_award(make_category(make_event(status=EventStatus.DRAFT)))
    make_nomination(draft_award, nominee=only_draft)
    assert api.get(f"/api/v1/nominees/{only_draft.id}/").status_code == 404
    assert api.get("/api/v1/nominees/00000000-0000-0000-0000-000000000000/").status_code == 404


def test_nominee_list_endpoint_does_not_exist(api):
    assert api.get("/api/v1/nominees/").status_code == 404


# --- moderation --------------------------------------------------------------


def test_moderator_sees_every_status_with_private_fields_and_can_filter():
    award = make_award(make_category(make_event()))
    pending = make_nomination(award, status=NominationStatus.PENDING)
    make_nomination(award, status=NominationStatus.APPROVED)
    mod = staff_client(make_moderator())
    assert mod.get(URL).data["count"] == 2
    only_pending = mod.get(URL + "?status=pending").data["results"]
    assert [r["id"] for r in only_pending] == [str(pending.id)]
    assert only_pending[0]["nominee"]["contact_email"] == pending.nominee.contact_email
    assert only_pending[0]["status"] == "pending"
    assert mod.get(f"{URL}{pending.id}/").status_code == 200


def test_event_admin_without_moderator_role_gets_only_the_public_view():
    award = make_award(make_category(make_event()))
    nomination = make_nomination(award, status=NominationStatus.PENDING)
    admin = staff_client(make_event_admin())
    assert admin.get(URL).data["count"] == 0
    assert admin.get(f"{URL}{nomination.id}/").status_code == 404


def test_approve_and_reject_require_moderator_and_record_the_reviewer(api):
    award = make_award(make_category(make_event()))
    to_approve = make_nomination(award, status=NominationStatus.PENDING)
    to_reject = make_nomination(award, status=NominationStatus.PENDING)
    for client in (api, staff_client(make_event_admin())):
        assert client.post(f"{URL}{to_approve.id}/approve/").status_code in (401, 403, 404)
    moderator = make_moderator()
    mod = staff_client(moderator)
    approved = mod.post(f"{URL}{to_approve.id}/approve/")
    assert approved.status_code == 200 and approved.data["status"] == "approved"
    to_approve.refresh_from_db()
    assert to_approve.reviewed_by == moderator and to_approve.reviewed_at is not None
    assert api.get(f"{URL}{to_approve.id}/").status_code == 200  # now public
    assert mod.post(f"{URL}{to_reject.id}/reject/", {}).status_code == 400  # reason required
    rejected = mod.post(f"{URL}{to_reject.id}/reject/", {"reason": "Not eligible"})
    assert rejected.status_code == 200 and rejected.data["status"] == "rejected"
    to_reject.refresh_from_db()
    assert to_reject.rejection_reason == "Not eligible"
    assert api.get(f"{URL}{to_reject.id}/").status_code == 404
    actions = list(AuditLog.objects.filter(action__startswith="nomination.").order_by("created_at"))
    assert [a.action for a in actions] == ["nomination.approved", "nomination.rejected"]
    assert all(a.actor_user == moderator for a in actions)
    assert actions[1].metadata["reason"] == "Not eligible"


def test_a_nomination_can_only_be_reviewed_once():
    nomination = make_nomination(status=NominationStatus.PENDING)
    mod = staff_client(make_moderator())
    assert mod.post(f"{URL}{nomination.id}/approve/").status_code == 200
    again = mod.post(f"{URL}{nomination.id}/reject/", {"reason": "changed my mind"})
    assert again.status_code == 409 and again.data["code"] == "already_reviewed"
    assert mod.post(f"{URL}{nomination.id}/approve/").status_code == 409


def test_reject_reason_is_cleaned_and_length_limited():
    nomination = make_nomination(status=NominationStatus.PENDING)
    mod = staff_client(make_moderator())
    assert mod.post(f"{URL}{nomination.id}/reject/", {"reason": "x" * 501}).status_code == 400
    ok = mod.post(f"{URL}{nomination.id}/reject/", {"reason": "<b>Duplicate</b> entry"})
    assert ok.status_code == 200
    nomination.refresh_from_db()
    assert nomination.rejection_reason == "Duplicate entry"


def test_staff_cannot_update_or_delete_nominations_through_the_api():
    nomination = make_nomination()
    mod = staff_client(make_moderator())
    assert mod.patch(f"{URL}{nomination.id}/", {"status": "approved"}).status_code == 405
    assert mod.put(f"{URL}{nomination.id}/", {}).status_code == 405
    assert mod.delete(f"{URL}{nomination.id}/").status_code == 405


# --- database constraints ----------------------------------------------------


def test_database_constraints_protect_nomination_integrity():
    nomination = make_nomination()
    with pytest.raises(IntegrityError), transaction.atomic():
        Nomination.objects.create(nominee=nomination.nominee, award=nomination.award)
    award = make_award()
    with pytest.raises(IntegrityError), transaction.atomic():
        Nomination.objects.create(
            nominee=make_nominee(), award=award, status="rejected", reviewed_at=timezone.now()
        )  # rejected needs a reason
    with pytest.raises(IntegrityError), transaction.atomic():
        Nomination.objects.create(nominee=make_nominee(), award=award, status="approved")
    assert str(nomination).endswith("[approved]")
    assert str(nomination.nominee) == nomination.nominee.name
