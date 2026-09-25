import uuid

from django.db import models


class BaseModel(models.Model):
    """Abstract base: UUID primary key named ``id`` plus created/updated timestamps.

    Timestamps are stored in UTC (``USE_TZ=True``); ``TIME_ZONE`` only affects display.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class E2ESentMessage(models.Model):
    """Every "SMS" sent by ``E2EDatabaseSmsBackend`` (see ``common.sms``).

    Exists so the ``last_otp`` management command can retrieve a code from a *different
    process* than the one that requested it: an end-to-end test drives a real, separately
    running dev server (e.g. via Playwright), so an in-memory outbox (``LocMemSmsBackend``)
    can't bridge the two. This table is only ever written to when
    ``SMS_BACKEND="e2e"`` (``settings.e2e`` only; dev and prod both refuse that value), so in
    every other environment it stays empty. Phone numbers here are not masked, unlike
    everywhere else in the app: this table only ever holds e2e test fixture numbers, since
    the backend that writes it can't be enabled where real voters exist.

    Deliberately not a ``BaseModel``: nothing references this row by id, so a plain
    auto-incrementing integer primary key is used instead of the app's usual UUID. Ordering
    by that (rather than ``created_at``) is what makes "most recent" reliable even when two
    messages are sent within the same timestamp tick.
    """

    phone_e164 = models.CharField(max_length=16, db_index=True)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"e2e SMS to {self.phone_e164} at {self.created_at:%Y-%m-%d %H:%M:%S}"
