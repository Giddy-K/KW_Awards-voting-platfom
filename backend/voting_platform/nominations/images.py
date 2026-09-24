"""Upload hardening for nominee photos (AUDIT F-27).

The client's file name, declared content type and metadata are all ignored. The bytes are
decoded with Pillow, validated by *content*, and re-encoded from raw pixels, which removes
EXIF/GPS/ICC and any trailing or polyglot payload. The result gets a random file name.
"""

import io
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

# Pillow format name -> stored file extension. Everything else (GIF, SVG, BMP, TIFF, ...) is refused.
ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


class InvalidImage(ValueError):
    """The upload is not an acceptable image; the message is safe to show to the client."""


def _has_alpha(image):
    return image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)


def _normalise_mode(image, output_format):
    if output_format == "JPEG":
        if _has_alpha(image):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB") if image.mode != "RGB" else image
    if image.mode in ("RGB", "RGBA", "L"):
        return image
    return image.convert("RGBA" if _has_alpha(image) else "RGB")


def process_image(uploaded):
    """Validate and re-encode an uploaded photo. Returns a ``ContentFile`` named ``<uuid>.<ext>``."""
    max_bytes = settings.MAX_UPLOAD_BYTES
    uploaded.seek(0)
    data = uploaded.read(max_bytes + 1)
    if not data:
        raise InvalidImage("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise InvalidImage(
            f"The file is larger than the {max_bytes // (1024 * 1024) or 1} MB limit."
        )

    try:
        probe = Image.open(io.BytesIO(data))
        output_format = probe.format
        if output_format not in ALLOWED_FORMATS:
            raise InvalidImage("Unsupported image type. Upload a JPEG, PNG or WebP image.")
        if probe.width * probe.height > settings.IMAGE_MAX_PIXELS:
            raise InvalidImage("Image dimensions are too large.")
        probe.verify()
        image = Image.open(io.BytesIO(data))
        image.load()  # decodes fully (first frame only for animated files); catches truncation
    except InvalidImage:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise InvalidImage("The file is not a valid image.") from exc

    image = ImageOps.exif_transpose(image)  # bake the EXIF orientation into the pixels
    image = _normalise_mode(image, output_format)
    # Rebuild from raw pixels: nothing from the original container (metadata, profiles,
    # extra frames, appended data) can survive this.
    clean = Image.frombytes(image.mode, image.size, image.tobytes())
    limit = settings.IMAGE_MAX_DIMENSION
    clean.thumbnail((limit, limit), Image.Resampling.LANCZOS)  # only ever shrinks

    buffer = io.BytesIO()
    if output_format == "JPEG":
        clean.save(buffer, "JPEG", quality=85, optimize=True, progressive=True)
    elif output_format == "PNG":
        clean.save(buffer, "PNG", optimize=True)
    else:
        clean.save(buffer, "WEBP", quality=85, method=4)
    return ContentFile(
        buffer.getvalue(), name=f"{uuid.uuid4().hex}.{ALLOWED_FORMATS[output_format]}"
    )
