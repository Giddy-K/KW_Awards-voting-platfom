from django.db.models import Prefetch, ProtectedError
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import IsEventAdmin, IsEventAdminOrReadOnly
from common.exceptions import Conflict
from voting import results as results_service
from voting.serializers import ResultsSerializer, StatsSerializer

from . import services
from .filters import AwardFilter, CategoryFilter, EventFilter
from .models import Award, Category, Event, EventStatus
from .selectors import exclude_drafts, is_event_admin
from .serializers import (
    AwardSerializer,
    CategorySerializer,
    EventSerializer,
    SetStatusSerializer,
)


class _EventScopedViewSet(viewsets.ModelViewSet):
    """Public read (non-draft events only), event-admin write."""

    permission_classes = [IsEventAdminOrReadOnly]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_inactive"] = is_event_admin(self.request.user)
        return context

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError as exc:
            raise Conflict(
                "This item cannot be deleted because votes already reference it.",
                code="conflict",
            ) from exc


class EventViewSet(_EventScopedViewSet):
    """Events with their categories and awards. Drafts are visible to event admins only."""

    serializer_class = EventSerializer
    lookup_field = "slug"
    filterset_class = EventFilter
    search_fields = ["name", "slug"]
    ordering_fields = ["year", "name", "created_at"]

    def get_queryset(self):
        queryset = Event.objects.prefetch_related(
            Prefetch("categories", queryset=Category.objects.prefetch_related("awards"))
        )
        return exclude_drafts(queryset, self.request.user)

    @extend_schema(request=SetStatusSerializer, responses=EventSerializer)
    @action(detail=True, methods=["post"], url_path="set-status", permission_classes=[IsEventAdmin])
    def set_status(self, request, slug=None):
        """Change the event's status (validated transitions; audited)."""
        event = self.get_object()
        serializer = SetStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = services.change_status(event, serializer.validated_data["status"], request=request)
        return Response(self.get_serializer(event).data)

    @extend_schema(responses=ResultsSerializer, auth=[])
    @action(detail=True, methods=["get"], permission_classes=[AllowAny])
    def results(self, request, slug=None):
        """Ranked results. Public only once the event's results are published (404 before);
        event admins can read them at any time."""
        event = self.get_object()
        if not is_event_admin(request.user) and event.status != EventStatus.RESULTS_PUBLISHED:
            raise NotFound()
        payload = results_service.event_results(event, request=request)
        return Response(ResultsSerializer(payload).data)

    @extend_schema(responses=StatsSerializer)
    @action(detail=True, methods=["get"], permission_classes=[IsEventAdmin])
    def stats(self, request, slug=None):
        """Dashboard numbers: totals, votes over time, per-category counts (event admins)."""
        event = self.get_object()
        return Response(StatsSerializer(results_service.event_stats(event)).data)


class CategoryViewSet(_EventScopedViewSet):
    serializer_class = CategorySerializer
    filterset_class = CategoryFilter
    search_fields = ["name", "slug"]

    def get_queryset(self):
        queryset = Category.objects.select_related("event").prefetch_related("awards")
        return exclude_drafts(queryset, self.request.user, "event__")


class AwardViewSet(_EventScopedViewSet):
    serializer_class = AwardSerializer
    filterset_class = AwardFilter
    search_fields = ["name", "slug", "description"]

    def get_queryset(self):
        queryset = Award.objects.select_related("category__event")
        queryset = exclude_drafts(queryset, self.request.user, "category__event__")
        if not is_event_admin(self.request.user):
            queryset = queryset.filter(is_active=True)
        return queryset
