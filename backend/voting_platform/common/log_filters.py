"""Logging helpers."""

import logging
import re

# 9-15 digit runs, optionally prefixed with '+': phone numbers in any common format.
_PHONE_RE = re.compile(r"(?<![\w.])\+?\d{9,15}(?![\w.])")


def _mask_match(match):
    text = match.group(0)
    if len(text) <= 7:
        return "*" * len(text)
    return text[:5] + "*" * (len(text) - 7) + text[-2:]


def mask_phones_in(text):
    return _PHONE_RE.sub(_mask_match, text)


class PhoneMaskFilter(logging.Filter):
    """Masks anything that looks like a phone number in log records (AUDIT: privacy)."""

    def filter(self, record):
        record.msg = mask_phones_in(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask(v) for k, v in record.args.items()}
            else:
                record.args = tuple(self._mask(a) for a in record.args)
        return True

    @staticmethod
    def _mask(value):
        return mask_phones_in(value) if isinstance(value, str) else value
