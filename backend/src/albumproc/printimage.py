"""A processed page resampled to its physical size at a fixed DPI, ready to print.

The page pipeline gives the page's true shape but no scale. Here the user's
page size supplies the scale: the page is resampled once to exactly
``round(inches x dpi)`` pixels a side and written with its resolution and an
sRGB profile, so print services and layout tools read it at the right size.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .printsize import PageSize

FITS = ("auto", "stretch", "fit", "fill")
ROTATIONS = {0: None, 90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
FORMATS = {"jpeg": ".jpg", "png": ".png", "tiff": ".tif"}
EXT_FORMATS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".tif": "tiff", ".tiff": "tiff"}


@dataclass
class PrintOptions:
    dpi: int = 300
    fit: str = "auto"  # auto | stretch | fit | fill
    aspect_tolerance: float = 0.02  # relative aspect difference treated as measurement noise
    rotate: int = 0  # 0 | 90 | 180 | 270, clockwise, applied first
    bleed_in: float = 0.0
    fill: tuple[int, int, int] = (255, 255, 255)  # RGB, for padding and placeholders
    low_dpi: float = 150.0  # capture DPI below this gets a low_resolution warning
    format: str = "jpeg"  # jpeg | png | tiff
    quality: int = 95  # JPEG only

    def validate(self) -> None:
        if not 72 <= self.dpi <= 1200:
            raise ValueError(f"dpi must be between 72 and 1200, got {self.dpi}")
        if self.fit not in FITS:
            raise ValueError(f"fit must be one of {', '.join(FITS)}, got {self.fit!r}")
        if self.rotate not in ROTATIONS:
            raise ValueError(f"rotate must be 0, 90, 180 or 270, got {self.rotate!r}")
        if self.format not in FORMATS:
            raise ValueError(f"format must be one of {', '.join(FORMATS)}, got {self.format!r}")
        if self.bleed_in < 0:
            raise ValueError("bleed cannot be negative")
        if not 1 <= self.quality <= 100:
            raise ValueError(f"quality must be between 1 and 100, got {self.quality}")


@dataclass
class PageWarning:
    code: str  # low_resolution | upsampled | aspect_mismatch | incomplete_coverage | photos_dropped
    message: str


@dataclass
class PrintInfo:
    size_in: tuple[float, float]  # (w, h) trim size as oriented
    size_px: tuple[int, int]  # (w, h) including bleed
    bleed_px: int
    capture_dpi: float  # the processed page's own pixels per inch of the real page
    fit_used: str  # stretch | fit | fill
    aspect_page: float  # w / h of the processed page after rotation
    aspect_size: float  # w / h of the trim size
    warnings: list[PageWarning] = field(default_factory=list)


def make_print_image(page_bgr: np.ndarray, size: PageSize, opt: PrintOptions | None = None) -> tuple[np.ndarray, PrintInfo]:
    """Resample a processed page (uint8 BGR) to ``size`` at ``opt.dpi``; returns RGB."""
    opt = opt or PrintOptions()
    opt.validate()
    img = page_bgr if ROTATIONS[opt.rotate] is None else cv2.rotate(page_bgr, ROTATIONS[opt.rotate])
    h, w = img.shape[:2]
    # The size is an unordered pair: its long side goes with the page's long side.
    w_in, h_in = size.oriented(landscape=w >= h)
    aspect_page, aspect_size = w / h, w_in / h_in
    mismatch = abs(aspect_page / aspect_size - 1)
    fit = opt.fit
    if fit == "auto":
        fit = "stretch" if mismatch <= opt.aspect_tolerance else "fit"

    tw, th = round(w_in * opt.dpi), round(h_in * opt.dpi)
    capture_dpi = max(w, h) / size.long_in
    warnings = []
    if mismatch > opt.aspect_tolerance:
        warnings.append(
            PageWarning(
                "aspect_mismatch",
                f"page measures {aspect_page:.3f} (w/h) but the page size is {aspect_size:.3f}; "
                f"printed with fit={fit}. Check the page size, or whether the right page was found.",
            )
        )
    if capture_dpi < opt.dpi:
        warnings.append(PageWarning("upsampled", f"captured at {capture_dpi:.0f} dpi, upsampled to {opt.dpi} dpi"))
    if capture_dpi < opt.low_dpi:
        warnings.append(
            PageWarning("low_resolution", f"captured at only {capture_dpi:.0f} dpi; shoot closer or in overlapping parts")
        )

    fill_bgr = tuple(int(c) for c in reversed(opt.fill))
    if fit == "stretch":
        out = _resize(img, tw, th)
    elif fit == "fit":
        s = min(tw / w, th / h)
        nw, nh = min(tw, max(1, round(w * s))), min(th, max(1, round(h * s)))
        x0, y0 = (tw - nw) // 2, (th - nh) // 2
        out = cv2.copyMakeBorder(_resize(img, nw, nh), y0, th - nh - y0, x0, tw - nw - x0, cv2.BORDER_CONSTANT, value=fill_bgr)
    else:  # fill
        s = max(tw / w, th / h)
        nw, nh = max(tw, round(w * s)), max(th, round(h * s))
        x0, y0 = (nw - tw) // 2, (nh - th) // 2
        out = _resize(img, nw, nh)[y0 : y0 + th, x0 : x0 + tw]

    bleed_px = round(opt.bleed_in * opt.dpi)
    if bleed_px:
        out = cv2.copyMakeBorder(out, bleed_px, bleed_px, bleed_px, bleed_px, cv2.BORDER_REFLECT_101)
    out = np.ascontiguousarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
    info = PrintInfo(
        size_in=(round(w_in, 4), round(h_in, 4)),
        size_px=(out.shape[1], out.shape[0]),
        bleed_px=bleed_px,
        capture_dpi=round(capture_dpi, 1),
        fit_used=fit,
        aspect_page=round(aspect_page, 4),
        aspect_size=round(aspect_size, 4),
        warnings=warnings,
    )
    return out, info


def _resize(img: np.ndarray, w: int, h: int) -> np.ndarray:
    """One resampling step: area averaging when shrinking both ways, Lanczos otherwise."""
    ih, iw = img.shape[:2]
    if (w, h) == (iw, ih):
        return img
    interp = cv2.INTER_AREA if w < iw and h < ih else cv2.INTER_LANCZOS4
    return cv2.resize(img, (w, h), interpolation=interp)


def placeholder_image(size: PageSize, landscape: bool, label: str, opt: PrintOptions | None = None) -> np.ndarray:
    """A blank RGB page of ``size`` labelled as missing, to keep facing pages paired."""
    opt = opt or PrintOptions()
    w_in, h_in = size.oriented(landscape)
    b = round(opt.bleed_in * opt.dpi)
    w, h = round(w_in * opt.dpi) + 2 * b, round(h_in * opt.dpi) + 2 * b
    img = np.empty((h, w, 3), np.uint8)
    img[:] = opt.fill
    text = f"{label} - missing"
    scale = max(0.5, w / 1500)
    thick = max(1, round(scale * 2))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    cv2.putText(img, text, ((w - tw) // 2, (h + th) // 2), cv2.FONT_HERSHEY_SIMPLEX, scale, (128, 128, 128), thick, cv2.LINE_AA)
    return img


@lru_cache(maxsize=1)
def srgb_icc() -> bytes:
    """An sRGB ICC profile, with its creation date zeroed so outputs are reproducible."""
    from PIL import ImageCms

    icc = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    icc[24:36] = bytes(12)
    return bytes(icc)


def write_print_image(path: str | Path, image_rgb: np.ndarray, opt: PrintOptions | None = None) -> None:
    """Write an RGB print image with its DPI and an sRGB profile, atomically."""
    from PIL import Image

    opt = opt or PrintOptions()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.fromarray(image_rgb, "RGB")
    kw = {"dpi": (opt.dpi, opt.dpi), "icc_profile": srgb_icc()}
    if opt.format == "jpeg":
        kw.update(quality=opt.quality, subsampling=0)
    elif opt.format == "tiff":
        kw.update(compression="tiff_lzw")
    tmp = path.with_name(path.name + ".tmp")
    im.save(tmp, format=opt.format.upper(), **kw)
    os.replace(tmp, path)
