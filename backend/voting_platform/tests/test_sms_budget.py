"""Global daily SMS budget (Phase 2.2): a shared, cache-backed counter per Nairobi calendar day.

See AUDIT.md "Phase 2.2 results" and common/sms_budget.py.
"""

import logging
from datetime import datetime, timedelta

import pytest
import time_machine

from audit.models import AuditAction, AuditLog
from common.sms_budget import NAIROBI

from .helpers import make_voter
from .test_otp import request_code

pytestmark = pytest.mark.django_db


def test_requests_succeed_while_under_budget(api, sms_outbox, settings):
    settings.SMS_DAILY_LIMIT = 3
    for _ in range(3):
        assert request_code(api).status_code == 202
    assert len(sms_outbox) == 3


def test_request_over_budget_returns_503_with_retry_after_and_audits(
    api, sms_outbox, settings, caplog
):
    settings.SMS_DAILY_LIMIT = 1
    assert request_code(api).status_code == 202
    assert len(sms_outbox) == 1

    with caplog.at_level(logging.ERROR, logger="common.sms_budget"):
        response = request_code(api)
    assert response.status_code == 503
    assert response.data["code"] == "sms_unavailable"
    assert int(response.headers["Retry-After"]) > 0
    assert len(sms_outbox) == 1  # no second SMS was sent
    assert "budget exhausted" in caplog.text

    entry = AuditLog.objects.get(action=AuditAction.SMS_BUDGET_EXHAUSTED)
    assert entry.metadata["count"] == 2 and entry.metadata["limit"] == 1
    assert "phone_masked" in entry.metadata


def test_warning_is_logged_once_at_eighty_percent(api, sms_outbox, settings, caplog):
    settings.SMS_DAILY_LIMIT = 5  # warn_at = ceil(5 * 0.8) = 4
    with caplog.at_level(logging.WARNING, logger="common.sms_budget"):
        for _ in range(5):
            assert request_code(api).status_code == 202
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "80%" in warnings[0].message

    # The 6th request is the one that breaks the limit; no second warning, just the error.
    with caplog.at_level(logging.WARNING, logger="common.sms_budget"):
        assert request_code(api).status_code == 503
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


def test_budget_resets_on_a_new_nairobi_day(api, sms_outbox, settings):
    settings.SMS_DAILY_LIMIT = 1
    almost_midnight = datetime(2026, 6, 1, 23, 59, 0, tzinfo=NAIROBI)
    with time_machine.travel(almost_midnight, tick=False):
        assert request_code(api).status_code == 202
        assert request_code(api).status_code == 503
    just_after_midnight = almost_midnight + timedelta(minutes=2)
    with time_machine.travel(just_after_midnight, tick=False):
        response = request_code(api, "0722000111")
        assert response.status_code == 202
    assert len(sms_outbox) == 2


def test_budget_disabled_when_limit_is_zero(api, sms_outbox, settings):
    settings.SMS_DAILY_LIMIT = 0
    for _ in range(10):
        assert request_code(api).status_code == 202
    assert len(sms_outbox) == 10


def test_blocked_voter_requests_do_not_consume_the_budget(api, sms_outbox, settings):
    settings.SMS_DAILY_LIMIT = 1
    blocked = make_voter(blocked=True)
    for _ in range(5):
        response = request_code(api, blocked.phone_e164)
        assert response.status_code == 202  # generic response; no SMS, no budget spent
    assert sms_outbox == []
    # The budget is still untouched: a real request still gets through.
    assert request_code(api, "0722000111").status_code == 202
    assert len(sms_outbox) == 1
    assert not AuditLog.objects.filter(action=AuditAction.SMS_BUDGET_EXHAUSTED).exists()
