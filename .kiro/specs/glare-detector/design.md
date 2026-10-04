# Design: glare detector and page warnings

## Overview

Today glare is handled entirely inside `fuse()`. Each shot's brightness is
compared with the darkest shot covering the same pixel, and the excess both
down-weights the shot and becomes the report's `glare_fraction`. Comparison is
the right tool for *removing* glare. It is no use for *finding* glare where
there is nothing to compare with, and that is where glare survives: the parts
of a stitched page seen by one shot, and spots every shot caught in the same
place.

The design adds a single-image detector beside the comparison and measures the
glare that remains in the composed page from the fusion weights. It then
summarises that glare and the uncovered area as regions and warnings in a
metadata file written next to the page.

```
shots ─register─▶ warp into page frame ─▶ fuse() ──▶ composed page (unchanged)
                                            │
                                            ├─ toned low-res shots, masks
                                            ├─ comparison excess per shot
                                            └─ fusion weights per shot
                                                     │
                       detect_glare (single-image cue, per shot)
                                                     │
                       combine with comparison cue ─▶ glare map per shot
                                                     │
            residual = Σ wᵢ·gᵢ / Σ wᵢ ─▶ residual glare map of the page
                                                     │
    coverage mask ─▶ uncovered map                   │
                 └──────────────▶ regions, causes, warnings ─▶ PageReport
                                                                  │
                                           <name>.json next to <name>.png
```

Everything runs on the low-resolution grid `fuse()` already uses for its
weights (long side ≤ 1000 px), so the added cost is a few small-image filters
per shot.

## Key decisions

### Report, don't repair

Where every covering shot has glare, the print underneath was never
photographed, so nothing can recover it. Inpainting would invent content in a
family archive, which is worse than a visible flaw. The detector therefore
leaves the composed image byte-identical (Requirement 2.5) and tells the user
where another shot is needed.

Feeding the new detector back into the fusion weights was also considered. It
would help only where two or more shots cover a pixel, and there the
comparison cue already works. So it is left out, which keeps the composed
image stable while the detector is tuned.

### A physical model of sleeve glare

Glare off a plastic sleeve is specular reflection of the light source. Under
the dichromatic reflection model it adds roughly the same amount of light to
all three channels on top of the print's own colour:
`I = T + a·(1, 1, 1)`, clipped at 255. That has four visible effects, and each
becomes one feature. All features are computed on the tone-matched low-res
shot, so thresholds do not depend on a shot's exposure.

| Feature | Definition | Why |
| --- | --- | --- |
| `veil` | min(B, G, R) minus its local background (masked median over a window of 6% of the long side), clamped at 0, in 0..1 | Adding white light raises the darkest channel. Saturated print colours have a low minimum channel, so even faint glare on them shows. |
| `desat` | 1 − chroma / local-background chroma (Lab), clamped to 0..1 | Added white light washes colour out. |
| `flat` | 1 − local texture / local-background texture, where texture is the standard deviation of L over a 5 × 5 window | Under a veil of strength *a*, texture shrinks by about (1 − *a*). Clipping removes it completely. |
| `clip` | share of the three channels at or above 250 in the *untoned* shot, smoothed | A clipped core is near-certain glare. |

The score is a logistic combination,
`s = σ(b + w_v·veil + w_d·desat + w_f·flat + w_c·clip)`. Its weights are fitted
offline on synthetic sleeve glare and checked by hand on Clark's five real
pages, then fixed in `GlareParams`. A linear model was chosen over a learned
segmentation network because there are no labelled real pages to train one,
the four features have physical meaning that can be debugged from the
`--debug` maps, and it adds no dependency.

### Not mistaking white content for glare

White print borders, cream paper and blown-out skies are bright and
unsaturated, so `veil` and `desat` alone would flag them. Two things separate
them from glare:

- **Texture survives.** Paper grain, JPEG noise and print detail remain in
  white content, while glare flattens them. `flat` carries most of the weight
  for that reason.
- **Edges are sharp.** A print border is a step. A sleeve reflection fades
  over many pixels. Seeds therefore need a strong score (≥ 0.7). They grow
  into connected pixels with a weak score (≥ 0.3), hysteresis-style, which
  follows a soft veil outward without starting new regions at sharp white
  edges. Seeds narrower than 3 low-res px after an opening are discarded,
  which removes thin border lines.

