# Implementation plan: glare detector and page warnings

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/`. Requirement numbers refer to `requirements.md`. Start after
the two-pages PR (#1) has merged, since both change `pipeline.py` and that PR
may change how `fuse.py` blends.

- [ ] 1. Synthetic test data for the detector
  - [ ] 1.1 Add `glare_style="sleeve"` to `synth.make_shots` and
    `make_case`: two to four long, wavy streaks with a clipped core about 1%
    of the page wide inside a soft veil about 5% wide. Keep `glare_mask` as
    ground truth. `spot` stays the default, so existing tests are unchanged.
    - _Requirements: 9.1, 10.3_
  - [ ] 1.2 Add `kind="clean_white"`: wide white print borders, one print with
    a blown-out sky, white paper, three shots, no glare.
    - _Requirements: 9.4_
  - [ ] 1.3 Add `evaluate.residual_truth(out, truth)`, reusing `_align`, and
    expose `--glare-style` on `albumproc synth`.
    - _Requirements: 9.2_

- [ ] 2. Single-image glare features and score
  - [ ] 2.1 Create `src/albumproc/glare.py` with `GlareParams`,
    `GlareFeatures`, `GlareMap` and `glare_features`: masked
    normalized-convolution background estimates, then `veil`, `desat`,
    `flat` and `clip`.
    - _Requirements: 1.1, 1.2_
  - [ ] 2.2 Add `detect_glare`: logistic score, an opening that removes thin
    seeds, and hysteresis growth from `seed` into `weak`. Shrink full-size
    input to the ≤ 1000 px grid.
    - _Requirements: 1.1, 1.5, 1.6_
  - [ ] 2.3 Fit the default weights and bias with a small script
    (`tools/fit_glare.py`, not installed) on `sleeve` and `spot` cases and
    `clean_white` negatives. Paste the fitted values into `GlareParams` with a
    comment naming the script and seeds.
    - _Requirements: 9.2, 9.4_
  - [ ] 2.4 Write `tests/test_glare.py`: feature behaviour on a known veil,
    mask isolation, and recall ≥ 0.85 with false positives ≤ 0.2% on
    synthetic shots. No seeds on `clean_white`.
    - _Requirements: 1.1, 1.5, 9.2, 9.4_

- [ ] 3. Combine with the comparison cue and adapt the bias
  - Extend `FuseResult` with `toned_small`, `small`, `masks_small` and
    `excess_small` (the excess before the halo dilation), with no change to
    the computation. Add a test that the composed image's hash is unchanged
    on the existing synthetic cases.
  - Add `adapt_bias` and `combine` to `glare.py`. Test that adaptation moves
    the bias towards the best separating value on a two-shot case, that it
    returns the default below the label minimum, and that `combine` ignores
    excess where the single-image score is under `weak`.
  - _Requirements: 1.3, 1.4, 2.5_

- [ ] 4. Residual glare, uncovered area and regions
  - [ ] 4.1 Add `residual` to `glare.py`.
    - _Requirements: 2.1, 2.2_
  - [ ] 4.2 Create `src/albumproc/regions.py` with `Region`,
    `QualityParams`, `find_regions`, `glare_regions` (shots and cause from
    per-shot low-res masks) and `uncovered_regions` (edges, full-resolution
    fraction).
    - _Requirements: 3.1–3.6, 4.1–4.3_
  - [ ] 4.3 Add `page_warnings` building the `glare`, `incomplete_coverage`
    and `photos_dropped` warnings and their messages, including the
    different advice for `single_shot` and `all_shots_glared`.
    - _Requirements: 5.1–5.6_
  - [ ] 4.4 Write `tests/test_quality.py` on drawn masks and hand-made
    weights: the residual equals the weighted mean, plus filtering, sorting,
    boxes, location words, edges for corner, edge and interior holes, causes,
    and the message text for each case.
    - _Requirements: 2, 3, 4, 5_

- [ ] 5. Wire into `process_page`
  - Add `glare` and `quality` to `PageOptions`; the new `PageReport` fields
    with defaults (`schema_version`, `glare`, `uncovered`, `warnings`,
    `detector`); and `residual_glare` and `coverage` to `PageResult`. Run the
    detector after the final `fuse()` only. Honour `GlareParams.enabled`.
  - Keep `coverage` and `glare_fraction` exactly as before.
  - Add the pipeline cases from the design: two-shot `stitch`/`sleeve` warns
    with a `single_shot` region and meets recall ≥ 0.8 and false positives
    ≤ 0.2% against `residual_truth`; seeds 10 and 13 `glare` give no glare
    warning; `clean_white` gives no glare warning; a four-part stitch missing
    one shot gives the right `incomplete_coverage` region; the same inputs
    twice give identical metadata; and the run-time overhead is within 10%.
  - _Requirements: 2.3, 2.4, 4.4, 5.7, 6.5, 6.6, 9.2–9.4, 10.1–10.3_

- [ ] 6. Command line
  - [ ] 6.1 `albumproc page`: write the metadata to `PAGE.json` by default or
    to `--report`, image first, each through a temporary file and
    `os.replace`. Add `--masks`, `--glare-threshold` and `--min-region`.
    Write `glare_NN.png` and `quality_overlay.jpg` under `--debug`. Keep
    stdout unchanged.
    - _Requirements: 5.7, 6.1–6.4, 7.1–7.3, 10.4_
  - [ ] 6.2 `albumproc glare PHOTOS... [--overlay DIR]`, and export
    `detect_glare` from `albumproc/__init__.py`.
    - _Requirements: 8.1–8.4_
  - [ ] 6.3 `albumproc glare-eval PAGE.json --truth MASK.png`.
    - _Requirements: 9.5_
  - [ ] 6.4 CLI tests via `main([...])`: default and explicit metadata
    paths, no `.tmp` left, mask sizes, `glare` JSON output and exit 2 on an
    unreadable file, and `glare-eval` output on a synthetic page whose truth
    mask comes from `residual_truth`.
    - _Requirements: 6, 7, 8, 9.5_

- [ ] 7. Check on the real pages and document
  - Run `albumproc page --masks --debug` on Clark's five real pages locally.
    Draw truth masks for at least two of them and record the `glare-eval`
    scores in the PR description. Do not commit the photos or masks.
  - Update the README: the metadata file and its warnings, the new commands,
    and the known limit that glare is reported, not removed, where no shot
    sees past it.
  - Note in the album-pdf spec's PR that the album step should copy page
    `warnings` instead of recomputing `incomplete_coverage` and
    `photos_dropped`.
  - _Requirements: 5.6, 9.5_
