"""Deterministic ordering (Phase 2.2, AUDIT.md "Phase 2.2 results").

`created_at` alone is not fine-grained enough to order several rows written in the same tick;
`seq` is a PostgreSQL-sequence-backed column, assigned atomically by the database, that gives
every `created_at`-ordered model a strict, gap-tolerant tiebreaker. These tests exercise it
directly (independent of the OTP flow that originally surfaced the flake -- see test_otp.py).
"""

import pytest
import time_machine
from django.utils import timezone

from audit.models import AuditAction, AuditLog
from nominations.models import Nomination
from payments.models import Payment
from voting.models import OTPChallenge, OTPPurpose, Vote, Voter

from .helpers import make_nomination, make_voter

pytestmark = pytest.mark.django_db


def make_otp_challenge(voter=None, **kwargs):
    voter = voter or make_voter()
    kwargs.setdefault("code_hash", "x" * 64)
    kwargs.setdefault("purpose", OTPPurpose.VOTE_LOGIN)
    kwargs.setdefault("expires_at", timezone.now())
    return OTPChallenge.objects.create(voter=voter, **kwargs)


def make_payment_row(voter=None, nomination=None, **kwargs):
    voter = voter or make_voter()
    nomination = nomination or make_nomination()
    kwargs.setdefault("phone_e164", voter.phone_e164)
    kwargs.setdefault("amount", "50.00")
    kwargs.setdefault("votes_purchased", 5)
    return Payment.objects.create(voter=voter, nomination=nomination, **kwargs)


# --- seq is assigned, unique and strictly increasing -------------------------------------


@pytest.mark.parametrize(
    "make_row",
    [
        lambda: AuditLog.objects.create(action=AuditAction.VOTE_CAST),
        lambda: make_voter(),
        lambda: make_otp_challenge(),
        lambda: make_payment_row(),
    ],
    ids=["AuditLog", "Voter", "OTPChallenge", "Payment"],
)
def test_seq_is_assigned_unique_and_strictly_increasing(make_row):
    rows = [make_row() for _ in range(4)]
    seqs = [row.seq for row in rows]
    assert all(isinstance(s, int) for s in seqs)
    assert len(set(seqs)) == len(seqs)  # unique
    assert seqs == sorted(seqs)  # strictly increasing in creation order
    assert seqs[0] < seqs[-1]


def test_vote_seq_is_assigned_unique_and_strictly_increasing():
    nomination = make_nomination()
    votes = [
        Vote.objects.create(
            event=nomination.award.category.event,
            award=nomination.award,
            nomination=nomination,
            voter=make_voter(),
        )
        for _ in range(4)
    ]
    seqs = [v.seq for v in votes]
    assert len(set(seqs)) == len(seqs)
    assert seqs == sorted(seqs)


def test_nomination_seq_is_assigned_unique_and_strictly_increasing():
    nominations = [make_nomination() for _ in range(4)]
    seqs = [n.seq for n in nominations]
    assert len(set(seqs)) == len(seqs)
    assert seqs == sorted(seqs)


# --- default ordering is (now) deterministic under a created_at tie ----------------------


def test_default_ordering_breaks_a_created_at_tie_for_audit_log():
    with time_machine.travel(timezone.now(), tick=False):
        first = AuditLog.objects.create(action=AuditAction.OTP_REQUESTED)
        second = AuditLog.objects.create(action=AuditAction.OTP_VERIFIED)
        third = AuditLog.objects.create(action=AuditAction.VOTE_CAST)
        assert first.created_at == second.created_at == third.created_at  # the tie, forced
    # Default ordering is "-seq"; the most recently created row must sort first regardless.
    assert list(AuditLog.objects.values_list("pk", flat=True)) == [third.pk, second.pk, first.pk]


def test_default_ordering_breaks_a_created_at_tie_for_otp_challenge():
    voter = make_voter()
    with time_machine.travel(timezone.now(), tick=False):
        first = make_otp_challenge(voter)
        second = make_otp_challenge(voter)
        third = make_otp_challenge(voter)
        assert first.created_at == second.created_at == third.created_at
    assert list(OTPChallenge.objects.values_list("pk", flat=True)) == [
        third.pk,
        second.pk,
        first.pk,
    ]


# --- Meta.ordering ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [AuditLog, Voter, OTPChallenge, Vote, Payment, Nomination],
    ids=lambda m: m.__name__,
)
def test_default_ordering_is_by_seq(model):
    assert model._meta.ordering == ["-seq"]


def test_seq_is_not_editable_on_the_model_field():
    # editable=False keeps it out of forms/serializers by default; application code is not
    # expected to ever set it (the database assigns it via db_default).
    for model in (AuditLog, Voter, OTPChallenge, Vote, Payment, Nomination):
        assert model._meta.get_field("seq").editable is False
