from django.contrib import admin

from common.admin import ReadOnlyAdminMixin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Append-only: nobody, superusers included, can add, change or delete entries here."""

    list_display = (
        "created_at",
        "action",
        "actor_user",
        "actor_voter_masked",
        "target_type",
        "target_id",
        "ip_address",
    )
    list_filter = ("action", "target_type")
    search_fields = ("target_id", "actor_user__email")
    date_hierarchy = "created_at"
    list_select_related = ("actor_user", "actor_voter")
    exclude = ("actor_voter",)
    readonly_fields = ("actor_voter_masked",)

    @admin.display(description="Voter")
    def actor_voter_masked(self, obj):
        return obj.actor_voter.masked_phone if obj.actor_voter else "-"

    def get_actions(self, request):
        return {}
