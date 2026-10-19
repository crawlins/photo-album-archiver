# Design: capturing pages with a flatbed scanner

## Overview

Two parts. `albumscan` is a new GTK 4 desktop app in `scanner/` that drives
SANE's `scanimage`, turns each scan into a JPEG shot that says it is a scan,
and uploads it through a local outbox with the phone app's requests. The page
step gains a scan mode, picked per page from the shots' EXIF, so the server
and its HTTP contract stay exactly as they are.

```
 ┌──────────────────────── albumscan (desktop) ────────────────────────┐
 │ GTK window ── Session (album, current page, limits) ── Settings     │
 │     │ Scan                    │ shots, metadata, deletions          │
 │     ▼                         ▼                                     │
 │ sane.Scanner ─▶ shot.encode ─▶ Outbox (SQLite + files) ─▶ Sender ───┼──▶ albumserver
 │  scanimage       JPEG + EXIF        FIFO of requests       thread   │   (unchanged)
 └─────────────────────────────────────────────────────────────────────┘        │
                                                                                ▼
                                  albumproc album ── is every shot a scan? ── scan mode
```

## Key decisions

### A separate desktop app, not a server feature

The scanner sits on the same machine as the server today, so the server could
scan by itself. It should not: scanning is interactive (the user changes the
page on the glass between scans), the server runs as an unprivileged service
user without a display or USB access, and the server spec keeps it to storing
and processing. A client using the existing HTTP contract also works from any
machine on the network with a scanner attached, with its own revocable token.

### `scanimage`, not a SANE binding

`python-sane` is not packaged for the user's Python 3.14 and would add a C
extension to build. `scanimage` (in `sane-utils`, already installed) gives
everything needed: a machine-readable device list
(`scanimage -f '%nalbumscan\t%d\t%v\t%m\t%t%n' -L`; the
leading newline and marker matter, because some backends print stray text
without a newline before the list, as the Epson `epsonscan2` backend does on
this machine), each device's options (`scanimage -A`),
progress on standard error (`--progress`) and cancellation on SIGTERM. A
subprocess also keeps a crashing backend driver out of the app's process.
SANE's `test` backend (`scanimage -d test`) gives a real scanner to test
against without hardware, and tests skip it where it is not available.

### GTK 4 through the distribution's PyGObject

The machine runs GNOME on Wayland and has PyGObject for GTK 4 installed;
neither Qt binding is. PyGObject cannot sensibly be pip-installed, so the app
runs on the system Python and its package depends only on what the system has
(PyGObject, Pillow) plus the standard library (`urllib`, `sqlite3`,
`tomllib`, `json`). It does not import `albumproc`, so it does not need
OpenCV, SciPy or the backend's virtual environment. Everything except the
window lives in plain modules that tests drive without a display.

### Defaults: the highest colour resolution, whole glass

