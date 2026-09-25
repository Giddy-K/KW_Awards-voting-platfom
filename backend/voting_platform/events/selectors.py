from accounts.roles import EVENT_ADMIN, user_has_role

from .models import EventStatus


def is_event_admin(user):
    return user_has_role(user, EVENT_ADMIN)


def exclude_drafts(queryset, user, event_lookup=""):
    """Public callers never see draft events (or anything inside them)."""
    if is_event_admin(user):
        return queryset
    return queryset.exclude(**{f"{event_lookup}status": EventStatus.DRAFT})
