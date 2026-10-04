# Design: page curvature correction

## Overview

Today the page is a plane. Every shot is mapped onto the reference shot with
a homography, the page is a quadrilateral in the reference shot, and one more
homography `P` takes it to the output rectangle. For a page bent near its
binding, all three steps are slightly wrong in the curved strip and right
everywhere else.

The design keeps the planar path as it is and adds a curved page model next to
it. The model is a camera looking at a bent sheet of paper. It is fitted to
the page outline and to lines in the page content, in the reference shot's
frame. When it explains the evidence clearly better than a plane, it replaces
`P` with a flattening map, and each shot gets a small extra correction for the
parallax a homography cannot model.

```
shots ─register─▶ homographies ─▶ preview mosaic ─▶ detect_page (quad)
                                          │
                       trace outline as curves, find line segments
                                          │
                      fit plane  ◀────────┴────────▶  fit bent page (per binding side)
                          │                                   │
                          └───────── compare, decide ─────────┘
                                          │
              flat ─▶ P homography (today, unchanged)
            curved ─▶ flattening map: page px ─▶ surface ─▶ reference shot ─▶ shot i
                                          │
                     per-shot displacement field (parallax in the strip)
                                          │
                       one cv2.remap per full-res shot ─▶ fuse() ─▶ composed page
```

## Key decisions

### A cylindrical page, parametrised by arc length

Paper bends but does not stretch, and a page held at its binding bends only
across the binding. The page surface is therefore modelled as a cylinder in
the broad sense: a cross-section curve swept along the binding direction.
Lines parallel to the binding stay straight in 3D, and the curve is free in
shape. This is the standard model for photographed book pages (Cao, Ding and
Liu 2003; Meng et al. 2012; Matt Zucker's `page_dewarp`).

In page coordinates, `u` runs across the binding (0 at the binding side) and
`v` runs along it, both in output pixels. The surface point is

```
X(u, v) = R · (x(u), v, z(u))ᵀ + t
x(u) = ∫₀ᵘ cos θ(s) ds,   z(u) = ∫₀ᵘ sin θ(s) ds
```

where `θ(u)` is the angle of the profile to the flat page plane, and
`(R, t)` is the pose of that plane in the reference camera. Writing the
profile through its angle makes `u` the distance along the surface by
construction, so flattening by `u` gives true distances (Requirement 2.1)
with no extra constraint. With `θ ≡ 0` the model is a flat rectangle, so the
planar fit is the same model with the profile switched off, and the two fits
can be compared fairly.

`θ` is a natural cubic spline through 8 knots at `u/W` = 0, 0.02, 0.05,
0.1, 0.18, 0.3, 0.55 and 1, dense near the binding where real pages bend and
sparse across the flat part. Clark's pages bend over roughly the 10% of the
width nearest the binding, which the first four knots cover.

Rejected alternatives:

- **A general smooth surface or mesh.** It can also model a wavy page or a
  sheet that is not developable, but it has hundreds of unknowns and the
  evidence on a photo-album page (a few edges and print borders, no text
  lines) cannot pin them down.
- **A polynomial height `z(x)` (as in `page_dewarp`).** It needs a separate
  arc-length integral to recover true distances, and a cubic cannot hold a
  page flat for 90% of its width and bent in the last 10%.
- **Multi-view stereo from the shots.** Two shots of a page are often taken
  from close angles, and the lift is a few millimetres, so the depth signal
  is weak. The shots are used for alignment instead (see below).

### The camera is known, so the fit is well posed

The pipeline already knows the reference shot's focal length (EXIF, or the
26 mm phone default) and assumes the principal point at the image centre. A
part of the page lifted towards the camera moves outward from the principal
point in the image, so a lift near the binding bows the top edge one way and
the bottom edge the other. Given the focal length, those two bows determine
`θ` near the binding, and the rest of the outline fixes the pose. That is
why the outline alone is enough on most pages, even without lines in the
content.

Unknowns: the pose (3 rotation, 3 translation), the page height in units of
the page width (the width is fixed at 1, since scale and distance trade off),
and the 8 knot values. That gives 15 in all. The pose and height start from
the planar solution: `cv2.solvePnP` on the detected quad with the aspect
from `estimate_aspect`. The knots start at 0.

### Evidence: outline first, then lines in the content

**Outline.** `detect_page` returns a quad. Around it, the outline is traced
as the page region's contour in a band of ±3% of the page diagonal (from the
same edge and colour masks `detect_page` builds), and split into four sides
at the quad's corners. The residual is a chamfer distance: model outline
points (40 per side) are projected into the mosaic and read off the distance
transform of the traced outline, bilinearly interpolated.

