"""Command line.

``albumproc page`` processes one page, ``albumproc album`` a whole album into
print images and a PDF, ``albumproc print`` turns one processed page into a
print image, and ``albumproc synth`` makes test photos.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import cv2


def _cmd_page(a) -> int:
    from .pipeline import PageOptions, process_page

    images = []
    for p in a.photos:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)  # honours EXIF orientation
        if img is None:
            print(f"cannot read {p}", file=sys.stderr)
            return 2
        images.append(img)
    res = process_page(images, PageOptions(max_side=a.max_side, focal_35mm=a.focal_35mm or None), debug_dir=a.debug)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.output), res.image)
    report = res.report.to_dict()
    report["inputs"] = [str(p) for p in a.photos]
    text = json.dumps(report, indent=2)
    if a.report:
        a.report.write_text(text + "\n")
    print(text)
    return 0


def _cmd_synth(a) -> int:
    from .synth import make_case

    case = make_case(a.seed, a.kind, a.n, a.gap)
    a.outdir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.outdir / "truth.png"), case.page)
    for i, img in enumerate(case.images):
        cv2.imwrite(str(a.outdir / f"shot_{i:02d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"wrote {len(case.images)} shots and truth.png to {a.outdir}")
    return 0


def _fill(text: str) -> tuple[int, int, int]:
    t = text.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", t):
        raise argparse.ArgumentTypeError(f"expected a colour like #ffffff, got {text!r}")
    return tuple(int(t[k : k + 2], 16) for k in (0, 2, 4))


def _size_arg(text: str):
    from .printsize import parse_page_size

    try:
        return parse_page_size(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _length_arg(text: str) -> float:
    from .printsize import parse_length

    try:
        return parse_length(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _print_options(a, fmt: str):
    from .printimage import PrintOptions

    return PrintOptions(
        dpi=a.dpi,
        fit=getattr(a, "fit", "auto"),
        rotate=getattr(a, "rotate", 0),
        bleed_in=a.bleed,
        fill=a.fill,
        format=fmt,
        quality=a.quality,
    )


def _cmd_album(a) -> int:
    from .album import AlbumOptions, PageFailed, process_album
    from .pipeline import PageOptions

    opt = AlbumOptions(
        page=PageOptions(max_side=a.max_side, focal_35mm=a.focal_35mm or None),
        print=_print_options(a, a.format),
        page_size=a.page_size,
        strict=a.strict,
        placeholder=a.placeholder,
        force=a.force,
        jobs=a.jobs,
        debug=a.debug,
    )
    try:
        opt.print.validate()
        report = process_album(a.album, a.output, opt, progress=lambda line: print(line, file=sys.stderr, flush=True))
    except ValueError as e:  # AlbumError, bad options
        print(f"error: {e}", file=sys.stderr)
        return 2
    except PageFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    pdf = a.output / report["pdf"] if report["pdf"] else "none"
    print(f"{report['pages_ok']} of {report['pages_found']} pages done, {report['pages_failed']} failed; PDF: {pdf} ({report['pdf_pages']} pages)")
    return 3 if report["pages_failed"] else 0


def _cmd_print(a) -> int:
    from .printimage import EXT_FORMATS, make_print_image, write_print_image

    fmt = a.format or EXT_FORMATS.get(a.output.suffix.lower())
    if fmt is None:
        print(f"error: cannot tell the format from {a.output.name}; pass --format", file=sys.stderr)
        return 2
    img = cv2.imread(str(a.image), cv2.IMREAD_COLOR)
    if img is None:
        print(f"error: cannot read {a.image}", file=sys.stderr)
        return 2
    opt = _print_options(a, fmt)
    try:
        rgb, info = make_print_image(img, a.page_size, opt)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    write_print_image(a.output, rgb, opt)
    for w in info.warnings:
        print(f"warning: {w.code}: {w.message}", file=sys.stderr)
    print(json.dumps({"output": str(a.output), **asdict(info)}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="albumproc")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("page", help="merge several photos of one page into a clean page image")
    p.add_argument("photos", nargs="+", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--report", type=Path, help="also write the JSON report here")
    p.add_argument("--debug", type=Path, help="write intermediate images to this folder")
    p.add_argument("--max-side", type=int, default=8000, help="cap on output long side (px)")
    p.add_argument("--focal-35mm", type=float, default=26.0, help="35 mm-equivalent focal length of the camera (0 = estimate)")
    p.set_defaults(func=_cmd_page)

    def print_args(q, page_size_required: bool):
        q.add_argument("--page-size", type=_size_arg, required=page_size_required, help="physical page size, e.g. 10x12in, 254x305mm, letter, a4" + ("" if page_size_required else " (default: album.json's, else letter)"))
        q.add_argument("--dpi", type=int, default=300, help="print resolution (72-1200)")
        q.add_argument("--quality", type=int, default=95, help="JPEG quality")
        q.add_argument("--bleed", type=_length_arg, default=0.0, help="bleed on each side, e.g. 0.125in or 3mm")
        q.add_argument("--fill", type=_fill, default=(255, 255, 255), help="padding colour, e.g. '#ffffff'")

    al = sub.add_parser("album", help="turn an album folder (one subfolder of photos per page) into print images and a PDF")
    al.add_argument("album", type=Path)
    al.add_argument("-o", "--output", type=Path, required=True, help="output folder")
    print_args(al, page_size_required=False)
    al.add_argument("--format", choices=["jpeg", "png", "tiff"], default="jpeg")
    al.add_argument("--max-side", type=int, default=8000, help="cap on processed page long side (px)")
    al.add_argument("--focal-35mm", type=float, default=26.0, help="35 mm-equivalent focal length of the camera (0 = estimate)")
    al.add_argument("--strict", action="store_true", help="stop at the first failed page and write no PDF")
    al.add_argument("--placeholder", action="store_true", help="put a blank 'missing' page in the PDF for each failed page")
    al.add_argument("--force", action="store_true", help="reprocess every page even if unchanged")
    al.add_argument("--jobs", type=int, default=1, help="pages processed in parallel")
    al.add_argument("--debug", action="store_true", help="write intermediate images under OUTPUT/debug/")
    al.set_defaults(func=_cmd_album)

    pr = sub.add_parser("print", help="turn one processed page image into a print image")
    pr.add_argument("image", type=Path)
    pr.add_argument("-o", "--output", type=Path, required=True)
    print_args(pr, page_size_required=True)
    pr.add_argument("--format", choices=["jpeg", "png", "tiff"], help="default: from the output extension")
    pr.add_argument("--fit", choices=["auto", "stretch", "fit", "fill"], default="auto")
    pr.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="clockwise, applied first")
    pr.set_defaults(func=_cmd_print)

    s = sub.add_parser("synth", help="generate synthetic test photos of a page")
    s.add_argument("outdir", type=Path)
    s.add_argument("--kind", choices=["glare", "stitch", "pair"], default="glare")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("-n", type=int, default=4)
    s.add_argument("--gap", type=float, default=0.03, help="pair: table between the pages, fraction of page width (0 = touching)")
    s.set_defaults(func=_cmd_synth)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
