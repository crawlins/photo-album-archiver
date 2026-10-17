# Implementation plan: shadow detection and removal

Each task builds on the previous ones and ends with passing tests. Paths are
under `backend/`. Requirement numbers refer to `requirements.md`. Fusion
changes (task 3) are the part that changes composed pages; the rest only
reports.

- [x] 1. Synthetic shadows
  - [x] 1.1 Add `_add_shadow` to `synth.py`: arm, hand or phone silhouette
    entering from a page edge, penumbra 0.5-3% of the long side, depth
    0.2-0.6 in linear light, ±3% tint. Return the ground-truth mask at half
    depth.
    - _Requirements: 6.1_
  - [x] 1.2 Add `shadow=` (`none` default, `one`, `all`, `part`) to
    `make_shots` and `make_case`, keep `shadow_mask` on `SynthShot`, and
    expose `--shadow` on `albumproc synth`. Existing tests stay unchanged.
    - _Requirements: 6.1, 6.4_

- [x] 2. Comparison shadow cue
  - [x] 2.1 Move `WEIGHT_MAX_SIDE` to `grid.py`, re-exported from `fuse.py`.
  - [x] 2.2 Create `shadow.py` with `ShadowParams` and
    `comparison_shadow`: linear light, per-pixel median reference of the
    other covering shots, box-filter linear fit, flat-content fallback on
    channel ratios, glare veto from the reference's veil and desaturation.
    - _Requirements: 1.1, 1.2, 1.3, 1.6_
  - [x] 2.3 Hysteresis, seed-width filter, minimum area, and growth into the
    penumbra at `min_depth / 3`.
    - _Requirements: 1.4, 1.5_
  - [x] 2.4 Write `tests/test_shadow.py`: the fit on constructed windows
    (scale, white offset, flat content), the glare veto, and recall ≥ 0.9
    with false positives ≤ 0.5% on `shadow="one"` cases.
    - _Requirements: 1.2, 1.3, 6.2_

- [x] 3. Fusion around shadows
  - [x] 3.1 In `fuse`, compute shadow maps after tone matching; when any are
    found, re-fit the tone without shadowed pixels and recompute them once.
    - _Requirements: 2.5_
  - [x] 3.2 Excess against the darkest unshadowed shot, `shadow_w`, the
    all-shadowed fallback, and today's path unchanged when no shadow is
    found or `enabled` is off. Add `shadow_small` and `shadow_raw_small` to
    `FuseResult`.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.7_
  - [x] 3.3 Tests: byte-identical output on every existing synthetic case
    with shadows on and off; on `shadow="one"` the composed page within 3
    grey levels of the shadow-free fusion inside the mask; glare plus shadow
    in different shots no worse on residual glare than without shadow
    handling. Unit tests for each fusion fix, as for every fix.
    - _Requirements: 2.6, 6.2, 6.3, 6.4_

- [x] 4. Single-image cue
  - [x] 4.1 Add `single_shadow` to `shadow.py`: soft, achromatic,
    texture-preserving log-gradients, DCT Poisson rebuild of the
    illumination, depth below its smooth upper envelope, edge-hugging dips
    ignored.
    - _Requirements: 3.1, 3.4_
  - [x] 4.2 `grow`: comparison seeds grown into single-shot area along the
    single-image cue; single-image seeds alone only above `seed`.
    - _Requirements: 3.2, 3.3_
  - [x] 4.3 Fit `single_weight` and the tolerances with
    `tools/fit_shadow.py` (not installed) on `shadow="part"` and `all`
    cases, with `clean_white` and dark-mat pages as negatives. Paste the
    values into `ShadowParams` with a comment naming the script and seeds.
    - _Requirements: 6.5_
  - [x] 4.4 Tests: at least half of single-shot shadows of depth ≥ 0.35
    found; no warning on clean, `clean_white` or dark-mat pages.
    - _Requirements: 3.4, 6.5_

- [x] 5. Report and warning
  - [x] 5.1 Generalise `glare_regions` into `cause_regions`; residual shadow
    in `pipeline._assess` from `s_raw` and the grown single-image cue;
    `shadow` section and `detector.shadow` in the page metadata.
    - _Requirements: 3.5, 4.1-4.6_
  - [x] 5.2 `shadow` warning in `regions.page_warnings`, `shadow` added to
    `PageWarning.code`'s list, and the album and server docs.
    - _Requirements: 5.1, 5.2, 5.3_
  - [x] 5.3 `--no-shadow` on `albumproc page`; shadow maps in `--debug`.
    - _Requirements: 2.7_
  - [x] 5.4 Tests: `shadow="part"` gives a `single_shot` warning at the right
    location, `shadow="all"` gives `all_shots_shadowed`, and the message
    wording for each.
    - _Requirements: 4.3, 4.4, 5.1, 5.2_

- [x] 6. Real pages
  - [x] 6.1 Label the shadows already in the real pages in
    `crawlins/photo-album-archiver-data`: for each page, which shot has a
    shadow (or every shot) and roughly where. Done 2026-10-08: labels in
    `real/shadows.md` of the data repository, confirmed by Clark. The whole
    glare album has the phone's shadow in the upper-left of most shots; the
    five older pages have none.
  - [x] 6.2 Run all real pages with shadows on and off. Check that pages
    without a shadow give no warning and an unchanged image, that labelled
    shadows in one of several shots are removed, and that the others get a
    `shadow` warning at the labelled place. Record results in a notes file in the data
    repository and adjust defaults if needed. No real photos in this
    repository.
    - _Requirements: 6.6_

