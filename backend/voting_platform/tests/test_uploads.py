"""Nominee photo uploads: type, size, EXIF stripping, resizing, file names (AUDIT F-27)."""

import io
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from nominations.images import InvalidImage, process_image
from nominations.models import Nominee

from .media_helpers import make_image_bytes, photo_upload
from .test_nominations import open_award, submit

pytestmark = pytest.mark.django_db


def upload(data, name="x.jpg", content_type="image/jpeg"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def reopen(content_file):
    return Image.open(io.BytesIO(content_file.read()))


@pytest.mark.parametrize("fmt,expected", [("JPEG", "JPEG"), ("PNG", "PNG"), ("WEBP", "WEBP")])
def test_allowed_formats_are_accepted_and_reencoded(fmt, expected):
    result = process_image(upload(make_image_bytes(fmt=fmt)))
    assert reopen(result).format == expected


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "TIFF"])
def test_other_image_formats_are_rejected(fmt):
    with pytest.raises(InvalidImage):
        process_image(upload(make_image_bytes(fmt=fmt)))


def test_content_not_extension_decides_the_type():
    """A GIF renamed .jpg is still a GIF; a JPEG named .txt is fine."""
    with pytest.raises(InvalidImage):
        process_image(
            upload(make_image_bytes(fmt="GIF"), name="fake.jpg", content_type="image/jpeg")
        )
    process_image(upload(make_image_bytes(fmt="JPEG"), name="photo.txt", content_type="text/plain"))


@pytest.mark.parametrize(
    "data",
    [
        b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
        b"%PDF-1.4 fake pdf",
        b"just some text",
        b"",
        b"\xff\xd8\xff\xe0 truncated jpeg header only",
    ],
)
def test_non_images_and_corrupt_files_are_rejected(data):
    with pytest.raises(InvalidImage):
        process_image(upload(data, name="evil.jpg"))


def test_oversize_files_are_rejected(settings):
    settings.MAX_UPLOAD_BYTES = 500
    with pytest.raises(InvalidImage, match="larger"):
        process_image(upload(make_image_bytes(size=(400, 400))))


def test_the_default_limit_is_five_megabytes(settings):
    assert settings.MAX_UPLOAD_BYTES == 5 * 1024 * 1024


def test_pixel_bombs_are_rejected_before_decoding(settings):
    settings.IMAGE_MAX_PIXELS = 10_000
    with pytest.raises(InvalidImage, match="dimensions"):
        process_image(upload(make_image_bytes(size=(200, 200))))


def test_exif_and_gps_metadata_are_stripped_and_orientation_applied():
    original = make_image_bytes(fmt="JPEG", size=(120, 80), exif=True)
    assert Image.open(io.BytesIO(original)).getexif().get(0x010F) == "SecretCameraMaker"
    result = reopen(process_image(upload(original)))
    exif = result.getexif()
    assert len(exif) == 0 and 0x010F not in exif and 0x8825 not in exif
    # Orientation 6 was applied to the pixels: the 120x80 image is now 80x120.
    assert result.size == (80, 120)


def test_stored_bytes_contain_no_metadata_markers():
    original = make_image_bytes(fmt="JPEG", exif=True)
    stored = process_image(upload(original)).read()
    assert b"SecretCameraMaker" not in stored and b"Exif" not in stored


def test_large_images_are_resized_down(settings):
    settings.IMAGE_MAX_DIMENSION = 100
    result = reopen(process_image(upload(make_image_bytes(size=(400, 200)))))
    assert max(result.size) == 100 and result.size == (100, 50)


def test_small_images_are_not_upscaled():
    assert reopen(process_image(upload(make_image_bytes(size=(60, 40))))).size == (60, 40)


def test_png_transparency_survives_and_jpeg_output_is_rgb():
    png = reopen(process_image(upload(make_image_bytes(fmt="PNG", mode="RGBA"), "a.png")))
    assert png.mode == "RGBA"
    jpeg = reopen(process_image(upload(make_image_bytes(fmt="JPEG"))))
    assert jpeg.mode == "RGB"


def test_animated_webp_keeps_only_the_first_frame():
    frames = [Image.new("RGB", (40, 40), c) for c in ((255, 0, 0), (0, 255, 0))]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="WEBP", save_all=True, append_images=frames[1:], duration=50)
    result = reopen(process_image(upload(buffer.getvalue(), "a.webp", "image/webp")))
    assert getattr(result, "n_frames", 1) == 1


def test_output_file_names_are_random_uuids_not_client_names():
    a = process_image(upload(make_image_bytes(), name="../../etc/passwd.jpg"))
    b = process_image(upload(make_image_bytes(), name="../../etc/passwd.jpg"))
    assert a.name != b.name
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", a.name)


def test_api_rejects_bad_uploads_with_a_field_error(api):
    award = open_award()
    for bad in (
        SimpleUploadedFile("a.jpg", b"not an image", content_type="image/jpeg"),
        SimpleUploadedFile("a.gif", make_image_bytes(fmt="GIF"), content_type="image/gif"),
    ):
        response = submit(api, award, photo=bad)
        assert response.status_code == 400 and "photo" in response.data, response.data
    assert Nominee.objects.count() == 0


def test_api_stores_a_sanitised_uuid_named_file_and_strips_exif(api):
    award = open_award()
    response = submit(api, award, photo=photo_upload("../../evil name.jpg", exif=True))
    assert response.status_code == 201, response.data
    nominee = Nominee.objects.get()
    assert re.fullmatch(r"nominees/[0-9a-f]{32}\.jpg", nominee.photo.name)
    stored = Image.open(nominee.photo.path)
    assert len(stored.getexif()) == 0


def test_api_rejects_oversized_uploads(api, settings):
    settings.MAX_UPLOAD_BYTES = 300
    award = open_award()
    response = submit(api, award, photo=photo_upload(size=(300, 300)))
    assert response.status_code == 400 and "photo" in response.data
