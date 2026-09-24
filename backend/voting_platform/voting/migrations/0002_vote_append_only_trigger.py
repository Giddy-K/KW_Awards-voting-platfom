from django.db import migrations

FORWARD = """
CREATE FUNCTION voting_vote_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'voting_vote is append-only: DELETE is not allowed';
    END IF;
    -- Everything except the voiding columns and updated_at is immutable.
    IF (NEW.id, NEW.event_id, NEW.award_id, NEW.nomination_id, NEW.voter_id, NEW.source,
        NEW.quantity, NEW.payment_id, NEW.ip_address, NEW.user_agent, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.event_id, OLD.award_id, OLD.nomination_id, OLD.voter_id, OLD.source,
        OLD.quantity, OLD.payment_id, OLD.ip_address, OLD.user_agent, OLD.created_at) THEN
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

CREATE TRIGGER voting_vote_append_only
    BEFORE UPDATE OR DELETE ON voting_vote
    FOR EACH ROW EXECUTE FUNCTION voting_vote_append_only();
"""

REVERSE = """
DROP TRIGGER IF EXISTS voting_vote_append_only ON voting_vote;
DROP FUNCTION IF EXISTS voting_vote_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [("voting", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, REVERSE)]
