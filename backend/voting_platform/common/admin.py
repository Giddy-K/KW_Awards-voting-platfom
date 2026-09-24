"""Shared admin helpers."""

from django import forms
from django.contrib import admin
from django.contrib.admin import helpers


class ReadOnlyAdminMixin:
    """Everything is view-only: no add, change or delete for anyone (superusers included)."""

    def has_add_permission(self, request, *args, **kwargs):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReasonActionForm(helpers.ActionForm):
    """Adds a free-text ``reason`` box next to the admin action selector."""

    reason = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Reason (needed for reject / void)"}),
    )


class MaskedPhoneAdminMixin:
    """Admin lists and details only ever show the masked number."""

    @admin.display(description="Phone")
    def phone(self, obj):
        return obj.masked_phone
