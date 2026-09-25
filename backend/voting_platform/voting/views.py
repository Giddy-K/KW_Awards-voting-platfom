from django.conf import settings
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsEventAdmin
from common.captcha import verify_captcha
from common.exceptions import ApiError, UnsupportedPhoneNumber
from common.ip import get_client_ip
from common.phone import InvalidPhoneNumber, normalize_phone, normalize_phone_strict

from . import otp, services
from .authentication import IsVoter, VoterOrStaffAuthentication, issue_voter_token
from .models import Vote
from .serializers import (
    DetailSerializer,
    MyVoteSerializer,
    OTPRequestSerializer,
    OTPVerifySerializer,
    StaffVoteSerializer,
    VoidSerializer,
    VoteCastSerializer,
    VoteReceiptSerializer,
    VoterTokenSerializer,
)
from .throttles import (
    OTPRequestIPThrottle,
    OTPRequestPhoneDayThrottle,
    OTPRequestPhoneShortThrottle,
    OTPVerifyIPThrottle,
    OTPVerifyPhoneThrottle,
    VoteIPThrottle,
    VoteVoterThrottle,
)

OTP_REQUEST_RESPONSE = {"detail": "If the number is valid, a verification code has been sent."}


class OTPRequestView(APIView):
    """Ask for an SMS code. The response is identical whether or not the number is known."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [
        OTPRequestPhoneShortThrottle,
        OTPRequestPhoneDayThrottle,
        OTPRequestIPThrottle,
    ]

    @extend_schema(request=OTPRequestSerializer, responses={202: DetailSerializer}, auth=[])
    def post(self, request):
        serializer = OTPRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # Phase 2.2: the one intentional exception to this endpoint's otherwise
        # phone-existence-blind responses -- see common.phone.normalize_phone_strict.
        try:
            phone = normalize_phone_strict(
                serializer.validated_data["phone"], settings.OTP_ALLOWED_REGIONS
            )
        except InvalidPhoneNumber:
            raise UnsupportedPhoneNumber() from None
        token = serializer.validated_data.get("captcha_token", "")
        if not verify_captcha(token, get_client_ip(request)):
            raise ApiError("CAPTCHA verification failed.", code="captcha_failed")
        otp.request_otp(phone, request=request)
        return Response(OTP_REQUEST_RESPONSE, status=status.HTTP_202_ACCEPTED)


class OTPVerifyView(APIView):
    """Exchange the SMS code for a short-lived voter token."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyPhoneThrottle, OTPVerifyIPThrottle]

    @extend_schema(request=OTPVerifySerializer, responses={200: VoterTokenSerializer}, auth=[])
    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        voter = otp.verify_otp(
            serializer.validated_data["phone"], serializer.validated_data["code"], request=request
        )
        token, expires_in = issue_voter_token(voter)
        return Response({"token": token, "token_type": "Bearer", "expires_in": expires_in})


class VoteFilter(filters.FilterSet):
    event = filters.CharFilter(field_name="event__slug")
    award = filters.UUIDFilter(field_name="award_id")
    nomination = filters.UUIDFilter(field_name="nomination_id")
    voided = filters.BooleanFilter(method="filter_voided")
    phone = filters.CharFilter(method="filter_phone")

    class Meta:
        model = Vote
        fields = ["event", "award", "nomination", "source", "voided", "phone"]

    def filter_voided(self, queryset, name, value):
        return queryset.filter(voided_at__isnull=not value)

    def filter_phone(self, queryset, name, value):
        try:
            return queryset.filter(voter__phone_e164=normalize_phone(value))
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError({"phone": str(exc)}) from exc


class VoteViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Voters cast votes here; event admins review and void them.

    One URL serves both audiences, so authentication accepts either token type and each action
    names the permission it needs: ``create`` and ``mine`` need a *voter* token, everything else
    needs the *EventAdmin* role.
    """

    authentication_classes = [VoterOrStaffAuthentication]
    filterset_class = VoteFilter
    ordering_fields = ["created_at"]
    search_fields = []

    def get_permissions(self):
        if self.action in ("create", "mine"):
            return [IsVoter()]
        return [IsEventAdmin()]

    def get_throttles(self):
        if self.action == "create":
            return [VoteVoterThrottle(), VoteIPThrottle()]
        return []

    def get_queryset(self):
        queryset = Vote.objects.select_related(
            "event", "award", "nomination__nominee", "voter", "voided_by"
        )
        if getattr(self, "swagger_fake_view", False):  # schema generation has no real user
            return queryset.none()
        if self.action == "mine":
            return queryset.filter(voter=self.request.user.voter)
        return queryset

    def get_serializer_class(self):
        return {
            "create": VoteCastSerializer,
            "mine": MyVoteSerializer,
            "void": VoidSerializer,
        }.get(self.action, StaffVoteSerializer)

    @extend_schema(request=VoteCastSerializer, responses={201: VoteReceiptSerializer})
    def create(self, request):
        """Cast a free vote for a nomination (one per award; 409 if you already voted)."""
        serializer = VoteCastSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vote = services.cast_free_vote(
            request.user.voter, serializer.validated_data["nomination_id"], request=request
        )
        return Response(VoteReceiptSerializer(vote).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses=MyVoteSerializer(many=True))
    @action(detail=False, methods=["get"], filterset_class=None)
    def mine(self, request):
        """Which awards the authenticated voter has voted in (no tallies)."""
        page = self.paginate_queryset(self.get_queryset())
        serializer = MyVoteSerializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @extend_schema(request=VoidSerializer, responses=StaffVoteSerializer)
    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        """Exclude a vote from all tallies (event admins). The row is kept."""
        serializer = VoidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vote = services.void_vote(
            self.get_object().pk,
            by=request.user,
            reason=serializer.validated_data["reason"],
            request=request,
        )
        return Response(StaffVoteSerializer(vote).data)
