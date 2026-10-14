# Design: overlap coverage

## Overview

Glare removal in this project is the comparison between overlapping shots in
`fuse()`. Everything else about glare is reporting. The glare detector spec
tried to make the report good enough to stand in for a second shot and could
not: on real sleeved pages the sheen the single-shot detectors miss is a soft
veil over a large part of the page, which one photo cannot separate from the
page's lighting. The measured numbers are in `backend/tools/train_glare_net.py`
and the README.

This design makes the second shot the guarantee. The pipeline already knows,
from the shot masks it fuses with, which page pixels only one shot reached.
It reports that as a `single_coverage` warning with regions, in the same shape
as the uncovered regions, so that everything downstream (the metadata file,
the album report, the server's status, the app) carries it the same way. The
app reads page feedback from the server's status endpoint, which already
returns each page's warnings, and shows it on the capture screen with a way to
add shots to the page it concerns.

```
shots ─▶ process_page ─▶ fuse() masks ─▶ coverage count per pixel
                                              │
                      count == 0 ─▶ uncovered regions      (exists)
                      count == 1 ─▶ single-coverage regions (new)
                                              │
                            PAGE.json: glare, uncovered, single_coverage, warnings
                                              │
                  albumserver worker ─▶ page.warnings ─▶ GET /albums/{id}/status
                                              │
                  app: status poll ─▶ feedback banner ─▶ "Add shots to page N"
```

## Key decisions

### Single coverage is a property of the masks, not of the detector

The coverage count is `sum(masks_small)`, already computed in
`pipeline._assess` for the glare regions' `single_shot` cause. Single coverage
is `count == 1`; it needs no image content and no threshold, so it is exact
and deterministic in a way no detector is. It is reported whether or not the
glare detector is enabled.

### The seam is not a warning

Two overlapping shots always leave a band of single coverage along the edge
of each, as wide as the shot's feather and the mask erosion. That band is
real but harmless, and warning about it on every page would teach the user to
ignore the banner. The minimum single-coverage area is therefore 1% of the
page, twenty times the glare and uncovered minimum, and regions are found
after a morphological opening of 3 grid pixels, which removes bands narrower
than that. A single shot's whole area, or a stitched page's halves, are far
above the minimum.

### A page shot once is always warned

Requirement 1.6 is the degenerate case: one used shot, count is 1 everywhere
covered, one region of the whole page. It is reported rather than special-
cased away because it is the most common way to end up with glare: the user
took one shot and moved on.

### Feedback is pulled by the app, not pushed

The server has no connection back to the phone and the app already polls
nothing. A 10 second poll of the status endpoint while the capture screen or
overview is open is cheap (one small JSON response), needs no new server
work, and stops with the screen. Polling after each completed upload makes the
common case fast: the last shot of a page uploads, the server's idle period
runs, the page is processed, and the next poll sees the result.

### The server's idle period drops to 10 seconds

With 30 seconds of idle time plus a run of 20 to 60 seconds, feedback for a
page arrives a minute or more after the last shot, by which time the user is
two pages on. Ten seconds is long enough that a burst of shots of one page is
not split into two runs (the server also cancels or re-queues a run when a
shot arrives during it), and short enough that a user who pauses to check the
page gets the answer before moving on. It is a default in the server's
configuration file, not a constant.

### The target page can be an earlier page

The Android app's rule that new shots always go to the last page kept the
MVP simple. It is relaxed only through the banner: the user can make the page
the banner names the target page, add shots, and come back. Everything else
about pages (numbering, "Next page", deletion, the shot actions) is
unchanged, and "Next page" from an earlier target page returns to the last
page rather than creating one, so there is no way to make pages out of order.
The target page id is stored with the album; a deleted target falls back to
the last page.

### The detectors stay, their targets go

Both single-shot detectors remain available and their tests keep the
properties that matter to the user: no false `glare` warnings on glare-free
pages. Their recall floors become regression floors set at what each reaches
today. The spec's numbers are rewritten so that the test suite asserts only
what holds.

## Components

### `regions.py` (extended)

- `single_coverage_regions(count, used, masks, page_size, q)`: `count == 1`,
  opened with a 3 × 3 kernel, through `find_regions` with
  `q.min_single_area`; each region gets `shots=[the one covering shot]`
  (the shot whose mask covers most of the region) and `edges` as uncovered
  regions do; `cause` and `severity` stay `None`.
- `QualityParams.min_single_area: float = 0.01`.
- `page_warnings(glare, uncovered, single, dropped)`: adds the
  `single_coverage` warning:

  ```json
  {"code": "single_coverage", "fraction": 0.31, "regions": 1, "locations": ["right"],
   "message": "Only one photo covers the right (31% of the page). Take another shot of that part from a different angle so that glare there can be removed."}
  ```

  With more than one region the message names the largest and counts the
  rest: "... covers the right and 2 other areas (38% of the page) ...".

### `pipeline.py` (extended)

- `_assess` computes `single = {"fraction", "regions"}` from the existing
  `count`, puts it in `_Quality` and `PageReport.single_coverage`, and passes
  it to `page_warnings`. `SCHEMA_VERSION = 3`. `PageResult` gains
  `single_coverage: np.ndarray | None` (bool, full resolution) for `--masks`.
- `quality_overlay` takes the single-coverage regions as a third list, drawn
  in yellow.

### `cli.py` (extended)

- `page --min-single-area` (default 0.01) into `QualityParams`.
- `--masks` also writes `<name>.single.png`.

### `album.py` (unchanged in code)

The album step copies page warnings; the new code passes through. Its test
gains one assertion.

### `albumserver` (configuration)

- `deploy/albumserver.toml` and the config default: `idle_period_s = 10`.
- The status endpoint is unchanged; it already returns the warning objects
  whole.

### Android app

- `ServerClient.albumStatus(albumId)`: `GET /api/v1/albums/{id}/status`,
  parsed into `AlbumStatus(pages: List<PageStatus>)` with `id`, `number`,
  `state`, `lastRun`, `warnings: List<PageWarning>` where `PageWarning` keeps
  `code`, `message`, and the optional `fraction`, `locations`, `regions`.
- `PageFeedback` entity in the local store (page id, state, warnings JSON,
  fetched at), written by a `FeedbackPoller` started by the capture and
  overview view models: on start, on each `UploadCompleted` event for the
  album, and every 10 seconds; cancelled when the screen's lifecycle stops.
  Failures are logged and the next poll retried; nothing is shown.
- `CaptureViewModel`: `feedbackBanner: StateFlow<FeedbackBanner?>` derived
  from the feedback of the target page and the page before it, filtered to
  pages whose shots are all uploaded (the upload queue has nothing for that
  page), ordered as Requirement 4.3 says. `FeedbackBanner(pageId, number,
  message, isTargetPage)`.
- Target page: `Album.targetPageId` (null means the last page). `CaptureViewModel`
  exposes `setTargetPage(pageId)` and `backToLastPage()`; the capture screen's
  page number, thumbnail strip, shot count and "Next page" button read the
  target page. "Next page" is labelled "Back to page M" when the target is not
  the last page and calls `backToLastPage()`. Shots are saved with the target
  page's id; the upload queue is unchanged (it carries page ids already).
- Page overview: each tile shows the page's state word and, when its feedback
  has warnings, a warning icon; tapping the icon shows the messages.

## Page metadata example (schema 3, the new part)

```json
{
  "schema_version": 3,
  "single_coverage": {
    "fraction": 0.3104,
    "regions": [
      {"bbox": [0.62, 0.0, 1.0, 1.0], "bbox_px": [2480, 0, 4000, 3200], "area": 0.3104,
       "location": "right", "edges": ["top", "right", "bottom"], "shots": [1]}
    ]
  },
  "warnings": [
    {"code": "single_coverage", "fraction": 0.3104, "regions": 1, "locations": ["right"],
     "message": "Only one photo covers the right (31% of the page). Take another shot of that part from a different angle so that glare there can be removed."}
  ]
}
```

## Error handling

- A page with no used shots has no single coverage and no warning (the
  existing `photos_dropped` path covers it).
- The app treats a missing `single_coverage` key, an unknown warning code, or
  a status request failure as "no feedback" for that page.
- A target page that no longer exists (deleted on the phone or on the server
  by the shot actions) falls back to the last page the next time the capture
  screen reads it.

## Testing strategy

- **Regions** (`tests/test_quality.py`): on drawn masks, two overlapping
  halves give one region per half minus the seam, the seam alone gives none,
  one shot gives one region of the whole page with `edges` on all four
  sides, and `shots` names the right shot.
- **Pipeline** (`tests/test_pipeline.py`): the `stitch` cases get a
  `single_coverage` warning whose regions cover ≥ 90% of `count == 1`; the
  three-shot `glare` cases get none and a residual fraction ≤ 0.5%; the
  composed image hash is unchanged; metadata is identical for identical
  inputs; `schema_version` is 3.
- **Detector floors**: the existing recall assertions are kept at today's
  values as regression floors, for `features` and for `net`.
- **CLI**: `--masks` writes the third mask; `--min-single-area 0.5` removes
  the warning on a stitched page.
- **Album**: the album report carries a page's `single_coverage` warning.
- **Server**: the idle default is 10 seconds in the parsed configuration;
  the status test asserts a `single_coverage` warning is returned with its
  `locations`.
- **App** (unit tests with the fake server): the poller fetches on open,
  after an upload and on the timer, and stops on close; the banner shows the
  right page and code order; "Add shots to page N" retargets saving and
  upload; "Back to page M" returns without creating a page; a deleted target
  falls back; no server means no banner. An emulator test opens the banner
  and takes a shot into the earlier page.

## Out of scope

- Estimating overlap on the phone before upload. It would need registration
  on the device; the server already does it.
- Live glare feedback from a single shot; the detectors' numbers say it is
  not worth a banner.
- Changing how `fuse()` blends overlapping shots.
- Reordering pages or adding shots to pages other than the one the banner
  names.
