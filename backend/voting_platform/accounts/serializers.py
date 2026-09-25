from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from audit import services as audit
from audit.models import AuditAction

User = get_user_model()


class StaffTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Email + password -> access/refresh pair. Staff only; every attempt is audited."""

    def validate(self, attrs):
        request = self.context.get("request")
        try:
            data = super().validate(attrs)
        except AuthenticationFailed:
            audit.log(
                AuditAction.STAFF_LOGIN_FAILED,
                request=request,
                metadata={"email": str(attrs.get(self.username_field, ""))[:254]},
            )
            raise
        if not self.user.is_staff:
            audit.log(
                AuditAction.STAFF_LOGIN_FAILED,
                request=request,
                actor_user=self.user,
                metadata={"reason": "not_staff"},
            )
            raise AuthenticationFailed(
                self.error_messages["no_active_account"], "no_active_account"
            )
        audit.log(AuditAction.STAFF_LOGIN, request=request, actor_user=self.user)
        return data


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class MeSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_staff", "is_superuser", "roles"]
        read_only_fields = fields

    def get_roles(self, user) -> list[str]:
        roles = list(user.groups.order_by("name").values_list("name", flat=True))
        if user.is_superuser:
            roles.insert(0, "superuser")
        return roles