The print step targets 300 dpi at the album's page size, so 300 dpi is the
floor (Requirement 2.1). The default is the highest resolution the scanner
offers in colour (Clark's choice), read with `scanimage --mode Color -A`
because a backend may offer different resolutions in each mode. That keeps
the most detail a scan can, at a cost the user sees at once and can lower in
the window: a letter-sized colour scan is about 35 megapixels at 600 dpi, 140
at 1200 and 570 at 2400, and each doubling takes about four times as long
per pass and as much memory.

Two limits had to move for that (Requirement 6.4, Clark's choice again). The
server's request limit (`max_body_mb`) goes from 50 MB to 1 GB, enough for a
2400 dpi scan; phone shots are unaffected. And Pillow refuses images over
about 179 million pixels as decompression bombs, which would make the server
reject a 1200 dpi scan as "not a JPEG"; `albumproc.scanmark` raises Pillow's
limit to `MAX_PIXELS`, 1 billion, which the server picks up by importing it,
in line with OpenCV's own reading limit of 2^30. A whole glass at 4800 dpi
(2.3 billion pixels for a LiDE's, about 7 GB decoded) stays out of reach:
the app leaves such resolutions out of its list when it knows the glass
size, and refuses any scan over the limit before keeping it. The page step
caps its output's long side at 8000 px (`max_side`) whatever the input, so
the gain above 2400 dpi would be lost anyway unless that cap rises too.

The whole glass is scanned rather than an area fitted to the page size: the
page step finds the page's edges anyway, the user does not have to align the
page with a corner, and an oversized page placed anywhere gets its edges in
view. A preview scan to pick an area is out of scope.

### Placement turns pixels, not an EXIF flag

An album page often lies sideways on the glass. Turning the pixels before
encoding (Requirement 6.1) means every reader agrees on which way is up: the
server's previews, `cv2.imread` in the page step, and the phone app opening
the same album later. The album's placement is remembered per album id in the
settings, since one album's pages all go on the glass the same way.

### The scan marker

The page step needs to know a page was scanned, and the contract carries only
JPEG bytes. EXIF is part of those bytes, survives the server byte for byte,
and makes a folder of scans processed by `albumproc album` on its own behave
the same as on the server. `Software = "albumscan <version>"` is the marker;
`Make`/`Model` and the resolution tags are informative. A page counts as
scanned only when all its shots carry the marker, because a photo among them
brings perspective and glare back in.

### What scan mode changes in the page step

- **Proportions.** A scan is an orthographic view at the same scale in both
  directions, so the page's width-to-height ratio is the ratio of its side
  lengths in the scan. `estimate_aspect` with a 26 mm focal length would land
  near that for a square-on view, but it is the wrong model; scan mode uses
  the side-length ratio directly.
- **No bend.** A page on the glass is flat where it touches; where a bound
  page lifts near the spine, the scanner sees blur and darkening, not the
  bowed outline the curvature fit models. Fitting a cylinder to it could only
  add distortion, so scan mode sets curvature to `off`.
- **No glare, shadow or single-coverage checks.** A flatbed's light travels
  with the sensor at a fixed angle, so there is no lamp reflection to chase,
  and no hand or phone to cast a shadow. The single-coverage report and its
  warnings exist to ask for another photo from a different angle, which a
  scanner cannot provide. Glare detection is switched off
  (`GlareParams.enabled = False`), shadow handling off, and the
  single-coverage summary left empty.
- **Still checked:** registration of several scans of an oversized page (the
  existing SIFT and RANSAC homography fits a pure rotation and translation
  fine), shots dropped because they match nothing, areas no scan covers, and
  the sleeve step, which applies to a sleeved page on the glass as to a
  photographed one.

The print step's `low_resolution` warning tells a photographer to shoot
closer; for a scanned page the album step words it as "scan at a higher
resolution", with the scan's dpi.

`PageOptions` gains `source: "photo" | "scan"`. The album step sets it per
page by reading the shots' EXIF (`albumproc/scanmark.py`), before computing
the page's process key, so changing a page from photos to scans reprocesses
it. `albumproc page` does the same. The page metadata gains `source` and
`scan_dpi` (the shots' common `XResolution`, or null when they differ), and
`SCHEMA_VERSION` goes to 6.

### The outbox

The phone app queues upload operations in Room; the desktop app does the same
in SQLite under `~/.local/share/albumscan/outbox.db`, with shot files in
`outbox/shots/` next to it. One table, `ops(seq, kind, album_id, page_id,
shot_id, sha256, path, body, created)`, ordered by `seq`:

| kind | request |
| --- | --- |
| `album_meta` | `PUT /api/v1/albums/{album}` with `body` (a snapshot of the metadata at the time) |
| `put_shot` | `PUT …/pages/{page}/shots/{shot}` with the file at `path` and `X-Content-SHA256` |
| `delete_shot` | `DELETE …/pages/{page}/shots/{shot}` |

A new `album_meta` replaces any unsent one for the same album, since only the
latest metadata matters. Deleting a shot whose `put_shot` is unsent removes
that row and its file in one transaction and queues nothing (Requirement
7.8). A shot file is written under a temporary name, flushed, renamed, and
only then is its row committed (Requirement 7.1); startup removes files with
no row.

The sender is one thread that sends the head of the queue and removes it on
success. Results are classified by `outbox.classify(status)`:

| result | action |
| --- | --- |
| 200, 201, 204 | done: delete the row (and file) |
| connection error, timeout, 5xx, 429 | retry the same row after 1, 2, 4 … 60 s |
| 401 | blocked: ask for a token (Requirement 3.3) |
| 409, 422, other 4xx | blocked: show the server's `detail` (Requirement 7.5) |

A blocked outbox stays blocked until the user acts: entering a token, or
choosing "Discard this upload" on the blocked request after reading why. A
`delete_shot` answered 404 cannot happen (the server answers 204 for absent
shots), so it needs no case.

### The session

`Session` holds the open album's metadata (id, name, page size, created, page
ids in order) and the current page's shots, and enforces the limits before
anything is scanned (Requirement 5.6). Every change goes through it: it
updates its state and queues outbox operations in one call, so the window
never talks to the outbox directly. The page order sent to the server always
comes from the session's own list, which starts as the server's list when an
album is opened (Requirement 4.3) and only ever grows at the end.

Shots on the current page are either local (in the outbox, previewed from the
file) or remote (previewed from `?size=thumb`). Opening an album downloads
nothing but its metadata, the last page's shot list and that page's
previews.

Next page with an empty current page stays put (Requirement 5.5), so pressing
Enter twice never leaves an empty page that would show as `empty` in the
album status forever.

### Scanning

`sane.Scanner(device).scan(resolution, on_progress, cancel_event)` runs:

```
scanimage -d DEV --format=pnm --mode Color --resolution R -l 0 -t 0 -x XMAX -y YMAX --progress -o TMP
```

`--format=pnm` avoids slow PNG compression of a 100 MB frame; Pillow reads
it. Progress lines (`Progress: 42.1%`) are parsed from standard error.
Cancel sends SIGTERM, which `scanimage` turns into `sane_cancel`, and waits.
A non-zero exit raises `ScanError` with the last line `scanimage` wrote.
Video devices (`v4l:`) are left out of the list, since SANE offers webcams
as scanners. Options are read from `scanimage -d DEV -A`: `--resolution` as a list
(`75|150|300|600dpi`) or a range (`4..1200dpi`), `-x`/`-y` as ranges whose
maximum is the glass size; a device whose mode list lacks `Color` is
refused. The `-x`/`-y` units are millimetres in every SANE backend the app
supports; if `-A` reports them in pixels the app omits the area options and
lets the backend scan its default (whole) area.

The scan runs on a worker thread; it posts progress and the result back to
the GTK main loop with `GLib.idle_add`. Encoding (Pillow: rotate, JPEG
quality 95, `subsampling=0`, EXIF from `Image.Exif`) runs on the same
thread, then `Session.add_scan(path, sha256)` on the main loop.

### Feedback

While the current or previous page is `changed`, `ready` or `processing`,
the window polls `GET /status` every 3 seconds and shows each page's state
and warning messages (Requirement 9.1); otherwise it stops polling.
"Open print" downloads `/pages/{page}/print` to a temporary file and opens
it with the desktop's default viewer (`Gio.AppInfo.launch_default_for_uri`).

### Settings

`~/.config/albumscan/settings.json`: server URL, scanner device, resolution,
placement per album id, last album id. The token lives alone in
`~/.config/albumscan/token`, created with mode 0600 (Requirement 3.2).

## Components

```
scanner/
  pyproject.toml           # albumscan; dependencies: Pillow (PyGObject from the distribution)
  albumscan.desktop
  src/albumscan/
    __init__.py            # __version__
    __main__.py, cli.py    # albumscan [--list-scanners]
    sane.py                # list_devices, read_options, Scanner.scan, ScanError
    shot.py                # encode(image, placement, ScanInfo) -> bytes; MARKER
    client.py              # Client: ping, albums, album, page, thumb, put_album, put_shot, delete_shot, status, print_image
    outbox.py              # Outbox (SQLite), classify, Sender thread
    session.py             # Session, LimitError, new album / open album
    settings.py            # Settings load/save, token file
    window.py              # GTK 4 window (the only module importing gi)
  tests/
    fake_scanimage.py      # stands in for scanimage on PATH
    fake_server.py         # http.server recording requests, scripted answers
    test_sane.py, test_shot.py, test_client.py, test_outbox.py, test_session.py, test_settings.py
    test_e2e_server.py     # real albumserver serve, skipped when it is not installed
backend/src/albumproc/
  scanmark.py              # is_scan(path), scan_dpi(paths)
  pipeline.py              # PageOptions.source; scan mode branches; report.source, report.scan_dpi
  album.py, cli.py         # set source per page from scanmark
```

## Testing

- `sane.py` against `fake_scanimage.py` (device lists, option parsing for
  list and range forms, progress, failure, cancellation) and against
  `scanimage -d test` when available.
- `shot.py`: placement turns the pixels, the EXIF tags read back with
  Pillow, JPEG without subsampling, no orientation tag.
- `outbox.py` against `fake_server.py`: order kept across restarts, each row
  of the classification table, coalesced metadata, deleting an unsent shot,
  temp files and orphans cleaned at startup.
- `session.py`: limits refused before scanning, Next page on an empty page,
  opening an album makes its last page current, metadata snapshots queued.
- End to end: a session scanning with the fake scanner into a real
  `albumserver`, checking the stored bytes and the album's page order.
- `albumproc`: `is_scan` on marked, unmarked and EXIF-less JPEGs; a
  synthetic flat page saved as marked JPEGs is processed in scan mode with
  its true aspect, no curvature, no glare or single-coverage warnings, and
  `source`/`scan_dpi` in the report; a page of two overlapping marked scans
  is stitched; a page mixing photos and scans is processed as photos.

The window is checked by hand with the real scanner.
