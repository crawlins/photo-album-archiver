# Implementation plan: glare only one shot sees

Each task ends with passing tests. Backend paths are under `backend/`, app
paths under `android/`. Requirement numbers refer to `requirements.md`.
Task 1 is a gate: it runs on the private real pages, and tasks 3 to 5 start
only if its results meet Requirement 1.7. Task 2 does not depend on it.
Real photos, crops, masks and labels stay in
`crawlins/photo-album-archiver-data`; only code and numbers come here.

- [ ] 1. Replay on real pages (the gate)
  - [ ] 1.1 `tools/replay_glare.py`: `reference`, `truth` (reusing
    `real_glare_data`'s gain and shift helpers), `stitch_pairs`, `replay`
    with best and worst prompted shot, `summarise`; refuse an output path
    inside the working tree.
    - _Requirements: 1.1 to 1.5, 7.4_
  - [ ] 1.2 `tests/test_replay.py` on synthetic stitch pages with three or
    more shots: truth against the generator's mask, a prompted shot removes
    the region's glare, summary shares, output path refused.
    - _Requirements: 1.2, 1.4, 1.5, 7.4_
  - [ ] 1.3 Run it by hand on `real/glare-*` with both detectors. Record the
    numbers, the date and the commit in the notes below, the README's glare
    section and the `TODO.md` training entry. Decide against Requirement
    1.7 and say which way in the PR.
    - _Requirements: 1.6, 1.7, 5.2_

- [ ] 2. Glare seen in single coverage
  - [ ] 2.1 `Region.glare_seen`, `QualityParams.glare_seen`, the residual
    passed into `single_coverage_regions` when the detector is on;
    `glare_seen_regions` and the two message forms in `page_warnings`;
    schema version up by one; `page --glare-seen`.
    - _Requirements: 2.1 to 2.6_
  - [ ] 2.2 Tests: `glare_seen` on drawn masks, omitted with the detector
    off, both messages; pipeline image hash unchanged, metadata
    deterministic, the glared half of a `sleeve` stitch meets the threshold.
    - _Requirements: 2.1 to 2.6, 7.1, 7.2_
  - [ ] 2.3 App: parse `glareSeen` and `glareSeenRegions`; dashed
    rectangles for regions without glare seen, in the review screen and
    the page view; parsing and styling tests.
    - _Requirements: 2.7, 2.8, 7.3_

- [ ] 3. Detector for the server's runs
  - [ ] 3.1 `Settings.glare_detector` (default `features`) passed through
    the worker into `AlbumOptions`; a test that the configured detector
    reaches the page options and the status carries `glare_seen`.
    - _Requirements: 2.7, 5.1_
  - [ ] 3.2 If task 1.3's numbers meet Requirement 5.3, set
    `glare_detector = "net"` in `deploy/albumserver.toml` and say why in
    the commit; otherwise leave it and record why here.
    - _Requirements: 5.3_

- [ ] 4. Other pages flagged on the capture screen
  - [ ] 4.1 `CaptureViewModel.otherWarned` and the vibrate-once set.
    - _Requirements: 3.1, 3.3, 3.5, 3.6_
  - [ ] 4.2 `OtherPageNotice` on the capture screen, its tap through the
    helper shared with the page overview's tile tap.
    - _Requirements: 3.1, 3.2, 3.4_
  - [ ] 4.3 View model tests and one emulator test with a fake server
    warning an earlier page.
    - _Requirements: 3.1 to 3.6_

- [ ] 5. Leaving an album with work left
  - [ ] 5.1 Warned-page counts in the album drawer.
    - _Requirements: 4.1_
  - [ ] 5.2 `LeaveAlbumGuard` on album switch, new album and the server
    album list; "Stay" opens the page overview; polling while open.
    - _Requirements: 4.2 to 4.5_
  - [ ] 5.3 Unit tests for when it asks and what each answer does.
    - _Requirements: 4.2 to 4.5_

- [ ] 6. Records
  - [x] 6.1 `TODO.md` entry to revisit training once a full album has
    been shot (added with this spec).
    - _Requirements: 6.1, 6.2_
  - [ ] 6.2 README: the notice, the leave question, `glare_seen`, and the
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

(Replay results go here.)
