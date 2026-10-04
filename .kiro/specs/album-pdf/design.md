# Design: album assembly, 300 DPI print images and album PDF

## Overview

The page pipeline (`albumproc.process_page`) already does the hard image work
for one page. This step wraps it in an album loop and adds the two things it
lacks: a physical scale, which only the user can supply, and output formats a
print service accepts.

```
album folder ──discover──▶ AlbumSpec (pages, sizes, overrides)
                              │  for each page (one at a time, or N workers)
                              ▼
               photos ──process_page──▶ processed page (BGR, true aspect)
                              │            saved losslessly as pages/NNN-name.png
                              ▼
                     make_print_image ──▶ print image (RGB, exact px at DPI)
                              │            saved as print/NNN-name.jpg + DPI + sRGB
                              ▼
               all print images ──write_album_pdf──▶ album.pdf
                              ▼
                         report.json
```

Nothing in the existing pipeline changes. The album step depends only on the
public `process_page`, `PageOptions` and `PageReport`; the open "two pages side
by side" branch changes how the page is chosen inside `process_page`, not that
interface, so the two are independent.

## Key decisions

### Physical size comes from the user

A processed page has the right shape but no scale: the photos contain no ruler,
and the phone's distance to the page is unknown. Album pages within one album
are almost always the same size, so the size is stated once (`--page-size
10x12in` or `"page_size"` in `album.json`) with per-page overrides for covers
or fold-outs. The capture app asks for the size when an album is created,
defaulting to 8.5 x 11 in, and writes it to `album.json`; the backend uses the
same default when nothing gives a size. Estimating size from EXIF focal length and a guessed distance was
rejected: it would be off by tens of percent, and printing at the wrong size is
worse than asking.

The size is an unordered pair. `make_print_image` assigns the longer side to
the processed page's longer side, so `10x12in` serves portrait and landscape
pages alike, and a page photographed sideways comes out at the right size but
sideways rather than squashed. The tool never guesses rotation (the processed
page's "up" is whatever the reference photo's was, and EXIF orientation is
already honoured by `cv2.imread`); the manifest's `rotate` fixes the rare page
that needs it.

### Shape check before scaling

The page pipeline's measured aspect is accurate to about 2% on the synthetic
tests (`aspect_err < 0.02` in `tests/test_pipeline.py`). Within that tolerance
the difference from the stated size is measurement noise, so the page is
stretched to fill the size exactly (`fit: auto` → `stretch`). Beyond it,
something is wrong, either the wrong page was detected or the size is wrong, so
the page is fitted inside the size with a white border, which shows the problem
on the printed page, and an `aspect_mismatch` warning goes in the report. `fit`
and `fill` (crop) can be forced per page.

### Exact pixel dimensions, single resample

Print pixels are `round(side_in × dpi)`; bleed adds `round(bleed_in × dpi)` on
each side. The PDF page size is derived from those pixel counts
(`px / dpi × 72` pt), not from the nominal size, so image and page always match
exactly; the rounding error is under 1/600 in at 300 DPI.

The processed page is resampled once, with `cv2.INTER_AREA` when the result is
smaller in both directions and `cv2.INTER_LANCZOS4` otherwise. Rotation by
multiples of 90° (`cv2.rotate`) is lossless and happens first. The pipeline's
`max_side` cap (8000 px) leaves plenty of headroom: a 12 in side at 300 DPI is
3600 px.

The capture DPI, `max(w, h) / long_side_in` of the processed page, is reported
for every page. Below the target DPI the page is upsampled, which adds no
detail, so it is flagged `upsampled`; below `low_dpi` (150) it gets a
`low_resolution` warning that the capture app can turn into "move closer or
shoot this page in parts".

### File formats and libraries

OpenCV cannot write DPI or ICC metadata, so print images are written with
**Pillow**: JPEG quality 95 with 4:4:4 chroma (`subsampling=0`, no colour
bleeding on fine print), PNG, or TIFF with LZW compression. All carry
`dpi=(D, D)` and an sRGB ICC profile from `PIL.ImageCms.createProfile("sRGB")`,
so no profile file is vendored.

The PDF is written with **img2pdf**, which embeds JPEG data as-is (DCTDecode,
no generation loss) and reads only the file header for size, so building a
100-page PDF never decodes an image. PNG and TIFF go in losslessly (Flate).
**pikepdf** (which img2pdf can also use as its backend) then sets the trim box
when there is a bleed, the page labels and the document title, and fixes the
dates from `SOURCE_DATE_EPOCH`. Pillow's own PDF writer was rejected because it re-encodes
JPEGs.

