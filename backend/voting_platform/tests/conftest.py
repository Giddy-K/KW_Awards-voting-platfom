import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from common.sms import LocMemSmsBackend


@pytest.fixture(autouse=True)
def _isolate_state(settings, tmp_path):
    """Fresh cache (throttles), SMS outbox and media directory for every test."""
    cache.clear()
    LocMemSmsBackend.outbox.clear()
    settings.MEDIA_ROOT = str(tmp_path / "media")
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def sms_outbox():
    return LocMemSmsBackend.outbox
