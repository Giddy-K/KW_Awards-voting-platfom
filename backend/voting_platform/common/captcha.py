"""CAPTCHA verification behind a small interface (Cloudflare Turnstile in production)."""

import logging

import requests
from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class CaptchaBackend:
    def verify(self, token, remote_ip=None):  # pragma: no cover - interface
        raise NotImplementedError


class DummyCaptchaBackend(CaptchaBackend):
    """Always passes. For local development and tests only (rejected in prod settings)."""

    def verify(self, token, remote_ip=None):
        return True


class TurnstileCaptchaBackend(CaptchaBackend):
    """Server-side verification against Cloudflare Turnstile. Fails closed."""

    def __init__(self, secret=None, timeout=5):
        self.secret = secret or settings.TURNSTILE_SECRET_KEY
        self.timeout = timeout

    def verify(self, token, remote_ip=None):
        if not token or not self.secret:
            return False
        payload = {"secret": self.secret, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip
        try:
            response = requests.post(TURNSTILE_VERIFY_URL, data=payload, timeout=self.timeout)
            response.raise_for_status()
            return bool(response.json().get("success"))
        except (requests.RequestException, ValueError):
            logger.exception("Turnstile verification request failed")
            return False


def get_captcha_backend():
    """Resolve ``settings.CAPTCHA_BACKEND``: ``auto`` | ``dummy`` | ``turnstile`` | dotted path."""
    name = getattr(settings, "CAPTCHA_BACKEND", "auto")
    if name == "auto":
        name = "turnstile" if settings.TURNSTILE_SECRET_KEY else "dummy"
    if name == "dummy":
        return DummyCaptchaBackend()
    if name == "turnstile":
        return TurnstileCaptchaBackend()
    return import_string(name)()


def verify_captcha(token, remote_ip=None):
    return get_captcha_backend().verify(token, remote_ip)
