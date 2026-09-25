from django.db.models import Q
from django_filters import rest_framework as filters
from rest_framework import serializers, viewsets

from accounts.permissions import IsEventAdmin
from common.phone import InvalidPhoneNumber, normalize_phone

from .models import AuditLog
from .services import hash_phone


class AuditLogFilter(filters.FilterSet):
    actor_user = filters.UUIDFilter(field_name="actor_user_id")
    actor_voter = filters.UUIDFilter(field_name="actor_voter_id")
    created_after = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lt")
    phone = filters.CharFilter(method="filter_phone")

    class Meta:
        model = AuditLog
        fields = ["action", "target_type", "target_id", "actor_user", "actor_voter"]

    def filter_phone(self, queryset, name, value):
        """Match entries for this phone, verified or not (Phase 2.3).

        Hashes the phone server-side the same way :func:`audit.services.log_voter_event` does,
        so staff search by the number they have, never by a hash they'd have to compute
        themselves. Matches pre-verification entries (``metadata__phone_hash``, no
        ``actor_voter``) and post-verification ones (joined through ``actor_voter``'s phone) in
        one query, since the same real-world number can appear both ways over time.
        """
        try:
            e164 = normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise serializers.ValidationError({"phone": str(exc)}) from exc
        return queryset.filter(
            Q(metadata__phone_hash=hash_phone(e164)) | Q(actor_voter__phone_e164=e164)
        )


class AuditLogSerializer(serializers.ModelSerializer):
    actor_user = serializers.SlugRelatedField(slug_field="email", read_only=True)
    actor_voter = serializers.CharField(
        source="actor_voter.masked_phone", read_only=True, default=None
    )
    metadata = serializers.SerializerMethodField()

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

    def get_metadata(self, obj) -> dict:
        """Never expose the raw phone_hash (Phase 2.3): staff search by phone (the ``?phone=``
        filter above), they never need to see or send the hash itself."""
        metadata = dict(obj.metadata or {})
        metadata.pop("phone_hash", None)
        return metadata


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only audit trail for dispute resolution (event admins). Entries cannot be changed."""

    permission_classes = [IsEventAdmin]
    serializer_class = AuditLogSerializer
    filterset_class = AuditLogFilter
    ordering_fields = ["created_at"]
    queryset = AuditLog.objects.select_related("actor_user", "actor_voter")