The background window (6% of the long side) is larger than a typical print
border but smaller than a print. A white border next to a dark print is
compared with a mix of both, and the `flat` term keeps it below the seed
threshold. The synthetic glare-free test page (Requirement 9.4) is built from
exactly these cases.

### Adapting to each page from its overlaps

Sleeves, lamps and phones differ. Where two or more shots cover a pixel, the
comparison cue gives a nearly free label for every shot's pixels there: glare
where the excess exceeds `glare_flag` (0.08), clean where it is under 0.02.
When a page has at least 2000 labelled pixels including at least 200 glare
pixels, the detector refits only the bias `b` (one parameter, clamped to ±1.5
around the default) to best separate those labels. The fitted bias is then used
on the whole page, including the single-shot areas. Refitting every weight was
rejected as too unstable for the few hundred labels a small overlap gives.
Pages with too few labels, for example single-shot pages, keep the default.
The bias used and whether it was adapted are recorded in the metadata.

### Combining with the comparison cue

For shot *i* at pixel *p*:

```
cᵢ(p) = clip(excessᵢ(p) / glare_flag, 0, 1)             # comparison cue, 0 where only i covers
gᵢ(p) = max(sᵢ(p), cᵢ(p) · [sᵢ(p) ≥ weak])              # weak = 0.3
```

The gate keeps a shadow or a slight misalignment in *another* shot, which
makes shot *i* look relatively bright, from counting as glare (Requirement
1.4).

### Residual glare follows the actual blend

