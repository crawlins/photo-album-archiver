"""A whole album: a folder of pages, each a folder of photos, in; print images and a PDF out.

Each page goes through the page pipeline, is resampled to its physical size at
the target DPI, and is written to disk before the next page starts, so memory
does not grow with the album. A page that fails is reported and skipped; the
rest of the album still comes out.

Re-runs reuse work: every page records a key over its photos' contents and
the pipeline options, and another over that plus the print options, so after
re-shooting one page only that page is processed again, and changing only the
DPI or bleed redoes only the cheap resampling step.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Callable

import cv2

from . import __version__
from .pdf import PdfPage, write_album_pdf
from .pipeline import PageOptions, process_page
from .printimage import FITS, FORMATS, ROTATIONS, PageWarning, PrintOptions, make_print_image, placeholder_image, write_print_image
from .printsize import PageSize, parse_page_size

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MANIFEST = "album.json"
REPORT = "report.json"
REPORT_VERSION = 1
DEFAULT_PAGE_SIZE = PageSize(8.5, 11)  # US letter, the capture app's default for a new album
_ALBUM_KEYS = {"title", "page_size", "fit", "pages"}
_PAGE_KEYS = {"folder", "name", "page_size", "rotate", "fit"}


class AlbumError(ValueError):
    """The album cannot be processed at all (no pages, bad manifest, missing size)."""


class PageFailed(RuntimeError):
    """A page failed and ``strict`` was set."""


@dataclass
class PageSpec:
    index: int  # 1-based position in the album
    name: str  # display name
    slug: str  # filesystem-safe name used in output files
    folder: Path
    photos: list[Path]  # sorted naturally
    size: PageSize
    rotate: int = 0
    fit: str | None = None  # None: the album options' fit
    problem: str | None = None  # why the page cannot be processed, found during discovery

    @property
    def stem(self) -> str:
        return f"{self.index:03d}-{self.slug}"


@dataclass
class AlbumSpec:
    title: str
    pages: list[PageSpec]


@dataclass
class AlbumOptions:
    page: PageOptions = field(default_factory=PageOptions)
    print: PrintOptions = field(default_factory=PrintOptions)
    page_size: PageSize | None = None  # album-wide default; album.json's wins; letter when None
    coverage_threshold: float = 0.99
    strict: bool = False
    placeholder: bool = False
    force: bool = False
    jobs: int = 1
    debug: bool = False


def natural_key(name: str) -> list:
    """Sort key putting ``page-2`` before ``page-10``."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in re.split(r"(\d+)", name) if t]


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name).strip("-.") or "page"


def _photos(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXTS and not p.name.startswith(".")]
    return sorted(files, key=lambda p: natural_key(p.name))


def _size(value, where: str) -> PageSize:
    try:
        return parse_page_size(value)
    except ValueError as e:
        raise AlbumError(f"{where}: {e}") from None


def _check_keys(d: dict, allowed: set[str], where: str) -> None:
    unknown = sorted(set(d) - allowed)
    if unknown:
        raise AlbumError(f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}")


def _check_fit(value, where: str) -> str:
    if value not in FITS:
        raise AlbumError(f"{where}: fit must be one of {', '.join(FITS)}, got {value!r}")
    return value


def _check_rotate(value, where: str) -> int:
    if isinstance(value, bool) or value not in ROTATIONS:
        raise AlbumError(f"{where}: rotate must be 0, 90, 180 or 270, got {value!r}")
    return int(value)


