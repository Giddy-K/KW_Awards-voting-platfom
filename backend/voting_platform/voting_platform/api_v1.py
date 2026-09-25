"""Version 1 of the REST API (mounted at /api/v1/)."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from accounts.views import LogoutView, MeView, StaffTokenObtainPairView, StaffTokenRefreshView
from audit.views import AuditLogViewSet
from events.views import AwardViewSet, CategoryViewSet, EventViewSet
from nominations.views import AwardNominationsView, NominationViewSet, NomineeViewSet
from voting.views import OTPRequestView, OTPVerifyView, VoteViewSet

router = SimpleRouter()
router.register("events", EventViewSet, basename="event")
router.register("categories", CategoryViewSet, basename="category")
router.register("awards", AwardViewSet, basename="award")
router.register("nominations", NominationViewSet, basename="nomination")
router.register("nominees", NomineeViewSet, basename="nominee")
router.register("votes", VoteViewSet, basename="vote")
router.register("audit-logs", AuditLogViewSet, basename="audit-log")

urlpatterns = [
    path("auth/token/", StaffTokenObtainPairView.as_view(), name="auth-token"),
    path("auth/refresh/", StaffTokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    path("voters/otp/request/", OTPRequestView.as_view(), name="voter-otp-request"),
    path("voters/otp/verify/", OTPVerifyView.as_view(), name="voter-otp-verify"),
    path(
        "awards/<uuid:award_id>/nominations/",
        AwardNominationsView.as_view(),
        name="award-nominations",
    ),
    *router.urls,
]
