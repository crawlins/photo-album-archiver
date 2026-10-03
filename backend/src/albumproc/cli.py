"""Command line: ``albumproc page`` processes one page, ``albumproc synth`` makes test photos."""

from __future__ import annotations

import argparse
import json
import sys
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

    case = make_case(a.seed, a.kind, a.n)
    a.outdir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(a.outdir / "truth.png"), case.page)
    for i, img in enumerate(case.images):
        cv2.imwrite(str(a.outdir / f"shot_{i:02d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"wrote {len(case.images)} shots and truth.png to {a.outdir}")
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

    s = sub.add_parser("synth", help="generate synthetic test photos of a page")
    s.add_argument("outdir", type=Path)
    s.add_argument("--kind", choices=["glare", "stitch"], default="glare")
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("-n", type=int, default=4)
    s.set_defaults(func=_cmd_synth)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
