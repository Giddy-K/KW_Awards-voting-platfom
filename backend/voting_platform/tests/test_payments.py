"""Payments: schema only in this phase (AUDIT F-13). No route may credit votes yet."""

from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from payments.models import Payment, PaymentStatus

from .helpers import make_nomination, make_user, make_voter, staff_client
from .vote_helpers import make_payment

pytestmark = pytest.mark.django_db


def test_payment_defaults_and_masked_representation():
    voter = make_voter("+254712345612")
    payment = make_payment(voter, make_nomination(), status=PaymentStatus.INITIATED)
    assert payment.currency == "KES" and payment.provider == "mpesa"
    assert payment.raw_callback == {} and payment.result_code is None
    assert payment.masked_phone == "+2547******12"
    assert "+254712345612" not in str(payment) and "+2547******12" in str(payment)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": Decimal("0")},
        {"amount": Decimal("-5")},
        {"votes_purchased": 0},
        {"status": PaymentStatus.SUCCEEDED, "provider_receipt": None},  # success needs a receipt
    ],
)
def test_database_rejects_invalid_payments(overrides):
    voter, nomination = make_voter(), make_nomination()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(voter, nomination, **{"provider_receipt": "R1", **overrides})


def test_receipts_and_checkout_ids_are_unique_but_nullable():
    voter, nomination = make_voter(), make_nomination()
    make_payment(voter, nomination, status=PaymentStatus.PENDING)  # no ids yet
    make_payment(voter, nomination, status=PaymentStatus.PENDING)  # a second null is fine
    make_payment(voter, nomination, checkout_request_id="ws_CO_1", provider_receipt="RCPT1")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(voter, nomination, status=PaymentStatus.PENDING, checkout_request_id="ws_CO_1")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_payment(voter, nomination, provider_receipt="RCPT1")  # replayed receipt


def test_raw_callbacks_are_stored_as_json():
    payment = make_payment(
        make_voter(), make_nomination(), raw_callback={"Body": {"stkCallback": {"ResultCode": 0}}}
    )
    payment.refresh_from_db()
    assert payment.raw_callback["Body"]["stkCallback"]["ResultCode"] == 0


def test_voters_and_nominations_with_payments_cannot_be_deleted():
    voter, nomination = make_voter(), make_nomination()
    make_payment(voter, nomination)
    with pytest.raises(ProtectedError):
        voter.delete()
    with pytest.raises(ProtectedError):
        nomination.delete()


def test_there_are_no_payment_or_purchase_endpoints_yet():
    """Votes must never be creditable by a client claim; the M-Pesa callback comes later."""
    client = staff_client(make_user(superuser=True))
    for path in (
        "/api/v1/payments/",
        "/api/v1/payments/callback/",
        "/api/v1/votes/buy/",
        "/api/v1/mpesa/callback/",
    ):
        assert client.get(path).status_code == 404, path
        # (votes/buy/ matches the votes/<id>/ route, which has no POST: 405, and never a 2xx)
        assert client.post(path, {"votes": 100}).status_code in (404, 405), path
    assert Payment.objects.count() == 0
