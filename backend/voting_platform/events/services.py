from django.db import transaction
from django.utils import timezone

from audit import services as audit
from audit.models import AuditAction
from common.exceptions import InvalidTransition, WindowRequired

from .models import STATUS_TRANSITIONS, Event, EventStatus


def change_status(event, new_status, *, request=None):
    """Move an event to ``new_status`` if the transition is allowed. Audited.

    The row is locked and the transition re-validated against the locked state, so two
    concurrent admins cannot both apply conflicting transitions.
    """
    new_status = EventStatus(new_status)
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        old_status = locked.status
        if new_status not in STATUS_TRANSITIONS.get(EventStatus(old_status), set()):
            raise InvalidTransition(f"Cannot change status from '{old_status}' to '{new_status}'.")
        if new_status == EventStatus.NOMINATIONS_OPEN and not (
            locked.nominations_open_at and locked.nominations_close_at
        ):
            raise WindowRequired("Set the nomination window before opening nominations.")
        if new_status == EventStatus.VOTING_OPEN and not (
            locked.voting_opens_at and locked.voting_closes_at
        ):
            raise WindowRequired("Set the voting window before opening voting.")
        locked.status = new_status
        if new_status == EventStatus.RESULTS_PUBLISHED:
            locked.results_published_at = timezone.now()
        elif old_status == EventStatus.RESULTS_PUBLISHED:
            locked.results_published_at = None
        locked.save(update_fields=["status", "results_published_at", "updated_at"])
        audit.log(
            AuditAction.EVENT_STATUS_CHANGED,
            request=request,
            target=locked,
            metadata={"from": old_status, "to": str(new_status)},
        )
    return locked