The composed pixel is `Σ wᵢ·toneᵢ(shotᵢ) / Σ wᵢ`, so the share of it that came
from glared light is `R = Σ wᵢ·gᵢ / Σ wᵢ` with the same `weights_small` that
`fuse()` already computes. This measures what is actually in the output, not a
proxy such as "every shot had glare here". When the shots switch over a seam,
it does so with the same feathering. If the open two-pages PR changes fusion
to a smoother or multi-band blend, `fuse()` must keep exposing one effective
per-shot weight per low-res pixel (the low-frequency band's weight), and
residual glare stays correct with no change here.

A region's cause comes from coverage, not from the weights. If more than half
of its area is covered by one shot only, it is `single_shot`: another shot
from a different angle will fix it. Otherwise it is `all_shots_glared`, and
the shots need a different light or angle. Real pages so far are dominated by
`single_shot`.

### Metadata always travels with the page

`albumproc page` writes the report only when `--report` is given, and the
album spec derives `incomplete_coverage` from `coverage` itself. With
warnings computed in the page step, every consumer should read the same
warnings, so the metadata is written by default as `<name>.json` beside
`<name>.png`. It is a superset of the old report (`schema_version: 2`), so
existing readers keep working. The album step (`.kiro/specs/album-pdf`, not
yet implemented) should copy `warnings` from the page metadata instead of
recomputing `incomplete_coverage` and `photos_dropped`. That is a one-line
change to its design, noted for its PR rather than made here.

## Components

### `glare.py` (new)

```python
@dataclass
class GlareParams:
    enabled: bool = True           # False skips detection (timing tests); report fields stay, empty
    bias: float = ...              # fitted offline; logistic intercept
    w_veil: float = ...
    w_desat: float = ...
    w_flat: float = ...
    w_clip: float = ...
    background_frac: float = 0.06  # background window, fraction of the long side
    seed: float = 0.7              # hysteresis thresholds on the score
    weak: float = 0.3
    min_seed_px: int = 3           # opening radius that removes thin bright lines
    adapt_bias: bool = True
    adapt_min_labels: int = 2000
    adapt_min_glare: int = 200
    adapt_max_shift: float = 1.5

@dataclass
class GlareFeatures:
    veil: np.ndarray; desat: np.ndarray; flat: np.ndarray; clip: np.ndarray   # float32, 0..1

@dataclass
class GlareMap:
    score: np.ndarray          # float32 0..1, raw logistic score
    glare: np.ndarray          # float32 0..1, after hysteresis (0 outside the grown mask)
    features: GlareFeatures    # kept for --debug

def glare_features(img_bgr: np.ndarray, mask: np.ndarray | None, untoned_bgr: np.ndarray | None = None,
                   params: GlareParams | None = None) -> GlareFeatures: ...
def detect_glare(img_bgr: np.ndarray, mask: np.ndarray | None = None,
                 params: GlareParams | None = None, bias: float | None = None) -> GlareMap: ...
def adapt_bias(features: list[GlareFeatures], excess: list[np.ndarray], masks: list[np.ndarray],
               params: GlareParams) -> tuple[float, bool]: ...
def combine(single: GlareMap, excess: np.ndarray, glare_flag: float, weak: float) -> np.ndarray: ...
def residual(glare: list[np.ndarray], weights: list[np.ndarray]) -> np.ndarray: ...
```

`detect_glare` on a full-size photo (Requirement 8) first shrinks it to the
same ≤ 1000 px grid and returns maps at that size. Callers scale region boxes
back up. Masked medians and blurs use normalized convolution (blur of
`value·mask` divided by blur of `mask`), so the black outside a warped shot
never leaks into its background estimates.

### `regions.py` (new)

```python
@dataclass
class Region:
    bbox: tuple[float, float, float, float]    # x0, y0, x1, y1 as fractions of page w, h
    bbox_px: tuple[int, int, int, int]         # in composed-page pixels
    area: float                                # fraction of the page
    location: str                              # 3 x 3 grid word from the area centroid
    shots: list[int]                           # input indexes covering it (glare only)
    cause: str | None                          # single_shot | all_shots_glared (glare only)
    severity: float | None                     # mean residual value (glare only)
    edges: list[str] | None                    # top | right | bottom | left (uncovered only)

@dataclass
class QualityParams:
    residual_threshold: float = 0.5
    min_region_area: float = 0.0005            # 0.05% of the page

def find_regions(mask: np.ndarray, page_size: tuple[int, int], min_area: float) -> list[tuple[Region, np.ndarray]]: ...
def glare_regions(residual: np.ndarray, coverage_count: np.ndarray, used: list[int], shot_masks: list[np.ndarray],
                  page_size, q: QualityParams) -> list[Region]: ...
def uncovered_regions(coverage: np.ndarray, page_size, q: QualityParams) -> list[Region]: ...
def page_warnings(glare: dict, uncovered: dict, dropped: list[int]) -> list[dict]: ...
```

`find_regions` applies a 3 × 3 closing, then `cv2.connectedComponentsWithStats`,
filters by area and sorts by area. A region touches an edge when its
bounding box comes within one low-res pixel of it. The uncovered map is the
full-resolution coverage mask shrunk with `INTER_AREA` and thresholded at 0.5,
so that the region grid matches the glare grid. The uncovered *fraction* is
still taken at full resolution, so it matches `coverage` exactly.

### `fuse.py` (extended, output image unchanged)

`FuseResult` gains the intermediate values the detector needs, all already
computed inside `fuse()`:

```python
    toned_small: list[np.ndarray]   # tone-matched low-res shots (uint8 BGR)
    small: list[np.ndarray]         # untoned low-res shots, for the clip feature
    masks_small: list[np.ndarray]   # eroded low-res coverage masks
    excess_small: list[np.ndarray]  # comparison excess before the halo dilation, 0..1
```

No computation changes, so the composed image is identical (Requirement 2.5,
asserted by a test that hashes the output before and after).

### `pipeline.py` (extended)

`PageOptions` gains `glare: GlareParams` and `quality: QualityParams`. After
the final `fuse()` (not the preview one), `process_page`:

1. computes features per used shot, then `adapt_bias`, `detect_glare` with
   the chosen bias, and `combine`,
2. computes `residual` from `weights_small`,
3. builds glare and uncovered regions and the warnings, and
4. fills the new `PageReport` fields.

```python
@dataclass
class PageReport:
    ...                                 # every existing field, unchanged
    schema_version: int = 2
    glare: dict = ...                   # see the metadata example
    uncovered: dict = ...
    warnings: list[dict] = ...
    detector: dict = ...                # GlareParams, QualityParams, bias used, adapted

@dataclass
class PageResult:
    image: np.ndarray
    report: PageReport
    corners_ref: np.ndarray
    residual_glare: np.ndarray          # float32 low-res map, 0..1
    coverage: np.ndarray                # bool, full resolution
```

New report fields get defaults so that `PageReport` stays constructible as
before.

### `cli.py` (extended)

```
albumproc page PHOTOS... -o PAGE.png [--report PATH] [--masks] [--debug DIR]
        [--glare-threshold 0.5] [--min-region 0.0005] [existing options]
albumproc glare PHOTOS... [--overlay DIR]
albumproc glare-eval PAGE_METADATA.json --truth MASK.png [--masks-dir DIR]
```

- `page` writes the image, then the metadata to `--report` or to
  `PAGE.json`. Each file is written to `<file>.tmp` and then `os.replace`d.
  stdout is unchanged. `--masks` writes `PAGE.glare.png` and
  `PAGE.uncovered.png`, upsampled with `INTER_LINEAR` and `INTER_NEAREST`
  respectively. `--debug` adds `glare_NN.png` per shot (score as grey,
  grown mask in red) and `quality_overlay.jpg`, with glare regions in magenta
  and uncovered regions in cyan, numbered as in the metadata.
- `glare` runs `detect_glare` on each photo with the default bias (no overlap
  to adapt from) and prints a JSON list of
  `{"photo", "glare_fraction", "regions"}`.
- `glare-eval` reads a page's metadata and its `PAGE.glare.png` (written with
  `--masks`), plus a hand-drawn mask the same size (white = glare). It prints
  recall, false-positive share and IoU. Clark draws the masks for the real
  pages locally, and neither photos nor masks are committed.

