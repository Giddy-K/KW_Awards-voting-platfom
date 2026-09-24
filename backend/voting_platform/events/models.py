from django.core.validators import MaxLengthValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from common.models import BaseModel


class EventStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    NOMINATIONS_OPEN = "nominations_open", "Nominations open"
    VOTING_OPEN = "voting_open", "Voting open"
    CLOSED = "closed", "Closed"
    RESULTS_PUBLISHED = "results_published", "Results published"


# Allowed status transitions (validated by ``events.services.change_status``).
STATUS_TRANSITIONS = {
    EventStatus.DRAFT: {EventStatus.NOMINATIONS_OPEN},
    EventStatus.NOMINATIONS_OPEN: {EventStatus.VOTING_OPEN, EventStatus.DRAFT},
    EventStatus.VOTING_OPEN: {EventStatus.CLOSED},
    EventStatus.CLOSED: {EventStatus.RESULTS_PUBLISHED, EventStatus.VOTING_OPEN},
    EventStatus.RESULTS_PUBLISHED: {EventStatus.CLOSED},
}


class Event(BaseModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80, unique=True)
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)]
    )
    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.DRAFT, db_index=True
    )
    nominations_open_at = models.DateTimeField(null=True, blank=True)
    nominations_close_at = models.DateTimeField(null=True, blank=True)
    voting_opens_at = models.DateTimeField(null=True, blank=True)
    voting_closes_at = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "name"]
        constraints = [
            models.CheckConstraint(
                name="event_nomination_window_ordered",
                condition=Q(nominations_open_at__isnull=True)
                | Q(nominations_close_at__isnull=True)
                | Q(nominations_open_at__lt=F("nominations_close_at")),
            ),
            models.CheckConstraint(
                name="event_voting_window_ordered",
                condition=Q(voting_opens_at__isnull=True)
                | Q(voting_closes_at__isnull=True)
                | Q(voting_opens_at__lt=F("voting_closes_at")),
            ),
            models.CheckConstraint(
                name="event_nominations_close_before_voting_opens",
                condition=Q(nominations_close_at__isnull=True)
                | Q(voting_opens_at__isnull=True)
                | Q(nominations_close_at__lte=F("voting_opens_at")),
            ),
            models.CheckConstraint(
                name="event_published_has_timestamp",
                condition=~Q(status="results_published") | Q(results_published_at__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.year})"

    @property
    def is_public(self):
        return self.status != EventStatus.DRAFT

    def nominations_are_open(self, at=None):
        """Status AND window must both allow it. Opens inclusive, closes exclusive."""
        at = at or timezone.now()
        return bool(
            self.status == EventStatus.NOMINATIONS_OPEN
            and self.nominations_open_at
            and self.nominations_close_at
            and self.nominations_open_at <= at < self.nominations_close_at
        )

    def voting_is_open(self, at=None):
        """Status AND window must both allow it. Opens inclusive, closes exclusive."""
        at = at or timezone.now()
        return bool(
            self.status == EventStatus.VOTING_OPEN
            and self.voting_opens_at
            and self.voting_closes_at
            and self.voting_opens_at <= at < self.voting_closes_at
        )


class Category(BaseModel):
    """A grouping of awards (what the UI calls a "genre")."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True, max_length=1000, validators=[MaxLengthValidator(1000)])
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(fields=["event", "slug"], name="category_event_slug_unique"),
        ]

    def __str__(self):
        return f"{self.event.name}: {self.name}"


class Award(BaseModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="awards")
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=80)
    description = models.TextField(blank=True, max_length=1000, validators=[MaxLengthValidator(1000)])
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="award_category_slug_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def event(self):
        return self.category.event
