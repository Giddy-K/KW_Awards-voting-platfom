"""Phase 2.2: a DB-generated monotonic `seq` column, the deterministic ordering tiebreaker.

See audit/migrations/0004_deterministic_ordering_seq.py for the full rationale.
"""

import common.db
from django.db import migrations, models

CREATE_SEQUENCE = "CREATE SEQUENCE nominations_nomination_seq_seq AS bigint;"
DROP_SEQUENCE = "DROP SEQUENCE IF EXISTS nominations_nomination_seq_seq;"

OWN_SEQUENCE = "ALTER SEQUENCE nominations_nomination_seq_seq OWNED BY nominations_nomination.seq;"
DISOWN_SEQUENCE = "ALTER SEQUENCE nominations_nomination_seq_seq OWNED BY NONE;"


class Migration(migrations.Migration):

    dependencies = [
        ("nominations", "0001_initial"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="nomination",
            options={"ordering": ["-seq"]},
        ),
        migrations.RunSQL(CREATE_SEQUENCE, DROP_SEQUENCE),
        migrations.AddField(
            model_name="nomination",
            name="seq",
            field=models.BigIntegerField(
                db_default=common.db.NextVal("nominations_nomination_seq_seq"),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RunSQL(OWN_SEQUENCE, DISOWN_SEQUENCE),
    ]
