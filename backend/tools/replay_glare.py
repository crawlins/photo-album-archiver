"""Replay real pages with fewer shots, then follow the second-shot prompts.

Not installed with the package. Run from ``backend/``:

    python tools/replay_glare.py ../real/glare-* [--out ~/paa-replay] [--pairs 6] [--detector both]

Each page folder holds the shots of one page (``*.jpg``, as in ``real/`` of
the private photo-album-archiver-data repository). Only numbers are written
(``replay.json`` under ``--out`` and a table on stdout), never images, masks
or photos, and ``--out`` may not lie inside this repository's working tree.

For each page with at least four used shots:

1. The **reference** is the page composed from all its shots, close to
   glare-free wherever any shot sees past the glare.
2. A replay starts from a **stitch pair**: two shots that together cover the
   reference page and overlap on less than half of it, as a user stitching a
   page would take them; or, since most of the glare album's shots are of
   the whole page, from **one whole-page shot**, as a user who took a single
   photo would. Each kind of start is summarised on its own and together.
3. It then **follows the prompts**: the largest ``single_coverage`` region of
   the composition is outlined, the unused shot that covers most of that
   outline is added, and the page is composed again, up to two prompted
   shots or until no ``single_coverage`` warning remains. The best such shot
   is what a user aiming at the rectangle would take at best; the worst one
   that still covers half the rectangle is reported as a bound.
4. Every composition is laid onto the reference's grid through a shot both
   runs used (``shots_to_grid``; feature matching between the composed
   pages, ``frame_homography``, for a bent page), refined by feature matching once both lie on one grid. With one
   or two shots the pipeline sometimes takes the whole photo or a
   neighbouring page for the page; such a replay is skipped
   (``other_outline``), since it is not of the same page. Its **truth
   glare** is what is brighter than the reference by the overlap labels'
   glare threshold, after their smooth gain and shift allowance
   (``tools/real_glare_data.py``), so that the truth means what the net's
   training labels mean. Glare every shot has is in the reference too and
   so counts as none here.

The numbers (``summarise``) are the single-shot glare before any prompted
shot, the share of it removed after one and two prompted shots, and the
shares of all glare inside and outside the warned regions, for each
detector. Requirement 1.7 of ``.kiro/specs/single-shot-glare`` is the gate:
the prompts are a sufficient guarantee when they remove at least 80% of the
single-shot glare and leave at most 10% of all glare in single coverage
outside every warned region.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from albumproc import pipeline  # noqa: E402
from albumproc.glare import GlareParams  # noqa: E402
from real_glare_data import LABEL_GLARE, LABEL_UNKNOWN, _relight, clean_labels  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DETECTORS = ("features", "net")
GLARE = 0.08  # real_glare_data.overlap_labels' glare threshold
SHIFT_PX = 3  # and its shift allowance, in grid px
MIN_SHOTS = 4
PROMPTS = 2  # prompted shots per replay
NO_GLARE = 0.001  # a region with truth glare on less than this share of it has none
MIN_COVER = 0.5  # the worst prompted shot still covers this share of the rectangle


@dataclass
class Composition:
    """One run of the page pipeline on some of a page's shots, laid onto the reference grid."""

    shots: tuple[int, ...]  # input indexes given
    used: list[int]
    dropped: list[int]
    page: np.ndarray | None  # composed page on the reference grid (BGR); None where it could not be aligned
    covered: np.ndarray | None  # bool: covered by at least one shot
    single: np.ndarray | None  # bool: covered by exactly one shot
    # Per detector: the warned area (``single_coverage`` and ``glare`` boxes),
    # and each single-coverage region as (box mask, glare_seen).
    warned: dict[str, np.ndarray] = field(default_factory=dict)
    single_regions: dict[str, list[tuple[np.ndarray, float | None]]] = field(default_factory=dict)
    largest: np.ndarray | None = None  # box of the largest single-coverage region, on the grid
    # The composed page's area on the reference grid over the reference
    # page's, and the share of the reference page it covers: with few shots
    # the pipeline can take the whole photo, or a neighbouring page, for the
    # page, and then there is nothing to compare.
    scale: float = 0.0
    overlap: float = 0.0
    inliers: int = 0  # SIFT inliers of the frame match; -1 where the frame came from the shots' geometry
    seconds: float = 0.0


