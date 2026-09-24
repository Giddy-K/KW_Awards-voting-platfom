from django_filters import rest_framework as filters
from rest_framework import serializers, viewsets

from accounts.permissions import IsEventAdmin

from .models import AuditLog


class AuditLogFilter(filters.FilterSet):
    actor_user = filters.UUIDFilter(field_name="actor_user_id")
    actor_voter = filters.UUIDFilter(field_name="actor_voter_id")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lt")

    class Meta:
        model = AuditLog
        fields = ["action", "target_type", "target_id", "actor_user", "actor_voter"]


class AuditLogSerializer(serializers.ModelSerializer):
    actor_user = serializers.SlugRelatedField(slug_field="email", read_only=True)
    actor_voter = serializers.CharField(
        source="actor_voter.masked_phone", read_only=True, default=None
    )

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "actor_user",
            "actor_voter",
            "target_type",
            "target_id",
            "metadata",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        read_only_fields = fields


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit trail for dispute resolution (event admins). Entries cannot be changed."""

    permission_classes = [IsEventAdmin]
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    ordering_fields = ["created_at"]
    queryset = AuditLog.objects.select_related("actor_user", "actor_voter")
