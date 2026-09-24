"""Phone number normalization and masking."""

import phonenumbers
from phonenumbers import PhoneNumberType

DEFAULT_REGION = "KE"
_MAX_RAW_LENGTH = 32
_ALLOWED_TYPES = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(raw, region=DEFAULT_REGION):
    """Return the E.164 form of a mobile number (default region Kenya) or raise."""
    if not isinstance(raw, str) or not raw.strip() or len(raw) > _MAX_RAW_LENGTH:
        raise InvalidPhoneNumber("Enter a valid mobile phone number.")
    try:
        number = phonenumbers.parse(raw.strip(), region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidPhoneNumber("Enter a valid mobile phone number.") from exc
    if not phonenumbers.is_valid_number(number):
        raise InvalidPhoneNumber("Enter a valid mobile phone number.")
    if phonenumbers.number_type(number) not in _ALLOWED_TYPES:
        raise InvalidPhoneNumber("Enter a valid mobile phone number.")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def mask_phone(value):
    """``+254712345612`` -> ``+2547******12``. Safe on empty/short input."""
    if not value:
        return ""
    value = str(value)
    if len(value) <= 7:
        return "*" * len(value)
    return value[:5] + "*" * (len(value) - 7) + value[-2:]
