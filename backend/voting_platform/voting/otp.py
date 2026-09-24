"""One-time codes for voter login.

Design notes (AUDIT F-01, F-10, F-16):

* The 6-digit code is only ever sent by SMS; the database stores an HMAC of it keyed with the
  server secret and bound to the challenge id, so a leaked table does not reveal codes.
* Codes are compared with ``hmac.compare_digest`` (constant time).
* Each verification attempt is counted *before* comparing and committed even when it fails;
  after ``OTP_MAX_ATTEMPTS`` the challenge is dead, whatever code is submitted.
* Every failure mode returns the same generic error, so the API does not reveal whether a
  phone number is known, blocked, expired or simply wrong.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit import services as audit
from audit.models import AuditAction
from common.exceptions import ApiError
from common.phone import mask_phone
from common.sms import SmsError, send_sms

from .models import OTPChallenge, OTPPurpose, Voter

logger = logging.getLogger(__name__)


class InvalidOTP(ApiError):
    default_detail = "Invalid or expired code."
    default_code = "invalid_otp"


def generate_code():
    return f"{secrets.randbelow(10**settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"


def hash_code(challenge_id, code):
    key = hashlib.sha256(b"otp-hmac-key:" + settings.SECRET_KEY.encode()).digest()
    message = f"{challenge_id}:{code}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def request_otp(phone_e164, *, request):
    """Create and SMS a fresh code. Always succeeds from the caller's point of view."""
    voter, _created = Voter.objects.get_or_create(phone_e164=phone_e164)
    if voter.is_blocked:
        audit.log(
            AuditAction.OTP_REQUESTED,
            request=request,
            actor_voter=voter,
            metadata={"phone_masked": mask_phone(phone_e164), "sent": False, "blocked": True},
        )
        return
    now = timezone.now()
    code = generate_code()
    with transaction.atomic():
        # Only the newest code is valid.
        OTPChallenge.objects.filter(
            voter=voter, consumed_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now)
        challenge = OTPChallenge(
            voter=voter,
            purpose=OTPPurpose.VOTE_LOGIN,
            expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
            requested_ip=audit_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:300],
        )
        challenge.code_hash = hash_code(challenge.id, code)
        challenge.save()
        audit.log(
            AuditAction.OTP_REQUESTED,
            request=request,
            actor_voter=voter,
            target=challenge,
            metadata={"phone_masked": mask_phone(phone_e164), "sent": True},
        )
    minutes = settings.OTP_TTL_SECONDS // 60
    try:
        send_sms(
            phone_e164,
            f"Your KW Awards verification code is {code}. It expires in {minutes} minutes.",
        )
    except SmsError:
        # Never surface provider problems to the caller (that would reveal deliverability).
        logger.exception("Could not deliver OTP to %s", mask_phone(phone_e164))


def audit_ip(request):
    from common.ip import get_client_ip

    return get_client_ip(request)


def verify_otp(phone_e164, code, *, request):
    """Return the verified ``Voter`` or raise :class:`InvalidOTP` (identical for every failure)."""
    now = timezone.now()
    reason = "no_active_challenge"
    voter = Voter.objects.filter(phone_e164=phone_e164).first()
    succeeded = False
    with transaction.atomic():
        challenge = None
        if voter is not None and not voter.is_blocked:
            challenge = (
                OTPChallenge.objects.select_for_update()
                .filter(voter=voter, consumed_at__isnull=True, expires_at__gt=now)
                .order_by("-created_at")
                .first()
            )
        elif voter is not None:
            reason = "blocked"
        attempts = 0
        if challenge is not None:
            if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
                reason = "attempts_exhausted"
                attempts = challenge.attempts
            else:
                # Count the attempt first so a crash or a wrong code can never grant a free retry.
                OTPChallenge.objects.filter(pk=challenge.pk).update(attempts=F("attempts") + 1)
                attempts = challenge.attempts + 1
                if hmac.compare_digest(challenge.code_hash, hash_code(challenge.id, code)):
                    succeeded = True
                    challenge.consumed_at = now
                    challenge.save(update_fields=["consumed_at", "updated_at"])
                    if voter.verified_at is None:
                        voter.verified_at = now
                        voter.save(update_fields=["verified_at", "updated_at"])
                    audit.log(
                        AuditAction.OTP_VERIFIED,
                        request=request,
                        actor_voter=voter,
                        target=challenge,
                        metadata={"phone_masked": mask_phone(phone_e164), "attempts": attempts},
                    )
                else:
                    reason = "invalid_code"
        if not succeeded:
            audit.log(
                AuditAction.OTP_FAILED,
                request=request,
                actor_voter=voter,
                metadata={
                    "phone_masked": mask_phone(phone_e164),
                    "reason": reason,
                    "attempts": attempts,
                },
            )
    # Raise only after the transaction committed, so the attempt counter and audit entry persist.
    if not succeeded:
        raise InvalidOTP()
    return voter
