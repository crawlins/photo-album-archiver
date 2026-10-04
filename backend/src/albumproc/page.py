"""Finding the page outline in a (fused) photo and estimating its true shape."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

DETECT_MAX_SIDE = 1000


@dataclass
class PageQuad:
    corners: np.ndarray  # (4, 2) float32: TL, TR, BR, BL in the input image's pixels
    method: str  # "edges", "color", "lines" or "fallback"
    score: float


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as TL, TR, BR, BL (clockwise on screen, y down)."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]  # clockwise on screen because y points down
    start = int(np.argmin(pts.sum(axis=1)))
    return np.roll(pts, -start, axis=0)


def _interior_angles_ok(q: np.ndarray, lo: float = 45, hi: float = 135) -> bool:
    for k in range(4):
        a, b, c = q[k - 1], q[k], q[(k + 1) % 4]
        v1, v2 = a - b, c - b
        cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
        ang = np.degrees(np.arccos(np.clip(cos, -1, 1)))
        if not lo <= ang <= hi:
            return False
    return True


def _quad_from_contour(cnt: np.ndarray) -> np.ndarray | None:
    hull = cv2.convexHull(cnt)
    peri = cv2.arcLength(hull, True)
    lo, hi = 0.001, 0.1
    for _ in range(30):
        eps = (lo + hi) / 2
        approx = cv2.approxPolyDP(hull, eps * peri, True)
        if len(approx) == 4:
            return order_corners(approx.reshape(4, 2))
        if len(approx) > 4:
            lo = eps
        else:
            hi = eps
    return None


def _line_intersection(l1, l2) -> np.ndarray | None:
    (p, d), (q, e) = l1, l2
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p + t * d


def _quad_from_sides(cnt: np.ndarray) -> np.ndarray | None:
    """Quad from the four longest straight runs of an outline.

    Unlike the convex hull, this ignores bulges such as a glare spot that
    bridges the page edge and the background: the page's sides are still the
    longest straight stretches, and their lines meet at the true corners.
    """
    peri = cv2.arcLength(cnt, True)
    poly = cv2.approxPolyDP(cnt, 0.004 * peri, True).reshape(-1, 2).astype(np.float64)
    if len(poly) < 4:
        return None
    segs = [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
    # Merge consecutive, nearly collinear pieces of one side.
    merged: list[list[np.ndarray]] = []
    for a, b in segs:
        if merged:
            pa, pb = merged[-1]
            d1, d2 = pb - pa, b - a
            cos = abs(np.dot(d1, d2)) / (np.linalg.norm(d1) * np.linalg.norm(d2) + 1e-9)
            if cos > np.cos(np.radians(6)):
                merged[-1][1] = b
                continue
        merged.append([a, b])
    lengths = [np.linalg.norm(b - a) for a, b in merged]
    size = np.sqrt(cv2.contourArea(cnt.astype(np.float32)))
    chosen = []
    for k in np.argsort(lengths)[::-1]:
        a, b = merged[k]
        if lengths[k] < 0.15 * size:
            break
        d = (b - a) / lengths[k]
        mid = (a + b) / 2
        dup = False
        for p, e, _ in chosen:
            same_dir = abs(np.dot(d, e)) > np.cos(np.radians(15))
            off = abs((mid - p)[0] * e[1] - (mid - p)[1] * e[0])
            if same_dir and off < 0.1 * size:
                dup = True
                break
        if not dup:
            chosen.append((a, d, mid))
        if len(chosen) == 4:
            break
    if len(chosen) < 4:
        return None
    c = cnt.reshape(-1, 2).mean(axis=0)
    chosen.sort(key=lambda l: np.arctan2(l[2][1] - c[1], l[2][0] - c[0]))
    corners = []
    for i in range(4):
        x = _line_intersection(chosen[i][:2], chosen[(i + 1) % 4][:2])
        if x is None:
            return None
        corners.append(x)
    q = order_corners(np.array(corners))
    return q if cv2.isContourConvex(q) else None


def _edge_support(q: np.ndarray, edges: np.ndarray) -> float:
    """Fraction of points along the quad's sides that lie on an image edge."""
    h, w = edges.shape
    hits = total = 0
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        for t in np.linspace(0.05, 0.95, 60):
            x, y = (a + t * (b - a)).round().astype(int)
            if 0 <= x < w and 0 <= y < h:
                total += 1
                hits += edges[y, x] > 0
    return hits / total if total else 0.0


