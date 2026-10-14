# Design: the paper's edge inside an album sleeve

## Overview

The sleeve step sits between choosing the page and measuring it. Today
`process_page` picks the outer outline from the page candidates
(`_choose_page`), estimates its aspect, and hands the outline to the
curvature fit, which traces it again. The new step takes the outer outline,
looks around each of its sides for the sleeve's bands and the paper's edge,
and returns the paper's outline, already traced. Everything after it (aspect,
the planar homography, the curvature fit, the crop) uses that outline instead
of the outer one.

```
preview mosaic ─▶ detect_pages ─▶ _choose_page ─▶ outer outline (quad)
                                                     │
                                  find_sleeve (new, sleeve.py)
           corridor per side ─▶ candidate edges ─▶ bands ─▶ classify
                                                     │
                       choose the four paper edges together ─▶ trace them
                                                     │
           not sleeved ─▶ outer outline, exactly as today
               sleeved ─▶ paper outline (traced sides + corners), strip side,
                          per-side band widths (for the sleeve crop)
                                                     │
          estimate_aspect(paper corners) ─▶ fit_page(outline=paper sides,
                                                     binding hint=strip side)
                                                     │
                     planar P or flattening map ─▶ crop: paper, or paper
                                                    extended by the bands
```

## Key decisions

### Work outward from what is already found, not a new detector

`detect_pages` is tuned for picking the right page among neighbours, page
stacks and the open album, and it already knows a little about sleeves (the
`CRIMP` exception for thin top and bottom strips in `page.py`). It gets the
right page; it just lands on different edges of it from side to side. So the
sleeve step does not replace it. It searches a corridor around each side of
the outline it returns, wide enough to reach every edge seen on the real pages
from wherever the outline landed: up to 5.5% of the width between the sleeve's
and the paper's edge on the free side, about 3% across crimp and gap, about
2.5% across the strip. The corridor runs from 4% outside the side to 8% inside
it.

Rejected alternatives:

- **Teach `detect_pages` about sleeves.** Its candidates come from closed
  outlines, colour contrast and long lines, scored as whole quads. A sleeve
  adds nested quads with nearly the same score; telling them apart needs the
  band classification below either way, and doing it inside the candidate
  search multiplies its cost by the number of candidates.
- **A learned edge or segmentation model.** There are 24 real sleeved pages,
  all from one album. The hand-made cues below are few and measurable, and the
  labelled pages are enough to check them but not to train on.

### Edges first, then bands between them

For each side, the mosaic is sampled into a straightened corridor strip, as
`trace_outline` already does: columns along the side, rows across it from
outside to inside, at the preview mosaic's own resolution (up to 3000 px,
`PREVIEW_MAX_SIDE`), not the 1000 px detection image, because the crimp's
ridges are only about 10 preview pixels apart. Edge strength is the Lab
gradient across the side, smoothed along it over 1% of the page width.

Candidate edges are continuous paths along the strip. The side is cut into 16
segments; in each, the peaks of the mean edge strength across the side are
found; the peaks are linked from segment to segment by dynamic programming
that allows a step of up to 0.25% of the page width per segment, which lets a
path follow a bow of up to 1.5% over a side (Requirement 1.1) but not hop
between edges 1% apart. A path's support is the share of segments where it
lies on a peak. Paths with support of at least 0.6 are kept, up to six per
side, ordered from outside to inside.

The bands are the stretches between consecutive kept paths, plus the stretch
outside the outermost path.

### Classifying bands by what a sleeve looks like

Each band is described by a few measurements, taken in the straightened strip
along its whole length:

| Measurement | How |
| --- | --- |
| width | mean and spread of the distance between its two paths, as a fraction of the page width |
| ridge | the band's mean row, high-passed along the side; the highest autocorrelation at lags of 0.2% to 1.5% of the page width |
| texture | standard deviation along the side after removing a 5% moving average |
| colour | median Lab, and chroma |
| like outside | Lab distance from the band outside the outermost path, relative to that band's own spread |
| like paper | Lab distance from the paper sample: the band 10% to 14% inside the outer outline, where only paper or print lies |

The rules, in order:

- **crimp**: ridge ≥ 0.5 and width 0.5% to 4%. On `grandma-wilson` the crimp's
  ridge is 0.75 at a lag of 10 px; paper and cloth stay under 0.25.
- **strip**: on a side perpendicular to a side with a crimp (the crimps run
  across the binding, so the strip is on one of the other two sides), ridge < 0.3, low texture, width 1% to 4% with a spread
  under 0.3%, unlike outside, and its outer path is the outermost path on
  that side. Only one side can hold a strip; when two qualify, the one with
  the smaller spread wins.
- **clear**: like outside, or within the gap next to a crimp (width ≤ 1.5%
  between a crimp and the next path inward).
