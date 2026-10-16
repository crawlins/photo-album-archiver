"""Album pages in clear plastic sleeves: the sleeve's bands, and the paper's own edge inside them.

A sleeve adds bands around the paper: crimped, heat-sealed seams on the two
sides across the binding, a gap of clear plastic inside them, an opaque
binding strip on the binding side, and a clear margin on the side away from
it. ``detect_pages`` lands on different ones of their edges from side to
side. This module looks around each side of the page it found for edges
that run the side's length, classifies the bands between them, chooses the
four paper edges together and traces each within a narrow band, so that the
outline neither mixes sleeve and paper nor jumps between edges along a side.
See ``.kiro/specs/sleeve-paper-edge/design.md``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy.ndimage import median_filter

from .curvature import OUTLINE_SIDES, _best_path, _traced_corners, eroded_valid, side_frame, side_strip, subpixel_rows

BAND_KINDS = ("crimp", "strip", "clear", "paper", "outside")
SLEEVE_BANDS = ("crimp", "strip", "clear")
SLEEVE_MODES = ("auto", "off")
CROPS = ("paper", "sleeve")
OPPOSITE = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}


@dataclass
class SleeveParams:
    mode: str = "auto"  # auto | off
    crop: str = "paper"  # paper | sleeve
    out: float = 0.04  # corridor outside the outer outline, fraction of page width
    inside: float = 0.08  # corridor inside it
    paper_ref: tuple[float, float] = (0.10, 0.14)  # paper sample, fraction of page width inside the outer outline
    segments: int = 16
    max_step: float = 0.0025  # path step per segment, fraction of page width
    min_support: float = 0.6
    max_paths: int = 6
    min_peak: float = 0.12  # a peak of edge strength, against the corridor's strongest edges
    ridge_min: float = 0.5  # crimp
    ridge_lags: tuple[float, float] = (0.002, 0.015)
    crimp_width: tuple[float, float] = (0.005, 0.04)
    strip_ridge: float = 0.3
    strip_texture: float = 6.0  # L* units
    strip_width: tuple[float, float] = (0.01, 0.04)
    strip_spread: float = 0.003
    gap_width: float = 0.015
    like: float = 2.5  # Lab distance, in units of the reference band's spread, under which two bands look alike
    end_reach: float = 0.02  # end support, fraction of side length
    min_reach: float = 0.1  # share of the end stretch where a chosen edge must show
    prefer: float = 0.5  # stop-score bonus for a seam side's likelier candidate
    stray: float = 0.5  # stop-score cost per 1% of the width a seamless side's choice lies from the outer outline
    plain_reach: float = 0.01  # on a side without a seam, farthest a paper edge may lie from the outer outline
    min_angle: float = 45.0  # corners of the paper quad, degrees
    trace_band: float = 0.003  # refinement corridor, fraction of page width
    bridge: float = 0.2  # longest bridged stretch, fraction of side length
    faint: float = 0.2  # an edge fainter than this share of its side's median is not seen there
    present: float = 0.15  # a candidate edge shows where its strength is at least this share of its median
    contrast: float = 3.0  # and this many times the corridor's typical strength at that point

    def validate(self) -> None:
        if self.mode not in SLEEVE_MODES:
            raise ValueError(f"sleeve must be auto or off, got {self.mode!r}")
        if self.crop not in CROPS:
            raise ValueError(f"crop must be paper or sleeve, got {self.crop!r}")


@dataclass
class Band:
    kind: str  # one of BAND_KINDS
    width: float  # mean, fraction of page width
    spread: float  # standard deviation of the width along the side, fraction of page width
    ridge: float  # periodicity along the side, 0..1
    texture: float = 0.0  # L* standard deviation along the side, after removing slow changes
    colour: tuple[float, float, float] = (0.0, 0.0, 0.0)  # median Lab (OpenCV's 8-bit scale)
    like_outside: float = float("inf")  # Lab distance from the outermost band, in its spreads
    like_paper: float = float("inf")  # Lab distance from the paper sample, in its spreads


@dataclass
class SideFinding:
    name: str  # top | right | bottom | left, of the outer outline
    paths: list[np.ndarray]  # candidate edges, outside to inside, (n, 2) mosaic px
    bands: list[Band]  # outside to inside: beyond the outermost path, between paths, inside the innermost
    paper: int | None  # index into paths, None when not found
    sleeve_seen: bool
    offsets: list[np.ndarray] = field(default_factory=list, repr=False)  # per path, its offset per column (px, outward positive)
    present: list[np.ndarray] = field(default_factory=list, repr=False)  # per path, whether its edge shows at each column
    t: np.ndarray | None = field(default=None, repr=False)  # columns, fractions along the side
    across: list[np.ndarray] = field(default_factory=list, repr=False)  # per path, columns where it runs across another side's seam
    width: float = 0.0  # the outer outline's width, mosaic px


@dataclass
class SleeveFit:
    sleeved: bool
    reason: str | None  # few_sides | no_sleeve_band | off | None
    corners: np.ndarray  # (4, 2) mosaic px, TL TR BR BL: paper, or the outer outline
    outline: list[np.ndarray | None]  # traced paper sides, trace_outline's order; [] when not sleeved
    strip_side: str | None  # in outer-outline side names
    margins: dict[str, float]  # sleeve band width per side, fraction of page width
    sides: list[SideFinding]
    mode: str = "auto"
    crop: str = "paper"
    outer: np.ndarray | None = None  # the outer outline, (4, 2) mosaic px

    @property
    def missing(self) -> list[str]:
        """Sides of a sleeved page whose paper edge was not found."""
        return [s.name for s in self.sides if s.paper is None] if self.sleeved else []

    def report(self) -> dict:
        """The page metadata's ``sleeve`` object."""
        sides = {}
        for name in OUTLINE_SIDES:
            s = next((x for x in self.sides if x.name == name), None)
            bands = []
            if s is not None and self.sleeved:
                upto = s.paper + 1 if s.paper is not None else len(s.bands)
                for b in s.bands[:upto]:
                    if b.kind not in SLEEVE_BANDS:
                        continue
                    if bands and bands[-1]["kind"] == b.kind:  # one seam split by an edge along it
                        bands[-1]["width"] = round(bands[-1]["width"] + b.width, 4)
                    else:
                        bands.append({"kind": b.kind, "width": round(b.width, 4)})
            sides[name] = {
                "bands": bands,
                "paper_found": bool(s is not None and self.sleeved and s.paper is not None),
                "sleeve_seen": bool(s is not None and self.sleeved and s.sleeve_seen),
            }
        return {
            "mode": self.mode,
            "crop": self.crop,
            "sleeved": self.sleeved,
            "reason": self.reason,
            "strip_side": self.strip_side,
            "sides": sides,
        }


