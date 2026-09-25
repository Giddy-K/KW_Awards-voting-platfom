from django.db import transaction
from django.utils import timezone

from audit import services as audit
from audit.models import AuditAction
from common.exceptions import AlreadyReviewed

from .models import Nomination, NominationStatus


def review_nomination(nomination, *, approve, reviewer, reason="", request=None):
    """Approve or reject a *pending* nomination (exactly once). Audited.

    Locks the row so two moderators cannot both decide the same nomination.
    """
    with transaction.atomic():
        locked = Nomination.objects.select_for_update().get(pk=nomination.pk)
        if locked.status != NominationStatus.PENDING:
            raise AlreadyReviewed(
                f"This nomination has already been reviewed (status: {locked.status})."
            )
        locked.status = NominationStatus.APPROVED if approve else NominationStatus.REJECTED
        locked.reviewed_by = reviewer
        locked.reviewed_at = timezone.now()
        locked.rejection_reason = "" if approve else reason
        locked.save(
            update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"]
        )
        metadata = {"award_id": str(locked.award_id), "nominee_id": str(locked.nominee_id)}
        if not approve:
            metadata["reason"] = reason
        audit.log(
            AuditAction.NOMINATION_APPROVED if approve else AuditAction.NOMINATION_REJECTED,
            request=request,
            actor_user=reviewer,
            target=locked,
            metadata=metadata,
        )
    return locked