- **outside**: the band beyond the outermost path, and any band like outside
  that touches it.
- **paper**: everything else.

The thresholds start from the measurements above and in the introduction of
`requirements.md`, and are set in task 1 from the labelled pages. They live
in `SleeveParams`.

### Choosing the paper edges together

Choosing per side would fail on prints near the edge: on `page-two` the
print's border lies about 2% inside the paper's left edge, closer than the
strip on other pages. The four paper edges are therefore chosen as a set. A
side's candidates are its kept paths whose bands outside are all sleeve bands
(`crimp`, `strip`, `clear`) or `outside`, with the outermost path also
allowed when no band is classified (no sleeve on that side). With at most six
paths per side this is at most 1296 combinations, each scored by:

- **end support**: each side's path must be present within 2% of the side's
  length of both ends, where the ends are its intersections with the two
  adjacent chosen paths. A print's border stops at the print's corners,
  which lie inside the paper, so it fails this unless the print fills the
  paper, in which case its border is the paper's edge.
- **corners**: the four corners must make a convex quad with interior angles
  between 45° and 135°.
- **outermost**: among combinations that pass, the one whose paths lie
  furthest out, summed over the sides, wins.

This is the same test `_dividers` uses for lines that cross a page, applied to
the page's own sides.

When no combination passes with all four sides, combinations with one side
left as the outer outline's side are tried, and that side is reported as
not found (Requirement 6.4). With two or more sides missing, the page is not
treated as sleeved (`few_sides`).

### Tracing the chosen edge without jumping

Each chosen path is refined with the column-by-column dynamic programming of
`trace_outline` (`_best_path`), but only within ±0.3% of the page width of
the path, and with edge strength taken with the path's own sign (bright paper
against a darker band, or the reverse), so a neighbouring edge of the
opposite sign cannot pull it. Sub-pixel position comes from the same parabola
fit. Columns where the edge strength along the path falls under a fifth of
the side's median, for runs up to 20% of the side, are replaced by a
quadratic fitted to the rest of the side (Requirement 2.2). `trace_outline`
is refactored so that both share the strip sampling and the refinement.

### Cropping to the sleeve

The sleeve's own outline is rarely seen whole: the clear margin is faint
against white paper beneath it, and the rounded corners on some sleeves are
not a quad. So the sleeve crop is not traced. It is the paper's outline grown
outward on each side by the mean width of that side's sleeve bands, in page
coordinates: on the planar path the output rectangle is enlarged and the
homography `P` is computed from the paper corners to the paper's rectangle
within it; on the curved path the page fractions passed to `flatten_maps`
extend below 0 and above 1, since the surface already continues past the page
(`EXTEND` in `curvature.py`). Requirement 3.2 gives the width used where a
side's sleeve is not seen.

### Curvature uses the paper's edges and the strip

`fit_page` gains two arguments: `outline`, the four traced sides in the
order `trace_outline` returns them (or `None` per side), used instead of
tracing when given; and `binding_hint`, the strip side. The hint gets the
same `neighbour_bonus` factor as `neighbour_side`, once when both name the
same side, and is ignored when `CurvatureParams.binding` names a side. Paper
edges are better evidence than today's traces for two reasons: they do not
jump, and on the crimp sides the paper's edge bends with the page while the
crimp, a stiffer welded seam, bends less.

### Unsleeved pages take today's path

When the page is not sleeved, `find_sleeve` returns the outer outline
unchanged and no traced sides, `fit_page` traces as today, and the crop
option is ignored, so every value downstream is as before (Requirement 4.1).
The existing synthetic pages have no sleeve; a test checks that none is found
on any of them, and the composed image hashes are compared with sleeve
detection on and off.

## Components

### `sleeve.py` (new)

