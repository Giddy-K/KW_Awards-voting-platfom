from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .throttles import StaffLoginIPThrottle

from .serializers import LogoutSerializer, MeSerializer, StaffTokenObtainPairSerializer


class StaffTokenObtainPairView(TokenObtainPairView):
    """Staff login. There is no public signup: accounts are created by an administrator."""

    serializer_class = StaffTokenObtainPairSerializer
    throttle_classes = [StaffLoginIPThrottle]


class StaffTokenRefreshView(TokenRefreshView):
    """Exchange a refresh token for a new access token (refresh tokens rotate)."""

    throttle_classes = [StaffLoginIPThrottle]


class LogoutView(APIView):
    """Blacklist the caller's refresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=LogoutSerializer, responses={204: None})
    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            # Only the owner's own refresh token may be revoked here.
            if str(token.get("user_id")) != str(request.user.pk):
                raise TokenError("Token does not belong to this user.")
            token.blacklist()
        except TokenError as exc:
            raise ValidationError({"refresh": "Invalid or expired refresh token."}) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """The authenticated staff member and their roles."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=MeSerializer)
    def get(self, request):
        return Response(MeSerializer(request.user).data)
