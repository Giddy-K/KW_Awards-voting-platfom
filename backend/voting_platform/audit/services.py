"""Write path for the audit log. Call :func:`log` from anywhere; entries are append-only."""

import hashlib
import hmac

from django.conf import settings

from common.ip import get_client_ip
from common.phone import mask_phone

from .models import AuditLog

# Guardrail: these must never be stored in metadata (mask phones with mask_phone()).
_FORBIDDEN_METADATA_KEYS = {"code", "otp", "password", "phone", "phone_e164", "token", "secret"}


def _clean_metadata(metadata):
    metadata = dict(metadata or {})
    bad = _FORBIDDEN_METADATA_KEYS & {str(key).lower() for key in metadata}
    if bad:
        raise ValueError(f"Refusing to write sensitive keys to the audit log: {sorted(bad)}")
    return metadata


def log(
    action,
    *,
    request=None,
    actor_user=None,
    actor_voter=None,
    target=None,
    target_type="",
    target_id="",
    metadata=None,
    ip_address=None,
    user_agent=None,
):
    """Create an audit entry and return it.

    ``request`` (Django or DRF) supplies IP and user agent and, unless given explicitly,
    the acting staff user or voter. ``target`` is any model instance.
    """
    from accounts.models import User  # local import: avoid app-loading cycles

    if request is not None:
        ip_address = ip_address or get_client_ip(request)
        if user_agent is None:
            user_agent = request.META.get("HTTP_USER_AGENT", "")
        principal = getattr(request, "user", None)
        if (
            actor_user is None
            and actor_voter is None
            and getattr(principal, "is_authenticated", False)
        ):
            if isinstance(principal, User):
                actor_user = principal
            elif hasattr(principal, "voter"):
                actor_voter = principal.voter
    if target is not None:
        target_type = target._meta.label_lower
        target_id = str(target.pk)
    return AuditLog.objects.create(
        action=action,
        actor_user=actor_user,
        actor_voter=actor_voter,
        target_type=target_type,
        target_id=str(target_id),
        metadata=_clean_metadata(metadata),
        ip_address=ip_address or None,
        user_agent=(user_agent or "")[:300],
    )


def hash_phone(phone_e164):
    """Keyed HMAC of an E.164 phone number (Phase 2.3).

    ``AUDIT_PHONE_HASH_KEY`` is a dedicated setting, deliberately separate from ``SECRET_KEY``
    (required and checked distinct from it in prod -- see ``settings.prod``), so a leak of one
    key doesn't compromise the other. Lets an EventAdmin search the audit log by phone
    (``AuditLogViewSet``'s ``?phone=`` filter) without the log ever storing the raw number, and
    correlates a phone's activity from *before* its voter is verified (see
    :func:`log_voter_event`) with that same phone's activity afterwards.
    """
    key = settings.AUDIT_PHONE_HASH_KEY.encode()
    return hmac.new(key, phone_e164.encode(), hashlib.sha256).hexdigest()


def log_voter_event(action, *, voter, phone_e164, request=None, target=None, metadata=None):
    """Like :func:`log`, but for events tied to a phone number that may not have a verified
    voter yet: ``OTP_REQUESTED``, ``OTP_FAILED``, ``SMS_BUDGET_EXHAUSTED`` (Phase 2.3).

    If ``voter`` is already verified (``verified_at`` set), the entry references it via
    ``actor_voter`` exactly like any other entry. Otherwise ``actor_voter`` is left null and
    the metadata carries a masked phone plus :func:`hash_phone` instead -- so the row can never
    let someone browsing the log correlate activity to a specific unverified phone number by
    joining on ``Voter`` (which also means an unverified voter with only this kind of activity
    is no longer PROTECTed from ``purge_unverified_voters``: see AUDIT.md "Phase 2.3 results").
    Staff can still find the entry, before or after that voter ever verifies, via the audit
    log's ``?phone=`` filter, which hashes the query phone the same way.
    """
    metadata = dict(metadata or {})
    metadata["phone_masked"] = mask_phone(phone_e164)
    verified = voter is not None and voter.verified_at is not None
    if not verified:
        metadata["phone_hash"] = hash_phone(phone_e164)
    return log(
        action,
        request=request,
        actor_voter=voter if verified else None,
        target=target,
        metadata=metadata,
    )
