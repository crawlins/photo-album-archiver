# Design: glare only one shot sees

## Overview

Where two shots overlap, fusion removes glare. Where one shot covers the
page alone, its glare stays. There are two ways to deal with that glare:
find it in the one photo (a better detector, which means more training), or
make sure there is no such area by getting the user to take another shot
(the second-shot prompts the overlap coverage spec built). This design
compares the two, recommends the second, and lists the work that makes it a
guarantee the user can rely on rather than one that only holds when they
happen to open the page overview.

```
            shots of a page
                  │
           process_page ── fuse(): darker wins where ≥ 2 shots
                  │
     coverage count ── 1 ──▶ single-coverage regions ──┐
                  │                                     │ glare_seen (new)
     detector (features | net) ── residual glare ──────┘
                  │
            PAGE.json warnings ──▶ server status ──▶ app
                                                       │
           capture screen notice for other pages (new) │
           red dots, review rectangles (exists)        │
           "leaving with work left" question (new) ────┘
                  │
           the user takes the prompted shot ──▶ page re-run, glare removed

     offline: replay_glare.py on the private real pages measures how much
     single-shot glare the prompts remove, and how much lies outside them
```

## What is known

- Real pages: 19 pages of the "glare-training" album with 9 to 25 shots
  each, and five 2-shot pages, all in the private repository
  `crawlins/photo-album-archiver-data` (`real/`, symlinked into the public
  checkout and never committed there).
- Overlap labels: `tools/real_glare_data.py` turns any page with overlapping
  shots into per-shot glare and clean labels. It is how the net was trained,
  and it works on any page shot under the overlap guarantee.
- Detectors on the nine held-out real pages: `features` recall 0.002, `net`
  recall 0.096, both with false alarms on at most 0.01% of clean area. The
  net's recall ranges from 0.62 (`glare-19`) to 0.02 (`glare-18`, a veil
  over a third of the page). On synthetic pages the net flags white paper and
  print borders, which is why it is not the default.
- Prompts: `single_coverage` regions are exact (they come from the shot
  masks, not from image content) and every one carries shot boxes the app
  draws on the photo. They have been tested on synthetic pages and in the
  emulator, not yet on a real album shot on the phone (remaining tasks,
  item 1).
- Latency: a page is processed 10 s after its last shot and a run takes
  20 to 60 s, so its feedback arrives while the user is on the next page.
  The capture screen marks only the target page.

## Options

### Option A: train the detector further

Make the single-shot detector good enough that glare in single coverage is
found, and report it as a `glare` warning with the cause `single_shot`.

What it would take:

- **Data.** Run `tools/real_glare_data.py` on every page of every album shot
  so far and keep the labels in the private repository. Today that is 24
  pages; a full album shot under the guarantee adds 10 to 50 more, each with
  at least two shots everywhere and so labels over nearly the whole page.
