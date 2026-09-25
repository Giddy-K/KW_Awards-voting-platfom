"""Vote casting and voiding.

The one-vote-per-voter-per-award rule is enforced by a PostgreSQL partial unique index. The
code never checks first and inserts second (which races); it inserts and translates the
unique-violation into HTTP 409.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework.exceptions import NotFound

from audit import services as audit
from audit.models import AuditAction
from common.exceptions import AlreadyVoted, Conflict, VotingClosed
from common.ip import get_client_ip
from events.models import EventStatus
from nominations.models import Nomination, NominationStatus

from .models import Vote, VoteSource

UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc):
    return getattr(exc.__cause__, "pgcode", None) == UNIQUE_VIOLATION


def cast_free_vote(voter, nomination_id, *, request):
    """Record a free vote for ``nomination_id`` on behalf of ``voter``.

    ``event`` and ``award`` are derived from the nomination here; they are never read from the
    request. Raises ``NotFound`` (unknown/unapproved/hidden nomination), ``VotingClosed`` or
    ``AlreadyVoted``.
    """
    try:
        nomination = (
            Nomination.objects.select_related("award__category__event")
            .filter(status=NominationStatus.APPROVED, award__is_active=True)
            .exclude(award__category__event__status=EventStatus.DRAFT)
            .get(pk=nomination_id)
        )
    except (Nomination.DoesNotExist, DjangoValidationError, ValueError) as exc:
        raise NotFound("Nomination not found.") from exc

    award = nomination.award
    event = award.category.event
    if not event.voting_is_open():
        raise VotingClosed()

    try:
        with transaction.atomic():
            vote = Vote.objects.create(
                event=event,
                award=award,
                nomination=nomination,
                voter=voter,
                source=VoteSource.FREE,
                quantity=1,
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
            )
            # Same transaction: a vote can never exist without its audit entry.
            audit.log(
                AuditAction.VOTE_CAST,
                request=request,
                actor_voter=voter,
                target=vote,
                metadata={"award_id": str(award.pk), "nomination_id": str(nomination.pk)},
            )
    except IntegrityError as exc:
        if not _is_unique_violation(exc):
            raise
        audit.log(
            AuditAction.VOTE_DUPLICATE,
            request=request,
            actor_voter=voter,
            metadata={"award_id": str(award.pk), "nomination_id": str(nomination.pk)},
        )
        raise AlreadyVoted() from exc
    return vote


def void_vote(vote_id, *, by, reason, request=None):
    """Exclude a vote from tallies (never deletes it). Audited."""
    with transaction.atomic():
        try:
            vote = Vote.objects.select_for_update().get(pk=vote_id)
        except Vote.DoesNotExist as exc:
            raise NotFound("Vote not found.") from exc
        if vote.voided_at is not None:
            raise Conflict("This vote has already been voided.", code="already_voided")
        vote.void(by, reason)
        audit.log(
            AuditAction.VOTE_VOIDED,
            request=request,
            actor_user=by,
            target=vote,
            metadata={
                "reason": reason,
                "award_id": str(vote.award_id),
                "nomination_id": str(vote.nomination_id),
            },
        )
    return vote
