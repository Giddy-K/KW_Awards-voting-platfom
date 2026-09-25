"""Phase 2.2: a DB-generated monotonic `seq` column, the deterministic ordering tiebreaker.

`created_at` alone is not fine-grained enough to order several rows written in the same tick
(this surfaced as a flaky test: test_otp.py::test_f16_..., see AUDIT.md "Phase 2.2 results").
`seq` is backed by a real PostgreSQL sequence, assigned atomically by the database via the
column's own `DEFAULT nextval(...)` -- see common.db.NextVal and AddField below.

The sequence must exist before the column can default from it, and is explicitly tied to the
column's lifecycle afterwards (`OWNED BY`) so it is dropped automatically if the column ever is.
"""

import common.db
from django.db import migrations, models

CREATE_SEQUENCE = "CREATE SEQUENCE audit_auditlog_seq_seq AS bigint;"
DROP_SEQUENCE = "DROP SEQUENCE IF EXISTS audit_auditlog_seq_seq;"

OWN_SEQUENCE = "ALTER SEQUENCE audit_auditlog_seq_seq OWNED BY audit_auditlog.seq;"
DISOWN_SEQUENCE = "ALTER SEQUENCE audit_auditlog_seq_seq OWNED BY NONE;"


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0003_auditlog_append_only_trigger"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="auditlog",
            options={"ordering": ["-seq"]},
        ),
        migrations.RunSQL(CREATE_SEQUENCE, DROP_SEQUENCE),
        migrations.AddField(
            model_name="auditlog",
            name="seq",
            field=models.BigIntegerField(
                db_default=common.db.NextVal("audit_auditlog_seq_seq"),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RunSQL(OWN_SEQUENCE, DISOWN_SEQUENCE),
    ]
