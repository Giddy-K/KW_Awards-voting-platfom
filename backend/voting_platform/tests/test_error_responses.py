"""Phase 2.3: every non-2xx response matches ErrorResponse (common.schema.ErrorResponse) --
{"code": <a member of common.exceptions.all_error_codes()>, "detail": str, "fields": dict|None}
-- whatever raised it: one of this app's ApiError subclasses, a plain serializer validation
error, or a built-in DRF/SimpleJWT exception.
"""

import pytest

from common.exceptions import all_error_codes
from common.schema import ErrorResponse
from events.models import EventStatus
from nominations.models import Nomination
from voting.models import Vote

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
from .test_events import set_status
from .test_nominations import open_award, submit
from .test_otp import request_code, verify
from .vote_helpers import cast

pytestmark = pytest.mark.django_db


def open_nomination(**kwargs):
    return make_nomination(make_award(make_category(make_event())), **kwargs)


def assert_matches_schema(response, expected_status):
    """The core conformance check: real status, real body, matched against the real schema."""
    assert response.status_code == expected_status, (response.status_code, response.data)
    serializer = ErrorResponse(data=response.data)
    assert serializer.is_valid(), (response.data, serializer.errors)
    assert response.data["code"] in all_error_codes(), response.data
    return response.data["code"]


# --- completeness: every code this test suite actually observes is in the enum -----------
# "Add a test that fails if any code raised in the codebase is missing from the enum: collect
# the codes from the exception classes/constants rather than hand-listing them." The enum
# itself (all_error_codes) is built by recursively walking ApiError.__subclasses__() plus a
# short, explicit, class-derived list of third-party DRF/SimpleJWT codes -- so a code can only
# ever go missing from it if some call site bypasses that convention entirely (a bare inline
# `code="..."` not backed by a subclass). This test drives a representative sample of every
# error path the API actually has and checks each *observed* code (collected from the real
# response, never hand-typed as an expected value here beyond the HTTP status) against the
# enum, and every body's shape against the schema, in one pass.


def test_a_representative_sample_of_error_responses_match_the_schema_and_the_enum(
    api, sms_outbox, settings
):
    admin = staff_client(make_event_admin())
    moderator = staff_client(make_moderator())
    observed = set()

    def check(response, expected_status):
        observed.add(assert_matches_schema(response, expected_status))

    # --- 400: plain field validation, and our own named ApiError subclasses -------------
    check(api.post("/api/v1/voters/otp/request/", {"captcha_token": "t"}), 400)  # missing phone
    check(request_code(api, "0201234567"), 400)  # unsupported_phone_number (KE landline)
    settings.CAPTCHA_BACKEND = "tests.media_helpers.RejectingCaptcha"
    check(request_code(api, "0712345678"), 400)  # captcha_failed
    settings.CAPTCHA_BACKEND = "dummy"
    check(verify(api, "000000"), 400)  # invalid_otp (no active challenge)
    check(moderator.post(f"/api/v1/nominations/{open_nomination().id}/reject/", {}), 400)  # blank reason
    check(set_status(admin, make_event(status=EventStatus.DRAFT), "bogus"), 400)  # bad enum value

    # --- 401: no/invalid credentials, and no_active_account (wrong staff credentials) ---
    check(api.get("/api/v1/auth/me/"), 401)
    check(api.post("/api/v1/auth/logout/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"), 401)
    check(
        api.post("/api/v1/auth/refresh/", HTTP_X_REQUESTED_WITH="XMLHttpRequest"), 401
    )  # token_not_valid: no cookie
    check(api.get("/api/v1/events/", HTTP_AUTHORIZATION="Bearer garbage"), 401)
    check(
        api.post(
            "/api/v1/auth/token/",
            {"email": "nobody@example.com", "password": "wrong"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ),
        401,
    )  # no_active_account: reaches credential checking, with the AJAX header present

    # --- 403: AJAX-header CSRF check, role checks, and business-rule 403s ---------------
    check(
        api.post("/api/v1/auth/token/", {"email": "nobody@example.com", "password": "wrong"}), 403
    )  # HasAjaxHeader: no header at all, so credential checking is never reached
    check(
        api.post("/api/v1/nominations/", {}), 400
    )  # a plain field-validation error (award/name/etc. all missing)
    closed_voting = make_nomination(make_award(make_category(make_event(voting_open_now=False))))
    check(cast(voter_client(make_voter()), closed_voting), 403)  # voting_closed
    not_open = make_award(make_category(make_event()))  # nominations window not open
    check(submit(api, not_open), 403)  # nominations_closed
    check(staff_client(make_user()).get("/api/v1/audit-logs/"), 403)  # no EventAdmin role

    # --- 404 -------------------------------------------------------------------------------
    check(api.get("/api/v1/events/does-not-exist/"), 404)
    check(admin.get("/api/v1/audit-logs/00000000-0000-0000-0000-000000000000/"), 404)
    check(
        cast(voter_client(make_voter()), "00000000-0000-0000-0000-000000000000"), 404
    )  # nomination not found

    # --- 409: conflicts --------------------------------------------------------------------
    nomination = open_nomination()
    voter = voter_client(make_voter())
    assert cast(voter, nomination).status_code == 201
    check(cast(voter, nomination), 409)  # already_voted
    vote = Vote.objects.get()
    assert admin.post(f"/api/v1/votes/{vote.id}/void/", {"reason": "test"}).status_code == 200
    check(admin.post(f"/api/v1/votes/{vote.id}/void/", {"reason": "again"}), 409)  # already_voided
    award = open_award()
    resp1 = submit(api, award)
    assert resp1.status_code == 201, resp1.data
    check(submit(api, award), 409)  # duplicate_nomination
    dup_nomination = Nomination.objects.get(pk=resp1.data["id"])
    assert moderator.post(f"/api/v1/nominations/{dup_nomination.id}/approve/").status_code == 200
    check(moderator.post(f"/api/v1/nominations/{dup_nomination.id}/approve/"), 409)  # already_reviewed
    live_event = make_event(status=EventStatus.DRAFT)
    check(set_status(admin, live_event, "voting_open"), 409)  # invalid_transition
    votable_event = nomination.award.category.event
    check(admin.delete(f"/api/v1/events/{votable_event.slug}/"), 409)  # conflict (has votes)

    # --- 429: throttled ----------------------------------------------------------------
    settings.APP_THROTTLE_RATES = {**settings.APP_THROTTLE_RATES, "otp_request_phone_short": "0/1d"}
    check(request_code(api, "0722334455"), 429)
    settings.APP_THROTTLE_RATES = dict.fromkeys(settings.APP_THROTTLE_RATES, "100000/1d")

    # --- 503: SMS budget exhausted -------------------------------------------------------
    settings.SMS_DAILY_LIMIT = 1
    assert request_code(api, "0733445566").status_code == 202
    check(request_code(api, "0733445566"), 503)

    # A sanity floor: this sample should exercise a good fraction of the whole enum, not just
    # one or two codes -- if it shrinks a lot, a code path was probably broken, not removed.
    assert len(observed) >= 15, observed
