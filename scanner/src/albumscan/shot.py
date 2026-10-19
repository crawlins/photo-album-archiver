"""A scan made into a shot: turned by the placement, JPEG, with the scan marker in its EXIF."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import __version__

MARKER = f"albumscan {__version__}"  # albumproc.scanmark looks for the "albumscan " prefix
PLACEMENTS = (0, 90, 180, 270)
MAX_BYTES = 1024 * 1024 * 1024  # the server's default request limit
# The largest image the server and its page step take (albumproc.scanmark.MAX_PIXELS).
MAX_PIXELS = 1_000_000_000
Image.MAX_IMAGE_PIXELS = None  # our own scans, checked against MAX_PIXELS instead

_MAKE, _MODEL, _SOFTWARE, _DATETIME = 0x010F, 0x0110, 0x0131, 0x0132
_X_RES, _Y_RES, _RES_UNIT = 0x011A, 0x011B, 0x0128
_EXIF_IFD, _DATETIME_ORIGINAL, _OFFSET_TIME_ORIGINAL = 0x8769, 0x9003, 0x9011
# Clockwise turns, as Pillow transposes.
_TURN = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


@dataclass(frozen=True)
class ScanInfo:
    vendor: str
    model: str
    dpi: int
    ended: datetime  # aware, when the scan finished


class TooLarge(ValueError):
    pass


def encode(image: Image.Image, placement: int, info: ScanInfo, max_bytes: int = MAX_BYTES) -> bytes:
    """The shot's JPEG bytes: pixels turned clockwise by ``placement``, quality 95, no chroma subsampling."""
    if placement not in PLACEMENTS:
        raise ValueError(f"placement must be one of {PLACEMENTS}, not {placement}")
    w, h = image.size
    if w * h > MAX_PIXELS:
        raise TooLarge(f"the scan is {w * h / 1e6:.0f} megapixels, over the {MAX_PIXELS / 1e6:.0f} the server can process; scan at a lower resolution")
    im = image.convert("RGB")
    if placement:
        im = im.transpose(_TURN[placement])
    ended = info.ended.astimezone()
    stamp = ended.strftime("%Y:%m:%d %H:%M:%S")
    offset = ended.strftime("%z")
    exif = Image.Exif()
    exif[_SOFTWARE] = MARKER
    exif[_MAKE] = info.vendor
    exif[_MODEL] = info.model
    exif[_X_RES] = float(info.dpi)
    exif[_Y_RES] = float(info.dpi)
    exif[_RES_UNIT] = 2  # inches
    exif[_DATETIME] = stamp
    ifd = exif.get_ifd(_EXIF_IFD)
    ifd[_DATETIME_ORIGINAL] = stamp
    ifd[_OFFSET_TIME_ORIGINAL] = f"{offset[:3]}:{offset[3:]}"
    buf = BytesIO()
    im.save(buf, "JPEG", quality=95, subsampling=0, dpi=(info.dpi, info.dpi), exif=exif.tobytes())
    data = buf.getvalue()
    if len(data) > max_bytes:
        raise TooLarge(f"the scan is {len(data) / 2**20:.0f} MB, over the server's {max_bytes / 2**20:.0f} MB upload limit; scan at a lower resolution")
    return data


def encode_file(path: Path, placement: int, info: ScanInfo, max_bytes: int = MAX_BYTES) -> bytes:
    with Image.open(path) as im:
        return encode(im, placement, info, max_bytes)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
