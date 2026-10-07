"""Command line.

``albumproc page`` processes one page, ``albumproc album`` a whole album into
print images and a PDF, ``albumproc print`` turns one processed page into a
print image, ``albumproc glare`` checks single photos for glare,
``albumproc glare-eval`` scores a page's glare map against a hand-drawn mask,
``albumproc straightness`` measures how straight long lines in a composed
page are, and ``albumproc synth`` makes test photos.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path

import cv2


def _write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file renamed into place."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_image(path: Path, img) -> None:
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if not ok:
        raise OSError(f"cannot encode {path.name}")
    _write_atomic(path, buf.tobytes())


def _curvature_params(a):
    from .curvature import CurvatureParams

    return CurvatureParams(mode=a.curvature, binding=a.binding)


def _cmd_page(a) -> int:
    from .pipeline import PageOptions, process_page
    from .regions import QualityParams

    images = []
    for p in a.photos:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)  # honours EXIF orientation
        if img is None:
            print(f"cannot read {p}", file=sys.stderr)
            return 2
        images.append(img)
    opt = PageOptions(
        max_side=a.max_side,
        focal_35mm=a.focal_35mm or None,
        quality=QualityParams(residual_threshold=a.glare_threshold, min_region_area=a.min_region),
        curvature=_curvature_params(a),
    )
    res = process_page(images, opt, debug_dir=a.debug)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    report = res.report.to_dict()
    report["inputs"] = [str(p) for p in a.photos]
    text = json.dumps(report, indent=2)
    meta = a.report or a.output.with_suffix(".json")
    try:
        _write_image(a.output, res.image)  # the image first: metadata never describes a missing image
        if a.masks:
            h, w = res.image.shape[:2]
            glare = cv2.resize(res.residual_glare, (w, h), interpolation=cv2.INTER_LINEAR)
            _write_image(a.output.with_suffix(".glare.png"), (glare * 255 + 0.5).clip(0, 255).astype("uint8"))
            _write_image(a.output.with_suffix(".uncovered.png"), (~res.coverage).astype("uint8") * 255)
        meta.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(meta, (text + "\n").encode())
    except OSError as e:
        print(f"cannot write {getattr(e, 'filename', None) or meta}: {e}", file=sys.stderr)
        return 2
    print(text)
    return 0


def _cmd_glare(a) -> int:
    from .glare import detect_glare
    from .regions import QualityParams, find_regions

    q = QualityParams()
    images = []
    for p in a.photos:
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            print(f"cannot read {p}", file=sys.stderr)
            return 2
        images.append(img)
    out = []
    for p, img in zip(a.photos, images):
        g = detect_glare(img).glare
        h, w = img.shape[:2]
        found = find_regions(g >= q.residual_threshold, (w, h), q.min_region_area)
        regions = []
        for r, m in found:
            r.severity, r.edges = round(float(g[m].mean()), 4), None
            regions.append(r.to_dict())
        out.append({"photo": str(p), "glare_fraction": round(float((g >= q.residual_threshold).mean()), 4), "regions": regions})
        if a.overlay:
            from .pipeline import quality_overlay

            a.overlay.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(a.overlay / f"{p.stem}.glare.jpg"), quality_overlay(img, regions, []))
    print(json.dumps(out, indent=2))
    return 0


def _cmd_glare_eval(a) -> int:
    meta = json.loads(a.metadata.read_text())
    threshold = meta.get("detector", {}).get("quality", {}).get("residual_threshold", 0.5)
    stem = a.metadata.name[: -len(a.metadata.suffix)] if a.metadata.suffix else a.metadata.name
    mask_path = (a.masks_dir or a.metadata.parent) / f"{stem}.glare.png"
    pred = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
    if pred is None:
        print(f"cannot read {mask_path} (write it with albumproc page --masks)", file=sys.stderr)
        return 2
    truth = cv2.imread(str(a.truth), cv2.IMREAD_GRAYSCALE)
    if truth is None:
        print(f"cannot read {a.truth}", file=sys.stderr)
        return 2
    if truth.shape != pred.shape:
        print(f"resizing {a.truth.name} from {truth.shape[1]}x{truth.shape[0]} to {pred.shape[1]}x{pred.shape[0]}", file=sys.stderr)
        truth = cv2.resize(truth, (pred.shape[1], pred.shape[0]), interpolation=cv2.INTER_NEAREST)
    p, t = pred >= threshold * 255, truth > 127
    scores = {
        "recall": round(float((p & t).sum() / max(t.sum(), 1)), 4),
        "false_positive_share": round(float((p & ~t).sum() / max((~t).sum(), 1)), 4),
        "iou": round(float((p & t).sum() / max((p | t).sum(), 1)), 4),
    }
    print(json.dumps(scores, indent=2))
    return 0


def _cmd_straightness(a) -> int:
    from .straightness import measure, overlay

    img = cv2.imread(str(a.image), cv2.IMREAD_COLOR)
    if img is None:
        print(f"cannot read {a.image}", file=sys.stderr)
        return 2
    result = measure(img, a.min_len)
    if a.overlay:
        a.overlay.parent.mkdir(parents=True, exist_ok=True)
        _write_image(a.overlay, overlay(img, result))
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _cmd_synth(a) -> int:
    from .synth import make_case

    case = make_case(a.seed, a.kind, a.n, a.gap, glare_style=a.glare_style, lift=a.lift, strip=a.strip, binding=a.binding)
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
        page=PageOptions(max_side=a.max_side, focal_35mm=a.focal_35mm or None, curvature=_curvature_params(a)),
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

    def curvature_args(q):
        q.add_argument("--curvature", choices=["auto", "off", "force"], default="auto", help="flatten pages bent near their binding: when they look bent (auto), never, or always")
        q.add_argument("--binding", choices=["auto", "left", "right", "top", "bottom"], default="auto", help="the page side nearest the album's spine (default: found from the photos)")

    p = sub.add_parser("page", help="merge several photos of one page into a clean page image")
    p.add_argument("photos", nargs="+", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--report", type=Path, help="write the page metadata (JSON) here instead of next to the image")
    p.add_argument("--masks", action="store_true", help="also write PAGE.glare.png and PAGE.uncovered.png")
    p.add_argument("--debug", type=Path, help="write intermediate images to this folder")
    p.add_argument("--glare-threshold", type=float, default=0.5, help="residual glare counted as glare (0-1)")
    p.add_argument("--min-region", type=float, default=0.0005, help="smallest region reported, as a fraction of the page")
    p.add_argument("--max-side", type=int, default=8000, help="cap on output long side (px)")
    p.add_argument("--focal-35mm", type=float, default=26.0, help="35 mm-equivalent focal length of the camera (0 = estimate)")
    curvature_args(p)
    p.set_defaults(func=_cmd_page)

    def print_args(q, page_size_required: bool):
        q.add_argument("--page-size", type=_size_arg, required=page_size_required, help="physical page size, e.g. 10x12in, 254x305mm, letter, a4" + ("" if page_size_required else " (default: album.json's, else letter)"))
        q.add_argument("--dpi", type=int, default=300, help="print resolution (72-1200)")
        q.add_argument("--quality", type=int, default=95, help="JPEG quality (1-100)")
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
    curvature_args(al)
    al.set_defaults(func=_cmd_album)

    pr = sub.add_parser("print", help="turn one processed page image into a print image")
    pr.add_argument("image", type=Path)
    pr.add_argument("-o", "--output", type=Path, required=True)
    print_args(pr, page_size_required=True)
    pr.add_argument("--format", choices=["jpeg", "png", "tiff"], help="default: from the output extension")
    pr.add_argument("--fit", choices=["auto", "stretch", "fit", "fill"], default="auto")
    pr.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], default=0, help="clockwise, applied first")
    pr.set_defaults(func=_cmd_print)

    g = sub.add_parser("glare", help="check single photos for glare")
    g.add_argument("photos", nargs="+", type=Path)
    g.add_argument("--overlay", type=Path, help="write each photo with its glare regions outlined to this folder")
    g.set_defaults(func=_cmd_glare)

    ge = sub.add_parser("glare-eval", help="score a page's glare map against a hand-drawn mask")
    ge.add_argument("metadata", type=Path, help="the page metadata (PAGE.json)")
    ge.add_argument("--truth", type=Path, required=True, help="hand-drawn mask, white where there is glare")
    ge.add_argument("--masks-dir", type=Path, help="where PAGE.glare.png is (default: next to the metadata)")
    ge.set_defaults(func=_cmd_glare_eval)

    st = sub.add_parser("straightness", help="measure how straight long lines in a composed page are")
    st.add_argument("image", type=Path)
    st.add_argument("--min-len", type=float, default=0.1, help="shortest line measured, fraction of the page's long side")
    st.add_argument("--overlay", type=Path, help="write the page with each line drawn, coloured by its deviation, here")
    st.set_defaults(func=_cmd_straightness)

    s = sub.add_parser("synth", help="generate synthetic test photos of a page")
    s.add_argument("outdir", type=Path)
    s.add_argument("--kind", choices=["glare", "stitch", "pair", "clean_white", "curved", "curved_stitch"], default="glare")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("-n", type=int, default=4)
    s.add_argument("--gap", type=float, default=0.03, help="pair: table between the pages, fraction of page width (0 = touching)")
    s.add_argument("--glare-style", choices=["spot", "sleeve"], default="spot")
    s.add_argument("--lift", type=float, default=0.03, help="curved: rise at the binding, fraction of the page across it")
    s.add_argument("--strip", type=float, default=0.12, help="curved: width of the bent strip, fraction of the page across the binding")
    s.add_argument("--binding", choices=["left", "right", "top", "bottom"], default="left", help="curved: the side the page bends at")
    s.set_defaults(func=_cmd_synth)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