Exit statuses follow the existing commands: 0 success, 2 unreadable input.

### `synth.py` and `evaluate.py` (extended)

- `make_case(..., glare_style="spot" | "sleeve")`. `sleeve` draws two to four
  long, thin, slightly wavy streaks, in the way a crinkled sleeve reflects a
  ceiling light: a clipped core about 1% of the page wide inside a soft veil
  about 5% wide, at a random angle. The ground-truth `glare_mask` is kept as
  today.
- `make_case(..., kind="clean_white")`: a glare-free page whose prints have wide
  white borders, one print with a blown-out sky, and white paper, shot from
  three angles with no glare. It is the false-positive test.
- `evaluate.residual_truth(out, truth)`: aligns the output to the ground-truth
  page as `score()` already does, and returns the mask of pixels at least 20
  levels brighter than the truth in the page frame. This is the "real"
  residual glare that the detector's residual map is scored against. It avoids
  exposing per-shot homographies to the tests.

## Page metadata example

```json
{
  "schema_version": 2,
  "n_inputs": 2, "reference": 0, "used": [0, 1], "dropped": [],
  "pair_inliers": {"0-1": 412}, "page_method": "lines", "page_score": 0.91,
  "aspect": 0.7734, "size": [3120, 4034],
  "coverage": 0.9873,
  "glare_fraction": {"0": 0.0121, "1": 0.0034},
  "gains": {"0": [1.0, 1.0, 1.0], "1": [1.031, 1.012, 0.998]},
  "glare": {
    "fraction": 0.0412,
    "per_shot": {"0": 0.0655, "1": 0.0388},
    "regions": [
      {"bbox": [0.08, 0.55, 0.47, 0.71], "bbox_px": [250, 2219, 1466, 2864],
       "area": 0.0298, "location": "bottom-left", "shots": [0],
       "cause": "single_shot", "severity": 0.81}
    ]
  },
  "uncovered": {
    "fraction": 0.0127,
    "regions": [
      {"bbox": [0.0, 0.0, 0.06, 0.21], "bbox_px": [0, 0, 187, 847],
       "area": 0.0127, "location": "top-left", "edges": ["top", "left"]}
    ]
  },
  "warnings": [
    {"code": "glare", "fraction": 0.0412, "regions": 1, "single_shot": 1,
     "message": "Glare remains on 4.1% of the page in 1 area (bottom-left). It was seen by only one photo; add a shot of that area from a different angle."},
    {"code": "incomplete_coverage", "fraction": 0.0127, "regions": 1,
     "message": "1.3% of the page is not in any photo (top-left corner)."}
  ],
  "detector": {
    "bias": -3.42, "bias_adapted": true,
    "glare": {"enabled": true, "bias": -3.0, "w_veil": 9.5, "w_desat": 2.0, "w_flat": 4.0, "w_clip": 6.0, "background_frac": 0.06, "seed": 0.7, "weak": 0.3, "min_seed_px": 3, "adapt_bias": true, "adapt_min_labels": 2000, "adapt_min_glare": 200, "adapt_max_shift": 1.5},
    "quality": {"residual_threshold": 0.5, "min_region_area": 0.0005}
  }
}
```

