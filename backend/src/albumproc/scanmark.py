"""Whether photos are flatbed scans, from the EXIF ``albumscan`` writes into them.

The scanner app (``scanner/``) marks every shot it makes with ``Software =
"albumscan <version>"`` and the scan resolution. A page whose photos all
carry the mark is processed in scan mode (see ``PageOptions.source``).
"""

from __future__ import annotations

import warnings
from pathlib import Path

from PIL import Image

MARKER_PREFIX = "albumscan "
# The largest photo the page step takes, in pixels: a whole-glass colour scan
# at 2400 dpi is about 570 million. OpenCV's own reading limit is 2**30.
MAX_PIXELS = 1_000_000_000

# Pillow refuses images over twice this (as decompression bombs) and only
# warns between once and twice; scans this big are expected, not an attack.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS // 2
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)

_SOFTWARE = 0x0131
_X_RESOLUTION = 0x011A
_RESOLUTION_UNIT = 0x0128


def _exif(path: str | Path):
    try:
        with Image.open(path) as im:
            return im.getexif()
    except Exception:  # unreadable, or over the pixel limit (DecompressionBombError)
        return None


def is_scan(path: str | Path) -> bool:
    """True when the file's EXIF ``Software`` carries the scan marker; unreadable EXIF is not a scan."""
    exif = _exif(path)
    sw = exif.get(_SOFTWARE) if exif is not None else None
    return isinstance(sw, str) and sw.startswith(MARKER_PREFIX)


def all_scans(paths: list[Path]) -> bool:
    """True when there is at least one photo and every one is a scan."""
    return bool(paths) and all(is_scan(p) for p in paths)


def scan_dpi(paths: list[Path]) -> float | None:
    """The scans' common horizontal resolution in dots per inch, or None when unknown or not common."""
    dpis = set()
    for p in paths:
        exif = _exif(p)
        if exif is None or _X_RESOLUTION not in exif:
            return None
        dpi = float(exif[_X_RESOLUTION])
        if exif.get(_RESOLUTION_UNIT, 2) == 3:  # centimetres
            dpi *= 2.54
        dpis.add(round(dpi, 2))
    return dpis.pop() if len(dpis) == 1 else None


def page_source(paths: list[Path]) -> tuple[str, float | None]:
    """``("scan", dpi)`` when every photo of a page is a scan, else ``("photo", None)``."""
    paths = [Path(p) for p in paths]
    return ("scan", scan_dpi(paths)) if all_scans(paths) else ("photo", None)
