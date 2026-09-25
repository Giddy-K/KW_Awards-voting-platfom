from django.conf import settings
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .cookies import clear_refresh_cookie, set_refresh_cookie
from .permissions import HasAjaxHeader
from .serializers import AccessTokenSerializer, MeSerializer, StaffTokenObtainPairSerializer
from .throttles import StaffLoginIPThrottle

_AJAX_HEADER_PARAM = OpenApiParameter(
    name="X-Requested-With",
    location=OpenApiParameter.HEADER,
    type=str,
    required=True,
    description='Must be exactly "XMLHttpRequest". A lightweight CSRF check for this '
    "cookie-setting/reading endpoint; see HasAjaxHeader.",
)


class StaffTokenObtainPairView(TokenObtainPairView):
    """Staff login. There is no public signup: accounts are created by an administrator.

    The refresh token is set as an HttpOnly cookie (never in the response body); only the
    access token is returned.
    """

    serializer_class = StaffTokenObtainPairSerializer
    permission_classes = [AllowAny, HasAjaxHeader]
    throttle_classes = [StaffLoginIPThrottle]

    @extend_schema(responses=AccessTokenSerializer, parameters=[_AJAX_HEADER_PARAM])
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        refresh = response.data.pop("refresh", None)
        if refresh:
            set_refresh_cookie(response, refresh)
        return response


class StaffTokenRefreshView(TokenRefreshView):
    """Exchange the refresh cookie for a new access token. Refresh tokens rotate.

    The refresh token is read from the cookie set by login (never from the request body);
    the response sets a new rotated cookie and returns only the access token.
    """

    permission_classes = [AllowAny, HasAjaxHeader]
    throttle_classes = [StaffLoginIPThrottle]

    @extend_schema(
        request=None,
        responses=AccessTokenSerializer,
        parameters=[_AJAX_HEADER_PARAM],
    )
    def post(self, request, *args, **kwargs):
        refresh_value = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if not refresh_value:
            raise InvalidToken("No refresh token cookie was sent.")
        serializer = self.get_serializer(data={"refresh": refresh_value})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        data = dict(serializer.validated_data)
        new_refresh = data.pop("refresh", None)
        response = Response(data, status=status.HTTP_200_OK)
        if new_refresh:
            set_refresh_cookie(response, new_refresh)
        return response


class LogoutView(APIView):
    """Blacklist the caller's refresh token (read from the cookie) and clear the cookie."""

    permission_classes = [IsAuthenticated, HasAjaxHeader]

    @extend_schema(request=None, responses={204: None}, parameters=[_AJAX_HEADER_PARAM])
    def post(self, request):
        refresh_value = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
        if refresh_value:
            try:
                token = RefreshToken(refresh_value)
                # Only ever blacklist the caller's own token, even if a stale/foreign
                # cookie value somehow ended up in this browser.
                if str(token.get("user_id")) == str(request.user.pk):
                    token.blacklist()
            except TokenError:
                pass  # already invalid/expired: nothing to blacklist
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


class MeView(APIView):
    """The authenticated staff member and their roles."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data)