- **Model.** The current net (88k parameters, about a fifth of the page in
  each output's view) cannot see a veil that spans half the page against the
  rest of the page. Try a wider context (a whole-page branch at 1/8 scale
  fused into the decoder), the page's low-frequency brightness as an input
  so that lighting fall-off is modelled rather than mistaken for sheen, and
  synthetic white paper and print borders as hard negatives so the net can
  become the default without false warnings.
- **Bar to become the default.** On held-out real pages from an album the
  net was not trained on: recall at least 0.5, false alarms on at most 0.2%
  of clean area, and no `glare` warning on the synthetic `clean_white`
  pages. Below that the user cannot rely on a missing warning, so the
  prompts would still be needed.
- **Cost.** PyTorch training outside the package (as now), a few hours of
  CPU per run, label generation per album, and the hard-negative work. No
  app change.

What it buys: glare found on pages the user cannot re-shoot (the album is
already back on the shelf), and fewer prompts on pages where the extra shot
was not needed.

Why it is uncertain: a soft veil over a large area is the same signal in one
photo as uneven light. More data narrows the gap between pages like
`glare-19` and `glare-18`, but no amount of it removes the ambiguity, and the
recall that matters (the user can skip the extra shot when no warning shows)
needs to be high on every page, not on average.

### Option B: rely on second-shot prompts

Keep "two shots over every part of a page" as the guarantee, and close the
gaps that stop it from holding on a real album: measure it, say where glare
was seen, show warnings for pages the user has left, and ask before the user
leaves an album with work outstanding.

What it buys: glare removed rather than reported, on every page the user
follows a prompt on, with no dependence on the detector's recall. The
detector's role shrinks to ranking: a single-coverage region where glare was
seen gets its shot first.

What it costs: one more shot on most stitched pages, including some that
had no glare; the app work below; and nothing for pages already shot whose
album is gone (they keep their warning in the album report, as now).

### Recommendation: B now, A after a full album

Option B, for three reasons:

1. **It removes glare instead of reporting it.** A comparison between two
   shots is exact wherever they overlap and their glare does not coincide;
   no detector is.
2. **It is mostly built.** The pipeline, server and app parts of the
   guarantee are merged. What is left is measurement and two app screens.
3. **It makes A cheaper later.** Every page shot under the guarantee is
   covered twice everywhere, so `tools/real_glare_data.py` labels nearly
   all of it. One full album gives more labelled real pages than the net was
   trained on, from a second album, which is also what an honest held-out
   test needs. Training now, on 15 pages of one album, would mostly fit that
   album.

Training is revisited once a full album has been shot (Requirement 6, a
`TODO.md` entry), with the replay results as the baseline a retrained net
must beat. The replay is also the check on this recommendation: if it shows
the prompts leave much of the glare (Requirement 1.7), the design is
revisited before the app work starts.

If Clark chooses A instead, Requirement 1 (the replay, which gives A its
baseline and its evaluation harness) and Requirement 6 still apply,
Requirements 3 and 4 are deferred, and `tasks.md` lists the tasks that
replace them.

### Revisit: stitched pages (2026-10-18)

The replay (`tasks.md` notes) failed Requirement 1.7 overall: two prompted
shots removed 55% of the single-shot glare. Split by how the page was first
shot, the two cases differ:

- **A page stitched from shots aimed at different parts** (each shot adding
  at least a fifth of the page the other does not): 93% removed after two
  prompted shots, 92% with the least covering prompted shot.
- **A page shot once, whole**: 54%. The prompted shot covering most of the
  page is then another whole-page shot, usually from about the same place,
  with its glare in about the same place; the warning clears and the glare
  stays.

Clark chose to rest the guarantee on stitched pages: a page is shot as a
stitch of shots aimed at different parts of it, and the prompts then ask
for the parts only one shot covers. The gate is read on the stitch starts
(Requirement 1.8), which meet it, and tasks 3 to 5 go ahead. Two things
qualify that:

- The stitch evidence is 3 replays of one page, because the glare album
  was mostly shot whole. The first real album shot with the app (remaining
  tasks, item 1) is the check; the replay runs on it unchanged.
- A page shot once, whole, keeps its `single_coverage` warning, which is
  what makes the user stitch it, but a second whole-page shot from the same
  place clears the warning without removing the glare. The README says to
  aim each shot at a different part of the page.

## Key decisions

### Truth from all the shots, not hand-drawn masks

Hand-drawn masks of sleeve glare are slow to make and disagree on where a
veil ends. The glare pages have 9 to 25 shots each, so the composition of
all of them is close to glare-free wherever any shot sees past the glare. A
replay composes a subset and compares it with that reference, using the same
thresholds, smooth gain and shift allowance as the overlap labels, so the
truth means the same thing as the net's training labels. Glare that every
shot of the page has (`all_shots_glared`) is in the reference too and so
counts as no glare in a replay; that case is out of scope here.

### Replays start from a stitch, and follow the rectangle

A user stitching a page takes two shots that meet in the middle. The replay
starts from every pair of shots that together cover the page with less than
half of it overlapping, capped per page so the run stays under an hour on
the 19 pages. A user following a prompt aims at the rectangle drawn on the
photo, so the prompted shot is the unused one that covers most of the
largest region's page box. That is a generous user (they get to pick the
best of the remaining shots) but not an unrealistic one: the rectangle is
exactly what they are shown. The replay also reports what the worst
remaining shot achieves, as a bound.

