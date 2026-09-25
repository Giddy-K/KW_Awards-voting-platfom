"""SMS delivery behind an interface: console (dev), locmem (tests), Africa's Talking."""

import logging

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from common.phone import mask_phone

logger = logging.getLogger(__name__)

AT_LIVE_URL = "https://api.africastalking.com/version1/messaging"
AT_SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"


class SmsError(Exception):
    pass


class SmsBackend:
    def send(self, to_e164, body):  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSmsBackend(SmsBackend):
    """Prints the message (including any OTP) to stdout. Local development only."""

    def send(self, to_e164, body):
        line = f"[SMS console] to={mask_phone(to_e164)} body={body}"
        logger.info(line)
        print(line, flush=True)  # noqa: T201 - intentional dev output


class LocMemSmsBackend(SmsBackend):
    """Collects messages in ``LocMemSmsBackend.outbox`` (tests, same process only)."""

    outbox = []

    def send(self, to_e164, body):
        self.outbox.append({"to": to_e164, "body": body})


class E2EDatabaseSmsBackend(SmsBackend):
    """Persists sent messages to the database (``common.models.E2ESentMessage``).

    For ``settings.e2e`` only. An end-to-end test drives a real, separately running server
    process; a same-process outbox like ``LocMemSmsBackend`` can't bridge that, so the
    ``last_otp`` management command reads this table from its own process instead.
    """

    def send(self, to_e164, body):
        from common.models import E2ESentMessage

        E2ESentMessage.objects.create(phone_e164=to_e164, body=body)


class AfricasTalkingSmsBackend(SmsBackend):
    def __init__(self, username=None, api_key=None, sender_id=None, sandbox=None, timeout=10):
        self.username = username or settings.AFRICASTALKING_USERNAME
        self.api_key = api_key or settings.AFRICASTALKING_API_KEY
        self.sender_id = settings.AFRICASTALKING_SENDER_ID if sender_id is None else sender_id
        self.sandbox = settings.AFRICASTALKING_SANDBOX if sandbox is None else sandbox
        self.timeout = timeout

    def send(self, to_e164, body):
        data = {"username": self.username, "to": to_e164, "message": body}
        if self.sender_id:
            data["from"] = self.sender_id
        url = AT_SANDBOX_URL if self.sandbox else AT_LIVE_URL
        try:
            response = requests.post(
                url,
                data=data,
                headers={"apiKey": self.api_key, "Accept": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            recipients = response.json().get("SMSMessageData", {}).get("Recipients", [])
        except (requests.RequestException, ValueError) as exc:
            raise SmsError(f"SMS provider request failed for {mask_phone(to_e164)}") from exc
        if not recipients or recipients[0].get("statusCode") not in (100, 101, 102):
            raise SmsError(f"SMS provider rejected message for {mask_phone(to_e164)}")


def get_sms_backend():
    """Resolve ``settings.SMS_BACKEND``.

    ``auto`` | ``console`` | ``locmem`` | ``e2e`` | ``africastalking`` | dotted path.
    ``auto`` uses Africa's Talking only when its credentials are set in the environment.
    ``e2e`` is set only by ``settings.e2e``; dev and prod both refuse it at startup.
    """
    name = getattr(settings, "SMS_BACKEND", "auto")
    if name == "auto":
        has_credentials = settings.AFRICASTALKING_USERNAME and settings.AFRICASTALKING_API_KEY
        name = "africastalking" if has_credentials else "console"
    if name == "console":
        return ConsoleSmsBackend()
    if name == "locmem":
        return LocMemSmsBackend()
    if name == "e2e":
        return E2EDatabaseSmsBackend()
    if name == "africastalking":
        return AfricasTalkingSmsBackend()
    return import_string(name)()


def send_sms(to_e164, body):
    get_sms_backend().send(to_e164, body)
