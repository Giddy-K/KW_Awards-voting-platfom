"""Rate limits: OTP request/verify, votes, staff login (AUDIT F-10)."""

from datetime import timedelta

import pytest
import time_machine
from django.utils import timezone

from common.throttles import IPThrottle, PhoneThrottle, UserOrIPThrottle, WindowThrottle
from voting.models import Voter

from .helpers import PASSWORD, make_nomination, make_user, make_voter, voter_client
from .test_otp import request_code, verify
from .vote_helpers import cast

pytestmark = pytest.mark.django_db


def rates(settings, **overrides):
    settings.APP_THROTTLE_RATES = {**settings.APP_THROTTLE_RATES, **overrides}


def test_otp_request_is_limited_per_phone_to_three_per_fifteen_minutes(api, settings, sms_outbox):
    rates(settings, otp_request_phone_short="3/15m")
    assert [request_code(api).status_code for _ in range(3)] == [202, 202, 202]
    blocked = request_code(api)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    assert len(sms_outbox) == 3
    # A different phone number is unaffected.
    assert request_code(api, "0722000111").status_code == 202


def test_the_phone_limit_resets_after_the_window(api, settings):
    rates(settings, otp_request_phone_short="1/15m")
    assert request_code(api).status_code == 202
    assert request_code(api).status_code == 429
    with time_machine.travel(timezone.now() + timedelta(minutes=16), tick=False):
        assert request_code(api).status_code == 202


def test_otp_request_daily_phone_limit(api, settings):
    rates(settings, otp_request_phone_short="100/15m", otp_request_phone_day="10/1d")
    assert all(request_code(api).status_code == 202 for _ in range(10))
    assert request_code(api).status_code == 429


def test_phone_limit_applies_across_number_formats(api, settings):
    rates(settings, otp_request_phone_short="2/15m")
    assert request_code(api, "0712345678").status_code == 202
    assert request_code(api, "+254712345678").status_code == 202
    assert request_code(api, "254 712 345 678").status_code == 429


def test_otp_request_is_limited_per_ip(api, settings):
    rates(settings, otp_request_ip="3/1h")
    codes = [request_code(api, f"07220001{i:02d}").status_code for i in range(5)]
    assert codes == [202, 202, 202, 429, 429]


def test_invalid_phone_numbers_still_count_against_the_ip_limit(api, settings):
    rates(settings, otp_request_ip="2/1h")
    assert request_code(api, "garbage").status_code == 400
    assert request_code(api, "garbage2").status_code == 400
    assert request_code(api, "garbage3").status_code == 429


def test_otp_verify_is_limited_per_phone_and_per_ip(api, settings, sms_outbox):
    rates(settings, otp_verify_phone="3/15m", otp_verify_ip="100/1h")
    request_code(api)
    assert [verify(api, "000000").status_code for _ in range(3)] == [400, 400, 400]
    assert verify(api, "000000").status_code == 429
    from django.core.cache import cache

    cache.clear()  # start the per-IP phase from a clean bucket
    rates(settings, otp_verify_phone="100/15m", otp_verify_ip="2/1h")
    assert verify(api, "000000", phone="0799000001").status_code == 400
    assert verify(api, "000000", phone="0799000002").status_code == 400
    assert verify(api, "000000", phone="0799000003").status_code == 429


def test_votes_are_limited_per_voter_and_per_ip(settings):
    rates(settings, vote_voter="2/1h", vote_ip="100/1h")
    voter = make_voter()
    client = voter_client(voter)
    nominations = [make_nomination() for _ in range(3)]
    assert cast(client, nominations[0]).status_code == 201
    assert cast(client, nominations[1]).status_code == 201
    assert cast(client, nominations[2]).status_code == 429
    # another voter from the same IP is fine while the per-IP limit has room
    assert cast(voter_client(make_voter()), nominations[2]).status_code == 201
    from django.core.cache import cache

    cache.clear()
    rates(settings, vote_voter="100/1h", vote_ip="1/1h")
    assert cast(voter_client(make_voter()), make_nomination()).status_code == 201
    assert cast(voter_client(make_voter()), make_nomination()).status_code == 429


def test_staff_login_is_limited_per_ip(api, settings):
    rates(settings, staff_login_ip="3/15m")
    user = make_user()
    statuses = [
        api.post("/api/v1/auth/token/", {"email": user.email, "password": "wrong"}).status_code
        for _ in range(4)
    ]
    assert statuses == [401, 401, 401, 429]
    assert (
        api.post("/api/v1/auth/token/", {"email": user.email, "password": PASSWORD}).status_code
        == 429
    )


def test_an_empty_rate_disables_a_limit(api, settings):
    rates(settings, otp_request_phone_short="", otp_request_phone_day="", otp_request_ip="")
    assert all(request_code(api).status_code == 202 for _ in range(6))


def test_throttle_counters_are_shared_through_the_cache_not_process_memory(api, settings):
    from django.core.cache import cache

    rates(settings, otp_request_ip="1/1h")
    request_code(api)
    keys = [k for k in getattr(cache, "_cache", {}) if "throttle:otp_request_ip" in k]
    assert keys, "counters must be stored in the Django cache"


def test_throttle_classes_skip_when_there_is_nothing_to_key_on(api, settings):
    rates(settings, otp_request_phone_short="1/15m")
    # No phone in the body: the phone throttle has nothing to key on and lets validation answer.
    assert api.post("/api/v1/voters/otp/request/", {"captcha_token": "t"}).status_code == 400
    assert api.post("/api/v1/voters/otp/request/", {"captcha_token": "t"}).status_code == 400
    assert Voter.objects.count() == 0


def test_base_throttle_classes_have_sane_defaults(settings):
    class Custom(WindowThrottle):
        scope = "custom_scope"

    settings.APP_THROTTLE_RATES = {}
    assert Custom().get_rate() is None
    assert Custom().wait() is None
    assert issubclass(IPThrottle, WindowThrottle) and issubclass(PhoneThrottle, WindowThrottle)
    assert issubclass(UserOrIPThrottle, WindowThrottle)
