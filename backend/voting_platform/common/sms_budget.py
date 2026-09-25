"""Global daily SMS budget (Phase 2.2).

A shared, cache-backed counter of SMS attempted per *Nairobi* calendar day, independent of
``settings.TIME_ZONE`` (which affects display generally but is pinned here explicitly, since the
budget is specified in Nairobi days regardless). Guards against runaway provider cost from a bug
or an attack. Uses the same atomic ``cache.add`` + ``cache.incr`` pattern as
``common.throttles.WindowThrottle``, so a shared cache (Redis) keeps the count correct across
worker processes.

Callers must call :func:`reserve` *before* sending an SMS, never after -- the budget tracks
attempted sends, not just accepted requests.
"""

import logging
import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from audit import services as audit
from audit.models import AuditAction

logger = logging.getLogger(__name__)

NAIROBI = ZoneInfo("Africa/Nairobi")
_WARN_FRACTION = 0.8


class SmsBudgetExhausted(Exception):
    """The daily SMS budget has been used up. Callers should return 503 with Retry-After."""

    def __init__(self, retry_after):
        self.retry_after = retry_after
        super().__init__(f"SMS daily budget exhausted; retry after {retry_after}s")


def _nairobi_now():
    return timezone.now().astimezone(NAIROBI)


def _seconds_until_next_nairobi_day(now):
    tomorrow_midnight = datetime.combine(now.date() + timedelta(days=1), time.min, tzinfo=NAIROBI)
    return max(1, math.ceil((tomorrow_midnight - now).total_seconds()))


def reserve(*, voter, phone_e164, request=None):
    """Count one SMS against today's budget; raise :class:`SmsBudgetExhausted` if that was the
    one that broke the limit. Also logs a one-time WARNING on the request that crosses 80%.

    A ``SMS_DAILY_LIMIT`` of ``0`` (or unset) disables the budget entirely. ``voter`` and
    ``phone_e164`` are passed through to :func:`audit.services.log_voter_event` (Phase 2.3) so
    the SMS_BUDGET_EXHAUSTED entry follows the same pre-/post-verification rule as OTP_REQUESTED
    and OTP_FAILED: no ``actor_voter`` reference until the voter has actually verified.
    """
    limit = settings.SMS_DAILY_LIMIT
    if not limit:
        return
    now = _nairobi_now()
    ttl = _seconds_until_next_nairobi_day(now)
    key = f"sms_budget:{now.date().isoformat()}"
    cache.add(key, 0, timeout=ttl)
    try:
        count = cache.incr(key)
    except ValueError:  # key expired between add and incr
        cache.set(key, 1, timeout=ttl)
        count = 1

    if count > limit:
        logger.error("SMS daily budget exhausted: %s/%s sent today", count, limit)
        audit.log_voter_event(
            AuditAction.SMS_BUDGET_EXHAUSTED,
            voter=voter,
            phone_e164=phone_e164,
            request=request,
            metadata={"count": count, "limit": limit},
        )
        raise SmsBudgetExhausted(retry_after=ttl)

    # The counter is a strict +1 sequence (cache.incr is atomic), so exactly one request ever
    # observes count == warn_at for a given day -- no extra "already warned" guard needed.
    warn_at = math.ceil(limit * _WARN_FRACTION)
    if count == warn_at:
        logger.warning(
            "SMS daily budget at %d%%: %s/%s sent today", int(_WARN_FRACTION * 100), count, limit
        )
