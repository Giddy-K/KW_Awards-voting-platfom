"""Small database helpers shared across apps."""

from django.db.models import Func, Value
from django.db.models.fields import BigIntegerField


class NextVal(Func):
    """SQL ``nextval('sequence_name')``: the next value of a PostgreSQL sequence.

    Used as the ``db_default`` of a ``seq`` column (see each app's ``Meta.ordering`` and
    "Phase 2.2 results" in AUDIT.md): a database-generated, strictly monotonically
    increasing, gap-tolerant counter that gives ``created_at``-ordered models a
    deterministic tiebreaker. ``created_at`` alone is not fine-grained enough to guarantee a
    strict order when several rows are written in the same tick (observed as a flaky test:
    ``tests/test_otp.py::test_f16_attempt_limit_locks_the_challenge_even_for_the_right_code``).

    Because this is a ``db_default`` rather than a Python-side ``default``, Django omits the
    column from the explicit INSERT column list and lets PostgreSQL fill it in from the
    column's own ``DEFAULT nextval(...)`` clause -- so every row's ``seq`` is assigned
    atomically by the database itself (``nextval()`` is defined to be safe under concurrent
    callers), never raced, reused, or set by application code.
    """

    function = "nextval"
    output_field = BigIntegerField()

    def __init__(self, sequence_name):
        super().__init__(Value(sequence_name))
