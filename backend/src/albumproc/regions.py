"""Regions of residual glare and shadow, of uncovered page and of single coverage, and the page's warnings.

All these kinds of region are found on the detector's low-resolution grid and
reported in page fractions and in composed-page pixels, with a location word
from a 3 x 3 grid, so that the capture app can say where to aim the next
shot. Each region also carries its box in every shot's own pixels
(``shot_boxes``), so the app can outline it on a photo it holds.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import cv2
import numpy as np

LOCATIONS = [["top-left", "top", "top-right"], ["left", "centre", "right"], ["bottom-left", "bottom", "bottom-right"]]
CORNERS = {"top-left", "top-right", "bottom-left", "bottom-right"}


@dataclass
class Region:
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page's width and height
    bbox_px: tuple[int, int, int, int]  # the same in composed-page pixels
    area: float  # fraction of the page
    location: str  # 3 x 3 grid word from the region's centroid
    shots: list[int] | None = None  # input indexes covering it (glare and shadow)
    cause: str | None = None  # single_shot | all_shots_glared (glare), single_shot | all_shots_shadowed (shadow)
    severity: float | None = None  # mean residual glare or shadow (glare and shadow)
    edges: list[str] | None = None  # page edges it touches (uncovered and single coverage)
    # The region's box in each used shot's full-resolution pixels, by input
    # index, for the shots its box falls into at all (see ``shot_boxes``).
    shot_boxes: dict[int, tuple[int, int, int, int]] | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class QualityParams:
    residual_threshold: float = 0.5  # residual glare at or above this counts as glare
    min_region_area: float = 0.0005  # smaller regions are dropped (fraction of the page)
    # Single-coverage regions smaller than this are dropped. Much larger than
    # min_region_area: the seam between two overlapping shots always leaves a
    # thin band only one of them covers, and that is not worth a warning.
    min_single_area: float = 0.01


def _location(cx: float, cy: float) -> str:
    return LOCATIONS[min(2, int(cy * 3))][min(2, int(cx * 3))]


def find_regions(mask: np.ndarray, page_size: tuple[int, int], min_area: float) -> list[tuple[Region, np.ndarray]]:
    """Connected areas of ``mask`` (a grid over the page), largest first.

    ``page_size`` is the composed page's (w, h) in pixels. Each region comes
    with its own pixel mask on the grid.
    """
    gh, gw = mask.shape
    W, H = page_size
    closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)
    found = []
    for k in range(1, n):
        x, y, w, h, count = (int(v) for v in stats[k])
        area = count / float(gh * gw)
        if area < min_area:
            continue
        cx, cy = centroids[k]
        edges = [e for e, touch in (("top", y <= 1), ("right", x + w >= gw - 1), ("bottom", y + h >= gh - 1), ("left", x <= 1)) if touch]
        region = Region(
            bbox=(round(x / gw, 4), round(y / gh, 4), round((x + w) / gw, 4), round((y + h) / gh, 4)),
            bbox_px=(int(round(x * W / gw)), int(round(y * H / gh)), int(round((x + w) * W / gw)), int(round((y + h) * H / gh))),
            area=round(area, 4),
            location=_location((cx + 0.5) / gw, (cy + 0.5) / gh),
            edges=edges,
        )
        found.append((count, y, x, region, labels == k))
    found.sort(key=lambda t: (-t[0], t[1], t[2]))
    return [(r, m) for _, _, _, r, m in found]


def cause_regions(
    residual: np.ndarray,
    coverage_count: np.ndarray,
    used: list[int],
    shot_masks: list[np.ndarray],
    page_size: tuple[int, int],
    q: QualityParams,
    causes: tuple[str, str] = ("single_shot", "all_shots_glared"),
) -> list[Region]:
    """Regions of residual glare or shadow, with the shots covering each and why it stayed.

    A region seen by one shot only over more than half of its area gets the
    first cause (another shot fixes it); otherwise every covering shot had it
    there, and it gets the second.
    """
    out = []
    for region, m in find_regions(residual >= q.residual_threshold, page_size, q.min_region_area):
        single = float((coverage_count[m] == 1).sum()) > 0.5 * float(m.sum())
        region.shots = [i for i, sm in zip(used, shot_masks) if (sm & m).any()]
        region.cause = causes[0] if single else causes[1]
        region.severity = round(float(residual[m].mean()), 4)
        region.edges = None
        out.append(region)
    return out


def glare_regions(
    residual: np.ndarray,
    coverage_count: np.ndarray,
    used: list[int],
    shot_masks: list[np.ndarray],
    page_size: tuple[int, int],
    q: QualityParams,
) -> list[Region]:
    """Residual glare regions: ``single_shot`` or ``all_shots_glared`` (see ``cause_regions``)."""
    return cause_regions(residual, coverage_count, used, shot_masks, page_size, q, ("single_shot", "all_shots_glared"))


def shadow_regions(
    residual: np.ndarray,
    coverage_count: np.ndarray,
    used: list[int],
    shot_masks: list[np.ndarray],
    page_size: tuple[int, int],
    q: QualityParams,
) -> list[Region]:
    """Residual shadow regions: ``single_shot`` or ``all_shots_shadowed`` (see ``cause_regions``)."""
    return cause_regions(residual, coverage_count, used, shot_masks, page_size, q, ("single_shot", "all_shots_shadowed"))


def uncovered_map(coverage: np.ndarray, grid_shape: tuple[int, int]) -> np.ndarray:
    """Full-resolution coverage shrunk to the grid; True where mostly uncovered."""
    gh, gw = grid_shape
    return cv2.resize(coverage.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA) < 0.5


def uncovered_regions(coverage: np.ndarray, page_size: tuple[int, int], q: QualityParams, grid_shape: tuple[int, int]) -> list[Region]:
    """Regions of the page no shot covers, with the page edges each touches."""
    return [r for r, _ in find_regions(uncovered_map(coverage, grid_shape), page_size, q.min_region_area)]


def single_coverage_regions(
    coverage_count: np.ndarray,
    used: list[int],
    shot_masks: list[np.ndarray],
    page_size: tuple[int, int],
    q: QualityParams,
) -> list[Region]:
    """Regions of the page exactly one shot covers, each with that shot.

    Glare there cannot be removed by comparison, only detected. An opening
    of 3 grid px first removes the band along the seam between overlapping
    shots, which is single-covered but harmless.
    """
    single = cv2.morphologyEx((coverage_count == 1).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    out = []
    for region, m in find_regions(single, page_size, q.min_single_area):
        overlap = [float((sm & m).sum()) for sm in shot_masks]
        region.shots = [used[int(np.argmax(overlap))]] if any(overlap) else []
        out.append(region)
    return out


def shot_outline(mask: np.ndarray, max_points: int = 64) -> list[list[float]]:
    """Where a shot lies on the page: the outline of its coverage mask on the grid, as page fractions.

    The largest connected area's contour, simplified to about half a
    percent of its length and thinned to at most ``max_points`` points; an
    empty mask gives no points. Works the same for a flat page, where the
    outline is the shot's frame through its homography, and for a bent
    one, where it is whatever the flattening map covered.
    """
    gh, gw = mask.shape
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    c = max(contours, key=cv2.contourArea)
    c = cv2.approxPolyDP(c, 0.005 * cv2.arcLength(c, True), True).reshape(-1, 2)
    if len(c) > max_points:
        c = c[np.linspace(0, len(c) - 1, max_points).round().astype(int)]
    return [[round(float(x) / max(gw - 1, 1), 4), round(float(y) / max(gh - 1, 1), 4)] for x, y in c]


ShotMapper = Callable[[np.ndarray], np.ndarray]  # page-frame (x, y) points -> the shot's own pixels


def shot_box(bbox_px: tuple[int, int, int, int], to_shot: ShotMapper, shot_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """A page-frame box in one shot's pixels, clipped to the shot; None where the box misses the shot.

    Points along the box's outline are mapped (a perspective map bends the
    box's edges, so corners alone can cut it short) and their extent taken.
    """
    x0, y0, x1, y1 = bbox_px
    t = np.linspace(0, 1, 9, dtype=np.float32)
    outline = np.concatenate(
        [np.stack([x0 + t * (x1 - x0), np.full_like(t, y0)], 1), np.stack([x0 + t * (x1 - x0), np.full_like(t, y1)], 1),
         np.stack([np.full_like(t, x0), y0 + t * (y1 - y0)], 1), np.stack([np.full_like(t, x1), y0 + t * (y1 - y0)], 1)]
    )
    pts = np.asarray(to_shot(outline), np.float64).reshape(-1, 2)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if len(pts) == 0:
        return None
    w, h = shot_size
    bx0, by0 = np.floor(pts.min(axis=0))
    bx1, by1 = np.ceil(pts.max(axis=0))
    bx0, bx1 = int(max(bx0, 0)), int(min(bx1, w - 1))
    by0, by1 = int(max(by0, 0)), int(min(by1, h - 1))
    if bx1 <= bx0 or by1 <= by0:
        return None
    return bx0, by0, bx1, by1


def attach_shot_boxes(regions: list[Region], used: list[int], mappers: list[ShotMapper], shot_sizes: list[tuple[int, int]]) -> list[Region]:
    """Fill each region's ``shot_boxes`` for every used shot its box falls into."""
    for r in regions:
        boxes = {}
        for i, to_shot, size in zip(used, mappers, shot_sizes):
            b = shot_box(r.bbox_px, to_shot, size)
            if b is not None:
                boxes[i] = b
        r.shot_boxes = boxes
    return regions


