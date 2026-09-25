from django.db.models import Prefetch
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from accounts.permissions import IsModerator
from accounts.roles import MODERATOR, user_has_role
from common.captcha import verify_captcha
from common.exceptions import ApiError
from common.ip import get_client_ip
from events.models import Award, EventStatus

from . import services
from .models import Nomination, NominationStatus, Nominee
from .serializers import (
    NominationSubmitSerializer,
    NomineeProfileSerializer,
    PublicNominationSerializer,
    RejectSerializer,
    StaffNominationSerializer,
    SubmissionResultSerializer,
)
from .throttles import NominationIPThrottle


class NominationFilter(filters.FilterSet):
    event = filters.CharFilter(field_name="award__category__event__slug")
    category = filters.UUIDFilter(field_name="award__category_id")
    award = filters.UUIDFilter(field_name="award_id")

    class Meta:
        model = Nomination
        fields = ["event", "category", "award", "status"]


def public_nominations():
    """Approved nominations of active awards in non-draft events. The ONLY public view of them."""
    return (
        Nomination.objects.filter(status=NominationStatus.APPROVED, award__is_active=True)
        .exclude(award__category__event__status=EventStatus.DRAFT)
        .select_related("nominee", "award__category__event")
        .order_by("nominee__name", "id")
    )


class NominationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Public: submit a nomination and browse approved ones. Moderators: review queue."""

    filterset_class = NominationFilter
    search_fields = ["nominee__name", "nominee__stage_name"]
    ordering_fields = ["created_at", "nominee__name"]

    def _is_moderator(self):
        return user_has_role(self.request.user, MODERATOR)

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsModerator()]
        return [AllowAny()]

    def get_throttles(self):
        return [NominationIPThrottle()] if self.action == "create" else []

    def get_queryset(self):
        if self._is_moderator():
            return Nomination.objects.select_related(
                "nominee", "award__category__event", "reviewed_by"
            ).order_by("-created_at")
        return public_nominations()

    def get_serializer_class(self):
        if self.action == "create":
            return NominationSubmitSerializer
        if self.action == "reject":
            return RejectSerializer
        return StaffNominationSerializer if self._is_moderator() else PublicNominationSerializer

    @extend_schema(
        request={"multipart/form-data": NominationSubmitSerializer},
        responses={201: SubmissionResultSerializer},
    )
    def create(self, request, *args, **kwargs):
        """Submit a nomination (multipart, CAPTCHA-protected). It stays hidden until approved."""
        token = request.data.get("captcha_token", "") if hasattr(request.data, "get") else ""
        if not verify_captcha(token, get_client_ip(request)):
            raise ApiError("CAPTCHA verification failed.", code="captcha_failed")
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nomination = serializer.save()
        data = SubmissionResultSerializer(nomination, context=self.get_serializer_context()).data
        return Response(data, status=201)

    @extend_schema(request=None, responses=StaffNominationSerializer)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Approve a pending nomination (moderators)."""
        nomination = services.review_nomination(
            self.get_object(), approve=True, reviewer=request.user, request=request
        )
        return Response(
            StaffNominationSerializer(nomination, context=self.get_serializer_context()).data
        )

    @extend_schema(request=RejectSerializer, responses=StaffNominationSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Reject a pending nomination with a reason (moderators)."""
        serializer = RejectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        nomination = services.review_nomination(
            self.get_object(),
            approve=False,
            reviewer=request.user,
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(
            StaffNominationSerializer(nomination, context=self.get_serializer_context()).data
        )


class NomineeViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Public nominee profile with their approved nominations only."""

    permission_classes = [AllowAny]
    serializer_class = NomineeProfileSerializer
    pagination_class = None

    def get_queryset(self):
        visible = public_nominations()
        return (
            Nominee.objects.filter(nominations__in=visible)
            .distinct()
            .prefetch_related(
                Prefetch("nominations", queryset=visible, to_attr="visible_nominations")
            )
        )


class AwardNominationsView(generics.ListAPIView):
    """Approved nominations for one award (public)."""

    permission_classes = [AllowAny]
    serializer_class = PublicNominationSerializer
    search_fields = ["nominee__name", "nominee__stage_name"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no URL kwargs
            return Nomination.objects.none()
        award = generics.get_object_or_404(
            Award.objects.filter(is_active=True).exclude(category__event__status=EventStatus.DRAFT),
            pk=self.kwargs["award_id"],
        )
        return public_nominations().filter(award=award)
