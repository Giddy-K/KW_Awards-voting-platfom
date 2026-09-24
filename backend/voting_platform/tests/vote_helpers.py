"""Helpers for voting tests."""

from decimal import Decimal

from payments.models import Payment, PaymentStatus
from voting.models import Vote, VoteSource

VOTES_URL = "/api/v1/votes/"


def cast(client, nomination, **extra):
    """POST a vote for ``nomination`` (an object with ``id``, or a raw id)."""
    nomination_id = getattr(nomination, "id", nomination)
    return client.post(VOTES_URL, {"nomination_id": str(nomination_id), **extra})


def make_vote(voter, nomination, **kwargs):
    kwargs.setdefault("source", VoteSource.FREE)
    return Vote.objects.create(
        event=nomination.award.category.event,
        award=nomination.award,
        nomination=nomination,
        voter=voter,
        **kwargs,
    )


def make_payment(voter, nomination, votes=5, status=PaymentStatus.SUCCEEDED, **kwargs):
    params = {
        "voter": voter,
        "nomination": nomination,
        "phone_e164": voter.phone_e164,
        "amount": Decimal("50.00"),
        "votes_purchased": votes,
        "status": status,
    }
    params.update(kwargs)
    if params["status"] == PaymentStatus.SUCCEEDED and "provider_receipt" not in params:
        params["provider_receipt"] = f"RCPT{Payment.objects.count() + 1:06d}"
    return Payment.objects.create(**params)


def make_paid_vote(voter, nomination, quantity=5):
    payment = make_payment(voter, nomination, votes=quantity)
    return make_vote(voter, nomination, source=VoteSource.PAID, quantity=quantity, payment=payment)