def _dividers(q: np.ndarray, edges: np.ndarray, min_support: float = 0.75, max_gap: float = 0.2, end_support: float = 0.8) -> list[np.ndarray]:
    """Split ``q`` along straight edges that cross it from side to opposite side.

    Pages in an open album touch with no background between them, so they
    come out as one outline. What separates them is a line running the full
    height (or width) of that outline: the join between page edges, the
    binding, the crimped edge of a sleeve, a stack of page edges. Lines inside
    a page, such as print borders, stop short of both sides at the page
    margins, so a divider must be present near both of its ends; a gap in the
    middle is tolerated, since glare can wash out a stretch of it. Returns the
    pieces on both sides of every divider found.
    """
    h, w = edges.shape
    s = np.linspace(0.02, 0.98, 100)
    pieces = []
    # Lines from the top side to the bottom one, then from the left to the right.
    for vertical, (a0, a1, b0, b1) in ((True, q[[0, 1, 3, 2]]), (False, q[[0, 3, 1, 2]])):
        # End points every ~2 px, so a slanted line can still hit a thin edge.
        side = max(np.linalg.norm(a1 - a0), np.linalg.norm(b1 - b0))
        ts = np.linspace(0.1, 0.9, max(81, int(0.8 * side / 2)))
        ii, jj = np.meshgrid(np.arange(len(ts)), np.arange(len(ts)), indexing="ij")
        near = np.abs(ts[ii] - ts[jj]) <= 0.12  # roughly parallel to the sides it runs between
        ii, jj = ii[near], jj[near]
        A = a0 + ts[:, None] * (a1 - a0)
        B = b0 + ts[:, None] * (b1 - b0)

        def on_edge(i, j, s):
            xy = np.round(A[i][:, None] + s[None, :, None] * (B[j] - A[i])[:, None]).astype(int)
            inside = (xy[..., 0] >= 0) & (xy[..., 1] >= 0) & (xy[..., 0] < w) & (xy[..., 1] < h)
            on = np.zeros(inside.shape, bool)
            on[inside] = edges[xy[..., 1][inside], xy[..., 0][inside]] > 0
            return on

        # Screen with every fourth sample first; most lines cross bare paper.
        keep = on_edge(ii, jj, s[::4]).mean(axis=1) >= min_support - 0.15
        ii, jj = ii[keep], jj[keep]
        on = on_edge(ii, jj, s)
        run = np.zeros(len(on))
        longest = np.zeros(len(on))
        for k in range(on.shape[1]):
            run = (run + 1) * ~on[:, k]
            longest = np.maximum(longest, run)
        support = on.mean(axis=1)
        end = len(s) // 10
        ends = np.minimum(on[:, :end].mean(axis=1), on[:, -end:].mean(axis=1))
        ok = np.flatnonzero((support >= min_support) & (longest <= max_gap * len(s)) & (ends >= end_support))
        taken: list[float] = []
        for k in ok[np.argsort(-support[ok])]:
            mid = (ts[ii[k]] + ts[jj[k]]) / 2
            if any(abs(mid - t) < 0.08 for t in taken):
                continue
            taken.append(mid)
            a, b = A[ii[k]], B[jj[k]]
            if vertical:  # left and right pieces
                pieces += [np.array([q[0], a, b, q[3]]), np.array([a, q[1], q[2], b])]
            else:  # top and bottom pieces
                pieces += [np.array([q[0], q[1], b, a]), np.array([a, b, q[2], q[3]])]
            if len(taken) == 3:
                break
    return [p.astype(np.float32) for p in pieces]


