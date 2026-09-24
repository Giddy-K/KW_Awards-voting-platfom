import os
import uuid

from django.conf import settings
from django.core.validators import MaxLengthValidator, URLValidator
from django.db import models
from django.db.models import Q

from common.models import BaseModel


def nominee_photo_path(instance, filename):
    """Random, non-guessable file name; the client-supplied name is discarded."""
    extension = os.path.splitext(filename)[1].lower()
    if extension not in {".jpg", ".png", ".webp"}:
        extension = ".jpg"
    return f"nominees/{uuid.uuid4().hex}{extension}"


class HttpUrlField(models.URLField):
    """URL restricted to http(s); nullable, never a placeholder like ``"NA"``."""

    default_validators = [URLValidator(schemes=["http", "https"])]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 300)
        kwargs.setdefault("null", True)
        kwargs.setdefault("blank", True)
        super().__init__(*args, **kwargs)

    def formfield(self, **kwargs):
        # Django 6 will assume https for scheme-less input; opt in now (no deprecation warning).
        kwargs.setdefault("assume_scheme", "https")
        return super().formfield(**kwargs)


class Nominee(BaseModel):
    name = models.CharField(max_length=120)
    stage_name = models.CharField(max_length=120, blank=True)
    bio = models.TextField(blank=True, max_length=2000, validators=[MaxLengthValidator(2000)])
    photo = models.ImageField(upload_to=nominee_photo_path, blank=True, max_length=200)

    website_url = HttpUrlField()
    instagram_url = HttpUrlField()
    facebook_url = HttpUrlField()
    x_url = HttpUrlField()
    youtube_url = HttpUrlField()
    tiktok_url = HttpUrlField()

    # Private: never exposed by the public API.
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_nominees",
        help_text="Reserved for future nominee accounts.",
    )

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["name"]), models.Index(fields=["stage_name"])]

    def __str__(self):
        return self.stage_name or self.name


class NominationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    WITHDRAWN = "withdrawn", "Withdrawn"


class Nomination(BaseModel):
    """A nominee entered into an award. Only ``approved`` nominations are public/votable."""

    nominee = models.ForeignKey(Nominee, on_delete=models.CASCADE, related_name="nominations")
    award = models.ForeignKey("events.Award", on_delete=models.CASCADE, related_name="nominations")
    status = models.CharField(
        max_length=12,
        choices=NominationStatus.choices,
        default=NominationStatus.PENDING,
        db_index=True,
    )
    submitted_by_ip = models.GenericIPAddressField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_nominations",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["nominee", "award"], name="nomination_nominee_award_unique"),
            models.CheckConstraint(
                name="nomination_rejected_has_reason",
                condition=~Q(status="rejected") | ~Q(rejection_reason=""),
            ),
            models.CheckConstraint(
                name="nomination_reviewed_has_timestamp",
                condition=Q(status__in=["pending", "withdrawn"]) | Q(reviewed_at__isnull=False),
            ),
        ]
        indexes = [models.Index(fields=["award", "status"])]

    def __str__(self):
        return f"{self.nominee} -> {self.award} [{self.status}]"
