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
  - [ ] 1.2 Add the `single_coverage` warning to `page_warnings`, with the
    one-region and several-regions messages.
    - _Requirements: 1.5, 1.6_
  - [ ] 1.3 Tests in `tests/test_quality.py`: halves, seam only, one shot,
    `shots` and `edges`, message text.
    - _Requirements: 1.2, 1.3, 1.5, 1.6_

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
  - [ ] 4.2 Add a status test that a processed stitched page returns the
    `single_coverage` warning with `locations` and `fraction`.
    - _Requirements: 3.2_

- [ ] 5. Page feedback in the app
  - [ ] 5.1 `ServerClient.albumStatus` and the status models; a fake-server
    test.
    - _Requirements: 4.1, 5.3_
  - [ ] 5.2 `PageFeedback` entity and DAO; `FeedbackPoller` started and
    stopped with the capture and overview screens, triggered by upload
    completion and a 10 second timer; failures silent.
    - _Requirements: 4.1, 4.2, 4.10_
  - [ ] 5.3 Target page: `Album.targetPageId`, `setTargetPage`,
    `backToLastPage`, the capture screen reading the target page for its
    number, strip and shot saving, "Next page" relabelled and rerouted,
    restore on restart, fallback on deletion.
    - _Requirements: 4.5, 4.6, 4.7_
  - [ ] 5.4 The feedback banner on the capture screen: page selection and
    code order, uploaded-only filter, "Add shots to page N", removal when the
    warning clears.
    - _Requirements: 4.3, 4.4, 4.8, 4.11_
  - [ ] 5.5 Page overview state word and warning mark with messages.
    - _Requirements: 4.9_
  - [ ] 5.6 View model unit tests for 5.2 to 5.5 and one emulator test that
    takes a shot into the earlier page from the banner.
    - _Requirements: 4.1 to 4.11_
