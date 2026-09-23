from authemail.views import Signup
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated

from ..models import User
from ..serializers.user import SignupSerializer, UserSerializer


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]  # Ensures that only authenticated users can access this view
    # To allow only admins to create or update users
    # permission_classes = [IsAdminUser]



class CustomSignup(Signup):
    permission_classes = [AllowAny]
    serializer_class = SignupSerializer

    @swagger_auto_schema(
        operation_description="User signup endpoint",
        request_body=UserSerializer,
        responses={
            201: openapi.Response("User successfully created", UserSerializer),
            400: "Bad Request",
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