```python
BAND_KINDS = ("crimp", "strip", "clear", "paper", "outside")

@dataclass
class SleeveParams:
    mode: str = "auto"                 # auto | off
    crop: str = "paper"                # paper | sleeve
    out: float = 0.04                  # corridor outside the outer outline, fraction of page width
    inside: float = 0.08               # corridor inside it
    segments: int = 16
    max_step: float = 0.0025           # path step per segment, fraction of page width
    min_support: float = 0.6
    max_paths: int = 6
    ridge_min: float = 0.5             # crimp
    ridge_lags: tuple[float, float] = (0.002, 0.015)
    crimp_width: tuple[float, float] = (0.005, 0.04)
    strip_width: tuple[float, float] = (0.01, 0.04)
    strip_spread: float = 0.003
    gap_width: float = 0.015
    end_reach: float = 0.02            # end support, fraction of side length
    trace_band: float = 0.003          # refinement corridor, fraction of page width
    bridge: float = 0.2                # longest bridged stretch, fraction of side length
    def validate(self) -> None: ...

@dataclass
class Band:
    kind: str                          # one of BAND_KINDS
    width: float                       # mean, fraction of page width
    spread: float
    ridge: float

@dataclass
class SideFinding:
    name: str                          # top | right | bottom | left, of the outer outline
    paths: list[np.ndarray]            # candidate edges, outside to inside, (n, 2) mosaic px
    bands: list[Band]                  # outside to inside
    paper: int | None                  # index into paths, None when not found
    sleeve_seen: bool

@dataclass
class SleeveFit:
    sleeved: bool
    reason: str | None                 # few_sides | no_sleeve_band | off | None
    corners: np.ndarray                # (4, 2) mosaic px, TL TR BR BL: paper, or the outer outline
    outline: list[np.ndarray | None]   # traced paper sides, trace_outline's order; [] when not sleeved
    strip_side: str | None             # in outer-outline side names
    margins: dict[str, float]          # sleeve band width per side, fraction of page width
    sides: list[SideFinding]
    def report(self) -> dict: ...

def corridor(img, valid, a, b, out_px, in_px) -> tuple[np.ndarray, np.ndarray, np.ndarray]: ...  # strip, valid, grid
def candidate_paths(strength: np.ndarray, p: SleeveParams, px_per_width: float) -> list[tuple[np.ndarray, float]]: ...
def classify_bands(strip: np.ndarray, paths, paper_ref: np.ndarray, p: SleeveParams, px_per_width: float) -> list[Band]: ...
def choose_paper(sides: list[SideFinding], p: SleeveParams) -> tuple[list[int | None], np.ndarray] | None: ...
def trace_path(strip, path, sign: int, p: SleeveParams, px_per_width: float) -> np.ndarray: ...
def find_sleeve(mosaic: np.ndarray, valid: np.ndarray, quad: np.ndarray, p: SleeveParams) -> SleeveFit: ...
```

Side names are those of the outer outline in the mosaic, which are the
composed page's sides, as for the curvature report's binding side.

### `curvature.py` (changed)

- The corridor sampling and the sub-pixel refinement in `trace_outline` move
  into helpers shared with `sleeve.py`; `trace_outline`'s results are
  unchanged.
- `fit_page(..., outline=None, binding_hint=None)` as described above.

### `pipeline.py` (changed)

- `PageOptions` gains `sleeve: SleeveParams`.
- After `_choose_page`: unless the mode is `off`, call `find_sleeve` on the
  preview mosaic, and when it is sleeved, use `fit.corners` for
  `corners_ref`, `fit.outline` and `fit.strip_side` for `fit_page`.
- With `crop="sleeve"` on a sleeved page, grow the output rectangle (planar)
  or the page fractions (curved) by `margins`.
- `PageReport` gains `sleeve: dict`; `SCHEMA_VERSION` goes from 3 to 4.
- `--debug` writes `sleeve.jpg`: the mosaic with each side's candidate paths
  in grey, bands tinted by kind (crimp orange, strip blue, clear cyan, paper
  none, outside dark), the paper outline in green and the outer outline in
  red.

### `cli.py`, `album.py` (changed)

```
albumproc page PHOTOS... -o PAGE.png [--sleeve auto|off] [--crop paper|sleeve] [existing options]
albumproc album DIR -o OUT [--sleeve ...] [--crop ...] [existing options]
```

`album.json` page entries accept `"sleeve"` and `"crop"` beside `"curvature"`
and `"binding"`, and `_page_options` folds them in the same way, so they join
the cache key. The album server passes no options, so its pages get the
defaults (`auto`, `paper`).

### `synth.py` (changed)

`make_case(..., sleeve=None | SleeveSpec)`, for the `flat`, `stitch` and
`curved` kinds. `SleeveSpec` gives the binding side, crimp width (default
1.5%) and ridge period (0.55%), gap (0 to 1%), strip width (2.5%), clear
margin (0 to 5.5%), corner radius (0 or 2%), paper shift (up to 0.5% each
way) and turn (up to 0.5°), and the print border's distance inside the paper
(1 to 3%). The sleeve is drawn in page coordinates around the paper before
the page is rendered into the shots, so a bent page bends the sleeve with
it: crimp as a grey band with a sinusoidal ridge pattern and a highlight,
strip as an opaque cream band, clear plastic as the background seen through
a 10% to 20% white haze, with the existing sleeve glare on top. The case keeps
`paper_corners`, `sleeve_corners` and the band kinds per side as truth.
`--sleeve` on `albumproc synth` turns on a default sleeve.

### Labelled real pages (private data repository)

`real/<page>/paper.json` for the five two-shot pages:

```json
{
  "binding": "left",
  "corners": {"shot": "shot_01.jpg", "points": [[412, 380], [3755, 402], [3731, 3933], [398, 3911]]},
  "edges": {"top": [{"shot": "shot_01.jpg", "points": [[...], ...]}], "right": [...], "bottom": [...], "left": [...]}
}
```

Corners are TL, TR, BR, BL of the upright page. Each side has 12 to 20
points on the paper's edge, each set recorded in the shot it was marked in.
The test maps them into the preview mosaic with the registration's
homographies. Labels are drawn on zoomed crops of the full-resolution shots
and checked by overlaying them on the shot.

## Page metadata example

```json
"sleeve": {
  "mode": "auto", "crop": "paper", "sleeved": true, "reason": null,
  "strip_side": "left",
  "sides": {
    "top":    {"bands": [{"kind": "crimp", "width": 0.015}, {"kind": "clear", "width": 0.009}], "paper_found": true, "sleeve_seen": true},
    "right":  {"bands": [{"kind": "clear", "width": 0.054}], "paper_found": true, "sleeve_seen": false},
    "bottom": {"bands": [{"kind": "crimp", "width": 0.016}, {"kind": "clear", "width": 0.004}], "paper_found": true, "sleeve_seen": true},
    "left":   {"bands": [{"kind": "strip", "width": 0.025}], "paper_found": true, "sleeve_seen": true}
  }
}
```

Bands are listed from the outside in, `outside` and `paper` bands left out.
On an unsleeved page the object is present with `"sleeved": false`, its
reason, and empty `bands`.

Warning example:

```json
{"code": "paper_edge_uncertain", "side": "right",
 "message": "The paper's right edge could not be told apart from the sleeve, so the crop there follows the outermost edge found and may include some sleeve."}
```

## Error handling

| Situation | Behaviour |
| --- | --- |
| Mode `off` | today's outer outline; `reason: "off"` |
| No crimp or strip band on any side | not sleeved; `reason: "no_sleeve_band"`; today's path |
| Paper edge found on three sides | sleeved; the fourth side is the outer outline's; `paper_edge_uncertain` |
| Paper edge found on two sides or fewer | not sleeved; `reason: "few_sides"`; today's path |
| A side of the outer outline lies outside what was photographed | that corridor is empty: no paths, the side counts as not found |
| Chosen paper quad not convex, or angles outside 45° to 135° | that combination is rejected (see Choosing the paper edges) |
| A bridged stretch longer than 20% of a side | the side counts as not found |
| `find_sleeve` raises | `process_page` fails as it does today; the sleeve step is not wrapped, so a bug shows up rather than silently cropping differently |

## Testing strategy

- **Unit** (`tests/test_sleeve.py`, on constructed strips): ridge measurement
  on a sinusoidal band at several periods; each band kind classified on a
  hand-built strip; candidate paths follow a 1.5% bow and do not hop to a
  parallel edge 1% away; `choose_paper` rejects a print border 2% inside the
  paper by end support and picks it when the print fills the paper; the trace
  bridges a 15% gap and refuses a 25% one.
- **Synthetic pipeline** (`tests/test_pipeline.py`, new cases): sleeved
  `flat`, `stitch` and `curved` pages over several seeds and binding sides,
  checking Requirement 8.2 to 8.4; `crop="sleeve"` puts the composed page's
  edges at the true sleeve outline within 0.5% of the diagonal; every existing
  case is not sleeved and its image hash and metadata are unchanged with the
  sleeve step on and off (Requirement 4.1).
- **Real** (`tests/test_real.py`, skipped without the data): Requirement 8.5
  and 8.6 on the labelled pages, the paper aspect from the labels within 1.5%,
  and the 19 `glare-*` pages all sleeved (8.7, marked slow).
- **CLI and album**: the options reach the pipeline; `album.json` per-page
  values override and change the cache key; `sleeve.jpg` is written with
  `--debug`.
- **Run time**: real pages with `auto` against `off` within 10%, synthetic
  flat cases within 5%, plus timer noise.

## Out of scope

- Using the whole album: every page of an album sits in the same sleeve, so
  the paper's size and the band widths could be learned from the confident
  pages and imposed on the doubtful ones. Pages are processed one at a time
  by the server, so this needs an album-level pass; it is a later change.
- Sleeve wrinkles and bubbles inside the paper area, and sheen over the paper.
  Glare handling covers sheen; wrinkles are left as they are.
- Paper torn or cut out of square, and pages with no straight edges.
- Sleeves of other designs (side-loading with no crimp, top-loading pocket
  pages with several photos). They fall back to today's path when no crimp
  or strip is found.
- Reprocessing pages the server processed before this change; they keep
  their old crop until a shot changes or the page is reprocessed.
