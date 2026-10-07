"""How straight are long lines in a composed page?

Print borders, mats and title baselines are straight on the real page, so
in a well-flattened page they come out straight too. This finds long line
segments with OpenCV's line segment detector, chains the pieces of each line
(a line bent near the binding is found as several straight pieces), and
measures how far each chain strays from one straight line. Run on the same page before and after curvature correction, it shows
whether the bend is gone, without needing the photos in the repository.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

from .curvature import chain_segments


@dataclass
class Line:
    length: float  # px, end to end
    deviation: float  # largest distance from the chain's best straight line, px
    deviation_frac: float  # the same, fraction of the page width
    pieces: int  # segments the line was found in; a bent line breaks into several
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass
class Straightness:
    lines: list[Line]
    worst: float  # largest deviation over all lines, fraction of the page width
    median: float  # median deviation of the lines found in pieces, fraction of the page width
    worst_px: float
    median_px: float

    def to_dict(self) -> dict:
        return asdict(self)


def measure(img: np.ndarray, min_len: float = 0.1) -> Straightness:
    """Long lines in ``img`` (at least ``min_len`` of its long side) and how straight each is."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    f = min(1.0, 2000 / max(h, w))  # LSD on a bounded size; deviations are scaled back
    small = cv2.resize(gray, (round(w * f), round(h * f)), interpolation=cv2.INTER_AREA) if f < 1 else gray
    segs = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(small)[0]
    lines: list[Line] = []
    if segs is not None:
        size = max(small.shape)
        keep = []
        for x1, y1, x2, y2 in segs.reshape(-1, 4).astype(np.float64):
            d = np.array([x2 - x1, y2 - y1])
            length = float(np.hypot(*d))
            if length >= 0.015 * size:
                keep.append((np.array([x1, y1]), np.array([x2, y2]), d / length, length))
        # Loose enough to keep a bent line's pieces together, so its bend is measured.
        for members in chain_segments(keep, angle_deg=6, gap=0.02 * size, offset=0.006 * size):
            pts = np.concatenate([keep[i][0] + np.linspace(0, 1, max(2, int(keep[i][3] / 2)))[:, None] * (keep[i][1] - keep[i][0]) for i in members])
            c = pts.mean(axis=0)
            _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
            along = (pts - c) @ vt[0]
            span = float(np.ptp(along))
            if span < min_len * size:
                continue
            dev = float(np.abs((pts - c) @ vt[1]).max()) / f
            a, b = c + along.min() * vt[0], c + along.max() * vt[0]
            lines.append(Line(round(span / f, 1), round(dev, 2), round(dev / w, 5), len(members), (round(a[0] / f, 1), round(a[1] / f, 1)), (round(b[0] / f, 1), round(b[1] / f, 1))))
    lines.sort(key=lambda l: -l.deviation)
    devs = [l.deviation for l in lines]
    worst = max(devs, default=0.0)
    # A line found in one piece is straight to the detector's precision, so
    # the median is over the lines that broke into pieces: where bends show.
    pieced = [l.deviation for l in lines if l.pieces > 1]
    median = float(np.median(pieced)) if pieced else 0.0
    return Straightness(lines, round(worst / w, 5), round(median / w, 5), round(worst, 2), round(median, 2))


def overlay(img: np.ndarray, result: Straightness) -> np.ndarray:
    """``img`` with each measured line drawn, green when straight and red at the worst deviation."""
    vis = img.copy()
    t = max(2, round(max(img.shape[:2]) / 800))
    top = max(result.worst_px, 1e-6)
    for line in result.lines:
        k = min(1.0, line.deviation / top)
        colour = (0, int(255 * (1 - k)), int(255 * k))
        cv2.line(vis, tuple(int(v) for v in line.start), tuple(int(v) for v in line.end), colour, t)
    return vis
