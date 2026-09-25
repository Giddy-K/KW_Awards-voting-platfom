from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm

from .models import User


class StaffCreationForm(BaseUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name")


class StaffChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
        field_classes = {}


@admin.register(User)
class StaffUserAdmin(BaseUserAdmin):
    """Staff accounts (there is no public signup). Roles are groups: Moderator, EventAdmin."""

    form = StaffChangeForm
    add_form = StaffCreationForm
    ordering = ("email",)
    list_display = ("email", "full_name", "is_active", "is_superuser", "role_names")
    list_filter = ("is_active", "is_superuser", "groups")
    search_fields = ("email", "full_name")
    filter_horizontal = ("groups", "user_permissions")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("full_name",)}),
        (
            "Access",
            {
                "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
                "description": "Deactivate instead of deleting: audit entries reference users.",
            },
        ),
        ("Important dates", {"fields": ("last_login", "created_at")}),
    )
    readonly_fields = ("last_login", "created_at")
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "password1", "password2", "groups"),
            },
        ),
    )

    @admin.display(description="Roles")
    def role_names(self, obj):
        names = [g.name for g in obj.groups.all()]
        return ", ".join(names) or ("superuser" if obj.is_superuser else "-")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")
