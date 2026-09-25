"""Image and CAPTCHA helpers for the tests."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def make_image_bytes(fmt="JPEG", size=(120, 80), color=(200, 30, 30), exif=False, mode="RGB"):
    """Return the encoded bytes of a small test image."""
    fill = color if mode == "RGB" else (*color, 128)
    image = Image.new(mode, size, fill)
    buffer = io.BytesIO()
    kwargs = {}
    if exif:
        data = Image.Exif()
        data[0x010F] = "SecretCameraMaker"  # Make
        data[0x0112] = 6  # Orientation: displayed rotated 90 degrees clockwise
        data[0x8825] = {1: "N", 2: (1.0, 2.0, 3.0)}  # GPS IFD
        kwargs["exif"] = data
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def photo_upload(name="me.jpg", content_type="image/jpeg", **kwargs):
    return SimpleUploadedFile(name, make_image_bytes(**kwargs), content_type=content_type)


class RejectingCaptcha:
    """CAPTCHA backend that always fails (select via settings.CAPTCHA_BACKEND)."""

    def verify(self, token, remote_ip=None):
        return False