def unsleeved(quad: np.ndarray, p: SleeveParams, reason: str, sides: list[SideFinding] | None = None) -> SleeveFit:
    q = np.asarray(quad, np.float64).reshape(4, 2)
    return SleeveFit(False, reason, q.copy(), [], None, {n: 0.0 for n in OUTLINE_SIDES}, sides or [], p.mode, p.crop, q.copy())


# --- Edges -------------------------------------------------------------------


def corridor(img: np.ndarray, valid: np.ndarray, a: np.ndarray, b: np.ndarray, out_px: int, in_px: int, beyond_px: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The image straightened around the side from ``a`` to ``b``, from ``out_px`` outside it to ``in_px`` inside.

    Returns the strip (Lab float32, rows from outside to inside, a column
    per pixel along the side, running ``beyond_px`` past both ends), what of
    it was photographed, and the grid: (offsets of the rows, outward
    positive; fractions of the columns along the side). ``valid`` is uint8,
    already eroded.
    """
    length, _, _ = side_frame(a, b)
    e = beyond_px / max(length, 1e-9)
    t = np.linspace(-e, 1.0 + e, int(np.clip(round(length + 2 * beyond_px), 50, 4000)))
    offs = np.arange(out_px, -in_px - 1, -1, dtype=np.float64)
    strip, ok = side_strip(img, valid, a, b, t, offs, blur=False)
    strip = cv2.GaussianBlur(strip, (3, 3), 0)
    return strip, ok, (offs, t)


def edge_strength(strip: np.ndarray, ok: np.ndarray, smooth_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Lab change across the side between rows, smoothed along it: (strength, signed change (rows-1, cols, 3))."""
    d = np.diff(strip, axis=0)
    g = np.sqrt((d**2).sum(axis=2))
    k = max(1, smooth_px) | 1
    g = cv2.blur(g, (k, 1))
    g[~(ok[1:] & ok[:-1])] = 0
    return g, d


def _viterbi(E: np.ndarray, step: int) -> np.ndarray:
    """Row per segment maximising the summed ``E`` (segments, rows), moving at most ``step`` rows between segments."""
    ns, nr = E.shape
    score = E[0].copy()
    back = np.zeros((ns, nr), np.int64)
    shifts = np.arange(-step, step + 1)
    for s in range(1, ns):
        cand = np.full((len(shifts), nr), -np.inf)
        for i, k in enumerate(shifts):  # predecessor at row r + k
            if k >= 0:
                cand[i, : nr - k] = score[k:]
            else:
                cand[i, -k:] = score[: nr + k]
        j = np.argmax(cand, axis=0)
        back[s] = np.arange(nr) + shifts[j]
        score = cand[j, np.arange(nr)] + E[s]
    rows = np.empty(ns, np.int64)
    rows[-1] = int(np.argmax(score))
    for s in range(ns - 1, 0, -1):
        rows[s - 1] = back[s, rows[s]]
    return rows


def candidate_paths(strength: np.ndarray, p: SleeveParams, px_per_width: float, seen: np.ndarray | None = None) -> list[tuple[np.ndarray, float]]:
    """Continuous edges running along the side, outside to inside: (row per column, support).

    ``strength`` is (rows, cols) edge strength. The side is cut into
    ``p.segments`` segments, the peaks of the mean strength across the side
    found in each, and peaks linked from segment to segment by dynamic
    programming that moves at most ``p.max_step`` of the page width per
    segment, so a path follows a bowed side but cannot hop between edges
    about 1% apart. A path's support is the share of segments where it lies
    on a peak; paths with at least ``p.min_support`` are kept.
    """
    nr, nc = strength.shape
    ns = p.segments
    edges = np.linspace(0, nc, ns + 1).round().astype(int)
    prof = np.zeros((ns, nr))
    seg_seen = np.ones(ns, bool)
    for s in range(ns):
        cols = slice(edges[s], edges[s + 1])
        prof[s] = strength[:, cols].mean(axis=1)
        if seen is not None:
            seg_seen[s] = seen[cols].mean() >= 0.5
    prof[~seg_seen] = 0
    scale = float(np.percentile(prof[seg_seen], 99.5)) if seg_seen.any() else 0.0
    if scale <= 0 or nr < 5:
        return []
    E = np.minimum(prof / scale, 1.5)
    # Peaks: local maxima across the side, above a floor.
    pad = np.pad(E, ((0, 0), (2, 2)), constant_values=-1)
    peak = (E >= p.min_peak) & seg_seen[:, None]
    for k in (1, 2):
        peak &= (E >= pad[:, 2 - k : 2 - k + nr]) & (E >= pad[:, 2 + k : 2 + k + nr])
    near = cv2.dilate(peak.astype(np.uint8), np.ones((1, 5), np.uint8)) > 0  # within 2 rows of a peak
    step = max(1, int(round(p.max_step * px_per_width)))
    sep = max(2, int(round(0.003 * px_per_width)))
    work = np.where(near, E, E - 0.3)  # off a peak a path pays for it
    centres = (edges[:-1] + edges[1:]) / 2 - 0.5
    found = []
    for _ in range(3 * p.max_paths):
        rows = _viterbi(work, step)
        if float(work[np.arange(ns), rows].mean()) < 0.5 * p.min_peak - 0.3:
            break
        on = near[np.arange(ns), rows] & seg_seen
        support = float(on.sum() / max(seg_seen.sum(), 1))
        for s in range(ns):
            work[s, max(0, rows[s] - sep) : rows[s] + sep + 1] = -10.0
        if support >= p.min_support:
            found.append((rows.astype(np.float64), support, float(E[np.arange(ns), rows][on].mean())))
    # Strongest first, then outside to inside.
    found.sort(key=lambda f: -f[1] * f[2])
    kept = []
    for rows, support, _ in found:
        if all(np.abs(rows - r).mean() > sep for r, _ in kept):
            kept.append((rows, support))
        if len(kept) == p.max_paths:
            break
    kept.sort(key=lambda f: f[0].mean())
    return [(_extrapolate(np.arange(nc), centres, rows), support) for rows, support in kept]


def refine_path(strength: np.ndarray, rows: np.ndarray, band: int) -> np.ndarray:
    """A path placed per segment, followed column by column within ``band`` rows of it, to a fraction of a row.

    By dynamic programming over the edge strength, moving at most a row per
    column, so a bend near a corner, which segments only follow coarsely, is
    traced as it is.
    """
    nr, nc = strength.shape
    cols = np.arange(nc)
    r0 = np.clip(np.round(rows).astype(int), 0, nr - 1)
    rel = np.arange(-band, band + 1)
    rr = np.clip(r0[None, :] + rel[:, None], 0, nr - 1)
    E = strength[rr, cols[None, :]]
    top = float(np.percentile(E, 99))
    if top <= 0:
        return rows
    k = _best_path(np.minimum(E / top, 1.5) - 0.002 * np.abs(rel)[:, None], step_cost=0.08)
    return subpixel_rows(E, k, rel.astype(np.float64)) + r0


def _extrapolate(x: np.ndarray, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """Linear interpolation, continued straight past both ends (a bent page's edge keeps curving there)."""
    y = np.interp(x, xp, fp)
    if len(xp) >= 2:
        lo, hi = x < xp[0], x > xp[-1]
        y[lo] = fp[0] + (x[lo] - xp[0]) * (fp[1] - fp[0]) / (xp[1] - xp[0])
        y[hi] = fp[-1] + (x[hi] - xp[-1]) * (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
    return y


# --- Bands -------------------------------------------------------------------


def _moving_average(x: np.ndarray, k: int) -> np.ndarray:
    k = max(1, int(k)) | 1
    return cv2.blur(x.reshape(1, -1).astype(np.float32), (k, 1), borderType=cv2.BORDER_REFLECT).ravel()


def ridge_score(signal: np.ndarray, lags: tuple[int, int]) -> float:
    """How periodic ``signal`` is at a period between ``lags`` (px): the highest autocorrelation there.

    Only lags past the autocorrelation's first dip count, so a signal that
    is merely smooth (its neighbours alike) does not score; a ridged seam's
    swings bring the correlation back up at its period.
    """
    lo, hi = max(1, lags[0]), max(lags[0] + 1, lags[1])
    x = np.asarray(signal, np.float64)
    x = x - _moving_average(x, 2 * hi + 1)
    if len(x) < 4 * hi or float(x.std()) < 1e-6:
        return 0.0
    ac = np.array([np.corrcoef(x[:-k], x[k:])[0, 1] for k in range(1, hi + 2)])
    ac = np.nan_to_num(ac)
    dip = next((k for k in range(1, len(ac) - 1) if ac[k] < ac[k - 1] and ac[k] <= ac[k + 1]), None)  # ac[k] is lag k+1
    if dip is None:
        return 0.0
    first = max(dip + 1, lo)  # in lags
    if first > hi:
        return 0.0
    return float(max(0.0, ac[first - 1 : hi].max()))


def _band_rows(lo: np.ndarray, hi: np.ndarray, nr: int, margin: float) -> np.ndarray:
    """Mask (rows, cols) of the rows strictly inside a band, a margin away from its edges (its middle row at least)."""
    r = np.arange(nr)[:, None]
    a, b = lo + margin, hi - margin
    narrow = b < a
    mid = (lo + hi) / 2
    a = np.where(narrow, mid - 0.5, a)
    b = np.where(narrow, mid + 0.5, b)
    return (r >= a[None, :]) & (r <= b[None, :])


def _mad(x: np.ndarray, axis=0) -> np.ndarray:
    """Robust standard deviation: 1.4826 times the median absolute deviation."""
    return 1.4826 * np.median(np.abs(x - np.median(x, axis=axis)), axis=axis)


def measure_band(strip: np.ndarray, ok: np.ndarray, mask: np.ndarray, px_per_width: float, p: SleeveParams) -> dict:
    """A band's colour, texture and ridge along the side, from the pixels in ``mask`` (rows, cols).

    Spread and texture are robust, so a post or a letter crossing a band
    for a short stretch does not count.
    """
    m = mask & ok
    cols = m.any(axis=0)
    if cols.sum() < 10:
        return {"colour": np.zeros(3), "spread": 0.0, "texture": 0.0, "ridge": 0.0, "seen": False}
    cnt = np.maximum(m.sum(axis=0), 1)
    mean = (strip * m[..., None]).sum(axis=0) / cnt[:, None]  # (cols, 3)
    mean = mean[cols]
    L = mean[:, 0]
    lags = (int(round(p.ridge_lags[0] * px_per_width)), int(round(p.ridge_lags[1] * px_per_width)))
    texture = float(_mad(L - _moving_average(L, 0.05 * px_per_width)))
    colour = np.median(mean, axis=0)
    spread = float(np.linalg.norm(_mad(mean)))
    # In pieces: the side is seen in perspective, so the seam's period
    # changes along it, and a single autocorrelation would smear it out.
    pieces = np.array_split(L - _moving_average(L, 2 * lags[1] + 1), max(1, min(8, len(L) // max(8 * lags[1], 1))))
    ridge = float(np.median([ridge_score(x, lags) for x in pieces]))
    return {"colour": colour, "spread": spread, "texture": texture, "ridge": ridge, "seen": True}


def _alike(a: dict, ref: dict, p: SleeveParams) -> float:
    if not (a["seen"] and ref["seen"]):
        return float("inf")
    return float(np.linalg.norm(a["colour"] - ref["colour"]) / max(ref["spread"], 3.0))


def band_measures(strip: np.ndarray, ok: np.ndarray, rows: list[np.ndarray], search_rows: int, paper_ref: dict | None, px_per_width: float, p: SleeveParams) -> list[dict]:
    """Measurements of the bands between consecutive paths (rows per column), outside to inside."""
    nr, nc = strip.shape[:2]
    bounds = [np.zeros(nc)] + [r + 0.5 for r in rows] + [np.full(nc, float(search_rows))]
    margin = max(1.0, 0.0015 * px_per_width)
    out = []
    for k in range(len(bounds) - 1):
        lo, hi = bounds[k], bounds[k + 1]
        width = hi - lo
        mk = measure_band(strip, ok, _band_rows(lo, hi, nr, margin if 0 < k < len(bounds) - 2 else margin / 2), px_per_width, p)
        mk["width"] = float(width.mean() / px_per_width)
        mk["wspread"] = float(width.std() / px_per_width)
        out.append(mk)
    for mk in out:
        mk["like_outside"] = _alike(mk, out[0], p)
        mk["like_paper"] = _alike(mk, paper_ref, p) if paper_ref is not None else float("inf")
    return out


def _kinds(ms: list[dict], p: SleeveParams, strip_ok: bool, paper: dict | None = None) -> list[str]:
    """Band kinds from their measurements, by the rules in the design.

    The outermost band is ``outside``. Ridged bands are a crimp, also where
    an edge inside the seam splits it in two. On a side across a crimped
    one, the outermost plain, even band about as light as the paper and
    unlike the band inside it and the ``paper`` (measured inside the
    crimps), with nothing like the paper outside it, is the binding strip.
    Everything outside the
    outermost crimp or strip is beyond the sleeve; on a side with neither,
    so are the bands that touch the outermost one and look like it. Inside,
    a narrow band next to a crimp or the strip is a gap of clear plastic, as
    is a band that looks like the outside; the rest is paper.
    """
    n = len(ms)
    kinds = ["paper"] * n
    kinds[0] = "outside"
    # Runs of ridged bands (not the innermost, which runs on into the page).
    k = 1
    while k < n - 1:
        if ms[k]["ridge"] >= p.ridge_min:
            j = k
            # A seam's parts on either side of an edge along it are less
            # clearly ridged each than the whole.
            while j + 1 < n - 1 and ms[j + 1]["ridge"] >= 0.6 * p.ridge_min:
                j += 1
            width = sum(ms[i]["width"] for i in range(k, j + 1))
            if p.crimp_width[0] <= width <= p.crimp_width[1]:
                for i in range(k, j + 1):
                    kinds[i] = "crimp"
            k = j + 1
        else:
            k += 1
    paper_l = paper["colour"][0] if paper is not None else (ms[-1]["colour"][0] if ms[-1]["seen"] else 0.0)
    if strip_ok:
        for k in range(1, n - 1):
            m, inner = ms[k], ms[k + 1]
            if kinds[k] != "paper":
                break
            if (
                m["ridge"] < p.strip_ridge
                and m["texture"] < p.strip_texture
                and p.strip_width[0] <= m["width"] <= p.strip_width[1]
                and m["wspread"] < p.strip_spread
                and m["colour"][0] >= 0.75 * paper_l
                and m["like_outside"] >= p.like
                and _alike(inner, m, p) >= p.like
                and (paper is None or _alike(m, paper, p) >= p.like)
                and (paper is None or all(_alike(ms[i], paper, p) >= p.like for i in range(1, k)))
            ):
                kinds[k] = "strip"
                break
    sleeve = [k for k in range(n) if kinds[k] in ("crimp", "strip")]
    if sleeve:
        for k in range(1, sleeve[0]):
            kinds[k] = "outside"
    else:
        for k in range(1, n - 1):
            if ms[k]["like_outside"] < p.like:
                kinds[k] = "outside"
            else:
                break
    first_in = sleeve[0] if sleeve else 1
    for k in range(first_in, n - 1):
        if kinds[k] != "paper":
            continue
        if kinds[k - 1] in ("crimp", "strip") and ms[k]["width"] <= p.gap_width:
            kinds[k] = "clear"  # the gap inside a crimp or a strip
        elif ms[k]["like_outside"] < p.like and kinds[k - 1] in SLEEVE_BANDS:
            kinds[k] = "clear"
    return kinds


def classify_bands(
    strip: np.ndarray, paths, paper_ref: np.ndarray | dict | None, p: SleeveParams, px_per_width: float, ok: np.ndarray | None = None, search_rows: int | None = None, strip_ok: bool = True, paper: dict | None = None
) -> list[Band]:
    """Each band between the paths (rows per column), outside to inside, classified as one of ``BAND_KINDS``.

    ``paper_ref`` is the paper sample's measurements (``measure_band``), or
    an image of it (rows, cols, 3 Lab). A strip band is only considered
    where ``strip_ok`` (the side runs across a crimped one).
    """
    ok = np.ones(strip.shape[:2], bool) if ok is None else ok
    if isinstance(paper_ref, np.ndarray):
        paper_ref = measure_band(paper_ref, np.ones(paper_ref.shape[:2], bool), np.ones(paper_ref.shape[:2], bool), px_per_width, p)
    ms = band_measures(strip, ok, list(paths), strip.shape[0] if search_rows is None else search_rows, paper_ref, px_per_width, p)
    return _bands(ms, _kinds(ms, p, strip_ok, paper))


def _paper_inside_crimps(measures: list[list[dict]], kinds: list[list[str]]) -> dict | None:
    """The paper's colour and spread, from the first band inside a crimp (and its gap), or None without a crimp."""
    found = []
    for ms, ks in zip(measures, kinds):
        if "crimp" not in ks:
            continue
        k = len(ks) - 1 - ks[::-1].index("crimp") + 1
        while k < len(ks) and ks[k] == "clear":
            k += 1
        if k < len(ks) and ms[k]["seen"]:
            found.append(ms[k])
    if not found:
        return None
    # The lightest: a print reaching into the band would only darken it.
    best = max(found, key=lambda m: m["colour"][0])
    # Paper is even: a larger spread is a print's edge or a letter caught in the band.
    return {"colour": best["colour"], "spread": min(best["spread"], 4.0), "seen": True}


def _bands(ms: list[dict], kinds: list[str]) -> list[Band]:
    return [
        Band(k, m["width"], m["wspread"], round(m["ridge"], 3), m["texture"], tuple(float(c) for c in m["colour"]), m["like_outside"], m["like_paper"])
        for m, k in zip(ms, kinds)
    ]


# --- The paper's outline -----------------------------------------------------


def _line(pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
    return c, vt[0]


def _meet(l1, l2) -> np.ndarray | None:
    (p, d), (r, e) = l1, l2
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-6:
        return None
    s = ((r[0] - p[0]) * e[1] - (r[1] - p[1]) * e[0]) / den
    return p + s * d


def _angles_ok(q: np.ndarray, lo: float) -> bool:
    if not cv2.isContourConvex(q.astype(np.float32).reshape(-1, 1, 2)):
        return False
    for k in range(4):
        u, v = q[k - 1] - q[k], q[(k + 1) % 4] - q[k]
        c = float(u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-9))
        if not lo <= np.degrees(np.arccos(np.clip(c, -1, 1))) <= 180 - lo:
            return False
    return True


def paper_candidates(side: SideFinding, p: SleeveParams | None = None) -> list[int]:
    """Paths that can be the paper's edge.

    On a side with a seam (crimp or strip): the seam's inner edge, and the
    next edge in when the band between is narrow enough to be the gap of
    clear plastic inside the seam (it may as well be a narrow margin of
    paper before a print). On a side without one: the edges from the
    outermost in, up to the inner edge of the first band that looks like the
    paper (one more than the paper's own edge, should a clear margin over a
    white backing look like paper too). Which of them is the paper's edge is
    left to the four sides together (see ``choose_paper``). On a side without
    a seam, only edges within ``p.plain_reach`` of the width of the outer
    outline's side count: the page detection mostly lands on the paper's own
    edge there, and one far from it is more likely the sleeve's, the next
    pages' or a print's. The likelier
    one comes first: the edge inside a band classified as the clear gap.
    """
    p = p or SleeveParams()
    n = len(side.paths)
    seams = [i for i, b in enumerate(side.bands) if b.kind in ("crimp", "strip")]
    if seams:
        k0 = seams[-1]
        out = [k0] if k0 < n else []
        if k0 + 1 < n and side.bands[k0 + 1].width <= p.gap_width:
            out.append(k0 + 1)
        return out[::-1] if side.bands[k0 + 1].kind == "clear" else out
    like = [j for j in range(1, len(side.bands)) if side.bands[j].like_paper < p.like]
    last = min(like[0], n - 1) if like else n - 1
    # With nothing on the side to say where the sleeve ends, an edge far
    # from the page detection's is more likely the sleeve's or a print's.
    return [k for k in range(0, last + 1) if abs(float(np.median(side.offsets[k]))) <= p.plain_reach * _side_width(side)]


def _side_line(side: SideFinding, k: int | None, outer: np.ndarray | None, i: int):
    """A straight line (point, direction) through path ``k``'s middle 80%, or the outer outline's side ``i`` for None."""
    if k is None:
        q = np.asarray(outer, np.float64)
        a, b = q[i], q[(i + 1) % 4]
        return a, (b - a) / max(np.linalg.norm(b - a), 1e-9)
    path = side.paths[k]
    n = len(path)
    return _line(path[int(0.1 * n) : max(int(0.9 * n), int(0.1 * n) + 2)])


def _side_width(side: SideFinding) -> float:
    """The outer outline's width in mosaic px, as found with the side."""
    return float(side.width) if side.width else 1.0


def combo_score(sides: list[SideFinding], combo, p: SleeveParams, outer: np.ndarray | None = None, detail: list | None = None, prefer: list | None = None):
    """How well a choice of one path per side (None: the outer outline's side) makes the paper's outline.

    Returns ((stop score, outwardness), corners TL TR BR BL) or None when the
    choice is impossible. The stop score sums, over the chosen paths' ends,
    the share of the last ``p.end_reach`` of the side's length before the
    corner where the path's edge shows, less the share just beyond it where
    it still shows, plus ``p.prefer`` for each side whose path is
    ``prefer[side]``. ``detail``, when given, gets one entry per side.
    """
    corners = []
    for c in range(4):  # corner c joins side c-1's end and side c's start
        x = _meet(_side_line(sides[c - 1], combo[c - 1], outer, (c - 1) % 4), _side_line(sides[c], combo[c], outer, c))
        if x is None:
            return None
        corners.append(x)
    q = np.array(corners)
    if not _angles_ok(q, p.min_angle):
        return None

    def share(s, k, lo, hi) -> float:
        # Not where the path runs across another side's seam: a crimp's
        # ridges show as edges in every direction.
        m = (s.t >= lo) & (s.t <= hi)
        if s.across:
            m &= ~s.across[k]
        return float(s.present[k][m].mean()) if m.any() else 0.0

    stops, out = 0.0, 0.0
    r = p.end_reach
    for i, k in enumerate(combo):
        if k is None:
            continue
        s = sides[i]
        a, b = q[i], q[(i + 1) % 4]
        sa, sb = s.paths[k][0], s.paths[k][-1]
        d = sb - sa
        L2 = max(float(d @ d), 1e-9)
        span = s.t[-1] - s.t[0]  # the path runs over the columns' span of the outer side
        ts, te = (float(s.t[0] + (x - sa) @ d / L2 * span) for x in (a, b))
        if te - ts < 0.3:
            return None
        before = (share(s, k, ts, ts + r), share(s, k, te - r, te))
        beyond = (share(s, k, ts - r, ts - 0.2 * r), share(s, k, te + 0.2 * r, te + r))
        if detail is not None:
            detail.append({"side": s.name, "path": k, "ts": round(ts, 3), "te": round(te, 3), "before": before, "beyond": beyond})
        if min(before) < p.min_reach:
            return None  # stops short of a corner, as a print's border does
        stops += sum(before) - sum(beyond)
        if prefer is not None and k == prefer[i]:
            stops += p.prefer  # the likelier of a seam side's two candidates
        if not any(b.kind in ("crimp", "strip") for b in s.bands):
            # On a side with no seam, the page detection mostly lands on the
            # paper's own edge already: it is the strongest edge there.
            stops -= p.stray * abs(float(np.median(s.offsets[k]))) / (0.01 * _side_width(s))
        out += float(s.offsets[k].mean())
    return (round(stops, 1), out), q


def choose_paper(sides: list[SideFinding], p: SleeveParams, outer: np.ndarray | None = None) -> tuple[list[int | None], np.ndarray] | None:
    """The four paper edges, chosen together: (path index per side or None, corners TL TR BR BL).

    Each side's candidates are scored as a set. The corners must make a
    convex quad with angles within ``p.min_angle`` of square, and every
    chosen path must show over at least ``p.min_reach`` of the last
    ``p.end_reach`` of the side's length before both its ends, where the ends
    are its meetings with the adjacent chosen paths (a print's border stops
    short at the print's corners). Of the sets passing, the one whose edges
    stop where they meet wins: each shows up to its corners and not beyond
    them, as the paper's own edges do, while an edge of the sleeve or the
    backing further out leaves the paper's edges across it stopping short.
    Ties go to the set lying furthest out. When no set passes, one side may
    be left as the outer outline's (``outer``, TL TR BR BL) and is returned
    as None.
    """
    cands = [paper_candidates(s, p) for s in sides]

    # On a side with a seam, the likelier of its two candidates.
    prefer = [cs[0] if len(cs) > 1 and any(b.kind in ("crimp", "strip") for b in s.bands) else None for s, cs in zip(sides, cands)]

    def evaluate(combo):
        return combo_score(sides, combo, p, outer, prefer=prefer)

    best = None
    for allowed in (0, 1):
        options = [cs if cs else [] for cs in cands]
        combos = []
        if allowed == 0:
            if all(options):
                combos = itertools.product(*options)
        elif outer is not None:
            combos = itertools.chain.from_iterable(itertools.product(*[([None] if i == j else options[i]) for i in range(4)]) for j in range(4))
        for combo in combos:
            r = evaluate(combo)
            if r is not None and (best is None or r[0] > best[0]):
                best = (r[0], list(combo), r[1])
        if best is not None:
            return best[1], best[2]
    return None


def trace_path(strip: np.ndarray, path: np.ndarray, sign: int | np.ndarray, p: SleeveParams, px_per_width: float, ok: np.ndarray | None = None) -> np.ndarray | None:
    """The edge near ``path`` (row per column, between rows of ``strip``), to a fraction of a row, or None.

    Only rows within ``p.trace_band`` of the page width of the path are
    searched, and only for a change of the path's own sign (``sign`` is a
    Lab direction, or +1/-1 for its mean direction), so a neighbouring edge
    of the opposite sign cannot pull the trace. Columns where the edge is
    fainter than ``p.faint`` of its median are bridged by a quadratic fitted
    to the rest; a faint stretch longer than ``p.bridge`` of the side gives
    None. Returns offsets in rows (between-row positions, as ``path``).
    """
    d = np.diff(strip, axis=0)
    nr, nc = d.shape[:2]
    if ok is not None:
        d[~(ok[1:] & ok[:-1])] = 0
    cols = np.arange(nc)
    r0 = np.clip(np.round(path).astype(int), 0, nr - 1)
    if np.ndim(sign) == 0:
        u = d[r0, cols].mean(axis=0)
        u = u / max(float(np.linalg.norm(u)), 1e-9) * (1 if sign >= 0 else -1)
    else:
        u = np.asarray(sign, np.float64) / max(float(np.linalg.norm(sign)), 1e-9)
    B = max(2, int(round(p.trace_band * px_per_width)))
    rel = np.arange(-B - 1, B + 2)
    rr = np.clip(r0[None, :] + rel[:, None], 0, nr - 1)
    g = np.maximum((d[rr, cols[None, :]] * u).sum(axis=2), 0)
    g = cv2.blur(g.astype(np.float32), (5, 1))
    scale = float(np.percentile(g, 99)) if g.size else 0.0
    if scale <= 0:
        return None
    E = np.minimum(g / scale, 1.5)
    rows = _best_path(E[1:-1], step_cost=0.08) + 1
    off = subpixel_rows(g, rows, rel.astype(np.float64)) + r0
    on = g[rows, cols]
    med = float(np.median(on))
    weak = on < p.faint * med if med > 0 else np.ones(nc, bool)
    if weak.all():
        return None
    # Faint runs, and the longest.
    longest, run = 0, 0
    for w in weak:
        run = run + 1 if w else 0
        longest = max(longest, run)
    if longest > p.bridge * nc:
        return None
    if weak.any():
        x = cols / max(nc - 1, 1)
        c = np.polyfit(x[~weak], off[~weak], 2 if (~weak).sum() > 3 else 1)
        off = np.where(weak, np.polyval(c, x), off)
    return off


# --- Putting it together -----------------------------------------------------


def _mark_seams(sides: list[SideFinding], shape: tuple[int, int], W: float) -> None:
    """For every path, the columns where it runs across another side's crimp or strip."""
    masks = []
    pad = max(2, int(round(0.003 * W)))
    for s in sides:
        m = np.zeros(shape, np.uint8)
        for k, b in enumerate(s.bands):
            if b.kind in ("crimp", "strip") and 1 <= k < len(s.paths):
                poly = np.concatenate([s.paths[k - 1], s.paths[k][::-1]])
                cv2.fillPoly(m, [np.round(poly).astype(np.int32).reshape(-1, 1, 2)], 1)
        masks.append(cv2.dilate(m, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) if m.any() else m)
    for i, s in enumerate(sides):
        other = np.zeros(shape, np.uint8)
        for j, m in enumerate(masks):
            if j != i:
                other |= m
        s.across = []
        for path in s.paths:
            xy = np.round(path).astype(int)
            inside = (xy[:, 0] >= 0) & (xy[:, 1] >= 0) & (xy[:, 0] < shape[1]) & (xy[:, 1] < shape[0])
            hit = np.zeros(len(path), bool)
            hit[inside] = other[xy[inside, 1], xy[inside, 0]] > 0
            s.across.append(hit)


def find_sleeve(mosaic: np.ndarray, valid: np.ndarray, quad: np.ndarray, p: SleeveParams) -> SleeveFit:
    """The sleeve around the page ``quad`` (TL TR BR BL, mosaic px) and the paper inside it.

    Not sleeved (``reason`` says why), the outer outline is returned as it
    is and no traced sides.
    """
    p.validate()
    q = np.asarray(quad, np.float64).reshape(4, 2)
    if p.mode == "off":
        return unsleeved(q, p, "off")
    W = float((np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2)
    inner = eroded_valid(valid)
    out_px = max(4, int(round(p.out * W)))
    in_px = max(4, int(round(p.inside * W)))
    ref_lo, ref_hi = (int(round(f * W)) for f in p.paper_ref)
    smooth = max(3, int(round(0.01 * W)))
    raw = []
    for k, name in enumerate(OUTLINE_SIDES):
        a, b = q[k], q[(k + 1) % 4]
        # Past the side's ends too, so that an edge can be seen to stop at a
        # corner lying outside the outer outline.
        strip, ok, (offs, t) = corridor(mosaic, inner, a, b, out_px, ref_hi, out_px + 0.01 * W)
        search = out_px + in_px  # rows of the corridor searched for edges
        g, dlab = edge_strength(strip[: search + 1], ok[: search + 1], smooth)
        seen = ok[: search + 1].mean(axis=0) > 0.5
        paths = candidate_paths(g, p, W, seen)
        rows = [r for r, _ in paths]
        ref_rows = slice(out_px + ref_lo, out_px + ref_hi + 1)
        paper_ref = measure_band(strip[ref_rows], ok[ref_rows], np.ones_like(ok[ref_rows]), W, p)
        ms = band_measures(strip, ok, rows, search, paper_ref, W, p)
        raw.append((name, a, b, strip, ok, offs, t, (g, dlab, smooth), rows, ms))
    # Crimps first: a binding strip lies on a side across the crimped ones,
    # and the paper just inside the crimps is what the strip must differ from.
    kinds0 = [_kinds(r[9], p, False) for r in raw]
    crimped = {r[0] for r, ks in zip(raw, kinds0) if "crimp" in ks}
    paper = _paper_inside_crimps([r[9] for r in raw], kinds0)
    kinds = []
    strip_sides = []
    for r, ks in zip(raw, kinds0):
        name = r[0]
        across = {"top", "bottom"} if name in ("left", "right") else {"left", "right"}
        if crimped & across:
            ks2 = _kinds(r[9], p, True, paper)
            if "strip" in ks2:
                strip_sides.append((r[9][ks2.index("strip")]["wspread"], name))
                ks = ks2
        kinds.append(ks)
    strip_side = min(strip_sides)[1] if strip_sides else None
    for i, r in enumerate(raw):  # only one side holds the strip
        if r[0] != strip_side and "strip" in kinds[i]:
            kinds[i] = _kinds(r[9], p, False)
    if paper is not None:  # the paper inside the crimps is a surer sample than a band inside the outline
        for r in raw:
            for m in r[9]:
                m["like_paper"] = _alike(m, paper, p)
    sides = []
    refined = []  # per side, its paths followed column by column
    for (name, a, b, strip, ok, offs, t, (g, dlab, smooth), rows, ms), ks in zip(raw, kinds):
        _, _, n = side_frame(a, b)
        mid = (offs[1:] + offs[:-1]) / 2
        # The corridor's typical strength over the whole side: per column it
        # would rise near the corners, where the next side's bands cross.
        seen_g = g > 0
        noise_signed = float(np.median(np.sqrt((dlab[seen_g] ** 2).sum(axis=1)))) if seen_g.any() else 0.0
        paths, offsets, present = [], [], []
        refined.append([])
        for r in rows:
            r = refine_path(g, r, max(2, int(round(2 * p.max_step * W))))
            refined[-1].append(r)
            o = np.interp(r, np.arange(len(mid)), mid)
            offsets.append(o)
            paths.append(a + t[:, None] * (b - a) + o[:, None] * n)
            ri = np.clip(np.round(r).astype(int), 0, g.shape[0] - 1)
            reach = max(2, int(round(p.max_step * W)))  # the path is only placed per segment
            # Only a change of the path's own sign counts (bright paper
            # against a darker gap, say), so that where the path's edge
            # stops, a nearby edge of the other sign, such as a crimp's,
            # does not stand in for it.
            cols = np.arange(len(t))
            u = dlab[ri, cols][g[ri, cols] >= np.median(g[ri, cols])].mean(axis=0)
            u = u / max(float(np.linalg.norm(u)), 1e-9)
            # Lightly smoothed along the side, so that an edge is not seen
            # to run on past the point where it stops.
            signed = cv2.blur(np.maximum((dlab @ u).astype(np.float32), 0), (3, 1))
            signed[g == 0] = 0
            # Followed column by column within the reach, so that its ends,
            # where the segment-by-segment placement is coarsest, are judged
            # where the edge actually is.
            rel = np.arange(-reach, reach + 1)
            band = signed[np.clip(ri[None, :] + rel[:, None], 0, g.shape[0] - 1), cols[None, :]]
            top = float(np.percentile(band, 99))
            on = _best_path(np.minimum(band / top, 1.5), step_cost=0.08) if top > 0 else np.full(len(cols), reach)
            along = band[on, cols]
            # The edge shows where it stands out from the corridor's flat
            # stretches and is not much fainter than along the rest of its
            # length.
            seen_cols = along > 0
            med = float(np.median(along[seen_cols])) if seen_cols.any() else 0.0
            k = max(3, int(round(0.005 * W))) | 1
            show = (along >= p.contrast * noise_signed + 0.5) & (along >= p.present * med)
            present.append(median_filter(show.astype(np.uint8), size=k, mode="nearest") > 0 if med > 0 else np.zeros(len(t), bool))
        sides.append(SideFinding(name, paths, _bands(ms, ks), None, False, offsets, present, t, width=W))
    _mark_seams(sides, mosaic.shape[:2], W)
    has_sleeve = bool(crimped) or strip_side is not None
    if not has_sleeve:
        return unsleeved(q, p, "no_sleeve_band", sides)
    chosen = choose_paper(sides, p, q)
    if chosen is None:
        return unsleeved(q, p, "few_sides", sides)
    picks, corners = chosen
    # Trace each chosen edge within a narrow band, between the corners.
    outline: list[np.ndarray | None] = []
    for i, ((name, a, b, strip, ok, offs, t, g, rows, ms), k) in enumerate(zip(raw, picks)):
        s = sides[i]
        if k is None:
            outline.append(None)
            continue
        off_rows = trace_path(strip[: out_px + in_px + 1], refined[i][k], 1, p, W, ok[: out_px + in_px + 1])
        if off_rows is None:
            s.paper = None
            outline.append(None)
            continue
        mid = (offs[1:] + offs[:-1]) / 2
        o = np.interp(off_rows, np.arange(len(mid)), mid)
        _, _, n = side_frame(a, b)
        pts = a + t[:, None] * (b - a) + o[:, None] * n
        d = b - a
        L2 = float(d @ d)
        ts = float((corners[i] - a) @ d / L2)
        te = float((corners[(i + 1) % 4] - a) @ d / L2)
        keep = (t >= ts + 0.01) & (t <= te - 0.01)
        if keep.sum() < 10:
            s.paper = None
            outline.append(None)
            continue
        s.paper = k
        outline.append(pts[keep])
    found = sum(o is not None for o in outline)
    if found < 3:
        return unsleeved(q, p, "few_sides", sides)
    # Corners where the traced edges' end tangents meet; a missing side is the outer outline's.
    filled = [o if o is not None else np.array([q[i], q[(i + 1) % 4]]) for i, o in enumerate(outline)]
    tc = _traced_corners([o if len(o) >= 5 else np.linspace(o[0], o[-1], 10) for o in filled])
    corners = np.array([c if c is not None else corners[i] for i, c in enumerate(tc)])
    margins = {}
    for s in sides:
        if s.paper is None:
            continue
        # Outside the paper's edge and inside the outermost band, whatever is
        # not the outside is the sleeve's clear plastic (and what lies under it).
        for j in range(1, s.paper + 1):
            if s.bands[j].kind == "paper":
                s.bands[j].kind = "clear"
        if s.paper >= 1 and s.bands[1].kind in SLEEVE_BANDS:
            s.sleeve_seen = True
    for s in sides:
        if s.sleeve_seen:
            margins[s.name] = float(sum(b.width for b in s.bands[1 : s.paper + 1] if b.kind in SLEEVE_BANDS))
    for s in sides:
        if s.name in margins:
            continue
        o = next(x for x in sides if x.name == OPPOSITE[s.name])
        same = o.sleeve_seen and o.name in crimped and o.paper is not None and any(b.kind == "crimp" for b in o.bands[: o.paper + 1])
        margins[s.name] = margins.get(o.name, 0.0) if same else 0.0
    return SleeveFit(True, None, corners, outline, strip_side, margins, sides, p.mode, p.crop, q)


def debug_image(mosaic: np.ndarray, fit: SleeveFit) -> np.ndarray:
    """The mosaic with each side's candidate edges in grey, bands tinted by kind, the paper outline in green and the outer outline in red."""
    vis = mosaic.copy()
    t = max(1, round(max(vis.shape[:2]) / 1000))
    tint = {"crimp": (0, 140, 255), "strip": (255, 80, 0), "clear": (255, 255, 0), "outside": (40, 40, 40)}
    overlay = vis.copy()
    for s in fit.sides:
        for k in range(len(s.paths)):
            kind = s.bands[k + 1].kind if k + 1 < len(s.bands) else "paper"
            if kind in tint and k + 1 < len(s.paths):
                poly = np.concatenate([s.paths[k], s.paths[k + 1][::-1]])
                cv2.fillPoly(overlay, [np.round(poly).astype(np.int32).reshape(-1, 1, 2)], tint[kind])
    vis = cv2.addWeighted(overlay, 0.45, vis, 0.55, 0)
    for s in fit.sides:
        for path in s.paths:
            cv2.polylines(vis, [np.round(path).astype(np.int32).reshape(-1, 1, 2)], False, (160, 160, 160), t)
    if fit.outer is not None:
        cv2.polylines(vis, [np.round(fit.outer).astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), t)
    if fit.sleeved:
        for o in fit.outline:
            if o is not None:
                cv2.polylines(vis, [np.round(o).astype(np.int32).reshape(-1, 1, 2)], False, (0, 200, 0), 2 * t)
        cv2.polylines(vis, [np.round(fit.corners).astype(np.int32).reshape(-1, 1, 2)], True, (0, 255, 0), t)
    return vis