@dataclass
class Ref:
    """The page composed from all its shots, the truth replays are measured against."""

    name: str
    images: list[np.ndarray]
    used: list[int]
    page: np.ndarray  # BGR on the fusion grid
    covered: np.ndarray  # bool on the grid
    masks: dict[int, np.ndarray]  # per used input index, where it covers the reference page on the grid
    # Per used input index, the homography from that shot's own pixels to the
    # grid; empty for a bent page, which no homography describes.
    from_shot: dict[int, np.ndarray] = field(default_factory=dict)


@dataclass
class Trace:
    """One replay: a stitch pair or one whole-page shot, then up to ``PROMPTS`` prompted shots by the best or the worst rule."""

    start: tuple[int, ...]
    rule: str  # best | worst
    steps: list[Composition]
    skipped: str | None = None  # dropped | unaligned | other_outline, when it does not count


def _run(images: list[np.ndarray], options: pipeline.PageOptions):
    """``process_page`` and the fusion result it composed the page from."""
    captured = {}
    fuse = pipeline.fuse

    def keep(*a, **k):
        captured["r"] = fuse(*a, **k)
        return captured["r"]

    pipeline.fuse = keep
    try:
        res = pipeline.process_page(images, options)
    finally:
        pipeline.fuse = fuse
    return res, captured["r"]


def reference(name: str, images: list[np.ndarray], max_side: int = 2400) -> Ref:
    """The reference composition: all shots, detector off, on the fusion grid."""
    res, r = _run(images, pipeline.PageOptions(max_side=max_side, glare=GlareParams(enabled=False)))
    grid = r.masks_small[0].shape
    page = cv2.resize(res.image, grid[::-1], interpolation=cv2.INTER_AREA)
    masks = {i: m.astype(bool) for i, m in zip(res.report.used, r.masks_small)}
    covered = np.any(np.stack(list(masks.values())), axis=0)
    return Ref(name, images, list(res.report.used), page, covered, masks, shots_to_grid(res, grid))


def shots_to_grid(res, grid: tuple[int, int], inset: float = pipeline.PageOptions().inset) -> dict[int, np.ndarray]:
    """Per used shot (by its index in the run), the homography from its pixels to the run's fusion grid.

    Rebuilt as ``process_page`` builds the page rectangle: the page corners
    in the reference photo (``corners_ref``) go to the inset rectangle, and
    each shot reaches the reference photo through the preview mosaic. Empty
    for a bent page.
    """
    if res.surface is not None:
        return {}
    W, H = res.report.size
    dx, dy = inset * W, inset * H
    rect = np.float32([[-dx, -dy], [W - 1 + dx, -dy], [W - 1 + dx, H - 1 + dy], [-dx, H - 1 + dy]])
    P = cv2.getPerspectiveTransform(res.corners_ref.astype(np.float32), rect).astype(np.float64)
    gh, gw = grid
    S = np.diag([gw / W, gh / H, 1.0])
    to_ref = np.linalg.inv(res.mosaic_from_shot[res.report.reference])
    return {i: S @ P @ to_ref @ M for i, M in res.mosaic_from_shot.items()}


