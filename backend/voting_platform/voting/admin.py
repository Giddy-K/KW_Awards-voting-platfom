from django.contrib import admin, messages

from common.admin import MaskedPhoneAdminMixin, ReadOnlyAdminMixin, ReasonActionForm
from common.exceptions import ApiError
from common.text import clean_text

from . import services
from .models import OTPChallenge, Vote, Voter


@admin.register(Voter)
class VoterAdmin(MaskedPhoneAdminMixin, admin.ModelAdmin):
    """Voters. The full number is never shown; search needs the exact number."""

    list_display = ("phone", "verified_at", "is_blocked", "created_at")
    list_filter = ("is_blocked",)
    search_fields = ("=phone_e164",)
    fields = ("phone", "verified_at", "is_blocked", "created_at")
    readonly_fields = ("phone", "verified_at", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OTPChallenge)
class OTPChallengeAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = (
        "voter_phone",
        "purpose",
        "attempts",
        "expires_at",
        "consumed_at",
        "requested_ip",
    )
    list_filter = ("purpose",)
    list_select_related = ("voter",)
    exclude = ("code_hash", "voter")
    readonly_fields = ("voter_phone",)

    @admin.display(description="Voter")
    def voter_phone(self, obj):
        return obj.voter.masked_phone


@admin.register(Vote)
class VoteAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """Votes are append-only. The only change allowed is voiding, through the action below."""

    list_display = (
        "created_at",
        "event",
        "award",
        "nominee",
        "voter_phone",
        "source",
        "quantity",
        "voided_at",
    )
    list_filter = ("event", "source", ("voided_at", admin.EmptyFieldListFilter))
    list_select_related = ("event", "award", "nomination__nominee", "voter")
    date_hierarchy = "created_at"
    exclude = ("voter",)
    readonly_fields = ("voter_phone",)
    action_form = ReasonActionForm
    actions = ["void_selected"]

    @admin.display(description="Nominee")
    def nominee(self, obj):
        return obj.nomination.nominee

    @admin.display(description="Voter")
    def voter_phone(self, obj):
        return obj.voter.masked_phone

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_void_permission(self, request):
        return request.user.has_perm("voting.void_vote")

    @admin.action(description="Void selected votes (reason required)", permissions=["void"])
    def void_selected(self, request, queryset):
        reason = clean_text(request.POST.get("reason", ""))
        if not reason:
            self.message_user(request, "Enter a reason to void votes.", messages.ERROR)
            return
        voided = 0
        for vote_id in queryset.filter(voided_at__isnull=True).values_list("pk", flat=True):
            try:
                services.void_vote(vote_id, by=request.user, reason=reason, request=request)
                voided += 1
            except ApiError as exc:
                self.message_user(request, exc.detail["detail"], messages.ERROR)
        self.message_user(request, f"Voided {voided} vote(s).", messages.SUCCESS)
