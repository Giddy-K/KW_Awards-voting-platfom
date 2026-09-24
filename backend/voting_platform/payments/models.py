from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from common.models import BaseModel
from common.phone import mask_phone


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class PaymentProvider(models.TextChoices):
    MPESA = "mpesa", "M-Pesa"


class Payment(BaseModel):
    """A payment for a bundle of paid votes. SCHEMA ONLY in this phase (no Daraja yet).

    Intended flow (to be implemented with the M-Pesa integration):

    1. The voter asks to buy ``votes_purchased`` votes for a nomination; a Payment is
       created as ``initiated`` and an STK push is sent (``pending``, with the
       ``merchant_request_id`` / ``checkout_request_id`` returned by the provider).
    2. The provider calls our callback. The callback is authenticated (allow-listed source
       and a matching ``checkout_request_id``), the payload is stored in ``raw_callback``,
       and the amount/phone are verified against this row.
    3. Only a validated, *idempotent* success callback (unique ``provider_receipt``, row
       locked and moved ``pending`` -> ``succeeded`` exactly once) may create a
       ``voting.Vote(source="paid", quantity=votes_purchased, payment=this)``. A vote is
       never credited on the strength of the client saying it paid.
    """

    voter = models.ForeignKey("voting.Voter", on_delete=models.PROTECT, related_name="payments")
    nomination = models.ForeignKey(
        "nominations.Nomination", on_delete=models.PROTECT, related_name="payments"
    )
    phone_e164 = models.CharField(max_length=16)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3, default="KES")
    votes_purchased = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.INITIATED,
        db_index=True,
    )
    provider = models.CharField(
        max_length=10, choices=PaymentProvider.choices, default=PaymentProvider.MPESA
    )
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    provider_receipt = models.CharField(max_length=50, unique=True, null=True, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(name="payment_amount_positive", condition=Q(amount__gt=0)),
            models.CheckConstraint(
                name="payment_votes_positive", condition=Q(votes_purchased__gte=1)
            ),
            models.CheckConstraint(
                name="payment_succeeded_has_receipt",
                condition=~Q(status="succeeded") | Q(provider_receipt__isnull=False),
            ),
        ]

    @property
    def masked_phone(self):
        return mask_phone(self.phone_e164)

    def __str__(self):
        return f"Payment {self.pk} {self.masked_phone} {self.amount} {self.currency} [{self.status}]"
