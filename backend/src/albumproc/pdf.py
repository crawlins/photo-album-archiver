"""One PDF per album, built from the print image files without decoding them.

img2pdf embeds JPEG data as-is (no generation loss) and sizes each page from
the image's pixels and DPI, so the PDF prints at the page's physical size.
pikepdf then adds what img2pdf doesn't: the trim box when there is a bleed,
page labels, the title, and fixed dates for reproducible output.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PdfPage:
    image_path: Path
    dpi: int
    bleed_px: int
    label: str


def _pdf_date(epoch: int) -> str:
    return time.strftime("D:%Y%m%d%H%M%SZ", time.gmtime(epoch))


def write_album_pdf(pages: list[PdfPage], path: str | Path, title: str, epoch: int | None = None) -> int:
    """Write ``pages`` in order to ``path``; returns the page count.

    ``epoch`` (seconds) fixes the creation and modification dates; with it,
    identical inputs give a byte-identical file.
    """
    if not pages:
        raise ValueError("no pages to write")
    dpis = {p.dpi for p in pages}
    if len(dpis) != 1:
        raise ValueError(f"all pages must share one DPI, got {sorted(dpis)}")
    dpi = dpis.pop()

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    raw = path.with_name(path.name + ".img2pdf.tmp")
    try:
        n = _write(pages, dpi, raw, tmp, title, epoch)
        os.replace(tmp, path)
    finally:
        # Neither temporary file outlives the call, whether it succeeded or not.
        raw.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return n


def _write(pages: list[PdfPage], dpi: int, raw: Path, tmp: Path, title: str, epoch: int | None) -> int:
    import img2pdf
    import pikepdf

    with open(raw, "wb") as f:
        img2pdf.convert(
            [str(p.image_path) for p in pages],
            layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi)),
            nodate=True,
            outputstream=f,
        )
    with pikepdf.open(raw) as pdf:
        nums = []
        for i, (page, spec) in enumerate(zip(pdf.pages, pages)):
            if spec.bleed_px:
                x0, y0, x1, y1 = (float(v) for v in page.MediaBox)
                b = spec.bleed_px / spec.dpi * 72
                page.TrimBox = pikepdf.Array([x0 + b, y0 + b, x1 - b, y1 - b])
                page.BleedBox = pikepdf.Array([x0, y0, x1, y1])
            nums += [i, pikepdf.Dictionary(P=pikepdf.String(spec.label))]
        pdf.Root.PageLabels = pikepdf.Dictionary(Nums=pikepdf.Array(nums))
        date = _pdf_date(int(time.time()) if epoch is None else epoch)
        pdf.docinfo["/Title"] = pikepdf.String(title)
        pdf.docinfo["/CreationDate"] = pikepdf.String(date)
        pdf.docinfo["/ModDate"] = pikepdf.String(date)
        if epoch is not None:
            # img2pdf's file ID is random; drop it so both halves derive from the content.
            del pdf.trailer.ID
        pdf.save(tmp, deterministic_id=epoch is not None)
        return len(pdf.pages)
