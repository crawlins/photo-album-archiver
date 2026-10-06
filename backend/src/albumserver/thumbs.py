"""Pillow helpers: checking an upload is a JPEG, reading when it was taken, and previews."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

from .files import write_atomic

THUMB_SIZE = 320
THUMB_QUALITY = 80
_EXIF_IFD = 0x8769
_DATETIME_ORIGINAL = 0x9003
_OFFSET_TIME_ORIGINAL = 0x9011


def is_jpeg(path: Path) -> bool:
    """True when the file starts with the JPEG SOI marker and Pillow accepts it as a JPEG."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return False
    try:
        with Image.open(path) as im:
            if im.format != "JPEG":
                return False
            im.verify()
    except Exception:
        return False
    return True


def exif_taken(path: Path) -> datetime | None:
    """EXIF ``DateTimeOriginal``, in UTC.

    Its offset comes from ``OffsetTimeOriginal`` when the camera wrote one,
    else the photo is taken to be in this machine's local time, which is
    right for a phone and server in the same house.
    """
    try:
        with Image.open(path) as im:
            ifd = im.getexif().get_ifd(_EXIF_IFD)
    except Exception:
        return None
    raw = ifd.get(_DATETIME_ORIGINAL)
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.strptime(raw.strip().rstrip("\x00"), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    offset = ifd.get(_OFFSET_TIME_ORIGINAL)
    if isinstance(offset, str):
        try:
            sign = -1 if offset.strip().startswith("-") else 1
            hh, mm = offset.strip().lstrip("+-").split(":")
            return dt.replace(tzinfo=timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))).astimezone(timezone.utc)
        except ValueError:
            pass
    return dt.astimezone(timezone.utc)  # naive: local time


def make_thumb(src: Path, dst: Path) -> None:
    """A JPEG at most 320 px on its long side, turned the right way up by its EXIF orientation."""
    with Image.open(src) as im:
        im.draft("RGB", (THUMB_SIZE, THUMB_SIZE))  # decode at reduced scale: much faster for 12 MP
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=THUMB_QUALITY)
    write_atomic(dst, buf.getvalue())