New runtime dependencies: `Pillow>=10`, `img2pdf>=0.5`, `pikepdf>=8`.

### Colour

Phone JPEGs are sRGB in practice, `cv2.imread` drops any embedded profile, and
the fusion step works in those values, so the output is tagged sRGB. A phone
shooting Display P3 would be printed slightly desaturated; carrying the source
profile through is noted as a known limit rather than solved now.

### Re-runs reuse work at two levels

Re-shooting a few pages is the normal loop, and the page pipeline is the
expensive part (SIFT on several 12 MP photos per page). So each page records
two keys in `report.json`:

- `process_key`: SHA-256 over the photos' contents in order, `PageOptions`
  (via `dataclasses.asdict`, canonical JSON) and the albumproc version.
- `print_key`: `process_key` plus the resolved page size, rotation, fit mode
  and print options.

On a re-run, a matching `process_key` with `pages/NNN-name.png` present skips
the page pipeline; a matching `print_key` with the print image present skips
resampling too. Changing only the DPI or bleed therefore redoes the cheap step
only. The PDF is always rebuilt (it is cheap and depends on every page).

Every file is written to `<name>.tmp` in the same folder and `os.replace`d into
place, and `report.json` is rewritten the same way after each page, so a crash
loses at most the page in progress and never leaves a truncated file behind a
matching key.

### Failure isolation

`process_page` raises on unusable input (for example nothing registers). The
album loop catches `Exception` per page, records the message and moves on;
`--strict` re-raises instead. With `--placeholder`, a failed page becomes a
white page of its size with its name and "missing" drawn in, so a printed
album keeps facing pages paired. The exit status is 0 when every page
succeeded, 3 when some failed, 2 for usage or input errors detected before
processing (no pages, bad page size).

### Memory and parallelism

A page's working set is a few full-resolution photos plus their warps, around
1–2 GB for four 12 MP shots of an oversized page. The loop holds one page at a
time and writes its outputs before taking the next. `--jobs N` runs pages in a
`ProcessPoolExecutor` (processes, because OpenCV and NumPy hold the GIL in
places and memory is easier to reason about per process), with
`cv2.setNumThreads(1)` in each worker to avoid oversubscribing cores. Results
are collected in album order, so the PDF and report are identical to a
single-worker run.

## Components

All new code lives in `backend/src/albumproc/`.

### `printsize.py`

```python
@dataclass(frozen=True)
class PageSize:
    a_in: float  # the two side lengths in inches, unordered
    b_in: float

    def oriented(self, landscape: bool) -> tuple[float, float]: ...  # (w_in, h_in)
    @property
    def aspect_long(self) -> float: ...  # long / short

def parse_page_size(text: str) -> PageSize: ...  # "10x12in", "254x305mm", "25.4x30.5cm", "letter", "a4", "a3"
def parse_length(text: str) -> float: ...       # "0.125in", "3mm" -> inches (used for bleed)
```

Raises `ValueError` with the offending text on anything else, including
non-positive values.

### `printimage.py`

```python
@dataclass
class PrintOptions:
    dpi: int = 300
    fit: str = "auto"            # auto | stretch | fit | fill
    aspect_tolerance: float = 0.02
    rotate: int = 0              # 0 | 90 | 180 | 270, clockwise
    bleed_in: float = 0.0
    fill: tuple[int, int, int] = (255, 255, 255)  # RGB
    low_dpi: float = 150.0
    format: str = "jpeg"         # jpeg | png | tiff
    quality: int = 95

@dataclass
class PageWarning:
    code: str     # low_resolution | upsampled | aspect_mismatch | incomplete_coverage | photos_dropped
    message: str

@dataclass
class PrintInfo:
    size_in: tuple[float, float]   # (w, h) trim size, as oriented
    size_px: tuple[int, int]       # (w, h) including bleed
    bleed_px: int
    capture_dpi: float
    fit_used: str                  # stretch | fit | fill
    aspect_page: float             # w / h of the processed page after rotation
    aspect_size: float             # w / h of the trim size
    warnings: list[PageWarning]

def make_print_image(page_bgr: np.ndarray, size: PageSize, opt: PrintOptions) -> tuple[np.ndarray, PrintInfo]: ...
def write_print_image(path: Path, image_rgb: np.ndarray, opt: PrintOptions) -> None: ...
def placeholder_image(size: PageSize, landscape: bool, label: str, opt: PrintOptions) -> np.ndarray: ...
```