**Content lines.** Print borders, mats and title baselines are long and
straight on the page. The detector runs `cv2.createLineSegmentDetector` on
the preview mosaic after flattening it with the current model. It keeps
segments longer than 8% of the page that cross into the curved strip, and
chains collinear pieces. Each chain adds residuals that are its points'
distances from their own best-fit line in page coordinates. This is what
pins the profile when the outline near the binding is hidden, for example
under a ring, a sleeve rim, or the opposite page. Lines parallel to the
binding carry no information about `θ`, so they are left out.

**Regularisation.** A small penalty on the second difference of the knots
keeps the profile smooth, and a smaller penalty on `θ` itself keeps it at
zero where nothing says otherwise.

The fit is two rounds of `scipy.optimize.least_squares` with a soft-L1 loss,
so stray outline points such as a glare blob crossing the edge are not
squared. The first round uses the outline only. Lines are then detected in
the image flattened by that fit, and the second round uses both. SciPy is
the one new dependency (Requirement 9.4). A hand-written Levenberg-Marquardt
was considered, but robust loss, bounds and a finite-difference Jacobian are
exactly what `least_squares` already provides, tested.

### Deciding whether a page is curved

For each candidate binding side (the four sides, or the one the user named),
the bent model is fitted, and the planar model (`θ ≡ 0`) is fitted to the
same residuals. The page is treated as curved when the best side

- reduces the robust cost by at least 30% compared with the plane, and
- has a maximum lift of at least 0.3% of the page width,

and passes the sanity checks in Error handling. Both thresholds are in
`CurvatureParams`. Real pages lift by about 1%, so the thresholds are well
under what they need while still above what corner noise produces on the
flat synthetic cases. Requirement 8.6 checks that last part.

