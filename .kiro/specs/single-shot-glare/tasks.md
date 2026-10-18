# Implementation plan: glare only one shot sees

Each task ends with passing tests. Backend paths are under `backend/`, app
paths under `android/`. Requirement numbers refer to `requirements.md`.
Task 1 is a gate: it runs on the private real pages, and tasks 3 to 5 start
only if its results meet Requirement 1.7. Task 2 does not depend on it.
Real photos, crops, masks and labels stay in
`crawlins/photo-album-archiver-data`; only code and numbers come here.

- [x] 1. Replay on real pages (the gate)
  - [x] 1.1 `tools/replay_glare.py`: `reference`, `truth` (reusing
    `real_glare_data`'s gain and shift helpers), `stitch_pairs`, `replay`
    with best and worst prompted shot, `summarise`; refuse an output path
    inside the working tree.
    - _Requirements: 1.1 to 1.5, 7.4_
  - [x] 1.2 `tests/test_replay.py` on synthetic stitch pages with three or
    more shots: truth against the generator's mask, a prompted shot removes
    the region's glare, summary shares, output path refused.
    - _Requirements: 1.2, 1.4, 1.5, 7.4_
  - [x] 1.3 Run it by hand on `real/glare-*` with both detectors. Record the
    numbers, the date and the commit in the notes below, the README's glare
    section and the `TODO.md` training entry. Decide against Requirement
    1.7 and say which way in the PR.
    - _Requirements: 1.6, 1.7, 5.2_

- [x] 2. Glare seen in single coverage
  - [x] 2.1 `Region.glare_seen`, `QualityParams.glare_seen`, the residual
    passed into `single_coverage_regions` when the detector is on;
    `glare_seen_regions` and the two message forms in `page_warnings`;
    schema version up by one; `page --glare-seen`.
    - _Requirements: 2.1 to 2.6_
  - [x] 2.2 Tests: `glare_seen` on drawn masks, omitted with the detector
    off, both messages; pipeline image hash unchanged, metadata
    deterministic, the glared half of a `sleeve` stitch meets the threshold.
    - _Requirements: 2.1 to 2.6, 7.1, 7.2_
  - [x] 2.3 App: parse `glareSeen` and `glareSeenRegions`; dashed
    rectangles for regions without glare seen, in the review screen and
    the page view; parsing and styling tests.
    - _Requirements: 2.7, 2.8, 7.3_

- [x] 3. Detector for the server's runs
  - [x] 3.1 `Settings.glare_detector` (default `features`) passed through
    the worker into `AlbumOptions`; a test that the configured detector
    reaches the page options and the status carries `glare_seen`.
    - _Requirements: 2.7, 5.1_
  - [x] 3.2 If task 1.3's numbers meet Requirement 5.3, set
    `glare_detector = "net"` in `deploy/albumserver.toml` and say why in
    the commit; otherwise leave it and record why here.
    - _Requirements: 5.3_

- [x] 4. Other pages flagged on the capture screen
  - [x] 4.1 `CaptureViewModel.otherWarned` and the vibrate-once set.
    - _Requirements: 3.1, 3.3, 3.5, 3.6_
  - [x] 4.2 `OtherPageNotice` on the capture screen, its tap through the
    helper shared with the page overview's tile tap.
    - _Requirements: 3.1, 3.2, 3.4_
  - [x] 4.3 View model tests and one emulator test with a fake server
    warning an earlier page.
    - _Requirements: 3.1 to 3.6_

- [x] 5. Leaving an album with work left
  - [x] 5.1 Warned-page counts in the album drawer.
    - _Requirements: 4.1_
  - [x] 5.2 `LeaveAlbumGuard` on album switch, new album and the server
    album list; "Stay" opens the page overview; polling while open.
    - _Requirements: 4.2 to 4.5_
  - [x] 5.3 Unit tests for when it asks and what each answer does.
    - _Requirements: 4.2 to 4.5_

- [ ] 6. Records
  - [x] 6.1 `TODO.md` entry to revisit training once a full album has
    been shot (added with this spec).
    - _Requirements: 6.1, 6.2_
  - [x] 6.2 README: the notice, the leave question, `glare_seen`, and the
    replay numbers from task 1.3.
    - _Requirements: 1.6_

## If option A (training) is chosen instead

Tasks 1 and 6 stay. Tasks 4 and 5 are deferred, and these take their place:

- [ ] A1. Label every real page with `tools/real_glare_data.py` into the
  private repository, and hold out a whole album for testing.
- [ ] A2. Extend `tools/train_glare_net.py`: a whole-page context branch,
  the page's low-frequency brightness as an input, synthetic `clean_white`
  pages and print borders as hard negatives.
- [ ] A3. Score with the replay (task 1) and with the held-out album: recall
  at least 0.5, false alarms at most 0.2% of clean area, no `glare` warning
  on `clean_white`. Make `net` the default only if all three hold.

## Notes from the implementation

### Replay on the real pages (task 1.3), 2026-10-17

Run by hand on the 19 `real/glare-*` pages of
`crawlins/photo-album-archiver-data`, on `main` at `62e9eb1` with
`tools/replay_glare.py` as added in the PR after it, both detectors, 6
stitch pairs and 4 whole-page shots per page at most, composed pages at
2400 px (68 minutes). Shares are of single-shot glare area, summed over
replays.

| Start | Replays (pages) | Single-shot glare, share of page | Removed, 1 prompted shot | Removed, 2 | Removed, 2 (worst shot) |
| --- | --- | --- | --- | --- | --- |
| Stitch pair | 3 (1) | 0.7% | 24% | 93% | 92% |
| One whole-page shot | 9 (8) | 13.4% | 53% | 54% | 66% |
| All | 12 (9) | 11.4% | 52% | 55% | 67% |

- Glare in single coverage outside every warned region: 0% of all glare,
  with either detector (`single_coverage` regions are exact, so the seam
  band below the 1% minimum held none at the label threshold). 99.8% of all
  glare lies inside a warned region; the rest is where shots overlap.
- Requirement 5.2: `features` flags no region at the 2% glare-seen
  threshold; `net` flags 5 regions holding 45% of the single-shot glare
  area, none of them without truth glare.
- 42 replays did not count: 33 composed another outline than the
  reference's (one photo taken for the whole photo or the neighbouring
  page), 5 dropped a shot, 4 could not be laid on the reference. Of the 19
  pages, 10 gave a counted replay: the rest have neither a stitch pair nor
  a whole-page shot whose run finds the same page.