def _pct(f: float) -> str:
    return f"{f * 100:.1f}%"


def _place(r: dict) -> str:
    loc = r["location"]
    if loc in CORNERS:
        return f"{loc} corner"
    if loc == "centre":
        return "centre"
    return f"{loc} edge" if r.get("edges") else loc


def _carried(r: dict) -> dict:
    """What a warning carries of a region: enough to draw and place it, without the page-frame detail."""
    return {k: r[k] for k in ("bbox", "bbox_px", "location", "shots", "cause", "edges", "shot_boxes") if k in r}


def shadow_warning(shadow: dict) -> dict:
    """The ``shadow`` warning, from the page's shadow summary (which has at least one region).

    The advice is to add a shot without the shadow where only one photo
    covers it, and to move so that nothing is between the light and the
    page where every photo has it.
    """
    regions = shadow["regions"]
    n = len(regions)
    single_n = sum(r["cause"] == "single_shot" for r in regions)
    largest = regions[0]
    if n == 1:
        msg = f"A shadow remains on {_pct(shadow['fraction'])} of the page ({largest['location']})."
    else:
        msg = f"Shadows remain on {_pct(shadow['fraction'])} of the page in {n} areas (largest {largest['location']})."
    add = " add a shot of that area without the shadow." if n == 1 else " add shots of those areas without the shadow."
    move = " hold the phone so that you and it are not between the light and the page."
    if single_n == n:
        msg += (" Only one photo covers it;" if n == 1 else " Only one photo covers each;") + add
    elif single_n == 0:
        msg += (" Every photo has it;" if n == 1 else " Every photo has them;") + move
    else:
        msg += f" {single_n} of them {'is' if single_n == 1 else 'are'} in one photo only; add shots of those areas without the shadow. Every photo has the rest;" + move
    return {
        "code": "shadow",
        "fraction": shadow["fraction"],
        "single_shot": single_n,
        "all_shots_shadowed": n - single_n,
        "location": largest["location"],
        "regions": [_carried(r) for r in regions],
        "message": msg,
    }