### `glare_seen` is a field, not a filter

The detectors miss most single-shot glare, so a region with nothing seen is
not shown to be clean, and the warning must still be raised. What the field
changes is order and emphasis: the message names a region with glare seen
first, and the app draws it solid rather than dashed. A precise detector
helps here even at low recall, which is why the net, at 0.01% false alarms,
may be worth using on the server (Requirement 5.3) while not being good
enough to replace the prompt.

### A notice, not a dot, for pages the user has left

The red dot works where the user looks for it. On the capture screen the
user looks at the album through the preview, and only the target page's
number carries a dot. A one-line notice above the bottom bar ("Page 3 needs
another shot"), with one short vibration when it first appears, reaches
them without taking over the screen. It names the lowest-numbered page
because that is the one furthest behind. Tapping it goes where a page tile
tap goes, so there is one path to the rectangle, not two.

### Ask once when leaving, never block

A user who knows a page is fine, or who has no time, must be able to leave.
The question is asked once per leave, on the three ways out of an album in
the app (switch album, new album, server album list), and "Stay" lands on
the page overview, where the dots already say which pages need what. Closing
the app is not intercepted; Android gives no reliable hook for it and the
drawer count (Requirement 4.1) shows the state on the next open.

### The detector default for the server is a configuration change

`albumproc page` keeps `features` as its default so the command-line
behaviour and the tests are unchanged. The server's runs choose their
detector through `albumserver.toml` (`glare_detector`), passed
into the `AlbumOptions` of its page runs, so switching it after the replay
is a configuration edit, reversible without a release.

## Components

### `tools/replay_glare.py` (new, not installed)

```
python tools/replay_glare.py ../real/glare-* --out ~/paa-replay/ [--pairs 6] [--detector both]
```

- `reference(page_dir) -> Ref`: `process_page` on all shots with the
  detector off; keeps the composed page on the fusion grid, its frame
  (homographies to the page) and the toned shots.
- `truth(composed_small, ref, params) -> mask`: bright excess over the
  reference after `real_glare_data`'s smooth gain and shift allowance, at
  its glare threshold; reuses its helpers rather than copying them.
- `stitch_pairs(ref, limit) -> list[tuple[int, int]]`: pairs whose union
  covers at least 98% of the reference page and whose overlap is under half
  of it, most different first, capped at `limit`.
- `replay(ref, pair, detector) -> Trace`: runs `process_page` on the pair
  with `PageOptions` fixing the page frame to the reference's (so masks and
  truth line up), reads `single_coverage` and `glare` regions, adds the best
  and the worst covering unused shot for the largest region, and repeats up
  to two prompted shots.
- `summarise(traces) -> dict` with the numbers of Requirement 1.5 per page
  and overall; written as `replay.json` under `--out` and printed as a
  table. `--out` defaults to a folder outside the repository and the tool
  refuses a path inside the repository's working tree.

Fixing the page frame needs `process_page` to accept a page frame from
outside; if that is not already possible through `PageOptions`, the tool
instead maps each replay's composed grid onto the reference's by the
homography between the two page frames, found the same way `evaluate._align`
does.

### `regions.py` and `pipeline.py` (extended)

- `single_coverage_regions(..., residual=None, threshold=0.5)`: when a
  residual map is given, each region's `glare_seen` is
  `(residual[m] >= threshold).mean()`; `Region.glare_seen: float | None`.