## Outcome (2026-10-15)

Built in crawlins/photo-album-archiver#32, with shadow handling **off by
default** (`albumproc page --shadow`, `ShadowParams(enabled=True)`), by
Clark's choice after task 6.2: on the real glare album no page came out
clearly better and five of 19 came out worse (glare-09, 10, 13, 14, 16).
Most shots there carry the phone's shadow somewhere; comparison finds it in
some of them, and pushing those out lets in another shot whose shadow it
could not see. The five older two-shot pages are unchanged with no warning.
Results: `real/shadow-results.md` in the data repository (branch
`shadow-results`).

Changes from the design, all for real pages or what the synthetic ones showed:

- The comparison tests colour first (per-channel ratios against the spread
  white light added to the reference would give), then the texture fit
  (reduced-major-axis slope, only where the shots' texture correlates), and
  on flat pale content asks whether the soft edge of the difference lies in
  this shot or in the others. With two shots on flat pale content it calls
  nothing: a shadow in one and glare in the other look alike.
- No shadow in dark content (luminance below 0.08 linear): tone errors and
  the phone's own brightening of dark areas look like shadows there.
- The glare veto reaches 3% beyond the reference's glare score.
- The tone curve can absorb a shallow shadow over a large pale area, so
  shadows are also looked for with gains alone; only the shadowed shots'
  curves are refitted, since refitting all of them moved the page's colours.
- The page-finding mosaic is fused without shadow handling, so the crop does
  not change.
- `min_area` is 0.2% (not 0.05%): smaller differences on the real two-shot
  pages were sleeve sheen and tone.
- The single-image cue measures a dip's depth on the image across its
  accepted edges; in overlaps it counts only where every covering shot
  agrees, with a stricter rim (`edge_cover` 0.3), which misses shadows every
  shot shares on the synthetic pages (0.15 finds them, with a false warning
  on one real page).

Not met at the time: 6.2 on two-shot pages with flat pale content; 6.4 on
one existing synthetic case (seed 2, four sleeve-glare shots) when shadow
handling is on; 6.5 next to synthetic glare.

## Follow-up (2026-10-17)

The three gaps above, worked in a second pass. Shadow handling stays off by
default. Scores from `tools/fit_shadow.py` (which had stopped running once
the default turned off; it now turns shadow handling on itself), before and
after:

| group | before | after |
|-------|--------|-------|
| `one`, 2 shots: recall / page error | 0.44 / 19.5 | 0.83 / 2.5 grey levels |
| `one`, 3 shots: recall / page error | 0.81 / 4.3 | 0.81 / 4.3 |
| `glare`, 2 shots: recall / false | 0.35 / 0.21% | 0.62 / 0.55% |
| single cue: deep shadows half found | 1 of 30 | 22 of 30 |
| `none`: pages changed by shadow handling | 1 of 30 | 0 of 30 |

Glare left in the page on the 24 `glare` cases is nowhere more than 0.1%
above shadow handling off. The five real two-shot pages still come out
identical with no warning.

- 6.4 (seed 2, four sleeve-glare shots): with four covering shots the
  reference was the mean of the middle two, so two glared shots of four
  put half their glare into it and the clean shot looked shadowed. The
  reference now takes the darker of the middle two (glare only adds light).
  Met.
- 6.2 on two shots: the edge test (does the soft edge of the difference lie
  in this shot or the other) now runs with two shots too, stricter
  (`own_min_two` 0.85 against 0.3), judged on the area deep enough to seed
  (`two_min_depth` 0.18) with the shallower rim taking the nearest core's
  verdict, and kept off the other shot's edges (`two_edge_reach`) and off
  places where the other shot is brighter than this one's page nearby
  (`two_level_tol`). Without the last two, seeds 9 and 10 with two shots let
  glare in where the shadow met the other shot's glare. Where the edge test
  places the edge in this shot, the glare veto uses the glare score as is,
  not widened: the score marks a print's bright border, and widened it hid
  the shadow beside it. Partly met: mean recall 0.83 against 0.9. Most of
  what is missed lies on content darker than `dark_level` in the shadowed
  shot. Judging darkness on the other shot instead found it (0.94), but on
  the real pages page-four and grandma-wilson a sleeve's reflection over a
  dark print then passed for a 70% shadow, so that change was dropped.
- 6.5: the single-image cue no longer rebuilds the illumination from edges
  with glare within `glare_edge_reach` (3%) of their bright side. With
  them, the page around a glare spot or a sleeve veil came out as a dip,
  which is why `edge_cover` had to be 0.3. Now 0.12 finds 22 of 30, and the
  report needs a rim of `grow_edge_cover` 0.15 where one shot alone covers
  (0.12 there warned on page-two, over a portrait's darker backdrop). The
  same shadow in every shot (seed 21, `shadow="all"`) is now reported with
  the defaults. Met; cue-level false seeds in overlaps remain, and the
  report does not count them unless every covering shot agrees.