def page_warnings(glare: dict, uncovered: dict, dropped: list[int], single: dict | None = None, shadow: dict | None = None) -> list[dict]:
    """Warnings for the page metadata, from its glare, shadow, uncovered and single-coverage summaries.

    Every warning about an area carries its ``regions``, so a reader of the
    warnings alone (the app, through the server's status) can draw them. The
    codes ``incomplete_coverage`` and ``photos_dropped`` mean the same as in
    the album report, which passes page warnings through.
    """
    out = []
    regions = glare["regions"]
    if regions:
        n = len(regions)
        single_n = sum(r["cause"] == "single_shot" for r in regions)
        where = regions[0]["location"] if n == 1 else f"largest {regions[0]['location']}"
        msg = f"Glare remains on {_pct(glare['fraction'])} of the page in {n} area{'s' if n > 1 else ''} ({where})."
        if single_n == n:
            msg += (
                " It was seen by only one photo; add a shot of that area from a different angle."
                if n == 1
                else " Each was seen by only one photo; add shots of those areas from a different angle."
            )
        elif single_n:
            msg += (
                f" {single_n} of them {'was' if single_n == 1 else 'were'} seen by only one photo; add shots of those areas from a different angle."
                " Every photo had glare on the rest; change the light or the camera angle for those."
            )
        else:
            msg += " Every photo had glare there; change the light or the camera angle for all the shots."
        out.append(
            {
                "code": "glare",
                "fraction": glare["fraction"],
                "single_shot": single_n,
                "all_shots_glared": n - single_n,
                "location": regions[0]["location"],
                "regions": [_carried(r) for r in regions],
                "message": msg,
            }
        )
    regions = shadow["regions"] if shadow else []
    if regions:
        out.append(shadow_warning(shadow))
    regions = uncovered["regions"]
    if regions:
        places = list(dict.fromkeys(_place(r) for r in regions))
        out.append(
            {
                "code": "incomplete_coverage",
                "fraction": uncovered["fraction"],
                "locations": [r["location"] for r in regions],
                "regions": [_carried(r) for r in regions],
                "message": f"{_pct(uncovered['fraction'])} of the page is not in any photo ({', '.join(places)}).",
            }
        )
    regions = single["regions"] if single else []
    if regions:
        n = len(regions)
        where = _place(regions[0]) if n == 1 else f"{_place(regions[0])} and {n - 1} other area{'s' if n > 2 else ''}"
        out.append(
            {
                "code": "single_coverage",
                "fraction": single["fraction"],
                "locations": [r["location"] for r in regions],
                "regions": [_carried(r) for r in regions],
                "message": (
                    f"Only one photo covers the {where} ({_pct(single['fraction'])} of the page)."
                    f" Take another shot of {'that part' if n == 1 else 'those parts'} from a different angle so that glare there can be removed."
                ),
            }
        )
    if dropped:
        which = ", ".join(str(i) for i in dropped)
        out.append(
            {
                "code": "photos_dropped",
                "photos": list(dropped),
                "message": f"Photo{'s' if len(dropped) > 1 else ''} {which} matched no other photo and {'were' if len(dropped) > 1 else 'was'} not used.",
            }
        )
    return out