When the two-pages change (PR #1) found a second page next to this one, the
side facing it gets its cost multiplied by 0.8 before the comparison. That is
where an open album's spine is.

When the page is not curved, nothing downstream changes: `P` is computed
exactly as today and the composed image is identical (Requirement 4.1). The
fit itself runs on the low-res mosaic, which keeps the detection cost within
Requirement 9.1.

### One resampling, through a map

When the page is curved, the output rectangle is `W × H` pixels with
`W = page width / profile arc length` scaled to the reference shot's
resolution on the flat part, as today, and `H` from the fitted height. The
inset trim applies to `(u, v)` as it does to the rectangle now.

For shot `i`, the map from output pixel `(U, V)` to that shot's pixel is

```
(U, V) ─inset→ (u, v) ─surface→ X ─K→ reference pixel ─H_i⁻¹→ shot i pixel
```

`H_i` is the homography from `register()`. It was fitted by RANSAC to all
matches, which on a page bent only near the binding means mostly to the
flat part, so the curved strip's matches are its outliers. For the flat part
the composition is exact. In the strip it is off by the parallax between the
shot and the reference, which the next step corrects.

The map is evaluated on a grid every 8 output pixels, upsampled bilinearly in
bands of 512 rows, and applied with `cv2.remap` (`INTER_LINEAR`). The same
band-wise remap produces the coverage mask. Memory stays at a band's worth of
float maps per shot (Requirement 9.3).

### Aligning shots in the curved strip

After every shot is warped through its map at low resolution (the ≤ 1000 px
grid `fuse()` already uses), SIFT matches are found between each shot and the
shots already aligned, in the order of `register()`'s spanning tree. The
reference is aligned by definition. Each shot gets a displacement field on a
16 × 16 grid of control points over the page, fitted by regularised least
squares to the match offsets. A thin-plate smoothness term keeps it smooth,
and a weak pull to zero keeps it at 0 where there are no matches. Offsets are
clamped to 1% of the long side, and matches more than 3 px from the fitted
field are dropped once and the field is refitted.

The field is added to the shot's map before the full-resolution remap, so the
shot is still resampled once (Requirement 3.4). The median match offset
before and after goes in the metadata (Requirement 3.5).

This correction runs only on curved pages. Sleeve wrinkles cause the same
kind of small misalignment on flat pages, but switching it on there would
change their output (Requirement 4.1). It is easy to turn on everywhere
later, once it has been checked on real flat pages.

### Resolution near the binding

Where the page bends away, each shot sees it at an angle, and a steep angle
means few camera pixels per page pixel. From the fitted surface and each
shot's map, the system computes, per output pixel on the low-res grid, the
angle between the surface normal and the ray to that shot's camera centre.
The camera centre comes from the reference pose and `H_i`, decomposed with
the known focal length. Where the smallest angle over the covering shots is
above 60°, the page is flagged `steep_binding`. The warning gives the share
of the page affected and the ratio of `cos` of that angle to the flat part's,
which is the resolution loss. Nothing is inpainted: as with glare, the fix is
another shot.

## Components

### `curvature.py` (new)

```python
@dataclass
class CurvatureParams:
    mode: str = "auto"                 # auto | off | force
    binding: str = "auto"              # auto | left | right | top | bottom
    knots: tuple[float, ...] = (0, 0.02, 0.05, 0.1, 0.18, 0.3, 0.55, 1.0)
    min_improvement: float = 0.3       # relative cost reduction over the plane
    min_lift: float = 0.003            # fraction of page width
    neighbour_bonus: float = 0.8       # cost factor for the side facing another page
    outline_band: float = 0.03         # tracing band, fraction of the diagonal
    min_line_len: float = 0.08         # content lines, fraction of the page
    smooth_weight: float = ...         # second-difference penalty on the knots
    zero_weight: float = ...           # pull of theta towards 0
    max_corner_shift: float = 0.02     # fallback check, fraction of the diagonal
    steep_angle_deg: float = 60.0
    align: AlignParams = field(default_factory=AlignParams)

@dataclass
class AlignParams:
    grid: int = 16
    max_shift: float = 0.01            # fraction of the long side
    min_matches: int = 40
    outlier_px: float = 3.0

@dataclass
class PageSurface:
    binding: str                       # left | right | top | bottom
    rvec: np.ndarray; tvec: np.ndarray # pose of the flat plane, reference camera
    K: np.ndarray                      # reference intrinsics, full resolution
    height: float                      # page height / page width
    theta: np.ndarray                  # knot values, radians
    def profile(self, n: int = 50) -> list[tuple[float, float]]: ...      # (u, lift)
    def max_lift(self) -> float: ...
    def strip_width(self, deg: float = 2.0) -> float: ...                 # where |theta| > deg
    def project(self, u: np.ndarray, v: np.ndarray) -> np.ndarray: ...    # page fractions -> ref px

@dataclass
class CurvatureFit:
    surface: PageSurface | None        # None when the page is treated as flat
    planar_cost: float; curved_cost: float
    outline_rms_planar: float; outline_rms_curved: float   # px, mosaic
    reason: str | None                 # flat | small_gain | fit_failed | off
    lines_used: int

def trace_outline(img: np.ndarray, valid: np.ndarray, quad: np.ndarray, band: float) -> list[np.ndarray]: ...
def find_lines(img: np.ndarray, valid: np.ndarray, surface: PageSurface, to_mosaic: np.ndarray,
               p: CurvatureParams) -> list[np.ndarray]: ...
def fit_surface(outline: list[np.ndarray], lines: list[np.ndarray], init: PageSurface,
                p: CurvatureParams) -> tuple[PageSurface, float]: ...
def fit_page(mosaic: np.ndarray, valid: np.ndarray, quad: np.ndarray, to_mosaic: np.ndarray, K: np.ndarray,
             aspect: float, p: CurvatureParams, neighbour_side: str | None = None) -> CurvatureFit: ...
def flatten_maps(surface: PageSurface, size: tuple[int, int], inset: float, H_ref_from_shot: list[np.ndarray],
                 step: int = 8) -> list[np.ndarray]: ...                  # coarse (h, w, 2) per shot
def view_angles(surface: PageSurface, H_ref_from_shot: list[np.ndarray], grid: np.ndarray) -> np.ndarray: ...
```

`to_mosaic` is the existing `to_canvas` homography. Residuals are computed in
mosaic pixels so that the fit sees the same image `detect_page` saw.

### `align.py` (new)

```python
@dataclass
class ShotAlignment:
    shot: int
    field: np.ndarray | None           # (grid, grid, 2), output px; None when skipped
    matches: int
    offset_before: float; offset_after: float   # median, output px
    skipped: str | None                # too_few_matches | reference

def align_shots(small: list[np.ndarray], masks: list[np.ndarray], order: list[int],
                p: AlignParams, scale: float) -> list[ShotAlignment]: ...
def apply_field(coarse_map: np.ndarray, field: np.ndarray, step: int) -> np.ndarray: ...
```

### `register.py` (extended)

`Registration` gains `tree_order: list[int]`, the order in which the spanning
tree placed the shots, which `align_shots` follows. Nothing else changes.

### `pipeline.py` (extended)

`PageOptions` gains `curvature: CurvatureParams`. In `process_page`, after
`detect_page` and `estimate_aspect`:

1. Unless the mode is `off`, call `fit_page` on the preview mosaic.
2. If `fit.surface is None`, continue exactly as today.
3. Otherwise compute `W, H` from the surface, build `flatten_maps`, warp
   each shot at low resolution through its map, call `align_shots`, fold the
   fields in, remap each full-resolution shot in bands, and call `fuse()` as
   today. Compute `view_angles` for the warning.
4. Fill `report.curvature` and add warnings.

`_warp_all` gets a sibling `_remap_all(images, maps, size)` that returns the
same `(warped, masks)` pair, so `fuse()` is unchanged.

```python
@dataclass
class PageReport:
    ...                                # every existing field, unchanged
    curvature: dict = ...              # see the metadata example

@dataclass
class PageResult:
    ...
    surface: PageSurface | None = None
```

If the glare-detector spec has landed first, curvature warnings are appended
to its `warnings` list. Otherwise this change adds `warnings: list[dict]` to
`PageReport` in that spec's format, and the glare work appends to it.

### `cli.py`, `album.py` (extended)

```
albumproc page PHOTOS... -o PAGE.png [--curvature auto|off|force]
        [--binding auto|left|right|top|bottom] [existing options]
albumproc album DIR -o OUT [--curvature ...] [--binding ...] [existing options]
albumproc straightness PAGE.png [--min-len 0.1] [--overlay OUT.png]
```

- `album.json` page entries accept `"curvature"` and `"binding"`, as they
  already accept `"rotate"` and `"fit"`. A page's settings are part of its
  cache key, so changing them reprocesses that page.
- `--debug` adds `curvature_outline.jpg` (traced outline in white, planar fit
  in red, curved fit in green, on the mosaic), `curvature_profile.png` (lift
  against position, drawn with OpenCV), `curvature_grid.jpg` (lines of
  constant `u` and `v` every 5% of the page, projected onto the reference
  shot) and `align_NN.png` (each shot's field as hue for direction and
  brightness for size).
- `straightness` finds long line segments in a composed page with the same
  detector, chains collinear ones, and prints for each chain its length and
  its largest deviation from a straight line, plus the worst and the median,
  as JSON. Run before and after on the same page, it shows whether the bend
  is gone. Neither photos nor results are committed.

### `synth.py` and `evaluate.py` (extended)

- `make_case(..., kind="curved", lift=0.03, strip=0.12, binding="left")`. The
  page is cut into 400 strips parallel to the binding. Each strip is placed in
  3D along a profile `z(u) = lift · W · (1 − u/strip)²` for `u < strip` and 0
  beyond, a smooth quadratic bend into the binding. That is not a spline in
  `θ`, so the model is not tested on its own family (Requirement 8.1). Each
  strip is a plane, so it is drawn with its own `warpPerspective` and masked
  into the shot, farthest first. Glare, exposure, noise and JPEG are added as
  for other cases. Both full-page and two-part stitched variants are made.
- `evaluate.geometry_error(out, truth)`: SIFT matches between the output and
  the truth, the best homography between them, and the largest and 95th
  percentile distance of matches from it, as a fraction of the page width.
  This is the bend and squeeze left after removing anything a flat page model
  could explain (Requirement 8.2).
- `evaluate.strip_mae(out, truth, binding, strip)`: the existing aligned MAE,
  split into the curved strip and the rest (Requirement 8.5).

## Page metadata example

```json
{
  "aspect": 1.0012,
  "size": [3612, 3608],
  "curvature": {
    "mode": "auto", "applied": true, "reason": null,
    "binding": "left",
    "max_lift": 0.0094, "strip_width": 0.11, "width_change": 0.0068,
    "outline_rms": {"planar": 6.8, "curved": 1.9},
    "lines_used": 5,
    "profile": [[0.0, 0.0094], [0.02, 0.0061], [0.05, 0.0027], [0.1, 0.0004], [0.2, 0.0], [1.0, 0.0]],
    "alignment": {
      "1": {"matches": 286, "offset_before": 7.4, "offset_after": 0.9, "skipped": null}
    },
    "steep_fraction": 0.0
  },
  "warnings": []
}
```

On a flat page the object is still present, so consumers never branch on
whether the key exists:

```json
"curvature": {"mode": "auto", "applied": false, "reason": "flat", "binding": null,
              "max_lift": 0.0011, "strip_width": 0.0, "width_change": 0.0,
              "outline_rms": {"planar": 1.7, "curved": 1.6}, "lines_used": 2,
              "profile": [], "alignment": {}, "steep_fraction": 0.0}
```

Warning examples:

```json
{"code": "steep_binding", "fraction": 0.021, "resolution": 0.42,
 "message": "2.1% of the page next to the binding was only seen at a steep angle, at 42% of the resolution of the rest. Add a shot aimed more squarely at the binding."}
{"code": "curvature_uncorrected", "max_bow": 0.008,
 "message": "The page looks bent near its binding, but its shape could not be fitted, so it was processed as flat. Lines near the binding may be bent."}
```

## Error handling

| Situation | Behaviour |
| --- | --- |
| Mode `off` | no fit; planar path; `reason: "off"` |
| Curved fit does not converge | planar path; `reason: "fit_failed"`; `curvature_uncorrected` if the outline bows more than `min_lift` |
| Profile folds back (`|θ|` ≥ 80° anywhere) | treated as not converged |
| Fitted corners > 2% of the diagonal from the traced outline | treated as not converged |
| Improvement or lift under the thresholds | planar path; `reason: "small_gain"` or `"flat"` |
| Mode `force` and the fit converged | curved path regardless of thresholds |
| Outline traced on fewer than three sides (page cut off in every shot) | outline residuals only on the traced sides; lines carry the rest; if neither is enough, `fit_failed` |
| A shot has fewer than `min_matches` matches for alignment | used without a field; `skipped: "too_few_matches"` |
| `fuse()` or anything else raises | `process_page` fails as it does today |

## Testing strategy

All automated tests use synthetic pages. Real pages are checked locally with
`albumproc straightness` and the debug images.

- **Model** (`tests/test_curvature.py`): with `θ ≡ 0`, `project` matches the
  planar homography from `solvePnP` to 0.01 px. Arc length along `u` is 1 for
  any `θ`. `max_lift` and `strip_width` are right for a hand-made profile.
- **Fit on rendered outlines**: outlines are projected from known surfaces with
  1 px noise, for each binding side and lifts of 0.5%, 2% and 5%. The fit
  recovers the binding side and the lift to within 10%. With `θ ≡ 0` the page
  is reported flat. With the outline near the binding removed, the content
  lines alone still recover the lift to within 25%.
- **Alignment** (`tests/test_align.py`): a smooth known displacement applied
  to an image is recovered to under 0.5 px at the grid. With no matches, the
  shot is skipped. Clamping holds.
- **Pipeline** (`tests/test_pipeline.py`, new cases):
  - `curved`, one shot, lifts 1%, 3% and 5%, binding on the left and on the
    top: curvature applied, the right binding side, `geometry_error` ≤ 0.3% of
    the width, `aspect` within 0.5%.
  - The same pages through `curvature=off`: `geometry_error` > 0.3% for lifts
    of 3% and 5%, so the test can see a regression.
  - `curved`, two full-page shots: strip MAE ≤ 1.2 × flat-part MAE, and the
    alignment offset after the correction is ≤ 1 px median.
  - `curved` stitched from two halves across the binding: applied, and
    `geometry_error` ≤ 0.3%.
  - Every existing case: `applied` is false, and the composed image's hash
    and every pre-existing metadata field equal the planar path's.
  - A `curved` case with a lift that makes the strip steeper than 60°:
    `steep_binding` warning.
  - Same inputs twice: identical metadata.
- **Run time**: flat cases with `auto` against `off` within 10% plus timer
  noise; curved cases within 60%.
- **CLI**: `--curvature` and `--binding` reach the pipeline. `album.json` per
  page overrides work and change the cache key. `straightness` prints valid
  JSON and reports a smaller worst deviation for a corrected synthetic page
  than for the same page with `--curvature off`.

## Out of scope

- Pages that are not developable or that bend along more than one axis: wavy
  or cockled paper, dog-eared corners, and wrinkles in the sleeve.
- Content hidden inside the gutter, which no shot sees and nothing can
  recover. `steep_binding` tells the user to re-shoot.
- Shading near the binding, where the bent page catches less light. Fusion's
  tone matching is global per shot. A shading correction from the fitted
  surface is possible later but is a separate change.
- Lens distortion. Phone cameras correct it before saving, and the planar path
  makes the same assumption.
- Live curvature feedback in the capture app.
