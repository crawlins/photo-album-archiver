# Implementation plan: page curvature correction

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/`. Requirement numbers refer to `requirements.md`. Start after
the two-pages PR (#1) has merged, since both change `page.py` and
`pipeline.py`, and the side-facing-a-neighbour cue uses its page candidates.

- [ ] 1. Dependency and synthetic curved pages
  - [ ] 1.1 Add `scipy>=1.10` to `pyproject.toml` dependencies.
    - _Requirements: 9.4_
  - [ ] 1.2 Add `kind="curved"` to `synth.make_case` with `lift`, `strip` and
    `binding`: 400 planar strips along the quadratic profile, each drawn with
    its own `warpPerspective` and masked in far to near, then the usual glare,
    exposure, noise and JPEG. Add a stitched two-part variant split across
    the binding. Expose `--lift`, `--strip` and `--binding` on
    `albumproc synth`. Existing kinds are unchanged.
    - _Requirements: 8.1, 4.3_
  - [ ] 1.3 Add `evaluate.geometry_error` and `evaluate.strip_mae`. Check that
    the planar path on a 3% and a 5% `curved` case exceeds 0.3% and record the
    numbers in a test, so that the target is shown to be meaningful.
    - _Requirements: 8.2, 8.3, 8.5_

- [ ] 2. Page surface model
  - Create `src/albumproc/curvature.py` with `CurvatureParams`,
    `AlignParams`, `PageSurface` (spline `θ`, arc-length integration,
    `project`, `profile`, `max_lift`, `strip_width`) for all four binding
    sides.
  - Write `tests/test_curvature.py` model cases: `θ ≡ 0` matches the
    `solvePnP` homography, unit arc length, and lift and strip width on a
    hand-made profile.
  - _Requirements: 2.1, 5.1, 5.2_

- [ ] 3. Fit the surface
  - [ ] 3.1 `trace_outline`: page contour within the band around the quad,
    split into sides at the corners.
    - _Requirements: 1.1_
  - [ ] 3.2 `fit_surface`: initial pose from `solvePnP` and `estimate_aspect`,
    chamfer residuals on the outline, smoothness and zero penalties,
    `least_squares` with soft-L1 loss.
    - _Requirements: 1.2_
  - [ ] 3.3 `find_lines`: LSD on the mosaic flattened by the current fit,
    long segments crossing the strip, collinear chaining, and straightness
    residuals added to a second round of the fit.
    - _Requirements: 1.2_
  - [ ] 3.4 `fit_page`: try each binding side (or the given one) and the
    plane, apply the neighbour bonus, the thresholds, the `force` and `off`
    modes and the fallback checks, and return `CurvatureFit` with its reason.
    - _Requirements: 1.2–1.6, 7.1_
  - [ ] 3.5 Tests on rendered outlines with noise: binding side and lift
    recovered for every side at 0.5%, 2% and 5%; flat reported flat; lines
    alone recover the lift with the binding-side outline removed; each
    fallback condition gives `fit_failed`.
    - _Requirements: 1, 7.1, 8.6_

- [ ] 4. Flattening maps and shot alignment
  - [ ] 4.1 `flatten_maps` (coarse grid every 8 px, through the inset, the
    surface, `K` and `H_i⁻¹`) and `_remap_all` in `pipeline.py` (bilinear
    upsampling and `cv2.remap` in 512-row bands, with coverage masks).
    - _Requirements: 2.1, 2.2, 2.4, 9.3_
  - [ ] 4.2 Add `tree_order` to `Registration`. Create
    `src/albumproc/align.py` with `align_shots` (low-res warps, SIFT matches
    against already-aligned shots in tree order, a regularised 16 × 16 field,
    one outlier pass, clamping) and `apply_field`.
    - _Requirements: 3.1–3.4, 7.3_
  - [ ] 4.3 Write `tests/test_align.py`: recovery of a known smooth field,
    skip on too few matches, clamping, and a stitched shot aligned through a
    non-reference neighbour.
    - _Requirements: 3_

- [ ] 5. Wire into `process_page`
  - Add `curvature` to `PageOptions`, the `curvature` field (and `warnings`,
    if the glare work has not added it yet) to `PageReport`, and `surface` to
    `PageResult`. Run `fit_page` after `estimate_aspect`. When a surface comes
    back, size the output from it, then build the maps, align, remap and fuse.
    Otherwise leave the planar path untouched.
  - Add `view_angles` and the `steep_binding` and `curvature_uncorrected`
    warnings.
  - Add the pipeline cases from the design: one-shot curved pages at three
    lifts and two binding sides, the same pages with `off`, two-shot strip
    MAE and alignment offset, a stitched curved page, every existing case
    unchanged by hash and metadata, a steep case, and identical metadata on a
    repeat run. Add the run-time checks for flat and curved cases.
  - _Requirements: 2.3, 2.5, 4.1–4.3, 5, 7.2, 8.2–8.6, 9.1, 9.2_

- [ ] 6. Command line and album options
  - [ ] 6.1 `albumproc page --curvature --binding`, and the `--debug` images
    (`curvature_outline.jpg`, `curvature_profile.png`, `curvature_grid.jpg`,
    `align_NN.png`).
    - _Requirements: 6.1, 6.3, 6.4_
  - [ ] 6.2 `albumproc album --curvature --binding`, and `curvature` and
    `binding` in `album.json` page entries, included in the page's cache key.
    - _Requirements: 6.2_
  - [ ] 6.3 `albumproc straightness PAGE.png [--min-len] [--overlay]`.
    - _Requirements: 8.7_
  - [ ] 6.4 CLI tests via `main([...])`: options reach the pipeline, the
    `album.json` override and cache key, debug files written only with
    `--debug`, and `straightness` JSON with a smaller worst deviation for the
    corrected synthetic page than for `--curvature off`.
    - _Requirements: 6, 8.7_

- [ ] 7. Check on the real pages and document
  - Run `albumproc page --debug` on Clark's five real pages (branch
    `real-test-photos`, never merged) with `auto` and with `off`. Record in
    the PR description, per page, whether curvature was applied, the binding
    side, the lift, and the `straightness` worst and median before and after.
    Look at `curvature_grid.jpg` for each by eye. Do not commit the photos or
    the outputs.
  - Update the README: replace the "Pages are assumed flat" limit with what
    the model handles and what it does not (wavy pages, gutter content), and
    document the new options, debug images and `straightness`.
  - _Requirements: 8.7_
