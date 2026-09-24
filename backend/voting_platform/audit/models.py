from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import models

from common.models import BaseModel


class AuditAction(models.TextChoices):
    NOMINATION_SUBMITTED = "nomination.submitted", "Nomination submitted"
    NOMINATION_APPROVED = "nomination.approved", "Nomination approved"
    NOMINATION_REJECTED = "nomination.rejected", "Nomination rejected"
    VOTE_CAST = "vote.cast", "Vote cast"
    VOTE_DUPLICATE = "vote.duplicate", "Duplicate vote rejected"
    VOTE_VOIDED = "vote.voided", "Vote voided"
    OTP_REQUESTED = "otp.requested", "OTP requested"
    OTP_VERIFIED = "otp.verified", "OTP verified"
    OTP_FAILED = "otp.failed", "OTP verification failed"
    EVENT_STATUS_CHANGED = "event.status_changed", "Event status changed"
    STAFF_LOGIN = "staff.login", "Staff login"
    STAFF_LOGIN_FAILED = "staff.login_failed", "Staff login failed"
    PERMISSION_DENIED = "permission.denied", "Permission denied"


class AppendOnlyError(PermissionDenied):
    pass


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit log entries cannot be modified.")

    def delete(self):
        raise AppendOnlyError("Audit log entries cannot be deleted.")


class AuditLog(BaseModel):
    """Append-only record of security- and integrity-relevant events.

    ``metadata`` must never contain OTP codes, full phone numbers or passwords; phone
    numbers must be masked with ``common.phone.mask_phone`` before being logged.
    PostgreSQL triggers (see the migration) additionally reject UPDATE and DELETE.
    """

    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        # PROTECT (not SET_NULL): the table is append-only, so a referenced user must be
        # deactivated rather than deleted.
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    actor_voter = models.ForeignKey(
        "voting.Voter",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=40, choices=AuditAction.choices)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.target_type}:{self.target_id}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Audit log entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit log entries cannot be deleted.")
