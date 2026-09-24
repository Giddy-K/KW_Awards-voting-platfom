"""Plain-text sanitising for user-supplied strings (AUDIT F-28)."""

import re
import unicodedata

from django.utils.html import strip_tags

# C0/C1 control characters except tab and newline.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_INLINE_WS = re.compile(r"[ \t]+")


def clean_text(value, *, multiline=False):
    """Normalise to NFC, drop control characters and any HTML tags, tidy whitespace.

    The result is plain text. Output encoding is still the client's job, but nothing
    markup-like is stored.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = _CONTROL_CHARS.sub("", text)
    text = strip_tags(text)
    if multiline:
        lines = [_INLINE_WS.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    else:
        text = _INLINE_WS.sub(" ", text.replace("\n", " ").replace("\r", " "))
    return text.strip()
