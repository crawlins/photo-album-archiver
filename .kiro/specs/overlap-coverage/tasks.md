# Implementation plan: overlap coverage

Each task ends with passing tests. Backend paths are under `backend/`, app
paths under `android/`. Requirement numbers refer to `requirements.md`.
Tasks 1 to 3 are one backend PR; task 4 is a small server PR or part of it;
task 5 is the app PR.

- [ ] 1. Single-coverage regions and warning
  - [ ] 1.1 Add `QualityParams.min_single_area` (0.01) and
    `single_coverage_regions` to `regions.py`: `count == 1`, a 3 × 3 opening,
    `find_regions`, `shots` set to the covering shot, `edges` kept.
    - _Requirements: 1.1, 1.2, 1.3_
  - [ ] 1.2 Add `shot_boxes` to `Region` and fill it in every region finder
    from the used shots' page-to-shot homographies and sizes; nearest shot
    for uncovered regions.
    - _Requirements: 1.3a_
  - [ ] 1.3 Add the `single_coverage` warning to `page_warnings`, with the
    one-region and several-regions messages; put `regions` and
    `all_shots_glared` on the warnings.
    - _Requirements: 1.5, 1.6, 1.11_
  - [ ] 1.4 Tests in `tests/test_quality.py`: halves, seam only, one shot,
    `shots` and `edges`, shot boxes under identity and affine maps, message
    text, warning fields.
    - _Requirements: 1.2, 1.3, 1.3a, 1.5, 1.6, 1.11_

- [ ] 2. Page metadata, masks and CLI
  - [ ] 2.1 In `pipeline.py`: compute `single_coverage` in `_assess`, add it
    to `PageReport` and `_Quality`, set `SCHEMA_VERSION = 3`, add the
    full-resolution mask to `PageResult`, draw the regions in
    `quality_overlay` and the debug output.
    - _Requirements: 1.4, 1.8, 1.10_
  - [ ] 2.2 In `cli.py`: `--min-single-area`, `<name>.single.png` under
    `--masks`.
    - _Requirements: 1.7, 1.8_
  - [ ] 2.3 Pipeline tests: `stitch` cases warn with ≥ 90% of the
    single-coverage area in regions; three-shot `glare` cases do not warn and
    have residual ≤ 0.5%; image hash unchanged; metadata deterministic;
    album report passes the warning through.
    - _Requirements: 1.9, 1.10, 2.1, 2.2, 5.1_

- [ ] 3. Detector targets rewritten
  - [ ] 3.1 Replace Requirement 9.2 in `.kiro/specs/glare-detector/
    requirements.md` with a pointer to this spec's Requirement 2, and add the
    measured real-page numbers to its implementation notes in `tasks.md`.
    - _Requirements: 2.5_
  - [ ] 3.2 Make the recall assertions in `tests/test_glare.py` and
    `tests/test_pipeline.py` regression floors at today's values, for both
    detectors; keep every no-false-warning assertion.
    - _Requirements: 2.3, 2.4_
  - [ ] 3.3 Update the README's glare section: what is guaranteed (two shots
    everywhere, checked by the app) and what is only warned about.
    - _Requirements: 2.5_

- [ ] 4. Server idle period
  - [ ] 4.1 Change the default `idle_period_s` to 10 in the server's config
    model and `deploy/albumserver.toml`; update its test and the album server
    spec's Requirement 8.3 note.
    - _Requirements: 3.1_
  - [ ] 4.2 In the worker, rewrite each region's `shot_boxes` keys from
    input index to shot id when storing a run's warnings; a status test that
    a processed stitched page returns the `single_coverage` warning with
    `locations`, `fraction` and shot boxes keyed by shot id.
    - _Requirements: 3.2_

- [x] 5. Page feedback in the app
  - [x] 5.1 `ServerClient.albumStatus` and the status models, regions and
    shot boxes included, unknown fields ignored; a fake-server test.
    - _Requirements: 4.1, 5.3_
  - [x] 5.2 `PageFeedback` entity and DAO; `FeedbackPoller` started and
    stopped with the capture, overview and review screens, triggered by
    upload completion and a 10 second timer; failures silent; `warnedPages`
    excluding pages with queued uploads.
    - _Requirements: 4.1, 4.2, 4.11, 4.12_
  - [x] 5.3 Red dots: overview tiles, both thumbnail strips, the capture
    screen's page number.
    - _Requirements: 4.3, 4.10_
  - [x] 5.4 Review screen: warning messages at the bottom, rectangles over
    the zoomable photo from the current photo's shot boxes, location words
    for regions without one; overview tap opens the first photo with a box.
    - _Requirements: 4.4, 4.5, 4.6, 4.10_
  - [x] 5.5 Target page: `Album.targetPageId`, "Add shots to page N" on the
    review screen, `setTargetPage`, `backToLastPage`, the capture screen
    reading the target page for its number, strip and shot saving, "Next
    page" relabelled and rerouted, restore on restart, fallback on deletion.
    - _Requirements: 4.7, 4.8, 4.9_
  - [x] 5.6 View model unit tests for 5.2 to 5.5 and one emulator test that
    taps a marked thumbnail, sees the rectangle, and takes a shot into the
    earlier page.
    - _Requirements: 4.1 to 4.12_