def discover_album(folder: str | Path, default_size: PageSize | None = None) -> AlbumSpec:
    """Find the album's pages, from ``album.json`` when present, else from its subfolders.

    Page sizes come from the page's manifest entry, else the manifest's
    ``page_size``, else ``default_size``, else US letter.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise AlbumError(f"{folder}: not a folder")
    manifest: dict = {}
    mpath = folder / MANIFEST
    if mpath.exists():
        try:
            manifest = json.loads(mpath.read_text())
        except json.JSONDecodeError as e:
            raise AlbumError(f"{mpath}: invalid JSON: {e}") from None
        if not isinstance(manifest, dict):
            raise AlbumError(f"{mpath}: must be a JSON object")
        _check_keys(manifest, _ALBUM_KEYS, MANIFEST)

    album_size = _size(manifest["page_size"], f"{MANIFEST}: page_size") if "page_size" in manifest else default_size or DEFAULT_PAGE_SIZE
    album_fit = _check_fit(manifest["fit"], f"{MANIFEST}: fit") if "fit" in manifest else None
    title = str(manifest.get("title") or folder.resolve().name)

    pages: list[PageSpec] = []
    if "pages" in manifest:
        entries = manifest["pages"]
        if not isinstance(entries, list):
            raise AlbumError(f"{MANIFEST}: pages must be a list")
        seen = set()
        for k, e in enumerate(entries):
            where = f"{MANIFEST}: pages[{k}]"
            if isinstance(e, str):
                e = {"folder": e}
            if not isinstance(e, dict) or not isinstance(e.get("folder"), str):
                raise AlbumError(f"{where}: must be a folder name or an object with a 'folder'")
            _check_keys(e, _PAGE_KEYS, where)
            if e["folder"] in seen:
                raise AlbumError(f"{where}: folder {e['folder']!r} is listed twice")
            seen.add(e["folder"])
            sub = folder / e["folder"]
            photos = _photos(sub)
            problem = None
            if not sub.is_dir():
                problem = f"folder {e['folder']!r} not found"
            elif not photos:
                problem = f"no photos in {e['folder']!r}"
            pages.append(
                PageSpec(
                    index=k + 1,
                    name=str(e.get("name") or e["folder"]),
                    slug=_slug(e["folder"]),
                    folder=sub,
                    photos=photos,
                    size=_size(e["page_size"], f"{where}: page_size") if "page_size" in e else album_size,
                    rotate=_check_rotate(e["rotate"], where) if "rotate" in e else 0,
                    fit=_check_fit(e["fit"], where) if "fit" in e else album_fit,
                    problem=problem,
                )
            )
    else:
        subs = sorted((p for p in folder.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: natural_key(p.name))
        for sub in subs:
            photos = _photos(sub)
            if photos:
                pages.append(PageSpec(len(pages) + 1, sub.name, _slug(sub.name), sub, photos, album_size, fit=album_fit))

    if not pages:
        raise AlbumError(f"{folder}: no pages found (expected one subfolder of photos per page)")
    return AlbumSpec(title, pages)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _key(obj) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _write_json(path: Path, obj) -> None:
    _write_bytes(path, (json.dumps(obj, indent=2) + "\n").encode())


def _page_warnings(page_report: dict, threshold: float) -> list[dict]:
    w = []
    if page_report["coverage"] < threshold:
        w.append(asdict(PageWarning("incomplete_coverage", f"photos cover only {page_report['coverage']:.1%} of the page")))
    if page_report["dropped"]:
        w.append(asdict(PageWarning("photos_dropped", f"photos {page_report['dropped']} matched no other photo and were not used")))
    return w


@dataclass
class _Job:
    page: PageSpec
    album: Path
    out: Path
    opt: AlbumOptions
    previous: dict | None


def _run_page(job: _Job) -> dict:
    """Process one page and write its outputs; never raises for a page-level failure."""
    page, opt, out = job.page, job.opt, job.out
    entry = {"index": page.index, "name": page.name, "folder": page.folder.name, "status": "failed"}
    entry["photos"] = [str(p.relative_to(job.album)) for p in page.photos]
    try:
        if page.problem:
            raise RuntimeError(page.problem)
        popt = PrintOptions(**{**asdict(opt.print), "rotate": page.rotate, "fit": page.fit or opt.print.fit})
        process_key = _key({"photos": [_sha256_file(p) for p in page.photos], "page": asdict(opt.page), "version": __version__})
        print_key = _key({"process": process_key, "size": [page.size.a_in, page.size.b_in], "print": asdict(popt), "coverage": opt.coverage_threshold})
        processed = out / "pages" / f"{page.stem}.png"
        printed = out / "print" / f"{page.stem}{FORMATS[popt.format]}"
        prev = job.previous if not opt.force else None

        if prev and prev.get("process_key") == process_key and processed.exists() and prev.get("processed") == str(processed.relative_to(out)):
            status, page_report = "reused", prev["page_report"]
            image = None
        else:
            images = []
            for p in page.photos:
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)  # honours EXIF orientation
                if img is None:
                    raise RuntimeError(f"cannot read {p.relative_to(job.album)}")
                images.append(img)
            debug = out / "debug" / page.stem if opt.debug else None
            res = process_page(images, opt.page, debug_dir=debug)
            del images
            image = res.image
            page_report = json.loads(json.dumps(res.report.to_dict()))  # int keys -> str, as in the saved report
            ok, buf = cv2.imencode(".png", image)
            if not ok:
                raise RuntimeError("cannot encode the processed page")
            _write_bytes(processed, buf.tobytes())
            status = "ok"
            prev = None  # the print image must be redone from the new page

        if prev and prev.get("print_key") == print_key and printed.exists() and prev.get("print") == str(printed.relative_to(out)):
            fields = {k: prev[k] for k in ("size_in", "size_px", "bleed_px", "capture_dpi", "fit", "warnings")}
        else:
            if image is None:
                image = cv2.imread(str(processed), cv2.IMREAD_COLOR)
            rgb, info = make_print_image(image, page.size, popt)
            write_print_image(printed, rgb, popt)
            fields = {
                "size_in": list(info.size_in),
                "size_px": list(info.size_px),
                "bleed_px": info.bleed_px,
                "capture_dpi": info.capture_dpi,
                "fit": info.fit_used,
                "warnings": _page_warnings(page_report, opt.coverage_threshold) + [asdict(w) for w in info.warnings],
            }
        entry.update(status=status, process_key=process_key, print_key=print_key, page_report=page_report, **fields)
        entry.update(processed=str(processed.relative_to(out)), print=str(printed.relative_to(out)))
    except Exception as e:  # one bad page must not stop the album
        msg = str(e) if isinstance(e, RuntimeError) and str(e) else f"{type(e).__name__}: {e}"
        entry.update(status="failed", error=msg, warnings=[])
        if opt.placeholder and not opt.strict:
            popt = opt.print
            ph = out / "print" / f"{page.stem}{FORMATS[popt.format]}"
            write_print_image(ph, placeholder_image(page.size, page.size.a_in > page.size.b_in, page.name, popt), popt)
            entry["placeholder"] = str(ph.relative_to(out))
            entry["bleed_px"] = round(popt.bleed_in * popt.dpi)
    return entry


def _init_worker() -> None:
    cv2.setNumThreads(1)


def process_album(folder: str | Path, out: str | Path, opt: AlbumOptions | None = None, progress: Callable[[str], None] | None = None) -> dict:
    """Process every page of the album in ``folder`` into ``out``; returns the report.

    Raises ``AlbumError`` before touching any page when the album as a whole
    is unusable, and ``PageFailed`` on the first failed page when
    ``opt.strict`` is set (the report is written either way, the PDF is not).
    """
    opt = opt or AlbumOptions()
    opt.print.validate()
    folder, out = Path(folder), Path(out)
    spec = discover_album(folder, opt.page_size)
    out.mkdir(parents=True, exist_ok=True)
    pdf_path = out / "album.pdf"
    pdf_path.unlink(missing_ok=True)  # rebuilt below; one from an earlier run would not match this report

    previous = {}
    try:
        old = json.loads((out / REPORT).read_text())
        previous = {e["folder"]: e for e in old.get("pages", [])}
    except (OSError, ValueError, KeyError, TypeError):
        pass

    n = len(spec.pages)
    report = {
        "version": REPORT_VERSION,
        "albumproc": __version__,
        "title": spec.title,
        "dpi": opt.print.dpi,
        "pdf": None,
        "pdf_pages": 0,
        "pages_found": n,
        "pages_ok": 0,
        "pages_failed": 0,
        "pages": [],
    }
    jobs = [_Job(p, folder, out, opt, previous.get(p.folder.name)) for p in spec.pages]

    def record(entry: dict) -> None:
        report["pages"].append(entry)
        failed = entry["status"] == "failed"
        report["pages_failed" if failed else "pages_ok"] += 1
        _write_json(out / REPORT, report)
        if progress:
            w = len(str(n))
            detail = entry.get("error", "") if failed else f"{entry['capture_dpi']:.0f} dpi"
            codes = [x["code"] for x in entry.get("warnings", [])]
            progress(f"[{entry['index']:>{w}}/{n}] {entry['name']} {entry['status']} {detail}" + (f" ({', '.join(codes)})" if codes else ""))
        if failed and opt.strict:
            raise PageFailed(f"page {entry['name']!r} failed: {entry['error']}")

    if opt.jobs > 1:
        with ProcessPoolExecutor(max_workers=opt.jobs, mp_context=get_context("spawn"), initializer=_init_worker) as ex:
            futures = [ex.submit(_run_page, j) for j in jobs]
            try:
                for f in futures:
                    record(f.result())
            except PageFailed:
                for f in futures:
                    f.cancel()
                raise
    else:
        for j in jobs:
            record(_run_page(j))

    pdf_pages = []
    for e in report["pages"]:
        img = e.get("print") if e["status"] != "failed" else e.get("placeholder")
        if img:
            pdf_pages.append(PdfPage(out / img, opt.print.dpi, e.get("bleed_px", 0), e["name"]))
    if pdf_pages:
        epoch = os.environ.get("SOURCE_DATE_EPOCH")
        report["pdf_pages"] = write_album_pdf(pdf_pages, pdf_path, spec.title, int(epoch) if epoch else None)
        report["pdf"] = pdf_path.name
    _write_json(out / REPORT, report)
    return report
