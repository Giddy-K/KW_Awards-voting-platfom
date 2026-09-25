"""purge_unverified_voters (Phase 2.2 item 4): dry-run by default, --execute to delete."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from audit import services as audit
from audit.models import AuditAction
from voting.models import OTPChallenge, OTPPurpose, Voter

from .helpers import make_nomination, make_voter
from .vote_helpers import make_payment, make_vote

pytestmark = pytest.mark.django_db


def age(voter, hours):
    Voter.objects.filter(pk=voter.pk).update(created_at=timezone.now() - timedelta(hours=hours))
    voter.refresh_from_db()
    return voter


def make_challenge(voter):
    return OTPChallenge.objects.create(
        voter=voter,
        code_hash="x" * 64,
        purpose=OTPPurpose.VOTE_LOGIN,
        expires_at=timezone.now(),
    )


def run(*args, **kwargs):
    call_command("purge_unverified_voters", *args, **kwargs)


def test_dry_run_reports_but_does_not_delete(capsys):
    stale = age(make_voter(verified=False), 72)
    make_challenge(stale)
    run()
    out = capsys.readouterr().out
    assert "1 would be deleted" in out or "would be deleted" in out
    assert Voter.objects.filter(pk=stale.pk).exists()
    assert OTPChallenge.objects.filter(voter=stale).exists()


def test_execute_deletes_stale_unverified_voters_and_their_otp_challenges():
    stale = age(make_voter(verified=False), 72)
    challenge = make_challenge(stale)
    run("--execute")
    assert not Voter.objects.filter(pk=stale.pk).exists()
    assert not OTPChallenge.objects.filter(pk=challenge.pk).exists()


def test_a_voter_younger_than_the_cutoff_is_left_alone():
    fresh = age(make_voter(verified=False), 1)  # only 1h old, default cutoff is 48h
    run("--execute")
    assert Voter.objects.filter(pk=fresh.pk).exists()


def test_the_cutoff_is_configurable():
    voter = age(make_voter(verified=False), 10)
    run("--execute", "--older-than-hours", "5")
    assert not Voter.objects.filter(pk=voter.pk).exists()

    voter2 = age(make_voter(verified=False), 3)
    run("--execute", "--older-than-hours", "5")
    assert Voter.objects.filter(pk=voter2.pk).exists()


def test_a_verified_voter_is_never_purged_even_if_stale():
    verified = age(make_voter(verified=True), 1000)
    run("--execute")
    assert Voter.objects.filter(pk=verified.pk).exists()


def test_a_voter_with_a_vote_is_never_purged():
    nomination = make_nomination()
    voter = age(make_voter(verified=False), 1000)
    make_vote(voter, nomination)
    run("--execute")
    assert Voter.objects.filter(pk=voter.pk).exists()


def test_a_voter_with_a_payment_is_never_purged():
    nomination = make_nomination()
    voter = age(make_voter(verified=False), 1000)
    make_payment(voter, nomination)
    run("--execute")
    assert Voter.objects.filter(pk=voter.pk).exists()


def test_an_audit_referenced_voter_is_skipped_and_counted_not_failed(capsys):
    voter = age(make_voter(verified=False), 1000)
    audit.log(AuditAction.OTP_REQUESTED, actor_voter=voter, metadata={"phone_masked": "x"})
    run("--execute")
    out = capsys.readouterr().out
    assert "Skipped 1" in out
    assert Voter.objects.filter(pk=voter.pk).exists()  # PROTECT: deletion refused, not crashed


def test_a_mix_of_purgeable_and_protected_voters_does_not_stop_at_the_first_protected_one():
    protected = age(make_voter(verified=False), 1000)
    audit.log(AuditAction.OTP_REQUESTED, actor_voter=protected, metadata={"phone_masked": "x"})
    purgeable_before = age(make_voter(verified=False), 1000)
    purgeable_after = age(make_voter(verified=False), 1000)
    run("--execute")
    assert Voter.objects.filter(pk=protected.pk).exists()
    assert not Voter.objects.filter(pk=purgeable_before.pk).exists()
    assert not Voter.objects.filter(pk=purgeable_after.pk).exists()
