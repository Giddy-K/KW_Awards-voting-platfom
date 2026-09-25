"""Phase 2.2: adds the sms.budget_exhausted AuditAction choice (common.sms_budget).

Metadata-only: no DB CHECK constraint enforces `choices`, so this doesn't touch any row.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0004_deterministic_ordering_seq"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("nomination.submitted", "Nomination submitted"),
                    ("nomination.approved", "Nomination approved"),
                    ("nomination.rejected", "Nomination rejected"),
                    ("vote.cast", "Vote cast"),
                    ("vote.duplicate", "Duplicate vote rejected"),
                    ("vote.voided", "Vote voided"),
                    ("otp.requested", "OTP requested"),
                    ("otp.verified", "OTP verified"),
                    ("otp.failed", "OTP verification failed"),
                    ("event.status_changed", "Event status changed"),
                    ("staff.login", "Staff login"),
                    ("staff.login_failed", "Staff login failed"),
                    ("permission.denied", "Permission denied"),
                    ("sms.budget_exhausted", "SMS daily budget exhausted"),
                ],
                max_length=40,
            ),
        ),
    ]
