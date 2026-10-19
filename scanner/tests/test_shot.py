from datetime import datetime, timezone
from io import BytesIO

import pytest
from PIL import Image

from albumscan import __version__
from albumscan.shot import MARKER, ScanInfo, TooLarge, encode, encode_file, sha256

INFO = ScanInfo("Canon", "LiDE 220", 600, datetime(2026, 10, 19, 18, 30, 5, tzinfo=timezone.utc))


def _image():
    """A 40 x 20 image: red on the left half, blue on the right."""
    im = Image.new("RGB", (40, 20), (255, 0, 0))
    im.paste((0, 0, 255), (20, 0, 40, 20))
    return im


def _open(data):
    return Image.open(BytesIO(data))


def test_marker_and_tags():
    im = _open(encode(_image(), 0, INFO))
    exif = im.getexif()
    assert MARKER == f"albumscan {__version__}" and exif[0x0131] == MARKER
    assert (exif[0x010F], exif[0x0110]) == ("Canon", "LiDE 220")
    assert float(exif[0x011A]) == 600 and float(exif[0x011B]) == 600 and exif[0x0128] == 2
    assert 0x0112 not in exif  # no orientation: the pixels are the right way up
    ifd = exif.get_ifd(0x8769)
    local = INFO.ended.astimezone()
    assert ifd[0x9003] == local.strftime("%Y:%m:%d %H:%M:%S")
    off = local.strftime("%z")
    assert ifd[0x9011] == f"{off[:3]}:{off[3:]}"
    assert im.format == "JPEG" and im.info.get("progressive") is None


def test_no_chroma_subsampling():
    from PIL import JpegImagePlugin

    assert JpegImagePlugin.get_sampling(_open(encode(_image(), 0, INFO))) == 0  # 4:4:4


@pytest.mark.parametrize(
    "placement, size, top_left_red",
    # Turning clockwise brings the left (red) half to the top, and anticlockwise the right (blue) one.
    [(0, (40, 20), True), (90, (20, 40), True), (180, (40, 20), False), (270, (20, 40), False)],
)
def test_placement_turns_clockwise(placement, size, top_left_red):
    im = _open(encode(_image(), placement, INFO)).convert("RGB")
    assert im.size == size
    r, _, b = im.getpixel((2, 2))
    assert (r > b) == top_left_red
    r, _, b = im.getpixel((size[0] - 3, size[1] - 3))
    assert (r > b) != top_left_red


def test_bad_placement():
    with pytest.raises(ValueError):
        encode(_image(), 45, INFO)


def test_too_large_is_refused():
    with pytest.raises(TooLarge, match="lower resolution"):
        encode(_image(), 0, INFO, max_bytes=100)


def test_too_many_pixels_is_refused(monkeypatch):
    from albumscan import shot

    monkeypatch.setattr(shot, "MAX_PIXELS", 799)
    with pytest.raises(TooLarge, match="megapixels"):
        encode(_image(), 0, INFO)


def test_encode_file_and_sha(tmp_path):
    p = tmp_path / "s.pnm"
    _image().save(p)
    data = encode_file(p, 0, INFO)
    assert len(sha256(data)) == 64 and data[:2] == b"\xff\xd8"
