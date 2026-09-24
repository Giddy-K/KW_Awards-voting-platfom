"""Phone OTP flow: request, verify, expiry, attempts, hashing, generic responses (AUDIT F-01, F-10, F-16)."""

import re
from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from audit.models import AuditAction, AuditLog
from voting.authentication import VoterToken
from voting.models import OTPChallenge, Voter

from .helpers import make_voter

pytestmark = pytest.mark.django_db

REQUEST = "/api/v1/voters/otp/request/"
VERIFY = "/api/v1/voters/otp/verify/"
PHONE = "0712345678"
E164 = "+254712345678"
GENERIC = {"detail": "If the number is valid, a verification code has been sent."}


def code_from(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1]["body"]).group(1)


def request_code(api, phone=PHONE, **extra):
    return api.post(REQUEST, {"phone": phone, "captcha_token": "t", **extra})


def verify(api, code, phone=PHONE):
    return api.post(VERIFY, {"phone": phone, "code": code})


# --- request ------------------------------------------------------------------


def test_request_sends_a_six_digit_code_by_sms_and_returns_a_generic_response(api, sms_outbox):
    response = request_code(api)
    assert response.status_code == 202
    assert response.data == GENERIC
    assert len(sms_outbox) == 1 and sms_outbox[0]["to"] == E164
    assert re.search(r"\b\d{6}\b", sms_outbox[0]["body"])
    assert Voter.objects.get(phone_e164=E164).verified_at is None


def test_response_is_identical_for_new_known_and_blocked_phones(api, sms_outbox):
    """The endpoint must not reveal whether a phone number is already registered or blocked."""
    known = make_voter("+254722000001")
    blocked = make_voter("+254722000002", blocked=True)
    bodies = []
    for phone in ("0711000000", known.phone_e164, blocked.phone_e164):
        response = request_code(api, phone)
        bodies.append((response.status_code, response.data))
    assert len({str(b) for b in bodies}) == 1
    # Blocked voters get no SMS at all.
    assert {m["to"] for m in sms_outbox} == {"+254711000000", "+254722000001"}


def test_f01_the_code_is_never_stored_in_plaintext(api, sms_outbox, monkeypatch):
    monkeypatch.setattr("voting.otp.generate_code", lambda: "482913")
    request_code(api)
    assert code_from(sms_outbox) == "482913"
    challenge = OTPChallenge.objects.get()
    assert "482913" not in challenge.code_hash and len(challenge.code_hash) == 64
    logged = AuditLog.objects.filter(action=AuditAction.OTP_REQUESTED).get()
    assert "482913" not in str(logged.metadata) and E164 not in str(logged.metadata)


def test_request_records_ip_and_user_agent_and_masked_audit_entry(api):
    api.post(REQUEST, {"phone": PHONE, "captcha_token": "t"}, HTTP_USER_AGENT="TestBrowser/1.0")
    challenge = OTPChallenge.objects.get()
    assert challenge.requested_ip == "127.0.0.1"
    assert challenge.user_agent == "TestBrowser/1.0"
    entry = AuditLog.objects.get(action=AuditAction.OTP_REQUESTED)
    assert entry.metadata["phone_masked"] == "+2547******78"


def test_new_request_invalidates_the_previous_code(api, sms_outbox, monkeypatch):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr("voting.otp.generate_code", lambda: next(codes))
    request_code(api)
    request_code(api)
    assert verify(api, "111111").status_code == 400  # superseded by the newer code
    assert verify(api, "222222").status_code == 200


def test_invalid_and_missing_phone_numbers_are_rejected(api, sms_outbox):
    for bad in ("", "abc", "123", "+1 555 0100", "0" * 40, "254 (0) 1"):
        assert request_code(api, bad).status_code == 400, bad
    assert api.post(REQUEST, {"captcha_token": "t"}).status_code == 400
    assert sms_outbox == []


def test_phone_formats_normalise_to_the_same_voter(api):
    for phone in ("0712345678", "+254712345678", "254712345678", "0712 345 678"):
        assert request_code(api, phone).status_code == 202
    assert Voter.objects.count() == 1


def test_captcha_is_required_and_verified(api, settings, sms_outbox):
    settings.CAPTCHA_BACKEND = "tests.media_helpers.RejectingCaptcha"
    response = request_code(api)
    assert response.status_code == 400 and response.data["code"] == "captcha_failed"
    assert sms_outbox == [] and Voter.objects.count() == 0


