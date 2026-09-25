"""Delete never-verified voters (no votes, no payments) older than a cutoff, and their OTP
challenges. Dry-run by default; --execute to actually delete.

Phase 2.2 item 4: a Voter row is created at OTP-request time, before the code is ever verified
(voting.otp.request_otp); an attacker or a mistyped number can leave many such rows behind.
Throttles bound the growth rate, but nothing previously reclaimed the never-verified ones.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from voting.models import Voter


class Command(BaseCommand):
    help = (
        "Delete never-verified voters (no votes, no payments) older than --older-than-hours, "
        "and their OTP challenges. Dry-run by default; pass --execute to actually delete."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=48,
            help="Only consider voters created at least this many hours ago (default: 48).",
        )
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually delete. Without this flag, only report what would happen.",
        )

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options["older_than_hours"])
        # No votes, no payments (both PROTECT anyway, so a candidate with either could never be
        # deleted); audit-referenced voters (AuditLog.actor_voter, also PROTECT) are not
        # excluded here -- they are attempted and counted separately below, per row, since the
        # audit log is the one reference that legitimately happens without a vote or payment.
        candidate_ids = list(
            Voter.objects.filter(verified_at__isnull=True, created_at__lt=cutoff)
            .filter(votes__isnull=True, payments__isnull=True)
            .distinct()
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        total = len(candidate_ids)

        if not options["execute"]:
            protected = (
                Voter.objects.filter(pk__in=candidate_ids, audit_entries__isnull=False)
                .distinct()
                .count()
            )
            self.stdout.write(
                f"Dry run: {total} unverified voter(s) older than "
                f"{options['older_than_hours']}h with no votes or payments -- "
                f"{total - protected} would be deleted, {protected} would be skipped "
                "(referenced by the audit log). Pass --execute to actually delete."
            )
            return

        deleted = 0
        challenges_deleted = 0
        skipped_protected = 0
        for voter_id in candidate_ids:
            try:
                with transaction.atomic():
                    voter = Voter.objects.get(pk=voter_id)
                    _count, per_model = voter.delete()
            except Voter.DoesNotExist:
                continue
            except ProtectedError:
                skipped_protected += 1
                continue
            deleted += 1
            challenges_deleted += per_model.get("voting.OTPChallenge", 0)

        self.stdout.write(
            f"Deleted {deleted} voter(s) and {challenges_deleted} OTP challenge(s). "
            f"Skipped {skipped_protected} voter(s) referenced by the audit log."
        )
