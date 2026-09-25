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