- `QualityParams.glare_seen: float = 0.02`.
- `page_warnings`: the `single_coverage` warning gains `glare_seen_regions`;
  the message builder takes the regions meeting the threshold, names the
  largest of them first ("Only one photo covers the right, and glare was
  seen there (31% of the page) ..."), and falls back to today's message
  when there are none.
- `_assess` passes `res` to `single_coverage_regions` only when
  `gp.enabled`. `SCHEMA_VERSION` increases by one. If another spec's change
  has taken the next number by the time this lands, this takes the one
  after; the field is optional either way.
- `cli.py`: `page --glare-seen`.

### `albumserver` (configuration)

- `Settings.glare_detector: Literal["features", "net"] = "features"`
  (`config.py`, a top-level key of `albumserver.toml`), passed to the
  worker's child-process target and from there as `GlareParams(detector=...)`
  into the page options of the `AlbumOptions` it gives `process_album`
  (today it passes `AlbumOptions()`). The status endpoint is unchanged:
  warnings are stored and served whole, so the new fields pass through.

### Android app

- `data/Feedback.kt`: `WarningRegion.glareSeen: Double?` and
  `PageWarning.glareSeenRegions: Int?`, both optional.
- `FeedbackUi.kt`: region rectangles take a `dashed` flag, set for a
  `single_coverage` region whose `glareSeen` is below the threshold (the
  app uses the warning's `glare_seen_regions` count and each region's
  value; with neither present every rectangle is solid, as today).
- `CaptureViewModel`: `otherWarned: StateFlow<OtherWarned?>` from
  `observeWarnedPages(albumId)` and the feedback's codes, excluding the
  target page and `incomplete_coverage`-only pages; holds the
  lowest-numbered page and the count of the rest. A `seenWarned` set in the
  view model (keyed by page id, cleared when a page's warnings clear)
  decides when to vibrate.
- `CaptureScreen`: `OtherPageNotice` above the bottom bar, tag
  `otherPageNotice`; tap routes to the review screen as
  `PageOverviewScreen`'s tile tap does (shared helper rather than a copy).
- `AlbumDrawer`: warned-page count per album from the feedback DAO.
- `LeaveAlbumGuard`: a small state holder used by the drawer's album
  switch, its "New album" and its server album list entry. When the
  current album has warned or pending pages it shows the "Stay" / "Leave
  anyway" dialog and keeps the album's `FeedbackPoller` running while open.
  Pending pages come from the existing `warnedPages` filter's other half:
  pages with queued uploads, or uploaded since their last run.

## Error handling

- The replay skips a page whose reference run fails or whose shots register
  into fewer than four used shots, and says so in its output.
- A replay pair that drops a shot (no match) is recorded as such and not
  counted in the removal shares.
- The app treats missing `glare_seen` fields as unknown, never as 0.
- A failed status poll while the leave question is open leaves the numbers
  as they were and the question answerable.

## Testing

- `tests/test_replay.py` on synthetic `stitch` pages with three or more
  shots (the synthetic generator's ground truth stands in for the
  reference): the truth mask matches the generator's glare mask within the
  shift allowance; a prompted shot that covers the region removes its glare;
  the summary's shares add up; the tool refuses an output path inside the
  repository.
- `tests/test_quality.py`: `glare_seen` on drawn masks and residual maps;
  omitted when the detector is off; `glare_seen_regions` and both message
  forms.
- `tests/test_pipeline.py`: the composed image is unchanged; the metadata is
  deterministic; schema version bumped; on a synthetic two-shot `sleeve`
  stitch, the glared half's region meets the glare-seen threshold with the
  `features` detector.
- `tests/test_server_*`: the configured detector reaches the page options;
  the status returns `glare_seen`.
- App unit tests: feedback parsing with and without the new fields; dashed
  versus solid rectangles; `otherWarned` excludes the target page and
  `incomplete_coverage`-only pages, picks the lowest number, counts the
  rest; vibrate once per appearance; `LeaveAlbumGuard` asks only when
  warned or pending pages exist and a server is configured.
- One emulator test: with a fake server warning page 1 while page 2 is the
  target, the notice appears, a tap opens the review screen at page 1's
  photo with the rectangle.
- The replay on real pages is run by hand, never in CI, because the photos
  are private.