`make_print_image` steps: rotate; orient the size to the page; compare
aspects and resolve `auto`; compute target pixels; resize (stretch), resize and
pad centred (fit) or resize and centre-crop (fill); add bleed with
`cv2.copyMakeBorder(..., cv2.BORDER_REFLECT_101)`; convert BGR→RGB. It is pure
(no I/O), which keeps it easy to test and lets `albumproc print` reuse it.

### `pdf.py`

```python
@dataclass
class PdfPage:
    image_path: Path
    dpi: int
    bleed_px: int
    label: str

def write_album_pdf(pages: list[PdfPage], path: Path, title: str, epoch: int | None = None) -> int: ...  # returns page count
```

Calls `img2pdf.convert` with `layout_fun=img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))`,
which sizes each page from its pixel count and the DPI, then opens the result with pikepdf to set `/TrimBox` (inset by
the bleed) when `bleed_px > 0`, `/PageLabels` (each page a `/P` prefix with its
label and no numbering style), `/Title`, and `/CreationDate` and `/ModDate`
from `epoch`. pikepdf is saved with `deterministic_id=True` so the
document ID does not vary between identical runs.

### `album.py`

```python
@dataclass
class PageSpec:
    index: int                 # 1-based position
    name: str                  # display name
    slug: str                  # filesystem-safe name used in output files
    folder: Path
    photos: list[Path]         # sorted naturally
    size: PageSize | None
    rotate: int
    fit: str | None

@dataclass
class AlbumSpec:
    title: str
    pages: list[PageSpec]
    problems: list[str]        # manifest pages with no folder or no photos (become failed pages)

@dataclass
class AlbumOptions:
    page: PageOptions = field(default_factory=PageOptions)
    print: PrintOptions = field(default_factory=PrintOptions)
    page_size: PageSize | None = None   # album-wide default; the manifest's wins; letter when None
    coverage_threshold: float = 0.99
    strict: bool = False
    placeholder: bool = False
    force: bool = False
    jobs: int = 1
    debug: bool = False

def discover_album(folder: Path, default_size: PageSize | None) -> AlbumSpec: ...
def process_album(folder: Path, out: Path, opt: AlbumOptions, progress: Callable[[str], None] | None = None) -> dict: ...
```

`discover_album` reads `album.json` when present, else lists subfolders.
Natural sort splits names into digit and non-digit runs and compares digits
numerically. The slug is the folder name with anything outside
`[A-Za-z0-9._-]` replaced by `-`. Pages without a size get 8.5 x 11 in
(Requirement 2.3).

`process_album` builds the spec, validates sizes, loads the previous
`report.json` if any, then for each page: compute keys, reuse or run
`process_page` (reading photos with `cv2.imread` exactly as `albumproc page`
does), save the processed page, `make_print_image`, `write_print_image`,
append warnings derived from the page report (`coverage` below the threshold
→ `incomplete_coverage`; non-empty `dropped` → `photos_dropped`), and rewrite
`report.json`. Finally it writes the PDF and the final report and returns the
report dict.

### CLI (`cli.py`)

```
albumproc album ALBUM_DIR -o OUT_DIR [--page-size SIZE] [--dpi 300]
        [--format jpeg|png|tiff] [--quality 95] [--bleed LEN] [--fill '#ffffff']
        [--focal-35mm 26] [--max-side 8000] [--strict] [--placeholder]
        [--force] [--jobs N] [--debug]

albumproc print PAGE_IMAGE -o FILE --page-size SIZE [--dpi 300]
        [--format ...] [--quality 95] [--bleed LEN] [--fit auto|stretch|fit|fill]
        [--rotate 0|90|180|270] [--fill '#ffffff']
```

`album` prints one line per page to stderr (`[ 3/40] page-03 ok 312 dpi`) and
the summary to stdout, and returns the exit status above. `print` infers the
format from the output extension when `--format` is not given. The existing
`page` and `synth` commands are untouched.

## Data formats

### Input manifest: `ALBUM_DIR/album.json` (optional)

```json
{
  "title": "Rawlins family 1962-1968",
  "page_size": "10x12in",
  "fit": "auto",
  "pages": [
    {"folder": "cover", "name": "Front cover", "page_size": "10.5x12.5in"},
    "page-01",
    {"folder": "page-02", "rotate": 90},
    {"folder": "page-03", "fit": "fit"}
  ]
}
```

