"""Print the latest OTP code sent to a phone number. End-to-end test support only."""

import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from common.phone import InvalidPhoneNumber, normalize_phone

CODE_RE = re.compile(r"\b(\d{6})\b")


class Command(BaseCommand):
    help = (
        "Print the most recent OTP code sent to a phone number, for driving an end-to-end "
        "test from an external runner. Only works when SMS_BACKEND=e2e "
        "(voting_platform.settings.e2e); refuses under any other settings module."
    )

    def add_arguments(self, parser):
        parser.add_argument("phone", help="Phone number, any format common.phone accepts.")

    def handle(self, *args, **options):
        if getattr(settings, "SMS_BACKEND", None) != "e2e":
            raise CommandError(
                "last_otp only works with SMS_BACKEND=e2e (voting_platform.settings.e2e). "
                f"Current SMS_BACKEND is {getattr(settings, 'SMS_BACKEND', None)!r}."
            )
        # Import lazily: the model only needs to exist for settings.e2e, and importing it
        # eagerly at module load time would run even when the command refuses to proceed.
        from common.models import E2ESentMessage

        try:
            phone = normalize_phone(options["phone"])
        except InvalidPhoneNumber as exc:
            raise CommandError(str(exc)) from exc

        message = E2ESentMessage.objects.filter(phone_e164=phone).order_by("-id").first()
        if message is None:
            raise CommandError(f"No SMS has been sent to {phone} yet.")

        match = CODE_RE.search(message.body)
        if not match:
            raise CommandError(f"Latest message to {phone} has no 6-digit code: {message.body!r}")

        self.stdout.write(match.group(1))
