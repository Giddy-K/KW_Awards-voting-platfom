"""Write path for the audit log. Call :func:`log` from anywhere; entries are append-only."""

from common.ip import get_client_ip

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
        if actor_user is None and actor_voter is None and getattr(principal, "is_authenticated", False):
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
