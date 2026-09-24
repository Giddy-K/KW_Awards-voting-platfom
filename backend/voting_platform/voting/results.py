"""Results are computed, never stored (AUDIT F-02, F-04).

A tally is ``SUM(quantity)`` over non-voided votes grouped by nomination. There is no counter
column anywhere to drift or be overwritten. The partial index ``vote_active_tally_idx`` covers
the query.
"""

from django.db.models import Count, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from events.models import Award, Category
from nominations.models import Nomination, NominationStatus
from voting.models import Vote


def _totals_by_nomination(**filters):
    rows = (
        Vote.objects.active()
        .filter(**filters)
        .values("nomination_id")
        .annotate(total=Coalesce(Sum("quantity"), 0))
    )
    return {row["nomination_id"]: row["total"] for row in rows}


def _rank(entries):
    """Sort by votes (desc) then name; ties share a rank (competition ranking: 1, 1, 3)."""
    entries.sort(key=lambda e: (-e["votes"], e["nominee"]["name"].lower(), str(e["nomination_id"])))
    for entry in entries:
        entry["rank"] = 1 + sum(1 for other in entries if other["votes"] > entry["votes"])
    return entries


def _entry(nomination, totals, request=None):
    nominee = nomination.nominee
    photo_url = None
    if nominee.photo:
        photo_url = request.build_absolute_uri(nominee.photo.url) if request else nominee.photo.url
    return {
        "nomination_id": nomination.id,
        "nominee": {
            "id": nominee.id,
            "name": nominee.name,
            "stage_name": nominee.stage_name,
            "photo": photo_url,
        },
        "votes": totals.get(nomination.id, 0),
    }


def award_tally(award, request=None):
    """Ranked tally for one award: list of ``{nomination_id, nominee, votes, rank}``."""
    totals = _totals_by_nomination(award=award)
    nominations = Nomination.objects.filter(
        award=award, status=NominationStatus.APPROVED
    ).select_related("nominee")
    return _rank([_entry(n, totals, request) for n in nominations])


def event_results(event, request=None):
    """Nested results for a whole event: categories -> awards -> ranked nominations."""
    totals = _totals_by_nomination(event=event)
    by_award = {}
    nominations = Nomination.objects.filter(
        award__category__event=event, status=NominationStatus.APPROVED
    ).select_related("nominee")
    for nomination in nominations:
        by_award.setdefault(nomination.award_id, []).append(_entry(nomination, totals, request))
    categories = []
    for category in Category.objects.filter(event=event).order_by("display_order", "name"):
        awards = []
        for award in Award.objects.filter(category=category, is_active=True).order_by(
            "display_order", "name"
        ):
            entries = _rank(by_award.get(award.id, []))
            awards.append(
                {
                    "id": award.id,
                    "name": award.name,
                    "slug": award.slug,
                    "total_votes": sum(e["votes"] for e in entries),
                    "nominations": entries,
                }
            )
        categories.append(
            {"id": category.id, "name": category.name, "slug": category.slug, "awards": awards}
        )
    return {
        "event": {
            "id": event.id,
            "slug": event.slug,
            "name": event.name,
            "year": event.year,
            "status": event.status,
            "results_published_at": event.results_published_at,
        },
        "generated_at": timezone.now(),
        "categories": categories,
    }


def event_stats(event):
    """Numbers for the admin dashboard."""
    votes = Vote.objects.filter(event=event)
    active = votes.active()
    per_category = (
        active.values("award__category_id", "award__category__name")
        .annotate(votes=Coalesce(Sum("quantity"), 0), voters=Count("voter", distinct=True))
        .order_by("award__category__name")
    )
    over_time = (
        active.annotate(day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(votes=Coalesce(Sum("quantity"), 0))
        .order_by("day")
    )
    nominations = Nomination.objects.filter(award__category__event=event)
    return {
        "event": {"id": event.id, "slug": event.slug, "name": event.name, "status": event.status},
        "totals": {
            "votes": active.aggregate(total=Coalesce(Sum("quantity"), 0))["total"],
            "voters": active.values("voter").distinct().count(),
            "voided_votes": votes.filter(voided_at__isnull=False).count(),
            "pending_nominations": nominations.filter(status=NominationStatus.PENDING).count(),
            "approved_nominations": nominations.filter(status=NominationStatus.APPROVED).count(),
        },
        "votes_over_time": [{"date": row["day"], "votes": row["votes"]} for row in over_time],
        "per_category": [
            {
                "category_id": row["award__category_id"],
                "category": row["award__category__name"],
                "votes": row["votes"],
                "voters": row["voters"],
            }
            for row in per_category
        ],
    }
