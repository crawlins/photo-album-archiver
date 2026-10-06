"""Regions of residual glare and of uncovered page, and the page's warnings.

Both kinds of region are found on the detector's low-resolution grid and
reported in page fractions and in composed-page pixels, with a location word
from a 3 x 3 grid, so that the capture app can say where to aim the next shot.
"""

from __future__ import annotations

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
    shots: list[int] | None = None  # input indexes covering it (glare only)
    cause: str | None = None  # single_shot | all_shots_glared (glare only)
    severity: float | None = None  # mean residual glare (glare only)
    edges: list[str] | None = None  # page edges it touches (uncovered only)

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class QualityParams:
    residual_threshold: float = 0.5  # residual glare at or above this counts as glare
    min_region_area: float = 0.0005  # smaller regions are dropped (fraction of the page)


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


def glare_regions(
    residual: np.ndarray,
    coverage_count: np.ndarray,
    used: list[int],
    shot_masks: list[np.ndarray],
    page_size: tuple[int, int],
    q: QualityParams,
) -> list[Region]:
    """Residual glare regions, with the shots covering each and why it stayed.

    A region seen by one shot only over more than half of its area is
    ``single_shot`` (another shot from a different angle fixes it); otherwise
    every covering shot had glare there (``all_shots_glared``).
    """
    out = []
    for region, m in find_regions(residual >= q.residual_threshold, page_size, q.min_region_area):
        single = float((coverage_count[m] == 1).sum()) > 0.5 * float(m.sum())
        region.shots = [i for i, sm in zip(used, shot_masks) if (sm & m).any()]
        region.cause = "single_shot" if single else "all_shots_glared"
        region.severity = round(float(residual[m].mean()), 4)
        region.edges = None
        out.append(region)
    return out


def uncovered_map(coverage: np.ndarray, grid_shape: tuple[int, int]) -> np.ndarray:
    """Full-resolution coverage shrunk to the grid; True where mostly uncovered."""
    gh, gw = grid_shape
    return cv2.resize(coverage.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA) < 0.5


def uncovered_regions(coverage: np.ndarray, page_size: tuple[int, int], q: QualityParams, grid_shape: tuple[int, int]) -> list[Region]:
    """Regions of the page no shot covers, with the page edges each touches."""
    return [r for r, _ in find_regions(uncovered_map(coverage, grid_shape), page_size, q.min_region_area)]


def _pct(f: float) -> str:
    return f"{f * 100:.1f}%"


def _place(r: dict) -> str:
    loc = r["location"]
    if loc in CORNERS:
        return f"{loc} corner"
    if loc == "centre":
        return "centre"
    return f"{loc} edge" if r.get("edges") else loc


def page_warnings(glare: dict, uncovered: dict, dropped: list[int]) -> list[dict]:
    """Warnings for the page metadata, from its glare and uncovered summaries.

    The codes ``incomplete_coverage`` and ``photos_dropped`` mean the same as
    in the album report, so the album step can pass these through.
    """
    out = []
    regions = glare["regions"]
    if regions:
        n = len(regions)
        single = sum(r["cause"] == "single_shot" for r in regions)
        where = regions[0]["location"] if n == 1 else f"largest {regions[0]['location']}"
        msg = f"Glare remains on {_pct(glare['fraction'])} of the page in {n} area{'s' if n > 1 else ''} ({where})."
        if single == n:
            msg += (
                " It was seen by only one photo; add a shot of that area from a different angle."
                if n == 1
                else " Each was seen by only one photo; add shots of those areas from a different angle."
            )
        elif single:
            msg += (
                f" {single} of them {'was' if single == 1 else 'were'} seen by only one photo; add shots of those areas from a different angle."
                " Every photo had glare on the rest; change the light or the camera angle for those."
            )
        else:
            msg += " Every photo had glare there; change the light or the camera angle for all the shots."
        out.append({"code": "glare", "fraction": glare["fraction"], "regions": n, "single_shot": single, "location": regions[0]["location"], "message": msg})
    regions = uncovered["regions"]
    if regions:
        places = list(dict.fromkeys(_place(r) for r in regions))
        out.append(
            {
                "code": "incomplete_coverage",
                "fraction": uncovered["fraction"],
                "regions": len(regions),
                "locations": [r["location"] for r in regions],
                "message": f"{_pct(uncovered['fraction'])} of the page is not in any photo ({', '.join(places)}).",
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
