"""Phase 2.2: a DB-generated monotonic `seq` column, the deterministic ordering tiebreaker.

See audit/migrations/0004_deterministic_ordering_seq.py for the full rationale. `voting_vote`
also has an append-only trigger (0002_vote_append_only_trigger) whose immutable-column check
names columns explicitly; it is replaced here to also cover `seq`, so a raw SQL UPDATE cannot
rewrite a vote's insertion order any more than it can rewrite any other immutable column.
`voting_otpchallenge` and `voting_voter` are not append-only tables, so no trigger change is
needed for them -- application code simply never assigns `seq` itself.
"""

import common.db
from django.db import migrations, models

CREATE_VOTER_SEQUENCE = "CREATE SEQUENCE voting_voter_seq_seq AS bigint;"
DROP_VOTER_SEQUENCE = "DROP SEQUENCE IF EXISTS voting_voter_seq_seq;"
OWN_VOTER_SEQUENCE = "ALTER SEQUENCE voting_voter_seq_seq OWNED BY voting_voter.seq;"
DISOWN_VOTER_SEQUENCE = "ALTER SEQUENCE voting_voter_seq_seq OWNED BY NONE;"

CREATE_OTP_SEQUENCE = "CREATE SEQUENCE voting_otpchallenge_seq_seq AS bigint;"
DROP_OTP_SEQUENCE = "DROP SEQUENCE IF EXISTS voting_otpchallenge_seq_seq;"
OWN_OTP_SEQUENCE = (
    "ALTER SEQUENCE voting_otpchallenge_seq_seq OWNED BY voting_otpchallenge.seq;"
)
DISOWN_OTP_SEQUENCE = "ALTER SEQUENCE voting_otpchallenge_seq_seq OWNED BY NONE;"

CREATE_VOTE_SEQUENCE = "CREATE SEQUENCE voting_vote_seq_seq AS bigint;"
DROP_VOTE_SEQUENCE = "DROP SEQUENCE IF EXISTS voting_vote_seq_seq;"
OWN_VOTE_SEQUENCE = "ALTER SEQUENCE voting_vote_seq_seq OWNED BY voting_vote.seq;"
DISOWN_VOTE_SEQUENCE = "ALTER SEQUENCE voting_vote_seq_seq OWNED BY NONE;"

# Replaces the function from 0002_vote_append_only_trigger to also protect `seq`.
UPDATE_TRIGGER_FORWARD = """
CREATE OR REPLACE FUNCTION voting_vote_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'voting_vote is append-only: DELETE is not allowed';
    END IF;
    -- Everything except the voiding columns and updated_at is immutable.
    IF (NEW.id, NEW.event_id, NEW.award_id, NEW.nomination_id, NEW.voter_id, NEW.source,
        NEW.quantity, NEW.payment_id, NEW.ip_address, NEW.user_agent, NEW.created_at, NEW.seq)
       IS DISTINCT FROM
       (OLD.id, OLD.event_id, OLD.award_id, OLD.nomination_id, OLD.voter_id, OLD.source,
        OLD.quantity, OLD.payment_id, OLD.ip_address, OLD.user_agent, OLD.created_at, OLD.seq) THEN
        RAISE EXCEPTION 'voting_vote is append-only: only the voiding columns may change';
    END IF;
    -- A vote can be voided once; the void cannot be undone or rewritten.
    IF OLD.voided_at IS NOT NULL AND
       (NEW.voided_at IS DISTINCT FROM OLD.voided_at
        OR NEW.void_reason IS DISTINCT FROM OLD.void_reason
        OR NEW.voided_by_id IS DISTINCT FROM OLD.voided_by_id) THEN
        RAISE EXCEPTION 'voting_vote: a voided vote cannot be changed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Restores the original (pre-seq) function body, for reversibility.
UPDATE_TRIGGER_REVERSE = """
CREATE OR REPLACE FUNCTION voting_vote_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'voting_vote is append-only: DELETE is not allowed';
    END IF;
    IF (NEW.id, NEW.event_id, NEW.award_id, NEW.nomination_id, NEW.voter_id, NEW.source,
        NEW.quantity, NEW.payment_id, NEW.ip_address, NEW.user_agent, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.event_id, OLD.award_id, OLD.nomination_id, OLD.voter_id, OLD.source,
        OLD.quantity, OLD.payment_id, OLD.ip_address, OLD.user_agent, OLD.created_at) THEN
        RAISE EXCEPTION 'voting_vote is append-only: only the voiding columns may change';
    END IF;
    IF OLD.voided_at IS NOT NULL AND
       (NEW.voided_at IS DISTINCT FROM OLD.voided_at
        OR NEW.void_reason IS DISTINCT FROM OLD.void_reason
        OR NEW.voided_by_id IS DISTINCT FROM OLD.voided_by_id) THEN
        RAISE EXCEPTION 'voting_vote: a voided vote cannot be changed';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("voting", "0003_voided_votes_free_the_slot"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="otpchallenge",
            options={"ordering": ["-seq"]},
        ),
        migrations.AlterModelOptions(
            name="vote",
            options={"ordering": ["-seq"], "permissions": [("void_vote", "Can void votes")]},
        ),
        migrations.AlterModelOptions(
            name="voter",
            options={"ordering": ["-seq"]},
        ),
        migrations.RunSQL(CREATE_OTP_SEQUENCE, DROP_OTP_SEQUENCE),
        migrations.AddField(
            model_name="otpchallenge",
            name="seq",
            field=models.BigIntegerField(
                db_default=common.db.NextVal("voting_otpchallenge_seq_seq"),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RunSQL(OWN_OTP_SEQUENCE, DISOWN_OTP_SEQUENCE),
        migrations.RunSQL(CREATE_VOTE_SEQUENCE, DROP_VOTE_SEQUENCE),
        migrations.AddField(
            model_name="vote",
            name="seq",
            field=models.BigIntegerField(
                db_default=common.db.NextVal("voting_vote_seq_seq"),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RunSQL(OWN_VOTE_SEQUENCE, DISOWN_VOTE_SEQUENCE),
        migrations.RunSQL(UPDATE_TRIGGER_FORWARD, UPDATE_TRIGGER_REVERSE),
        migrations.RunSQL(CREATE_VOTER_SEQUENCE, DROP_VOTER_SEQUENCE),
        migrations.AddField(
            model_name="voter",
            name="seq",
            field=models.BigIntegerField(
                db_default=common.db.NextVal("voting_voter_seq_seq"),
                editable=False,
                unique=True,
            ),
        ),
        migrations.RunSQL(OWN_VOTER_SEQUENCE, DISOWN_VOTER_SEQUENCE),
    ]
