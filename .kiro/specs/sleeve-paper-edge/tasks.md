# Implementation plan: the paper's edge inside an album sleeve

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/` unless they start with `real/`, which is the private data
repository linked into the repository root. Requirement numbers refer to
`requirements.md`. Never commit real photos or crops of them to this
repository.

- [ ] 1. Labels and measurements on the real pages
  - [ ] 1.1 Mark the paper corners, 12 to 20 points along each paper edge and
    the binding side on the five two-shot pages, in `real/<page>/paper.json`
    (format in `design.md`), on zoomed crops of the full-resolution shots.
    Overlay them on the shots and have Clark confirm them by eye. Commit to
    the data repository.
    - _Requirements: 8.5, 8.6, 8.8_
  - [ ] 1.2 With a throwaway script outside the repository, measure on the
    five pages and on five `glare-*` pages: crimp width and ridge period, the
    ridge score of crimp, paper and background bands, gap, strip and clear
    margin widths, and how far the paper sits from the sleeve's edge on each
    side. Record the numbers (not the images) in the PR description and set
    the `SleeveParams` defaults from them.
    - _Requirements: 1.2_

- [ ] 2. Synthetic sleeved pages
  - Add `SleeveSpec` and `sleeve=` to `synth.make_case` for the `flat`,
    `stitch` and `curved` kinds, drawn in page coordinates as in `design.md`,
    with paper and sleeve corners and band kinds kept as truth, and
    `--sleeve` on `albumproc synth`. Existing kinds and seeds render as
    before.
  - _Requirements: 8.1, 4.2_

- [ ] 3. Edges and bands
  - [ ] 3.1 Move the corridor sampling and sub-pixel refinement out of
    `trace_outline` into shared helpers; `trace_outline`'s output is
    unchanged (existing curvature tests pass untouched).
    - _Requirements: 4.2_
  - [ ] 3.2 Create `sleeve.py` with `SleeveParams`, `Band`, `SideFinding`,
    `SleeveFit`, `corridor` and `candidate_paths` (16 segments, peaks,
    dynamic-programming linking, support).
    - _Requirements: 1.1_
  - [ ] 3.3 `classify_bands`: width, ridge, texture, colour, likeness to the
    outside and to the paper sample, and the rules in `design.md`.
    - _Requirements: 1.2_
  - [ ] 3.4 Write `tests/test_sleeve.py` for the ridge score, each band kind
    on hand-built strips, and paths that follow a 1.5% bow without hopping
    to a parallel edge 1% away.
    - _Requirements: 1.1, 1.2_

- [ ] 4. The paper's outline
  - [ ] 4.1 `choose_paper`: candidates per side, end support, convexity and
    angles, outermost wins, one missing side allowed, `few_sides` and
    `no_sleeve_band`.
    - _Requirements: 1.3, 1.4, 1.6, 6.3_
  - [ ] 4.2 `trace_path`: refinement within ±0.3% of the width with the
    path's own edge sign, bridging up to 20% of a side with a quadratic,
    corners from the end tangents. `find_sleeve` ties 3 and 4 together and
    finds the sleeve outline and margins per side.
    - _Requirements: 1.5, 2.1-2.3_
  - [ ] 4.3 Tests: a print border 2% inside the paper is rejected and a
    full-bleed print's border is taken; bridging 15% works and 25% fails;
    every synthetic sleeved case meets Requirement 8.2 and 8.3; no existing
    synthetic case is sleeved.
    - _Requirements: 1.4, 2.2, 8.2, 8.3_

- [ ] 5. Pipeline, crop and curvature
  - [ ] 5.1 Add `sleeve` to `PageOptions`; call `find_sleeve` after
    `_choose_page`; use the paper corners for the aspect and the planar
    homography. Test that every existing case gives the same image hash and
    metadata with the sleeve step on and off.
    - _Requirements: 3.1, 3.3, 3.4, 4.1_
  - [ ] 5.2 Add `outline` and `binding_hint` to `fit_page`, pass the paper's
    traced sides and the strip side, and apply the neighbour factor once
    when both cues agree. Test Requirement 8.4 on sleeved `curved` cases at
    1%, 3% and 5% lift, and that a named binding side overrides the strip.
    - _Requirements: 5.1-5.4, 8.4_
  - [ ] 5.3 The sleeve crop: grow the output rectangle (planar) or the page
    fractions (curved) by the margins, with the opposite-side rule where the
    sleeve is not seen. Test the crop edges against the synthetic sleeve
    corners.
    - _Requirements: 3.2_

- [ ] 6. Report, options and debug output
  - [ ] 6.1 `sleeve` object in `PageReport`, `paper_edge_uncertain`
    warnings, `SCHEMA_VERSION` 4. Same inputs twice give identical metadata.
    - _Requirements: 6.1-6.6_
  - [ ] 6.2 `--sleeve` and `--crop` on `albumproc page` and `albumproc
    album`, `sleeve` and `crop` in `album.json` page entries and the cache
    key, and `sleeve.jpg` under `--debug`. CLI and album tests.
    - _Requirements: 7.1-7.3_
  - [ ] 6.3 Update the README's page-detection section and its known limits
    (the sleeve line under page curvature), and TODO.md's page-curvature
    entry.
    - _Requirements: 7.3_

- [ ] 7. Real pages
  - [ ] 7.1 Extend `tests/test_real.py`: paper corners and edge points
    against the labels, binding side under `force`, no `fit_failed` under
    `auto`, and the expected aspects replaced by the labelled paper aspects.
    - _Requirements: 4.3, 8.5, 8.6_
  - [ ] 7.2 A slow test that runs the 19 `glare-*` pages and checks that each
    is sleeved and completes.
    - _Requirements: 8.7_
  - [ ] 7.3 Time `process_page` with the sleeve step on and off on the real
    and synthetic pages. Report in the PR description, per real page, the
    curvature outcome before and after (reason, binding side, lift) and the
    composed page's aspect.
    - _Requirements: 9.1, 9.2_
