from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers

from common.exceptions import ApiError

from . import services
from .models import Award, Category, Event, EventStatus


class StatusActionForm(helpers.ActionForm):
    new_status = forms.ChoiceField(
        choices=[("", "New status..."), *EventStatus.choices], required=False
    )


class CategoryInline(admin.TabularInline):
    model = Category
    extra = 0
    fields = ("name", "slug", "display_order")
    show_change_link = True


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("name", "year", "status", "voting_opens_at", "voting_closes_at")
    list_filter = ("status", "year")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    # Status changes go through the validated, audited service (see the action below).
    readonly_fields = ("status", "results_published_at", "created_at", "updated_at")
    inlines = [CategoryInline]
    action_form = StatusActionForm
    actions = ["change_status"]

    @admin.action(description="Change status of selected events (choose the new status above)")
    def change_status(self, request, queryset):
        new_status = request.POST.get("new_status")
        if not new_status:
            self.message_user(request, "Choose the new status first.", messages.ERROR)
            return
        for event in queryset:
            try:
                services.change_status(event, new_status, request=request)
            except ApiError as exc:
                self.message_user(request, f"{event.name}: {exc.detail['detail']}", messages.ERROR)
            else:
                self.message_user(request, f"{event.name} is now {new_status}.", messages.SUCCESS)


class AwardInline(admin.TabularInline):
    model = Award
    extra = 0
    fields = ("name", "slug", "display_order", "is_active")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "display_order")
    list_filter = ("event",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [AwardInline]


@admin.register(Award)
class AwardAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "event_name", "is_active", "display_order")
    list_filter = ("is_active", "category__event", "category")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    list_select_related = ("category__event",)

    @admin.display(description="Event")
    def event_name(self, obj):
        return obj.category.event.name
