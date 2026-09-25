from django.contrib import admin, messages

from common.admin import ReasonActionForm
from common.exceptions import ApiError
from common.text import clean_text

from . import services
from .models import Nomination, NominationStatus, Nominee


@admin.register(Nominee)
class NomineeAdmin(admin.ModelAdmin):
    list_display = ("name", "stage_name", "has_photo", "owner", "created_at")
    search_fields = ("name", "stage_name")
    readonly_fields = ("created_at", "updated_at")

    @admin.display(boolean=True, description="Photo")
    def has_photo(self, obj):
        return bool(obj.photo)


@admin.register(Nomination)
class NominationAdmin(admin.ModelAdmin):
    """Review queue. Status can only change through the approve/reject actions, which audit."""

    list_display = ("nominee", "award", "event_name", "status", "created_at", "reviewed_by")
    list_filter = ("status", "award__category__event", "award__category")
    search_fields = ("nominee__name", "nominee__stage_name")
    list_select_related = ("nominee", "award__category__event", "reviewed_by")
    readonly_fields = (
        "nominee",
        "award",
        "status",
        "submitted_by_ip",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "created_at",
        "updated_at",
    )
    action_form = ReasonActionForm
    actions = ["approve_selected", "reject_selected"]

    def has_add_permission(self, request):
        return False  # nominations enter through the public API (or the seed command)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Event")
    def event_name(self, obj):
        return obj.award.category.event.name

    def _review(self, request, queryset, approve):
        reason = clean_text(request.POST.get("reason", ""))
        if not approve and not reason:
            self.message_user(request, "Enter a reason to reject nominations.", messages.ERROR)
            return
        done = 0
        for nomination in queryset.filter(status=NominationStatus.PENDING):
            try:
                services.review_nomination(
                    nomination,
                    approve=approve,
                    reviewer=request.user,
                    reason=reason,
                    request=request,
                )
                done += 1
            except ApiError as exc:
                self.message_user(request, f"{nomination}: {exc.detail['detail']}", messages.ERROR)
        skipped = queryset.count() - done
        self.message_user(
            request,
            f"{'Approved' if approve else 'Rejected'} {done} nomination(s)."
            + (f" Skipped {skipped} that were not pending." if skipped else ""),
            messages.SUCCESS if done else messages.WARNING,
        )

    @admin.action(description="Approve selected pending nominations")
    def approve_selected(self, request, queryset):
        self._review(request, queryset, approve=True)

    @admin.action(description="Reject selected pending nominations (reason required)")
    def reject_selected(self, request, queryset):
        self._review(request, queryset, approve=False)