def refine(src: np.ndarray, dst: np.ndarray, H: np.ndarray, max_move: float = 25.0) -> np.ndarray:
    """``H`` corrected by feature matching once ``src`` lies on ``dst``'s grid.

    Two runs register their shots separately, so the frame through a shared
    shot can be a few grid px off, more than the truth's shift allowance.
    Laid on the same grid at the same scale, the two composed pages match
    well; a correction that moves the page by more than ``max_move`` px is
    a wrong match, and ``H`` is kept.
    """
    shape = dst.shape[:2]
    warped = cv2.warpPerspective(src, H, shape[::-1], flags=cv2.INTER_LINEAR)
    C, _ = frame_homography(warped, dst, 50)
    if C is None:
        return H
    h, w = shape
    c = np.float64([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    moved = float(np.abs(cv2.perspectiveTransform(c, C) - c).max())
    return C @ H if moved < max_move else H


def frame_homography(src: np.ndarray, dst: np.ndarray, min_inliers: int = 30) -> tuple[np.ndarray | None, int]:
    """The homography taking ``src``'s pixels to ``dst``'s, from SIFT matches, as ``evaluate._align`` finds it; None if too few agree."""
    sift = cv2.SIFT_create(4000)
    k1, d1 = sift.detectAndCompute(cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), None)
    k2, d2 = sift.detectAndCompute(cv2.cvtColor(dst, cv2.COLOR_BGR2GRAY), None)
    if d1 is None or d2 is None or len(k1) < min_inliers or len(k2) < min_inliers:
        return None, 0
    knn = cv2.BFMatcher().knnMatch(d1, d2, k=2)
    good = [m for m, *r in knn if r and m.distance < 0.75 * r[0].distance]
    if len(good) < min_inliers:
        return None, len(good)
    a = np.float32([k1[m.queryIdx].pt for m in good])
    b = np.float32([k2[m.trainIdx].pt for m in good])
    H, inl = cv2.findHomography(a, b, cv2.USAC_MAGSAC, 2.0)
    n = int(inl.sum()) if inl is not None else 0
    return (H, n) if H is not None and n >= min_inliers else (None, n)


def _box_mask(bbox: list[float], shape: tuple[int, int]) -> np.ndarray:
    """A region's box (page fractions) filled on a grid of ``shape``."""
    h, w = shape
    x0, y0, x1, y1 = bbox
    m = np.zeros(shape, np.uint8)
    m[int(np.floor(y0 * h)) : int(np.ceil(y1 * h)), int(np.floor(x0 * w)) : int(np.ceil(x1 * w))] = 1
    return m


def _onto(a: np.ndarray, H: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A grid mask warped onto the reference grid."""
    return cv2.warpPerspective(a.astype(np.uint8), H, shape[::-1], flags=cv2.INTER_NEAREST) > 0


def compose(ref: Ref, shots: tuple[int, ...], detectors=DETECTORS, max_side: int = 2400) -> Composition:
    """Run the pipeline on ``shots`` and lay what it found onto the reference grid.

    The page is run once, with the first detector; the others reassess the
    same fusion (``pipeline._assess``), since the detector changes only the
    metadata, never the composed page.
    """
    t = time.perf_counter()
    opt = pipeline.PageOptions(max_side=max_side, glare=GlareParams(detector=detectors[0]))
    res, r = _run([ref.images[i] for i in shots], opt)
    rep = res.report
    used = [shots[i] for i in rep.used]
    dropped = [shots[i] for i in rep.dropped]
    grid = r.masks_small[0].shape
    page = cv2.resize(res.image, grid[::-1], interpolation=cv2.INTER_AREA)
    c = Composition(tuple(shots), used, dropped, None, None, None)
    # Through a shot both runs used: its pixels to this run's grid and to the
    # reference's. With one shot the page found may be another outline (the
    # open album, say) at another scale, which feature matching between the
    # composed pages cannot follow. A bent page falls back to the matching.
    mine = shots_to_grid(res, grid)
    common = [k for k, i in enumerate(shots) if k in mine and i in ref.from_shot]
    if common:
        k = rep.reference if rep.reference in common else common[0]
        H = ref.from_shot[shots[k]] @ np.linalg.inv(mine[k])
        c.inliers = -1  # by geometry
    else:
        H, c.inliers = frame_homography(page, ref.page)
    if H is not None:
        H = refine(page, ref.page, H)
    if H is None:
        c.seconds = time.perf_counter() - t
        return c
    shape = ref.page.shape[:2]
    gh, gw = grid
    corners = cv2.perspectiveTransform(np.float64([[0, 0], [gw, 0], [gw, gh], [0, gh]]).reshape(-1, 1, 2), H).astype(np.float32)
    c.scale = round(float(cv2.contourArea(corners)) / float(shape[0] * shape[1]), 3)
    count = np.sum([m.astype(np.uint8) for m in r.masks_small], axis=0)
    c.page = cv2.warpPerspective(page, H, shape[::-1], flags=cv2.INTER_LINEAR)
    c.covered = _onto(count > 0, H, shape)
    c.overlap = round(float((c.covered & ref.covered).sum()) / max(float(ref.covered.sum()), 1.0), 3)
    c.single = _onto(count == 1, H, shape)
    for det in detectors:
        if det == detectors[0]:
            warnings, single = rep.warnings, rep.single_coverage
        else:
            q = pipeline._assess(r, rep.used, rep.dropped, rep.size, replace(opt, glare=GlareParams(detector=det)))
            warnings, single = q.warnings, q.single
        warned = np.zeros(shape, bool)
        for w in warnings:
            if w["code"] in ("single_coverage", "glare"):
                for g in w["regions"]:
                    warned |= _onto(_box_mask(g["bbox"], grid), H, shape)
        c.warned[det] = warned
        c.single_regions[det] = [(_onto(_box_mask(g["bbox"], grid), H, shape), g.get("glare_seen")) for g in single["regions"]]
    regions = rep.single_coverage["regions"]
    c.largest = _onto(_box_mask(regions[0]["bbox"], grid), H, shape) if regions else None
    c.seconds = time.perf_counter() - t
    return c


def truth(page: np.ndarray, covered: np.ndarray, ref: Ref, glare: float = GLARE, shift_px: int = SHIFT_PX) -> np.ndarray:
    """Glare in a composition laid onto the reference grid: clearly brighter than the reference.

    As ``real_glare_data.overlap_labels`` labels a shot against the others:
    luminance lightly smoothed, brought to the reference by a smooth gain,
    glare where it exceeds the brightest reference within ``shift_px`` by
    ``glare``; then the rim and thin lines dropped as ``clean_labels`` does.
    """

    def lum(img):
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        return cv2.GaussianBlur(g, (0, 0), 2.0)

    sel = covered & ref.covered
    li, lr = lum(page), lum(ref.page)
    li = _relight(li, lr, sel, glare)
    k = 2 * shift_px + 1
    hi = cv2.dilate(lr, np.ones((k, k), np.uint8)) if shift_px > 0 else lr
    lab = np.full(sel.shape, LABEL_UNKNOWN, np.uint8)
    lab[sel & (li - hi >= glare)] = LABEL_GLARE
    return clean_labels(lab, sel) == LABEL_GLARE


def stitch_pairs(ref: Ref, limit: int = 6, min_union: float = 0.98, max_overlap: float = 0.5, min_own: float = 0.2) -> list[tuple[int, int]]:
    """Pairs of shots that stitch the page, least overlap first.

    Together they cover at least ``min_union`` of the reference page and
    overlap on less than ``max_overlap`` of it, and each covers at least
    ``min_own`` of it that the other does not: a whole-page shot and a
    close-up of a corner is not how anyone stitches a page.
    """
    area = float(ref.covered.sum())
    found = []
    for a, b in itertools.combinations(ref.used, 2):
        ma, mb = ref.masks[a], ref.masks[b]
        union = float((ma | mb).sum()) / area
        overlap = float((ma & mb).sum()) / area
        own = min(float((ma & ~mb).sum()), float((mb & ~ma).sum())) / area
        if union >= min_union and overlap < max_overlap and own >= min_own:
            found.append((overlap, a, b))
    found.sort()
    return [(a, b) for _, a, b in found[:limit]]


def prompted_shot(ref: Ref, box: np.ndarray, unused: list[int], rule: str) -> int | None:
    """The unused shot a user aiming at ``box`` would add: the one covering most of it (best), or the least that still covers ``MIN_COVER`` of it (worst)."""
    n = float(box.sum())
    if n == 0 or not unused:
        return None
    cover = {j: float((ref.masks[j] & box).sum()) / n for j in unused}
    best = max(cover, key=lambda j: (cover[j], -j))
    if cover[best] == 0:
        return None
    if rule == "best":
        return best
    enough = [j for j in cover if cover[j] >= MIN_COVER]
    return min(enough, key=lambda j: (cover[j], j)) if enough else best


def whole_shots(ref: Ref, limit: int = 4, min_cover: float = 0.98) -> list[tuple[int]]:
    """Shots covering at least ``min_cover`` of the reference page on their own, at most ``limit`` of them spread over the capture order."""
    area = float(ref.covered.sum())
    whole = [i for i in ref.used if float(ref.masks[i].sum()) / area >= min_cover]
    if len(whole) > limit:
        whole = [whole[k] for k in np.linspace(0, len(whole) - 1, limit).round().astype(int)]
    return [(i,) for i in whole]


# A composition must cover this share of the reference page, and its page
# be no more than this much larger: few shots can give a smaller outline
# (part of the page, which is still measured where it overlaps), the whole
# photo (at a third of the resolution, which reads as glare along every
# edge), or a neighbouring page (no overlap).
MIN_OVERLAP = 0.3
MAX_SCALE = 1.5


def same_page(c: Composition) -> bool:
    """Whether a composition is of the reference's page outline (see ``Composition.scale``)."""
    return c.page is not None and c.overlap >= MIN_OVERLAP and c.scale <= MAX_SCALE


def replay(ref: Ref, start: tuple[int, ...], rule: str, run=None) -> Trace:
    """Start from the shots in ``start`` and add prompted shots by ``rule`` (``prompted_shot``).

    ``run(shots) -> Composition`` composes a subset (``compose`` by default);
    the caller may cache it, since the best and worst rules often agree.
    """
    run = run or (lambda shots: compose(ref, shots))
    shots = tuple(sorted(start))
    steps = [run(shots)]
    while len(steps) <= PROMPTS:
        c = steps[-1]
        if c.dropped:
            return Trace(start, rule, steps, "dropped")
        if c.page is None:
            return Trace(start, rule, steps, "unaligned")
        if not same_page(c):
            return Trace(start, rule, steps, "other_outline")
        if c.largest is None:
            break
        j = prompted_shot(ref, c.largest, [i for i in ref.used if i not in shots], rule)
        if j is None:
            break
        shots = tuple(sorted(shots + (j,)))
        steps.append(run(shots))
    last = steps[-1]
    if last.dropped:
        return Trace(start, rule, steps, "dropped")
    if last.page is None:
        return Trace(start, rule, steps, "unaligned")
    if not same_page(last):
        return Trace(start, rule, steps, "other_outline")
    return Trace(start, rule, steps)


def measure(ref: Ref, trace: Trace, glare_seen: float = 0.02) -> dict:
    """The numbers of one replay, in reference-grid pixels (see ``summarise``)."""
    first = trace.steps[0]
    g0 = truth(first.page, first.covered, ref)
    s0 = g0 & first.single  # single-shot glare before any prompted shot
    removed = []
    for k in range(1, PROMPTS + 1):
        c = trace.steps[min(k, len(trace.steps) - 1)]
        left = truth(c.page, c.covered, ref) & s0
        removed.append(int(s0.sum() - left.sum()))
    page = float(ref.covered.sum())
    out = {
        "start": list(trace.start),
        "kind": "stitch" if len(trace.start) == 2 else "single",
        "rule": trace.rule,
        "shots": [list(c.shots) for c in trace.steps],
        "warnings_left": first.largest is not None and trace.steps[-1].largest is not None,
        "page_px": int(page),
        "glare_px": int(g0.sum()),
        "single_glare_px": int(s0.sum()),
        "removed_px": removed,
        "inliers": [c.inliers for c in trace.steps],
        "scale": [c.scale for c in trace.steps],
        "detectors": {},
    }
    for det, warned in first.warned.items():
        regions = first.single_regions[det]
        meets = [m for m, seen in regions if seen is not None and seen >= glare_seen]
        flagged = np.zeros_like(s0)
        for m in meets:
            flagged |= m
        out["detectors"][det] = {
            "glare_in_warned_px": int((g0 & warned).sum()),
            "single_glare_unwarned_px": int((s0 & ~warned).sum()),
            "overlap_glare_unwarned_px": int((g0 & ~first.single & ~warned).sum()),
            "single_glare_flagged_px": int((s0 & flagged).sum()),
            "regions_flagged": len(meets),
            "regions_flagged_clean": sum(float((s0 & m).sum()) < NO_GLARE * float(m.sum()) for m in meets),
        }
    return out


def _share(a: float, b: float) -> float | None:
    return round(a / b, 4) if b > 0 else None


def summarise(results: list[dict]) -> dict:
    """Area-weighted shares over the counted replays in ``results`` (``measure``'s output).

    - ``single_glare``: single-shot glare before any prompted shot, share of the page;
    - ``removed_1``, ``removed_2``: share of it gone after one and two prompted shots;
    - per detector, ``glare_in_warned``: share of all glare inside a warned
      region; ``single_glare_unwarned``: share of all glare in single
      coverage outside every warned region; ``overlap_glare_unwarned``: the
      rest, glare where shots overlap outside every warned region (the three
      add up to 1); ``single_glare_flagged``: share
      of the single-shot glare inside regions meeting the glare-seen
      threshold; ``flagged_clean``: share of those regions with no truth glare.
    """
    tot = lambda k: float(sum(r[k] for r in results))  # noqa: E731
    out = {
        "replays": len(results),
        "single_glare": _share(tot("single_glare_px"), tot("page_px")),
        "glare": _share(tot("glare_px"), tot("page_px")),
    }
    s0 = tot("single_glare_px")
    for k in range(PROMPTS):
        out[f"removed_{k + 1}"] = _share(float(sum(r["removed_px"][k] for r in results)), s0)
    dets = sorted({d for r in results for d in r["detectors"]})
    g = tot("glare_px")
    out["detectors"] = {}
    for d in dets:
        rs = [r["detectors"][d] for r in results if d in r["detectors"]]
        t = lambda k: float(sum(x[k] for x in rs))  # noqa: E731
        out["detectors"][d] = {
            "glare_in_warned": _share(t("glare_in_warned_px"), g),
            "single_glare_unwarned": _share(t("single_glare_unwarned_px"), g),
            "overlap_glare_unwarned": _share(t("overlap_glare_unwarned_px"), g),
            "single_glare_flagged": _share(t("single_glare_flagged_px"), s0),
            "regions_flagged": int(t("regions_flagged")),
            "flagged_clean": _share(t("regions_flagged_clean"), t("regions_flagged")),
        }
    return out


def gate(summary: dict) -> dict:
    """Requirement 1.7: whether the prompts are a sufficient guarantee on these replays."""
    removed = summary.get(f"removed_{PROMPTS}")
    unwarned = max((d["single_glare_unwarned"] or 0.0 for d in summary["detectors"].values()), default=0.0)
    return {
        "removed": removed,
        "single_glare_unwarned": unwarned,
        "pass": removed is not None and removed >= 0.8 and unwarned <= 0.1,
    }


def check_out(out: Path, repo: Path = REPO) -> Path:
    """``out``, resolved; refused when inside the repository's working tree, where real-page numbers must not be written by accident."""
    o = out.expanduser().resolve()
    r = repo.resolve()
    if o == r or r in o.parents:
        raise SystemExit(f"--out {out} is inside the repository ({r}); write replay results outside it")
    return o


def run_page(d: Path, pairs: int, singles: int, detectors: tuple[str, ...], max_side: int, glare_seen: float, log=print) -> dict:
    """Every replay of one page folder."""
    images = [cv2.imread(str(f)) for f in sorted(d.glob("*.jpg"))]
    entry: dict = {"page": d.name, "shots": len(images)}
    try:
        ref = reference(d.name, images, max_side)
    except Exception as e:  # noqa: BLE001 - one bad page must not stop the run
        entry["skipped"] = f"reference failed: {e}"
        log(_row(d.name, entry))
        return entry
    entry["used"] = len(ref.used)
    if len(ref.used) < MIN_SHOTS:
        entry["skipped"] = f"only {len(ref.used)} shots used"
        log(_row(d.name, entry))
        return entry
    cache: dict[tuple[int, ...], Composition] = {}

    def run(shots):
        if shots not in cache:
            cache[shots] = compose(ref, shots, detectors, max_side)
        return cache[shots]

    entry["pairs"] = [list(p) for p in stitch_pairs(ref, pairs)]
    entry["singles"] = [list(p) for p in whole_shots(ref, singles)]
    entry["replays"], entry["skipped_replays"] = [], []
    for start in entry["pairs"] + entry["singles"]:
        for rule in ("best", "worst"):
            tr = replay(ref, tuple(start), rule, run)
            if tr.skipped:
                entry["skipped_replays"].append({"start": start, "rule": rule, "why": tr.skipped})
                continue
            entry["replays"].append(measure(ref, tr, glare_seen))
    entry["runs"] = len(cache)
    entry.update(_summaries(entry["replays"]))
    log(_row(d.name, entry))
    return entry


def _summaries(replays: list[dict]) -> dict:
    """``summarise`` by rule, over all starts and over each kind of start."""
    out = {rule: summarise([r for r in replays if r["rule"] == rule]) for rule in ("best", "worst")}
    for kind in ("stitch", "single"):
        out[kind] = {rule: summarise([r for r in replays if r["rule"] == rule and r["kind"] == kind]) for rule in ("best", "worst")}
    return out


def _fmt(v) -> str:
    return "  -  " if v is None else f"{v * 100:5.1f}"


def _row(name: str, e: dict) -> str:
    if "skipped" in e:
        return f"{name:<12} skipped: {e['skipped']}"
    b, w = e["best"], e["worst"]
    dets = "".join(f" {_fmt(b['detectors'][d]['glare_in_warned'])} {_fmt(b['detectors'][d]['single_glare_unwarned'])}" for d in b["detectors"])
    return f"{name:<12} {e['used']:>3} {e['stitch']['best']['replays']:>3} {e['single']['best']['replays']:>3} {_fmt(b['single_glare'])} {_fmt(b['removed_1'])} {_fmt(b['removed_2'])} {_fmt(w['removed_2'])}{dets}"


def _header(detectors) -> str:
    dets = "".join(f" {d[:5] + ' in':>5} {'out':>5}" for d in detectors)
    return f"{'page':<12} {'used':>3} {'stc':>3} {'one':>3} {'sgl%':>5} {'rm1%':>5} {'rm2%':>5} {'wrst':>5}{dets}"


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pages", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, default=Path("~/paa-replay"), help="folder for replay.json, outside the repository (default ~/paa-replay)")
    ap.add_argument("--pairs", type=int, default=6, help="stitch pairs tried per page, least overlap first")
    ap.add_argument("--singles", type=int, default=4, help="whole-page shots tried per page as one-photo starts")
    ap.add_argument("--detector", choices=["features", "net", "both"], default="both")
    ap.add_argument("--max-side", type=int, default=2400, help="composed page's long side; the fusion grid is the same at any size above 1000")
    ap.add_argument("--glare-seen", type=float, default=0.02, help="glare-seen threshold for the detectors' flagged regions")
    a = ap.parse_args(argv)
    out = check_out(a.out)
    out.mkdir(parents=True, exist_ok=True)
    detectors = DETECTORS if a.detector == "both" else (a.detector,)
    started = datetime.now(timezone.utc)
    print(_header(detectors), flush=True)
    pages = [run_page(d, a.pairs, a.singles, detectors, a.max_side, a.glare_seen, lambda s: print(s, flush=True)) for d in a.pages]
    counted = [r for p in pages for r in p.get("replays", [])]
    overall = _summaries(counted)
    result = {
        "commit": _commit(),
        "started": started.isoformat(timespec="seconds"),
        "seconds": round((datetime.now(timezone.utc) - started).total_seconds()),
        "options": {"pairs": a.pairs, "singles": a.singles, "detectors": list(detectors), "max_side": a.max_side, "glare_seen": a.glare_seen},
        "overall": overall,
        "gate": gate(overall["best"]),
        "pages": pages,
    }
    print(_row("all", {"used": sum(p.get("used", 0) for p in pages), **overall}))
    g = result["gate"]
    print(f"gate (requirement 1.7): removed {_fmt(g['removed'])}% after {PROMPTS} prompted shots, {_fmt(g['single_glare_unwarned'])}% of glare unwarned: {'pass' if g['pass'] else 'FAIL'}")
    (out / "replay.json").write_text(json.dumps(result, indent=1))
    print(f"wrote {out / 'replay.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
