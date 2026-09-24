"""Version 1 of the REST API (mounted at /api/v1/)."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from accounts.views import LogoutView, MeView, StaffTokenObtainPairView, StaffTokenRefreshView
from events.views import AwardViewSet, CategoryViewSet, EventViewSet

router = SimpleRouter()
router.register("events", EventViewSet, basename="event")
router.register("categories", CategoryViewSet, basename="category")
router.register("awards", AwardViewSet, basename="award")

urlpatterns = [
    path("auth/token/", StaffTokenObtainPairView.as_view(), name="auth-token"),
    path("auth/refresh/", StaffTokenRefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", MeView.as_view(), name="auth-me"),
    *router.urls,
]
