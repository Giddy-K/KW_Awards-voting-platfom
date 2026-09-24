import json

from django.contrib import admin

from common.admin import MaskedPhoneAdminMixin, ReadOnlyAdminMixin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(MaskedPhoneAdminMixin, ReadOnlyAdminMixin, admin.ModelAdmin):
    """Payments are written by the (future) M-Pesa integration only; staff can only view them."""

    list_display = (
        "created_at",
        "phone",
        "amount",
        "currency",
        "votes_purchased",
        "status",
        "provider_receipt",
    )
    list_filter = ("status", "provider", "currency")
    search_fields = ("provider_receipt", "checkout_request_id")
    list_select_related = ("voter", "nomination__nominee")
    exclude = ("phone_e164", "voter", "raw_callback")
    readonly_fields = ("phone", "callback")

    @admin.display(description="Raw callback")
    def callback(self, obj):
        return json.dumps(obj.raw_callback, indent=2, sort_keys=True)