def test_sms_failures_do_not_change_the_response_or_leak(api, settings):
    settings.SMS_BACKEND = "tests.test_common.ExplodingSms"
    response = request_code(api)
    assert response.status_code == 202 and response.data == GENERIC


# --- verify -------------------------------------------------------------------


def test_correct_code_returns_a_voter_token_and_marks_the_voter_verified(api, sms_outbox):
    request_code(api)
    response = verify(api, code_from(sms_outbox))
    assert response.status_code == 200, response.data
    assert response.data["token_type"] == "Bearer"
    assert response.data["expires_in"] == 30 * 60
    token = VoterToken(response.data["token"])
    voter = Voter.objects.get()
    assert token["voter_id"] == str(voter.id) and token["scope"] == "vote"
    assert voter.verified_at is not None
    challenge = OTPChallenge.objects.get()
    assert challenge.consumed_at is not None and challenge.attempts == 1
    assert AuditLog.objects.filter(action=AuditAction.OTP_VERIFIED, actor_voter=voter).exists()


def test_a_code_can_only_be_used_once(api, sms_outbox):
    request_code(api)
    code = code_from(sms_outbox)
    assert verify(api, code).status_code == 200
    assert verify(api, code).status_code == 400


def test_wrong_code_is_rejected_with_a_generic_error_and_audited(api, sms_outbox):
    request_code(api)
    good = code_from(sms_outbox)
    wrong = "000000" if good != "000000" else "111111"
    response = verify(api, wrong)
    assert response.status_code == 400 and response.data["code"] == "invalid_otp"
    entry = AuditLog.objects.get(action=AuditAction.OTP_FAILED)
    assert entry.metadata["reason"] == "invalid_code" and entry.metadata["attempts"] == 1
    assert wrong not in str(entry.metadata) and good not in str(entry.metadata)
    assert verify(api, good).status_code == 200  # a wrong attempt does not burn the code


def test_errors_are_identical_for_unknown_phone_expired_and_wrong_code(api, sms_outbox):
    request_code(api)
    unknown = verify(api, "123456", phone="0799999999")
    wrong = verify(api, "000001" if code_from(sms_outbox) != "000001" else "000002")
    assert unknown.status_code == wrong.status_code == 400
    assert unknown.data == wrong.data


def test_f16_attempt_limit_locks_the_challenge_even_for_the_right_code(api, sms_outbox, settings):
    request_code(api)
    good = code_from(sms_outbox)
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(settings.OTP_MAX_ATTEMPTS):
        assert verify(api, wrong).status_code == 400
    assert verify(api, good).status_code == 400  # correct code, but the challenge is exhausted
    assert OTPChallenge.objects.get().attempts == settings.OTP_MAX_ATTEMPTS
    reasons = list(
        AuditLog.objects.filter(action=AuditAction.OTP_FAILED).values_list("metadata", flat=True)
    )
    assert reasons[0]["reason"] == "attempts_exhausted"
    request_code(api)  # a fresh code works again
    assert verify(api, code_from(sms_outbox)).status_code == 200


def test_codes_expire_after_five_minutes(api, sms_outbox):
    request_code(api)
    code = code_from(sms_outbox)
    issued = timezone.now()
    with time_machine.travel(issued + timedelta(minutes=5, seconds=1), tick=False):
        assert verify(api, code).status_code == 400
    with time_machine.travel(issued + timedelta(minutes=4, seconds=59), tick=False):
        assert verify(api, code).status_code == 200


def test_blocked_voters_cannot_verify_or_receive_a_token(api, sms_outbox):
    request_code(api)
    code = code_from(sms_outbox)
    Voter.objects.update(is_blocked=True)
    assert verify(api, code).status_code == 400


def test_verify_validates_input_shape(api):
    assert api.post(VERIFY, {"phone": PHONE}).status_code == 400
    assert api.post(VERIFY, {"phone": PHONE, "code": "abcdef"}).status_code == 400
    assert api.post(VERIFY, {"phone": PHONE, "code": "12345"}).status_code == 400
    assert api.post(VERIFY, {"phone": "nope", "code": "123456"}).status_code == 400


def test_code_comparison_is_constant_time(monkeypatch, api, sms_outbox):
    import hmac

    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    request_code(api)
    verify(api, "000000")
    assert calls, "codes must be compared with hmac.compare_digest"


def test_otp_str_and_voter_str_never_expose_the_full_number(api):
    request_code(api)
    voter = Voter.objects.get()
    challenge = OTPChallenge.objects.get()
    assert E164 not in str(voter) and E164 not in str(challenge)
    assert voter.masked_phone == "+2547******78"
