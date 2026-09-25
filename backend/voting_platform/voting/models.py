from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models
from django.db.models import Q
from django.utils import timezone

from common.models import BaseModel
from common.phone import mask_phone


class Voter(BaseModel):
    """A member of the public who votes. Identified only by a verified phone number."""

    phone_e164 = models.CharField(max_length=16, unique=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]

    @property
    def masked_phone(self):
        return mask_phone(self.phone_e164)

    def __str__(self):
        # Never leak the full number through str()/logging.
        return f"Voter {self.masked_phone}"


class OTPPurpose(models.TextChoices):
    VOTE_LOGIN = "vote_login", "Vote login"


class OTPChallenge(BaseModel):
    """A one-time code sent by SMS. Only a keyed hash of the code is stored."""

    voter = models.ForeignKey(Voter, on_delete=models.CASCADE, related_name="otp_challenges")
    code_hash = models.CharField(max_length=64)
    purpose = models.CharField(
        max_length=20, choices=OTPPurpose.choices, default=OTPPurpose.VOTE_LOGIN
    )
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["voter", "-created_at"])]

    def __str__(self):
        return f"OTP for {self.voter.masked_phone} ({self.purpose})"

    def is_usable(self, at=None):
        at = at or timezone.now()
        return (
            self.consumed_at is None
            and self.expires_at > at
            and self.attempts < settings.OTP_MAX_ATTEMPTS
        )


class VoteSource(models.TextChoices):
    FREE = "free", "Free"
    PAID = "paid", "Paid"


class AppendOnlyError(PermissionDenied):
    pass


class VoteQuerySet(models.QuerySet):
    MUTABLE_FIELDS = {"voided_at", "voided_by", "voided_by_id", "void_reason", "updated_at"}

    def active(self):
        """Votes that count: not voided."""
        return self.filter(voided_at__isnull=True)

    def update(self, **kwargs):
        if set(kwargs) - self.MUTABLE_FIELDS:
            raise AppendOnlyError("Votes are append-only; only voiding is allowed.")
        return super().update(**kwargs)

    def delete(self):
        raise AppendOnlyError("Votes are append-only and cannot be deleted.")


class Vote(BaseModel):
    """One vote (or a bundle of paid votes). Append-only; voiding never deletes.

    ``event`` and ``award`` are denormalised from ``nomination`` for constraints and
    fast tallies. They are ALWAYS derived server-side from the nomination
    (``voting.services.cast_free_vote``), never accepted from a client (AUDIT F-08).
    A PostgreSQL trigger (see the migration) also blocks UPDATE of the immutable columns
    and any DELETE, so this holds even for raw SQL or ``QuerySet._raw_delete``.
    """

    event = models.ForeignKey("events.Event", on_delete=models.PROTECT, related_name="votes")
    award = models.ForeignKey("events.Award", on_delete=models.PROTECT, related_name="votes")
    nomination = models.ForeignKey(
        "nominations.Nomination", on_delete=models.PROTECT, related_name="votes"
    )
    voter = models.ForeignKey(Voter, on_delete=models.PROTECT, related_name="votes")
    source = models.CharField(max_length=4, choices=VoteSource.choices, default=VoteSource.FREE)
    quantity = models.PositiveIntegerField(default=1)
    payment = models.ForeignKey(
        "payments.Payment",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="votes",
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    # Voiding (admin only): the vote stays in the table but stops counting.
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,  # append-only table: deactivate the user instead of deleting
        related_name="voided_votes",
    )
    void_reason = models.CharField(max_length=500, blank=True)

    objects = VoteQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        permissions = [("void_vote", "Can void votes")]
        constraints = [
            # One free vote per voter per award, enforced by the database.
            models.UniqueConstraint(
                fields=["voter", "award"],
                condition=Q(source="free"),
                name="vote_one_free_per_voter_per_award",
            ),
            models.CheckConstraint(
                name="vote_source_rules",
                condition=(Q(source="free") & Q(quantity=1) & Q(payment__isnull=True))
                | (Q(source="paid") & Q(payment__isnull=False)),
            ),
            models.CheckConstraint(name="vote_quantity_positive", condition=Q(quantity__gte=1)),
            models.CheckConstraint(
                name="vote_void_has_reason",
                condition=Q(voided_at__isnull=True) | ~Q(void_reason=""),
            ),
        ]
        indexes = [
            # Tallies only ever look at non-voided votes.
            models.Index(
                fields=["award", "nomination"],
                condition=Q(voided_at__isnull=True),
                name="vote_active_tally_idx",
            ),
            models.Index(
                fields=["event", "created_at"],
                name="vote_event_created_idx",
            ),
            models.Index(fields=["voter", "created_at"], name="vote_voter_created_idx"),
        ]

    _IMMUTABLE_ON_UPDATE = {"voided_at", "voided_by", "void_reason", "updated_at"}

    def __str__(self):
        return f"Vote {self.pk} ({self.source})"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = set(kwargs.get("update_fields") or [])
            if not fields or fields - self._IMMUTABLE_ON_UPDATE:
                raise AppendOnlyError("Votes are append-only; only voiding is allowed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Votes are append-only and cannot be deleted.")

    @property
    def is_voided(self):
        return self.voided_at is not None

    def void(self, by, reason):
        """Exclude this vote from tallies. Use ``voting.services.void_vote`` (adds audit)."""
        self.voided_at = timezone.now()
        self.voided_by = by
        self.void_reason = reason
        self.save(update_fields=["voided_at", "voided_by", "void_reason", "updated_at"])
