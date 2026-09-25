"""Scoped, cache-backed, fixed-window throttles configurable per scope.

DRF's built-in rates only allow ``N/second|minute|hour|day``; the OTP limits need
windows such as "3 per 15 minutes", so rates here look like ``3/15m`` or ``10/1d``
(``s``, ``m``, ``h``, ``d``). Counters use the atomic ``cache.add`` + ``cache.incr`` pair, so
a shared cache backend (Redis) gives correct limits across worker processes.
"""

import hashlib
import math
import re
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

from common.ip import get_client_ip
from common.phone import InvalidPhoneNumber, normalize_phone

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)?\s*([smhd])\s*$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_rate(rate):
    """``"3/15m"`` -> ``(3, 900)``. Raises ``ValueError`` on malformed input."""
    match = _RATE_RE.match(rate or "")
    if not match:
        raise ValueError(f"Invalid throttle rate: {rate!r}")
    count, multiplier, unit = match.groups()
    return int(count), int(multiplier or 1) * _UNIT_SECONDS[unit]


class WindowThrottle(BaseThrottle):
    """Base class. Subclasses set ``scope`` and implement :meth:`get_ident`."""

    scope = None

    def get_rate(self):
        return settings.APP_THROTTLE_RATES.get(self.scope)

    def get_ident(self, request):  # pragma: no cover - interface
        raise NotImplementedError

    def allow_request(self, request, view):
        rate = self.get_rate()
        if not rate:
            return True
        ident = self.get_ident(request)
        if not ident:
            return True
        limit, window = parse_rate(rate)
        now = time.time()
        bucket = int(now // window)
        digest = hashlib.sha256(str(ident).encode()).hexdigest()[:32]
        key = f"throttle:{self.scope}:{digest}:{bucket}"
        cache.add(key, 0, timeout=window)
        try:
            count = cache.incr(key)
        except ValueError:  # key expired between add and incr
            cache.set(key, 1, timeout=window)
            count = 1
        if count > limit:
            self._wait = max(1, math.ceil((bucket + 1) * window - now))
            return False
        return True

    def wait(self):
        return getattr(self, "_wait", None)


class IPThrottle(WindowThrottle):
    def get_ident(self, request):
        return get_client_ip(request)


class PhoneThrottle(WindowThrottle):
    """Keyed on the (normalised) ``phone`` in the request body."""

    def get_ident(self, request):
        raw = request.data.get("phone") if hasattr(request.data, "get") else None
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return normalize_phone(raw)
        except InvalidPhoneNumber:
            return "raw:" + raw.strip()[:40]


class UserOrIPThrottle(WindowThrottle):
    """Keyed on the authenticated principal id when available, else the client IP."""

    def get_ident(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return f"id:{getattr(user, 'pk', None)}"
        return get_client_ip(request)