Every key is optional. Without `pages`, pages are the subfolders in natural
order. With it, only the listed folders are used, in the listed order; a page
entry may be a bare folder name. Unknown keys are rejected so that typos don't
pass silently.

### Output folder

```
OUT_DIR/
  report.json
  album.pdf
  pages/001-cover.png        processed page, lossless, for reuse and re-printing
  print/001-cover.jpg        print image
  debug/001-cover/           with --debug: process_page's debug images
```

### `report.json`

```json
{
  "version": 1,
  "albumproc": "0.2.0",
  "title": "Rawlins family 1962-1968",
  "dpi": 300,
  "pdf": "album.pdf",
  "pdf_pages": 39,
  "pages_found": 40,
  "pages_ok": 39,
  "pages_failed": 1,
  "pages": [
    {
      "index": 1,
      "name": "Front cover",
      "folder": "cover",
      "status": "ok",
      "photos": ["cover/IMG_0001.jpg", "cover/IMG_0002.jpg"],
      "process_key": "sha256:…",
      "print_key": "sha256:…",
      "page_report": { "...": "PageReport.to_dict()" },
      "size_in": [10.5, 12.5],
      "size_px": [3150, 3750],
      "bleed_px": 0,
      "capture_dpi": 412.3,
      "fit": "stretch",
      "processed": "pages/001-cover.png",
      "print": "print/001-cover.jpg",
      "warnings": []
    },
    {
      "index": 7, "name": "page-06", "status": "failed",
      "error": "no photos registered", "warnings": []
    }
  ]
}
```

Paths are relative to the album or output folder so the report survives
moving either. `PageReport` dicts with integer keys (`glare_fraction`, `gains`)
become string keys in JSON, as `albumproc page --report` already does.

## Error handling

| Situation | Behaviour | Exit |
| --- | --- | --- |
| No pages found | error naming the folder, no outputs | 2 |
| Page size unparsable | error quoting the value, no outputs | 2 |
| No page size given anywhere | 8.5 x 11 in (letter) | 0 |
| Manifest invalid (bad JSON, unknown key, bad rotate/fit) | error with the key path | 2 |
| Manifest page with no folder or photos | page failed, others continue | 3 |
| `process_page` raises | page failed, others continue (`--strict`: stop, no PDF) | 3 (`--strict`: 1) |
| Unreadable photo | page failed with the file name | 3 |
| Every page failed | report written, no PDF | 3 |
| Shape, resolution or coverage problems | page kept, warning in report | 0 |

## Testing strategy

All tests use the existing synthetic generator (`albumproc.synth.make_case`),
so no real album is needed in the repo.

- **printsize**: parsing table (units, named sizes, decimals, rejects), unit
  round-trips, orientation.
- **printimage**: exact output dimensions for stretch, fit and fill across
  DPI and bleed values; aspect within tolerance resolves to stretch, beyond to
  fit with a warning; rotation by 90 maps a landscape page onto a portrait size;
  capture DPI and the `upsampled` / `low_resolution` warnings; fill colour in
  the padding; bleed is mirrored content (pixels in the margin equal their
  reflection); written files read back with Pillow show the DPI and an ICC
  profile.
- **pdf**: page count and order, MediaBox equal to `px / dpi × 72`, TrimBox
  inset by the bleed, page labels, title; each embedded JPEG stream is
  byte-identical to its file (no re-encode); two runs with the same
  `SOURCE_DATE_EPOCH` give identical bytes.
- **album** (end to end, small pages and few photos to stay fast): a three-page
  synthetic album produces three print images and a three-page PDF; manifest
  order and overrides are honoured; a page with unmatched photos fails without
  stopping the others and exits 3; `--placeholder` keeps the page count;
  `--strict` stops; a second run reports `reused` for every page; changing one
  photo reprocesses only that page; changing only `--dpi` reuses processed
  pages but re-prints; `--jobs 2` output matches `--jobs 1`.
- **cli**: argument parsing and exit codes via `main([...])`, as the existing
  commands are tested.

## Out of scope

- Uploads, storage, job queueing and the Android app (later steps).
- Layout, captions, decoration or reflowing several album pages onto one sheet.
- PDF/A conformance, CMYK conversion and print-shop preflight beyond bleed and
  trim box.
- Automatic rotation and automatic page-size estimation.
- Carrying non-sRGB source profiles through the pipeline.