**Decision against Requirement 1.7: not met.** Two prompted shots remove
55% of the single-shot glare, well under 80%; the second condition (at most
10% of glare unwarned) holds. Tasks 3 to 5 stop until the design is
revisited. What the replays show: after the prompted shot the area is
covered twice, the `single_coverage` warning goes away, and on the
one-photo starts much of the glare stays, because the shot covering most
of the rectangle is usually the next one taken, from about the same
angle, with its glare in about the same place. A less fully covering shot
(the worst rule) removes more. The stitch pairs, whose two shots point at
different halves, did meet the bar, but they are 3 replays of one page.

**Revisit, 2026-10-18: stitched pages.** Clark chose to rest the guarantee
on pages stitched from shots aimed at different parts (design, "Revisit:
stitched pages"). Read on the stitch starts, the gate is met (93% removed,
0% unwarned; Requirement 1.8), so tasks 3 to 5 go ahead, with the first
real album shot with the app as the check on 3 replays of one page.

Points raised before the revisit, kept for that check:

- The guarantee may need "two shots whose glare does not coincide" rather
  than "two shots": a shot from a clearly different angle (the prompt says
  so, but nothing checks it), or a third shot wherever two agree on a
  bright area.
- The glare album was not shot under the overlap guarantee, so the
  prompted shot here is picked from shots taken for other reasons; a real
  album shot with the app's prompts is the better test.

Departures from the design in the tool: one-photo starts besides stitch
pairs (only 4 stitch pairs exist on the 19 pages); each composition is laid
on the reference grid through a shot both runs used, then corrected by
feature matching on one grid (matching the composed pages directly failed
when one photo gave another outline at another scale); compositions of
another outline are skipped; a stitch pair's shots must each add at least
a fifth of the page.

Tasks 3.1, 4 and 5 were written before the gate's result was known, held
back when it failed, and added after the revisit unchanged.

Task 3.2: Requirement 5.3 holds for `net` on these replays (5 regions met
the glare-seen threshold, none without truth glare, holding 45% of the
single-shot glare area, against none for `features`; on the stitch starts
alone, 1 region and 3.5%), so `deploy/albumserver.toml` sets
`glare_detector = "net"`. The evidence is thin, five regions, and the
setting is one line to change back. `albumproc page` keeps `features`.

Task 4.3's emulator test runs in CI; on this machine the emulator run
failed in setup (WorkManager not initialised) for the existing tests too. Task 2.2's pipeline test uses the `net`
detector: `features` sees under 2% of either half of every synthetic
two-shot stitch tried, sleeve or spot glare.