def _prepare(img: np.ndarray, valid: np.ndarray | None):
    """Downscale for detection: (scale, valid mask, Lab image, edges)."""
    H, W = img.shape[:2]
    if valid is None:
        valid = np.ones((H, W), bool)
    f = min(1.0, DETECT_MAX_SIDE / max(H, W))
    small = cv2.resize(img, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA) if f < 1 else img.copy()
    vmask = cv2.resize(valid.astype(np.uint8), (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    inner = cv2.erode(vmask, np.ones((9, 9), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (5, 5), 0), cv2.COLOR_BGR2LAB)
    edges = np.zeros(vmask.shape, np.uint8)
    for ch in cv2.split(lab):
        edges |= cv2.Canny(ch, 30, 90)
    edges &= inner * 255
    return f, vmask, lab, edges


def _share(q: np.ndarray, mask: np.ndarray | None) -> float:
    """Share of the quad's interior where ``mask`` is set (0 without a mask).

    Used with a background mask: an outline that takes in a strip of table
    or cloth beside the page, e.g. because wood grain lines up with a page
    edge, is not the page.
    """
    if mask is None:
        return 0.0
    inside = np.zeros(mask.shape, np.uint8)
    cv2.fillConvexPoly(inside, np.round(q).astype(np.int32), 1)
    n = int(inside.sum())
    return float(mask[inside > 0].sum()) / n if n else 0.0


def _pieces(q: np.ndarray, support_edges: np.ndarray, levels: int) -> list[np.ndarray]:
    """Pieces of ``q`` cut along dividers, and pieces of those, ``levels`` deep."""
    out, level = [], _dividers(q, support_edges)
    for _ in range(levels):
        next_level = []
        for piece in level:
            if _interior_angles_ok(piece):
                out.append(piece)
                next_level += _dividers(piece, support_edges)
        level = next_level
    return out


def detect_page(img: np.ndarray, valid: np.ndarray | None = None) -> PageQuad:
    """The best-scoring page candidate; see :func:`detect_pages`."""
    return detect_pages(img, valid)[0]


def detect_pages(img: np.ndarray, valid: np.ndarray | None = None) -> list[PageQuad]:
    """Candidate pages, best first: well-supported quadrilaterals.

    ``valid`` marks pixels that hold real image content (a stitched mosaic has
    empty areas around it). Two candidate generators are tried, edges and
    colour contrast against the surrounding background, and each outline is
    also cut along straight lines that cross it (see :func:`_dividers`), which
    separates pages that touch. Several can come back when more than one page
    is in view. If none is found, the whole valid area
    is returned, which is right when the page fills the frame.
    """
    f, vmask, lab, edges = _prepare(img, valid)
    h, w = vmask.shape
    valid_area = float(vmask.sum())
    support_edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))

    contours = []
    # A: closed edge outlines.
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    found, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours += [("edges", c) for c in found]

    # B: whatever differs in colour from the background seen at the frame rim.
    ring = (vmask > 0) & (cv2.erode(vmask, np.ones((25, 25), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0) == 0)
    background = None  # pixels that look like the background, when it is known
    if ring.sum() > 100:
        labf = lab.astype(np.float32)
        bg = np.median(labf[ring], axis=0)
        dist = np.linalg.norm(labf - bg, axis=2)
        # Threshold relative to how much the background itself varies (wood
        # grain, cloth texture), not Otsu: colourful prints would dominate that.
        # Pages running off the frame put page colours in the rim too, so the
        # spread is taken from the rim pixels near the background colour.
        rd = dist[ring]
        rd = rd[rd <= 2 * np.median(rd) + 1e-3]
        thr = max(12.0, 3.0 * float(np.percentile(rd, 75)))
        fg = ((dist > thr) & (vmask > 0)).astype(np.uint8)
        # Only trust that when the rim and the middle differ, i.e. the page
        # does not fill the frame.
        middle = np.zeros((h, w), bool)
        middle[h // 3 : 2 * h // 3, w // 3 : 2 * w // 3] = True
        middle &= vmask > 0
        if middle.any() and np.linalg.norm(np.median(labf[middle], axis=0) - bg) > thr:
            background = (dist <= thr) & (vmask > 0)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # small: keep gaps between pages
        found, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours += [("color", c) for c in found]

    found_quads: list[PageQuad] = []
    split: list[np.ndarray] = []  # outlines already cut into pieces
    for method, cnt in contours:
        carea = cv2.contourArea(cnt)
        if carea < 0.1 * valid_area:
            continue
        for q in (_quad_from_contour(cnt), _quad_from_sides(cnt)):
            if q is None or not _interior_angles_ok(q):
                continue
            qarea = cv2.contourArea(q)
            area_frac = qarea / valid_area
            if not 0.1 < area_frac < 1.05:
                continue
            fill = min(carea / qarea, qarea / carea)
            if fill < 0.75:
                continue
            support = _edge_support(q, support_edges)
            score = area_frac * fill * (0.3 + 0.7 * support) * (1 - _share(q, background)) ** 2
            found_quads.append(PageQuad((q / f).astype(np.float32), method, float(score)))
            # Pieces of the outline, twice over for a page with neighbours on
            # both sides. The two quad fits often agree, so skip repeats.
            if any(np.abs(q - o).max() < 3 for o in split):
                continue
            split.append(q)
            for piece in _pieces(q, support_edges, 2):
                parea = cv2.contourArea(piece) / valid_area
                if parea < 0.1:
                    continue
                pscore = parea * fill * (0.3 + 0.7 * _edge_support(piece, support_edges)) * (1 - _share(piece, background)) ** 2
                found_quads.append(PageQuad((piece / f).astype(np.float32), "lines", float(pscore)))

    if not found_quads:
        pts = cv2.findNonZero(vmask)
        box = cv2.boxPoints(cv2.minAreaRect(pts))
        found_quads.append(PageQuad((order_corners(box) / f).astype(np.float32), "fallback", 0.0))
    return sorted(found_quads, key=lambda c: -c.score)


def focal_px_from_35mm(focal_35mm: float, width: int, height: int) -> float:
    """Focal length in pixels from a 35 mm-equivalent focal length."""
    return focal_35mm / 43.27 * float(np.hypot(width, height))


def estimate_aspect(corners: np.ndarray, principal_point: tuple[float, float], focal_px: float | None = None) -> float:
    """Width/height of the real rectangle seen as ``corners`` (TL, TR, BR, BL).

    Uses the single-view rectangle geometry of Zhang & He ("Whiteboard
    scanning and image enhancement", 2007). With ``focal_px`` known, the
    result is stable to a few pixels of corner error. Without it, the focal
    length is solved from the corners too, which is far more sensitive, and
    when that is ill-conditioned the ratio of average side lengths is used.
    """
    tl, tr, br, bl = [np.array([*(c - np.asarray(principal_point)), 1.0]) for c in corners]
    m1, m2, m3, m4 = tl, tr, bl, br
    width_px = (np.linalg.norm(tr[:2] - tl[:2]) + np.linalg.norm(br[:2] - bl[:2])) / 2
    height_px = (np.linalg.norm(bl[:2] - tl[:2]) + np.linalg.norm(br[:2] - tr[:2])) / 2
    naive = width_px / height_px

    k2 = np.dot(np.cross(m1, m4), m3) / np.dot(np.cross(m2, m4), m3)
    k3 = np.dot(np.cross(m1, m4), m2) / np.dot(np.cross(m3, m4), m2)
    n2 = k2 * m2 - m1
    n3 = k3 * m3 - m1
    if focal_px is not None:
        f2 = focal_px**2
    else:
        denom = n2[2] * n3[2]
        if abs(denom) < 1e-9:
            return naive
        f2 = -(n2[0] * n3[0] + n2[1] * n3[1]) / denom
        span = max(width_px, height_px)
        if not np.isfinite(f2) or f2 <= 0 or not (0.3 * span) ** 2 < f2 < (20 * span) ** 2:
            return naive
    num = (n2[0] ** 2 + n2[1] ** 2) / f2 + n2[2] ** 2
    den = (n3[0] ** 2 + n3[1] ** 2) / f2 + n3[2] ** 2
    aspect = float(np.sqrt(num / den))
    # Guard against degenerate solutions far from what the image shows.
    if not 0.5 * naive < aspect < 2.0 * naive:
        return naive
    return aspect