Integer-keyed dicts become string keys in JSON, as they already do. The
`regions` arrays are included even when no warning is raised (they are empty
then), so consumers never need to branch on whether a key exists.

## Error handling

| Situation | Behaviour |
| --- | --- |
| Single shot, nothing to compare | single-image cue only; bias not adapted (`bias_adapted: false`) |
| Overlap too small or glare-free for adaptation | default bias, recorded |
| Shot with no covered low-res pixels after erosion | no glare map for it; `per_shot` value 0 |
| Detector raises on an unexpected input | `process_page` fails as it would for any pipeline error. Silently skipping the detector would hide glare, which is the failure this spec exists to fix |
| Metadata path not writable | exit 2 after the image is written, with the path in the message |
| `glare-eval` mask size differs from the page | resize the hand-drawn mask with `INTER_NEAREST` and say so on stderr |

## Testing strategy

All automated tests use synthetic pages. Real pages are checked locally with
`glare-eval`.

- **Features** (`tests/test_glare.py`): on a flat-colour patch plus an added
  white veil of known strength *a*, `veil` rises with *a*, `flat` approaches
  *a* on textured content, and `clip` is 1 only where the source clipped. A
  masked-out black border changes no feature inside the mask.
- **Single-image detection**: on synthetic `sleeve` and `spot` shots, the
  detector's grown mask scored against the shot's ground-truth `glare_mask`
  gives recall ≥ 0.85 and false positives ≤ 0.2% of the glare-free area. On
  `clean_white` shots there are no seeds.
- **Bias adaptation**: on a two-shot full-page case, the adapted bias moves
  towards the value that best separates the ground truth, and with fewer than
  `adapt_min_labels` labels it returns the default unchanged.
- **Residual and regions** (`tests/test_quality.py`): with hand-made weights
  and glare maps, `residual` equals the weighted mean. Regions are found,
  filtered, sorted, boxed and located correctly on drawn masks, and `edges`
  are right for corner, edge and interior holes.
- **Pipeline** (`tests/test_pipeline.py`, new cases):
  - `stitch`/`sleeve` with two shots: a `glare` warning with at least one
    `single_shot` region. The residual map has recall ≥ 0.8 against
    `residual_truth`, and false positives are ≤ 0.2% of the residual-free
    page.
  - `glare` with three full-page shots, the existing seeds 10 and 13: no
    `glare` warning, so merging removed the glare and the detector agrees.
  - `clean_white`: no `glare` warning.
  - A four-part `stitch` with one shot left out: an `incomplete_coverage`
    warning with one region at the right location and edges, and `coverage`
    equal to 1 − `uncovered.fraction`.
  - Same inputs twice: identical metadata. Composed image identical to
    `fuse()` before this change, by hash.
- **Run time**: the pipeline tests record `process_page` time with and without
  the detector (`GlareParams(enabled=False)`) and fail if the
  difference exceeds 10% plus a small constant for timer noise.
- **CLI**: `page` writes `PAGE.json` by default and to `--report` when given,
  with no `.tmp` left behind. `--masks` writes both masks at the page size.
  `glare` prints valid JSON and exits 2 on an unreadable file. `glare-eval`
  prints the three scores.

## Out of scope

- Removing or inpainting residual glare.
- Using the new detector to change fusion weights.
- Live glare feedback on the phone. The Android spec captures and uploads
  only. `albumproc glare` is the hook a later upload-server step can call.
- Detecting blur, shadows, or a finger in the frame. The warning format is
  general enough to add those codes later.
