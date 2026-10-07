"""Pages bent near their binding: a curved page model, fitted to the outline.

A page held at its binding bends only across the binding, without
stretching, so it is modelled as a cylindrical surface: a cross-section
(the *profile*) swept along the binding. The profile is written as its angle
``theta(s)`` to the flat part of the page, a natural cubic spline over the
distance ``s`` from the binding measured along the page, so flattening by
``s`` gives true distances, and ``theta = 0`` is exactly the flat page.

The model is fitted in the reference photo's frame (known focal length,
principal point at the centre) to the page outline traced in the preview
mosaic, then again with long straight lines found in the page content added,
for each candidate binding side, and compared with a flat page fitted to the
same evidence. See ``.kiro/specs/page-curvature/design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from functools import lru_cache

import cv2
import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import least_squares

SIDES = ("left", "right", "top", "bottom")
OUTLINE_SIDES = ("top", "right", "bottom", "left")  # the quad's sides TL-TR, TR-BR, BR-BL, BL-TL
KNOTS = (0.0, 0.02, 0.05, 0.1, 0.18, 0.3, 0.55, 1.0)
EXTEND = 0.2  # fraction of the page across the binding; the profile continues straight beyond the page
OUTLINE_POINTS = 40  # model points per side of the outline
FOLD = np.radians(80)  # a profile this steep anywhere folds back on itself
FLAT_FROM = 0.5  # knots this far across or more stay at 0: the far half of the page is the flat part


@dataclass
class AlignParams:
    grid: int = 16  # control points each way of a shot's displacement field
    max_shift: float = 0.01  # largest displacement, fraction of the page's long side
    min_matches: int = 40  # fewer matched features and the shot is left as it is
    outlier_px: float = 3.0  # matches this far (low-res px) from the fitted field are dropped once
    smooth: float = 4.0  # weight of the thin-plate smoothness term, per control point
    zero: float = 0.3  # weight of the pull towards no displacement, per control point


@dataclass
class CurvatureParams:
    mode: str = "auto"  # auto | off | force
    binding: str = "auto"  # auto | left | right | top | bottom
    knots: tuple[float, ...] = KNOTS
    min_improvement: float = 0.3  # relative cost reduction over the plane
    min_lift: float = 0.003  # fraction of the page across the binding
    neighbour_bonus: float = 0.8  # cost factor for the side facing another page
    outline_band: float = 0.03  # tracing band, fraction of the diagonal
    min_line_len: float = 0.08  # content lines, fraction of the page
    smooth_weight: float = 8.0  # second-difference penalty on the knots, px per radian
    zero_weight: float = 0.5  # pull of theta towards 0, px per radian
    line_weight: float = 1.0  # weight of a content line's points against the outline's
    loss_scale: float = 1.0  # soft-L1 loss: residuals (mosaic px) beyond this count linearly
    max_corner_shift: float = 0.02  # fallback check, fraction of the diagonal
    steep_angle_deg: float = 60.0
    align: AlignParams = field(default_factory=AlignParams)

    def validate(self) -> None:
        if self.mode not in ("auto", "off", "force"):
            raise ValueError(f"curvature must be auto, off or force, got {self.mode!r}")
        if self.binding not in ("auto", *SIDES):
            raise ValueError(f"binding must be auto, left, right, top or bottom, got {self.binding!r}")


@lru_cache(maxsize=8)
def _basis(knots: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Profile sample positions (fraction across) and the spline basis there.

    A natural cubic spline is linear in its knot values, so ``theta`` at the
    samples is ``B @ knot_values``. Beyond the page the angle is held at its
    value at the edge, so the surface continues straight.
    """
    # Dense where the page bends, sparse over the flat part.
    sf = np.concatenate([np.linspace(-EXTEND, 0.35, 111), np.linspace(0.35, 1 + EXTEND, 35)[1:]])
    B = CubicSpline(np.asarray(knots), np.eye(len(knots)), bc_type="natural")(np.clip(sf, 0, 1))
    return sf, B


def _cumtrapz(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum((y[1:] + y[:-1]) / 2 * np.diff(x))])


@dataclass
class PageSurface:
    """A page bent across one side, in the reference photo's camera.

    Page coordinates are in units of the page width: the flat page is
    ``[0, 1] x [0, height]`` in its plane, with x to the right and y down as
    in the composed page. ``rvec``/``tvec`` put that plane (the one the flat
    part of the page lies in) into the reference camera, as from
    ``cv2.solvePnP``; z points away from the camera, so a lift is negative z.
    The profile is anchored at the side opposite the binding: the far part
    of the page lies in the plane, and the binding edge rises out of it.
    """

    binding: str  # left | right | top | bottom
    rvec: np.ndarray
    tvec: np.ndarray
    K: np.ndarray  # reference intrinsics, full resolution
    height: float  # page height / page width
    theta: np.ndarray  # knot values, radians; positive lifts the binding side towards the camera
    knots: tuple[float, ...] = KNOTS
    _cache: tuple | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def across(self) -> float:
        """The page's size across the binding, in page widths."""
        return 1.0 if self.binding in ("left", "right") else self.height

    @property
    def aspect(self) -> float:
        return 1.0 / self.height

    def _profile(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Samples of (distance from the binding along the page, footprint distance from the binding, lift)."""
        key = (self.binding, float(self.height), np.asarray(self.theta, np.float64).tobytes(), tuple(self.knots))
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        sf, B = _basis(tuple(self.knots))
        L = self.across
        th = B @ np.asarray(self.theta, np.float64)
        s = sf * L
        c, sn = _cumtrapz(np.cos(th), s), _cumtrapz(np.sin(th), s)
        cL, snL = np.interp(L, s, c), np.interp(L, s, sn)
        out = (s, L - (cL - c), snL - sn)
        self._cache = (key, out)
        return out

    def theta_at(self, u: np.ndarray) -> np.ndarray:
        """Profile angle at ``u``, fractions across the page from the binding."""
        knots = np.asarray(self.knots)
        return CubicSpline(knots, np.asarray(self.theta, np.float64), bc_type="natural")(np.clip(u, 0, 1))

    def profile(self, n: int = 50, at: np.ndarray | None = None) -> list[tuple[float, float]]:
        """(position, lift) across the page from the binding, both as fractions of the size across it."""
        u = np.linspace(0, 1, n) if at is None else np.asarray(at, np.float64)
        s, _, lift = self._profile()
        L = self.across
        return [(float(a), float(b)) for a, b in zip(u, np.interp(u * L, s, lift) / L)]

    def max_lift(self) -> float:
        """Largest lift out of the flat part's plane, fraction of the size across the binding."""
        s, _, lift = self._profile()
        inside = (s >= 0) & (s <= self.across)
        return float(np.abs(lift[inside]).max() / self.across)

    def strip_width(self, deg: float = 2.0) -> float:
        """How far from the binding the profile is steeper than ``deg``, fraction across."""
        u = np.linspace(0, 1, 401)
        steep = np.abs(self.theta_at(u)) > np.radians(deg)
        return float(u[np.flatnonzero(steep)[-1]]) if steep.any() else 0.0

    def footprint_width(self) -> float:
        """Distance in the plane from the binding edge to the far edge, fraction across."""
        s, x, _ = self._profile()
        return float((self.across - np.interp(0.0, s, x)) / self.across)

    def _along_across(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Page fractions (composed orientation) -> (distance from the binding along the page, position along it)."""
        h = self.height
        if self.binding == "left":
            return x * 1.0, y * h
        if self.binding == "right":
            return (1 - x) * 1.0, y * h
        if self.binding == "top":
            return y * h, x * 1.0
        return (1 - y) * h, x * 1.0

    def plane_points(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """3D points (n, 3) of page fractions ``x``, ``y`` in the page plane's frame."""
        x, y = np.asarray(x, np.float64).ravel(), np.asarray(y, np.float64).ravel()
        s, along = self._along_across(x, y)
        ps, px, pl = self._profile()
        foot, lift = np.interp(s, ps, px), np.interp(s, ps, pl)
        h = self.height
        if self.binding == "left":
            X, Y = foot, along
        elif self.binding == "right":
            X, Y = 1 - foot, along
        elif self.binding == "top":
            X, Y = along, foot
        else:
            X, Y = along, h - foot
        return np.stack([X, Y, -lift], axis=1)

    def _camera(self) -> tuple[np.ndarray, np.ndarray]:
        R, _ = cv2.Rodrigues(np.asarray(self.rvec, np.float64).reshape(3, 1))
        return R, np.asarray(self.tvec, np.float64).reshape(3)

    def project(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Reference-photo pixels (n, 2) of page fractions ``x``, ``y`` (0..1, composed orientation)."""
        R, t = self._camera()
        c = self.plane_points(x, y) @ R.T + t
        q = c @ np.asarray(self.K, np.float64).T
        return q[:, :2] / q[:, 2:3]

    def unproject(self, uv: np.ndarray) -> np.ndarray:
        """Page fractions (n, 2) seen at reference-photo pixels ``uv`` (n, 2); NaN off the surface.

        The surface is a cylinder, so a viewing ray meets it where the ray's
        shadow on the profile's plane crosses the profile curve; the crossing
        nearest the camera is taken.
        """
        uv = np.asarray(uv, np.float64).reshape(-1, 2)
        R, t = self._camera()
        C = -R.T @ t
        rays = np.linalg.solve(np.asarray(self.K, np.float64), np.column_stack([uv, np.ones(len(uv))]).T).T @ R  # = (R.T @ ray).T
        ps, px, pl = self._profile()
        h = self.height
        axis = 0 if self.binding in ("left", "right") else 1
        other = 1 - axis
        a = {"left": px, "right": 1 - px, "top": px, "bottom": h - px}[self.binding]
        z = -pl
        da, dz = rays[:, axis : axis + 1], rays[:, 2:3]
        F = (a[None, :] - C[axis]) * dz - (z[None, :] - C[2]) * da
        cross = (F[:, :-1] * F[:, 1:] <= 0) & (F[:, :-1] != F[:, 1:])
        w = np.where(cross, F[:, :-1] / np.where(cross, F[:, :-1] - F[:, 1:], 1), 0)
        s = ps[:-1] + w * np.diff(ps)
        ar = a[:-1] + w * np.diff(a)
        zr = z[:-1] + w * np.diff(z)
        lam = np.where(np.abs(da) > np.abs(dz), (ar - C[axis]) / np.where(da == 0, 1e-12, da), (zr - C[2]) / np.where(dz == 0, 1e-12, dz))
        lam = np.where(cross & (lam > 0), lam, np.inf)
        k = np.argmin(lam, axis=1)
        rows = np.arange(len(uv))
        found = np.isfinite(lam[rows, k])
        s_hit = np.where(found, s[rows, k], np.nan)
        along = C[other] + np.where(found, lam[rows, k], np.nan) * rays[:, other]
        if self.binding == "left":
            return np.stack([s_hit, along / h], axis=1)
        if self.binding == "right":
            return np.stack([1 - s_hit, along / h], axis=1)
        if self.binding == "top":
            return np.stack([along, s_hit / h], axis=1)
        return np.stack([along, 1 - s_hit / h], axis=1)

    def normals(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Unit surface normals (n, 3) in the page plane's frame, pointing towards the camera side."""
        x, y = np.asarray(x, np.float64).ravel(), np.asarray(y, np.float64).ravel()
        s, _ = self._along_across(x, y)
        th = self.theta_at(s / self.across)
        # Along the profile, away from the binding, the page runs (cos, sin)
        # in (footprint, z); its normal on the camera's side (z < 0) is (sin, -cos).
        sign = {"left": 1, "right": -1, "top": 1, "bottom": -1}[self.binding]
        out = np.zeros((len(x), 3))
        out[:, 0 if self.binding in ("left", "right") else 1] = sign * np.sin(th)
        out[:, 2] = -np.cos(th)
        return out

    def camera_centre(self) -> np.ndarray:
        R, t = self._camera()
        return -R.T @ t


def initial_surface(corners_ref: np.ndarray, K: np.ndarray, aspect: float, binding: str = "left") -> PageSurface | None:
    """The flat page seen as ``corners_ref`` (TL, TR, BR, BL) with the given aspect ratio."""
    h = 1.0 / aspect
    obj = np.array([[0, 0, 0], [1, 0, 0], [1, h, 0], [0, h, 0]], np.float64)
    ok, rvec, tvec = cv2.solvePnP(obj, np.asarray(corners_ref, np.float64).reshape(4, 1, 2), np.asarray(K, np.float64), None, flags=cv2.SOLVEPNP_IPPE)
    if not ok or float(np.ravel(tvec)[2]) <= 0:
        return None
    return PageSurface(binding, rvec.reshape(3), tvec.reshape(3), np.asarray(K, np.float64), h, np.zeros(len(KNOTS)))


# --- Evidence: the outline ---------------------------------------------------


def trace_outline(img: np.ndarray, valid: np.ndarray, quad: np.ndarray, band: float) -> list[np.ndarray | None]:
    """The page's outline near each side of ``quad``, as traced curves.

    For each side (top, right, bottom, left), the image is sampled in a band
    of ``band`` times the page diagonal on both sides of it, and the
    strongest continuous edge running along the side is followed by dynamic
    programming, so a bowed side is traced as it is, not as a straight line.
    Returns each side's points (n, 2) in ``img`` pixels, or None where the
    side is not seen (cut off by the edge of what was photographed, or no
    clear edge).
    """
    inner = cv2.erode(np.asarray(valid, np.uint8), np.ones((9, 9), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0)
    q = np.asarray(quad, np.float64).reshape(4, 2)
    diag = max(np.linalg.norm(q[2] - q[0]), np.linalg.norm(q[3] - q[1]))
    B = max(4, int(round(band * diag)))
    offs = np.arange(-B, B + 1, dtype=np.float64)
    out: list[np.ndarray | None] = []
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        length = float(np.linalg.norm(b - a))
        d = (b - a) / max(length, 1e-9)
        n = np.array([d[1], -d[0]])  # outward for TL, TR, BR, BL on screen
        t = np.linspace(0.02, 0.98, int(np.clip(length / 2, 50, 1200)))
        pts = a + t[None, :, None] * (b - a) + offs[:, None, None] * n
        mx, my = pts[..., 0].astype(np.float32), pts[..., 1].astype(np.float32)
        strip = cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        strip = cv2.cvtColor(cv2.GaussianBlur(strip, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
        ok = cv2.remap(inner, mx, my, cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0) > 0
        g = np.sqrt((np.diff(strip, axis=0) ** 2).sum(axis=2))  # edge strength across the side, between rows
        g = cv2.blur(g, (5, 1))
        g[~(ok[1:] & ok[:-1])] = 0
        scale = float(np.percentile(g[g > 0], 99)) if (g > 0).any() else 0.0
        if scale <= 0:
            out.append(None)
            continue
        E = np.minimum(g / scale, 1.5)
        mid = (offs[1:] + offs[:-1]) / 2
        E -= 0.002 * np.abs(mid)[:, None]  # a faint preference for the detected side itself
        rows = _best_path(E, step_cost=0.08)
        on = E[rows, np.arange(len(t))]
        seen = ok[rows, np.arange(len(t))]
        if float(np.mean(on)) < 0.2 or float(np.mean(seen)) < 0.8:
            out.append(None)
            continue
        # To a fraction of a pixel: the peak of a parabola through the edge
        # strength at the chosen row and its neighbours.
        cols = np.arange(len(t))
        r0 = np.clip(rows, 1, len(mid) - 2)
        gm, g0, gp = g[r0 - 1, cols], g[r0, cols], g[r0 + 1, cols]
        den = gm - 2 * g0 + gp
        sub = np.where(den < 0, 0.5 * (gm - gp) / np.where(den < 0, den, -1), 0.0)
        off = mid[r0] + np.clip(sub, -0.5, 0.5)
        out.append((a + t[:, None] * (b - a) + off[:, None] * n).astype(np.float64))
    return out


def _best_path(E: np.ndarray, step_cost: float) -> np.ndarray:
    """Row per column maximising the summed ``E``, moving at most one row per column."""
    nr, nc = E.shape
    score = E[:, 0].copy()
    back = np.zeros((nr, nc), np.int8)
    for j in range(1, nc):
        up = np.concatenate([[-np.inf], score[:-1]]) - step_cost
        down = np.concatenate([score[1:], [-np.inf]]) - step_cost
        stack = np.stack([up, score, down])
        k = np.argmax(stack, axis=0)
        back[:, j] = k - 1
        score = stack[k, np.arange(nr)] + E[:, j]
    rows = np.empty(nc, np.int64)
    rows[-1] = int(np.argmax(score))
    for j in range(nc - 1, 0, -1):
        rows[j - 1] = rows[j] + back[rows[j], j]
    return rows


def side_bows(outline: list[np.ndarray | None], page_width_px: float) -> dict[str, float]:
    """How far each traced side bows from a straight line, fraction of the page width."""
    bows = {}
    for name, pts in zip(OUTLINE_SIDES, outline):
        if pts is None:
            continue
        a, b = pts[0], pts[-1]
        d = (b - a) / max(np.linalg.norm(b - a), 1e-9)
        t = (pts - a) @ d
        off = (pts - a) @ np.array([d[1], -d[0]])
        fit = np.polyval(np.polyfit(t, off, 1), t)
        bows[name] = round(float(np.abs(off - fit).max() / page_width_px), 4)
    return bows


def _traced_corners(outline: list[np.ndarray | None]) -> list[np.ndarray | None]:
    """Corners (TL, TR, BR, BL) where the ends of adjacent traced sides meet."""

    def end_line(pts, at_start):
        m = max(5, len(pts) // 8)
        seg = pts[:m] if at_start else pts[-m:]
        c = seg.mean(axis=0)
        _, _, vt = np.linalg.svd(seg - c)
        return c, vt[0]

    corners = []
    for k in range(4):  # corner k joins side k-1's end and side k's start
        prev, cur = outline[k - 1], outline[k]
        if prev is None or cur is None:
            corners.append(None)
            continue
        (p, d), (r, e) = end_line(prev, False), end_line(cur, True)
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-6:
            corners.append(None)
            continue
        s = ((r[0] - p[0]) * e[1] - (r[1] - p[1]) * e[0]) / den
        corners.append(p + s * d)
    return corners


# --- Evidence: lines in the page ---------------------------------------------


def _flat_view(img: np.ndarray, valid: np.ndarray, surface: PageSurface, to_mosaic: np.ndarray, long_side: int = 1000):
    """The page flattened by ``surface``, at ``long_side`` px: (image, valid mask, size)."""
    W = long_side if surface.aspect >= 1 else max(2, int(round(long_side * surface.aspect)))
    H = max(2, int(round(W / surface.aspect))) if surface.aspect >= 1 else long_side
    step = 4
    gx = np.arange(0, W + step, step)
    gy = np.arange(0, H + step, step)
    X, Y = np.meshgrid(gx / max(W - 1, 1), gy / max(H - 1, 1))
    ref = surface.project(X.ravel(), Y.ravel())
    m = cv2.perspectiveTransform(ref.reshape(-1, 1, 2), to_mosaic).reshape(len(gy), len(gx), 2).astype(np.float32)
    full = upsample_map(m, step, (W, H))
    flat = cv2.remap(img, full[..., 0], full[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    ok = cv2.remap(np.asarray(valid, np.uint8), full[..., 0], full[..., 1], cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT) > 0
    return flat, ok, (W, H)


def upsample_map(coarse: np.ndarray, step: int, size: tuple[int, int], rows: tuple[int, int] | None = None, scale: float = 1.0) -> np.ndarray:
    """Bilinear upsampling of a grid sampled every ``step`` px (node k at k * step) to ``size`` (w, h).

    (cv2.remap interpolates with 1/32 px weights; on an 8 px grid of
    smoothly varying coordinates that is far below a pixel.)

    ``rows`` picks a band of output rows. With ``scale`` below 1 the output
    is that much smaller than the grid's frame. Positions beyond the grid
    extrapolate linearly from its last cell.
    """
    W, H = size
    r0, r1 = rows or (0, H)
    gx = (np.arange(W, dtype=np.float32) / np.float32(scale * step))[None, :].repeat(r1 - r0, axis=0)
    gy = (np.arange(r0, r1, dtype=np.float32) / np.float32(scale * step))[:, None].repeat(W, axis=1)
    inside = gx.max() <= coarse.shape[1] - 1 and gy.max() <= coarse.shape[0] - 1
    if not inside:
        return bilinear(coarse, gx, gy)
    # Within the grid, cv2.remap does the same bilinear interpolation much faster.
    return cv2.remap(np.ascontiguousarray(coarse, np.float32), gx, gy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def bilinear(grid: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Sample ``grid`` (h, w, c) at fractional node positions, extrapolating linearly outside."""
    h, w = grid.shape[:2]
    x0 = np.clip(np.floor(gx).astype(np.int64), 0, max(w - 2, 0))
    y0 = np.clip(np.floor(gy).astype(np.int64), 0, max(h - 2, 0))
    fx = (gx - x0)[..., None]
    fy = (gy - y0)[..., None]
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    top = grid[y0, x0] * (1 - fx) + grid[y0, x1] * fx
    bot = grid[y1, x0] * (1 - fx) + grid[y1, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def chain_segments(segs: list, angle_deg: float, gap: float, offset: float) -> list[list[int]]:
    """Group line segments that continue one another into chains.

    ``segs`` are (start, end, unit direction, length). Two segments join
    when they point the same way (LSD's direction also says which side is
    darker, so the two edges of a thin line stay apart), their nearest ends
    are within ``gap``, and each one's nearer end lies within ``offset`` of
    the other's line. Returns the chains as lists of indices.
    """
    n = len(segs)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cos = np.cos(np.radians(angle_deg))
    for i in range(n):
        a1, a2, da, _ = segs[i]
        na = np.array([da[1], -da[0]])
        for j in range(i + 1, n):
            b1, b2, db, _ = segs[j]
            if np.dot(da, db) < cos:
                continue
            ends = [(np.linalg.norm(p - q), p, q) for p in (a1, a2) for q in (b1, b2)]
            d, pa, pb = min(ends, key=lambda e: e[0])
            if d > gap:
                continue
            nb = np.array([db[1], -db[0]])
            if abs(np.dot(pb - a1, na)) > offset or abs(np.dot(pa - b1, nb)) > offset:
                continue
            parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def find_lines(img: np.ndarray, valid: np.ndarray, surface: PageSurface, to_mosaic: np.ndarray, p: CurvatureParams, limit: int = 24) -> list[np.ndarray]:
    """Long straight lines in the page that run into the bent strip, as point chains in ``img`` pixels.

    The page is flattened with ``surface`` and searched with OpenCV's line
    segment detector; collinear pieces are chained, so a line still bent by
    a poor first fit is found as one. Lines parallel to the binding say
    nothing about the bend and are left out, as are the page's own edges.
    """
    flat, ok, (W, H) = _flat_view(img, valid, surface, to_mosaic)
    gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)
    segs = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(gray)[0]
    if segs is None:
        return []
    segs = segs.reshape(-1, 4).astype(np.float64)
    size = min(W, H)
    across_x = surface.binding in ("left", "right")  # the across direction is x in the flat view
    rim = 0.015 * size
    keep = []
    for x1, y1, x2, y2 in segs:
        d = np.array([x2 - x1, y2 - y1])
        length = float(np.hypot(*d))
        if length < 0.02 * size:
            continue
        d /= length
        # The binding runs along y for a left or right binding: such lines are parallel to it.
        if abs(d[0] if across_x else d[1]) < np.sin(np.radians(30)):
            continue
        xs, ys = np.array([x1, x2]), np.array([y1, y2])
        if (xs < rim).all() or (xs > W - 1 - rim).all() or (ys < rim).all() or (ys > H - 1 - rim).all():
            continue  # the page's own edge
        mid = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
        if not ok[int(np.clip(mid[1], 0, H - 1)), int(np.clip(mid[0], 0, W - 1))]:
            continue
        keep.append((np.array([x1, y1]), np.array([x2, y2]), d, length))
    groups = chain_segments(keep, angle_deg=10, gap=0.04 * size, offset=0.004 * size)
    # Within the strip next to the binding (generously: where a poor first fit may have put it).
    strip = max(0.25, 1.5 * surface.strip_width())
    chains = []
    for members in groups:
        pts = []
        total = 0.0
        for i in members:
            a, b, _, length = keep[i]
            pts.append(a + np.linspace(0, 1, max(2, int(length / 4)))[:, None] * (b - a))
            total += length
        pts = np.concatenate(pts)
        span = float(np.ptp(pts[:, 0] if across_x else pts[:, 1]))
        if span < p.min_line_len * (W if across_x else H) or total < p.min_line_len * size:
            continue
        across = pts[:, 0] / (W - 1) if across_x else pts[:, 1] / (H - 1)
        from_binding = across if surface.binding in ("left", "top") else 1 - across
        if from_binding.min() > strip:
            continue
        chains.append((total, pts))
    chains.sort(key=lambda c: -c[0])
    out = []
    gray_f = gray.astype(np.float32)
    for _, pts in chains[:limit]:
        # The points must be measured in the image: LSD's segments are
        # straight by construction, so points along them would be straight
        # under any model. The edge is located across the chain's line at
        # regular steps, so a line still bent in this view stays bent.
        c = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - c, full_matrices=False)
        along = (pts - c) @ vt[0]
        edge = _edge_points(gray_f, c, vt[0], vt[1], along.min(), along.max(), max(3.0, 0.01 * size))
        if edge is None or len(edge) < 8:
            continue
        if len(edge) > 20:
            edge = edge[np.linspace(0, len(edge) - 1, 20).round().astype(int)]
        frac = np.stack([edge[:, 0] / (W - 1), edge[:, 1] / (H - 1)], axis=1)
        ref = surface.project(frac[:, 0], frac[:, 1])
        out.append(cv2.perspectiveTransform(ref.reshape(-1, 1, 2), to_mosaic).reshape(-1, 2))
    return out


def _edge_points(gray: np.ndarray, c, d, n, t0: float, t1: float, reach: float) -> np.ndarray | None:
    """Sub-pixel points of the edge running along the line ``c + t d``, ``t0 <= t <= t1``.

    Across the line, every 3 px along it, the strongest change in brightness
    of the line's polarity within ``reach`` is located to a fraction of a pixel. Points where it is
    faint (a gap in the line) or that jump away from their neighbours (a
    crossing line, a letter) are left out.
    """
    t = np.arange(t0, t1, 3.0)
    if len(t) < 8:
        return None
    r = np.arange(-np.ceil(reach), np.ceil(reach) + 1)
    grid = c + t[:, None, None] * d + r[None, :, None] * n
    prof = cv2.remap(gray, grid[..., 0].astype(np.float32), grid[..., 1].astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    gs = np.diff(cv2.GaussianBlur(prof, (1, 5), 0), axis=1)
    rows = np.arange(len(t))
    # One polarity only (dark to light, or light to dark, as the line's
    # strongest steps mostly are), so the two edges of a thin printed line
    # are not mixed up.
    polarity = np.sign(np.median(gs[rows, np.argmax(np.abs(gs), axis=1)])) or 1.0
    g = np.maximum(polarity * gs, 0)
    k = np.clip(np.argmax(g, axis=1), 1, g.shape[1] - 2)
    gm, g0, gp = g[rows, k - 1], g[rows, k], g[rows, k + 1]
    den = gm - 2 * g0 + gp
    sub = np.where(den < 0, 0.5 * (gm - gp) / np.where(den < 0, den, -1), 0.0)
    off = r[0] + k + 0.5 + np.clip(sub, -0.5, 0.5)
    ok = g0 >= 0.4 * np.median(g0)
    med = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(off, 3, mode="edge"), 7), axis=1)
    ok &= np.abs(off - med) <= 1.0
    if ok.sum() < 8:
        return None
    return (c + t[:, None] * d + off[:, None] * n)[ok]


# --- Fitting -----------------------------------------------------------------


@dataclass
class SurfaceFit:
    surface: PageSurface
    cost: float  # robust cost at the solution
    converged: bool
    outline_rms: float  # mosaic px
    outline_cost: float = float("inf")  # the outline's share of the robust cost


class _Problem:
    """Residuals of a surface against the outline and the lines, in mosaic px."""

    def __init__(self, outline, lines, init: PageSurface, to_mosaic: np.ndarray, p: CurvatureParams, curved: bool, pose: bool = True):
        self.init, self.p, self.curved, self.pose = init, p, curved, pose
        # The knots on the far half stay at 0: a profile tilted there would
        # only tilt the page plane, which the pose already does.
        self.free = np.asarray(init.knots) < FLAT_FROM
        self.to_mosaic = np.asarray(to_mosaic, np.float64)
        self.from_mosaic = np.linalg.inv(self.to_mosaic)
        t = np.linspace(0.02, 0.98, OUTLINE_POINTS)
        side_fracs = {
            "top": (t, np.zeros_like(t)),
            "right": (np.ones_like(t), t),
            "bottom": (t, np.ones_like(t)),
            "left": (np.zeros_like(t), t),
        }
        self.sides = []  # (model fractions x, y, chord origin, direction, normal, traced t, traced offset)
        for name, pts in zip(OUTLINE_SIDES, outline):
            if pts is None:
                continue
            a, b = pts[0], pts[-1]
            length = max(float(np.linalg.norm(b - a)), 1e-9)
            d = (b - a) / length
            nrm = np.array([d[1], -d[0]])
            tt = (pts - a) @ d / length
            order = np.argsort(tt)
            self.sides.append((*side_fracs[name], a, d / length, nrm, tt[order], ((pts - a) @ nrm)[order]))
        self.lines = [cv2.perspectiveTransform(np.asarray(c, np.float64).reshape(-1, 1, 2), self.from_mosaic).reshape(-1, 2) for c in lines]
        self.line_pts = np.concatenate(self.lines) if self.lines else np.zeros((0, 2))
        self.line_ends = np.cumsum([0] + [len(c) for c in self.lines])
        # All the lines together count for at most two sides of the outline:
        # many lines would otherwise drown the outline, and with it the
        # comparison with the flat page, in their measurement noise.
        self.line_scale = p.line_weight * min(1.0, np.sqrt(2 * OUTLINE_POINTS / max(len(self.line_pts), 1)))
        # Mosaic px per page width, for the lines' residuals.
        q = init.project(np.array([0, 1, 1, 0.0]), np.array([0, 0, 1, 1.0]))
        qm = cv2.perspectiveTransform(q.reshape(-1, 1, 2), self.to_mosaic).reshape(4, 2)
        self.px_per_width = (np.linalg.norm(qm[1] - qm[0]) + np.linalg.norm(qm[2] - qm[3])) / 2
        self.n_outline = sum(len(s[0]) for s in self.sides)

    def pack(self, s: PageSurface) -> np.ndarray:
        x = [*np.ravel(s.rvec), *np.ravel(s.tvec), s.height] if self.pose else []
        return np.array(x + (list(np.asarray(s.theta)[self.free]) if self.curved else []), np.float64)

    def unpack(self, x: np.ndarray) -> PageSurface:
        theta = np.zeros(len(self.init.knots))
        k = 7 if self.pose else 0
        if self.curved:
            theta[self.free] = x[k:]
        if not self.pose:
            return replace(self.init, theta=theta)
        return replace(self.init, rvec=x[0:3].copy(), tvec=x[3:6].copy(), height=float(x[6]), theta=theta)

    def outline_residuals(self, s: PageSurface) -> np.ndarray:
        if not self.sides:
            return np.zeros(0)
        fx = np.concatenate([side[0] for side in self.sides])
        fy = np.concatenate([side[1] for side in self.sides])
        m_all = cv2.perspectiveTransform(s.project(fx, fy).reshape(-1, 1, 2), self.to_mosaic).reshape(-1, 2)
        out = []
        k = 0
        for fxs, _, a, d, nrm, tt, off in self.sides:
            m = m_all[k : k + len(fxs)]
            k += len(fxs)
            out.append((m - a) @ nrm - np.interp((m - a) @ d, tt, off))
        return np.concatenate(out)

    def line_residuals(self, s: PageSurface) -> np.ndarray:
        if not self.lines:
            return np.zeros(0)
        flat = s.unproject(self.line_pts)
        flat = np.where(np.isfinite(flat), flat, 0.0) * [1.0, s.height]
        out = []
        for k in range(len(self.lines)):
            f = flat[self.line_ends[k] : self.line_ends[k + 1]]
            c = f.mean(axis=0)
            _, _, vt = np.linalg.svd(f - c, full_matrices=False)
            out.append((f - c) @ vt[1] * self.px_per_width * self.line_scale)
        return np.concatenate(out)

    def __call__(self, x: np.ndarray) -> np.ndarray:
        s = self.unpack(x)
        r = [self.outline_residuals(s), self.line_residuals(s)]
        if self.curved:
            th = s.theta
            r.append(self.p.smooth_weight * (th[:-2] - 2 * th[1:-1] + th[2:]))
            r.append(self.p.zero_weight * th)
        return np.concatenate(r)


def fit_surface(outline, lines, init: PageSurface, to_mosaic: np.ndarray, p: CurvatureParams, curved: bool = True, pose: bool = True) -> SurfaceFit:
    """Fit a bent (or, with ``curved=False``, a flat) page to the outline and lines.

    ``least_squares`` with a soft-L1 loss, so stray outline points, such as a
    glare blob crossing the edge, are not squared. With ``pose=False`` only
    the bend is fitted, the page's pose and height staying ``init``'s: a
    quick first look at a side.
    """
    prob = _Problem(outline, lines, init, to_mosaic, p, curved, pose)
    x0 = prob.pack(init)
    lo = np.full(len(x0), -np.inf)
    hi = np.full(len(x0), np.inf)
    k = 7 if pose else 0
    if pose:
        lo[6], hi[6] = 0.2 * init.height, 5.0 * init.height
        lo[5] = 1e-3  # the page stays in front of the camera
    if curved:
        lo[k:], hi[k:] = -1.5, 1.5
    x0 = np.clip(x0, lo + 1e-9, hi - 1e-9)
    try:
        res = least_squares(prob, x0, bounds=(lo, hi), loss="soft_l1", f_scale=p.loss_scale, x_scale="jac", ftol=1e-6, xtol=1e-6, max_nfev=60 * len(x0))
    except (ValueError, np.linalg.LinAlgError):
        return SurfaceFit(init, float("inf"), False, float("inf"))
    s = prob.unpack(res.x)
    r = prob.outline_residuals(s)
    rms = float(np.sqrt(np.mean(r**2))) if len(r) else 0.0
    z = (r / p.loss_scale) ** 2
    outline_cost = float(0.5 * p.loss_scale**2 * np.sum(2 * (np.sqrt(1 + z) - 1)))  # least_squares' soft-L1 cost
    return SurfaceFit(s, float(res.cost), bool(res.status > 0 and np.isfinite(res.cost)), rms, outline_cost)


@dataclass
class CurvatureFit:
    surface: PageSurface | None  # None when the page is treated as flat
    planar_cost: float
    curved_cost: float
    outline_rms_planar: float  # px, mosaic
    outline_rms_curved: float
    reason: str | None  # flat | small_gain | fit_failed | off
    lines_used: int
    best: PageSurface | None = None  # the best bent fit, applied or not
    planar: PageSurface | None = None  # the flat fit to the same evidence
    bows: dict = field(default_factory=dict)  # per traced side, fraction of the page width
    outline: list = field(default_factory=list)  # traced sides, mosaic px

    @property
    def max_bow(self) -> float:
        return max(self.bows.values(), default=0.0)


def fit_page(
    mosaic: np.ndarray,
    valid: np.ndarray,
    quad: np.ndarray,
    to_mosaic: np.ndarray,
    K: np.ndarray,
    aspect: float,
    p: CurvatureParams,
    neighbour_side: str | None = None,
) -> CurvatureFit:
    """Decide whether the page in ``mosaic`` is bent, and if so how.

    ``quad`` is the detected page in ``mosaic``, ``to_mosaic`` maps the
    reference photo's full-resolution pixels into it, ``K`` is the reference
    camera and ``aspect`` the flat page's measured width/height.
    """
    if p.mode == "off":
        return CurvatureFit(None, 0.0, 0.0, 0.0, 0.0, "off", 0)
    quad = np.asarray(quad, np.float64).reshape(4, 2)
    outline = trace_outline(mosaic, valid, quad, p.outline_band)
    width_px = (np.linalg.norm(quad[1] - quad[0]) + np.linalg.norm(quad[2] - quad[3])) / 2
    bows = side_bows(outline, width_px)
    failed = CurvatureFit(None, 0.0, 0.0, 0.0, 0.0, "fit_failed", 0, bows=bows, outline=outline)
    corners_ref = cv2.perspectiveTransform(quad.reshape(-1, 1, 2), np.linalg.inv(to_mosaic)).reshape(4, 2)
    init = initial_surface(corners_ref, K, aspect)
    traced = sum(o is not None for o in outline)
    if init is None or traced < 2:
        return failed
    planar1 = fit_surface(outline, [], init, to_mosaic, p, curved=False)
    if not planar1.converged:
        return failed
    diag = float(np.hypot(*(quad.max(axis=0) - quad.min(axis=0))))
    traced_corners = _traced_corners(outline)
    sides = SIDES if p.binding == "auto" else (p.binding,)
    first = {}
    for side in sides:
        # A quick look at each side: the bend alone, on the flat fit's pose.
        start = replace(init, binding=side)
        look = fit_surface(outline, [], replace(planar1.surface, binding=side), to_mosaic, p, curved=True, pose=False)
        ratio = look.cost / max(planar1.cost, 1e-9) * (p.neighbour_bonus if side == neighbour_side else 1.0)
        first[side] = (start, look, ratio if look.converged else np.inf)
    best1 = min(r for _, _, r in first.values())
    straight = max(bows.values(), default=0.0) < p.min_lift  # no side bows as a bent page's do
    found = []
    for side in sides:
        start, look, ratio1 = first[side]
        seen = outline[OUTLINE_SIDES.index(side)] is not None
        # Sides are looked at in full where they can change the verdict: the
        # sides the outline already favours, and those whose own outline is
        # hidden. With most of the outline seen and straight, it decides (see
        # _cost), so a side where it shows no sign of a bend is settled by the
        # quick look.
        promising = ratio1 <= 1.25 * best1 + 0.05 or not seen or side == neighbour_side
        quiet = traced >= 3 and seen and straight and (look.surface.max_lift() < p.min_lift / 3 or ratio1 > 1 - p.min_improvement / 2)
        if (quiet and p.mode != "force") or not promising:
            if look.converged and _sane(look.surface, traced_corners, to_mosaic, diag, p):
                planar = replace(planar1, surface=replace(planar1.surface, binding=side))
                found.append((ratio1, side, planar, look, 0, True))
            continue
        curved1 = fit_surface(outline, [], start, to_mosaic, p, curved=True)
        lines = find_lines(mosaic, valid, curved1.surface, to_mosaic, p) if curved1.converged else []
        if traced < 3 and len(lines) < 2:
            continue  # not enough evidence for this side
        if lines:
            planar2 = fit_surface(outline, lines, start, to_mosaic, p, curved=False)
            curved2 = fit_surface(outline, lines, curved1.surface, to_mosaic, p, curved=True)
        else:
            planar2, curved2 = replace(planar1, surface=start), curved1
        if not (curved2.converged and planar2.converged) or not _sane(curved2.surface, traced_corners, to_mosaic, diag, p):
            continue
        ratio = _cost(curved2, traced) / max(_cost(planar2, traced), 1e-9)
        if side == neighbour_side:
            ratio *= p.neighbour_bonus
        found.append((ratio, side, planar2, curved2, len(lines), False))
    if not found:
        return failed
    _, side, planar, curved, n_lines, quick = min(found, key=lambda f: (f[0], SIDES.index(f[1])))
    if quick:  # the best side was only looked at quickly: fit it in full
        full = fit_surface(outline, [], replace(init, binding=side), to_mosaic, p, curved=True)
        if full.converged and _sane(full.surface, traced_corners, to_mosaic, diag, p):
            curved = full
    surface = curved.surface
    improvement = 1 - _cost(curved, traced) / max(_cost(planar, traced), 1e-9)
    lift = surface.max_lift()
    reason = None
    if p.mode != "force":
        if lift < p.min_lift:
            reason = "flat"
        elif improvement < p.min_improvement:
            reason = "small_gain"
    return CurvatureFit(
        surface if reason is None else None,
        _cost(planar, traced),
        _cost(curved, traced),
        planar1.outline_rms if not n_lines else planar.outline_rms,
        curved.outline_rms,
        reason,
        n_lines,
        best=surface,
        planar=planar.surface,
        bows=bows,
        outline=outline,
    )


def _cost(fit: SurfaceFit, traced: int) -> float:
    """The cost the flat and bent fits are compared on.

    With the outline traced on three or four sides, it alone decides: the
    lines then only refine the bend's shape, and their measurement noise,
    which is the same under either model, would water the comparison down.
    With less of the outline seen, the lines are needed to decide too.
    """
    return fit.outline_cost if traced >= 3 else fit.cost


def _sane(s: PageSurface, traced_corners, to_mosaic, diag: float, p: CurvatureParams) -> bool:
    """A fit is usable when its profile does not fold back and its corners lie on the traced outline."""
    if np.abs(s.theta_at(np.linspace(0, 1, 201))).max() >= FOLD:
        return False
    corners = cv2.perspectiveTransform(s.project(np.array([0, 1, 1, 0.0]), np.array([0, 0, 1, 1.0])).reshape(-1, 1, 2), to_mosaic).reshape(4, 2)
    for c, t in zip(corners, traced_corners):
        if t is not None and np.linalg.norm(c - t) > p.max_corner_shift * diag:
            return False
    return bool(np.all(np.isfinite(corners)))


# --- Flattening --------------------------------------------------------------


def grid_shape(size: tuple[int, int], step: int) -> tuple[int, int]:
    """Nodes (rows, cols) of a coarse map over ``size`` (w, h): every ``step`` px, covering the last pixel."""
    W, H = size
    return (H - 1) // step + 2, (W - 1) // step + 2


def page_fractions(size: tuple[int, int], inset: float, step: int) -> tuple[np.ndarray, np.ndarray]:
    """Page fractions at the coarse nodes of an output of ``size``, through the inset trim.

    As in the flat path, the page's edges sit ``inset`` of the size outside
    the output rectangle, so a sliver of background at the edge is trimmed.
    """
    W, H = size
    gh, gw = grid_shape(size, step)
    U, V = np.meshgrid(np.arange(gw) * step, np.arange(gh) * step)
    dx, dy = inset * W, inset * H
    return (U + dx) / (W - 1 + 2 * dx), (V + dy) / (H - 1 + 2 * dy)


def output_size(surface: PageSurface, max_side: int) -> tuple[int, int]:
    """Composed page size (w, h): the flat part's resolution in the reference photo, capped.

    As in the flat path, the page gets the resolution of its best-resolved
    side; for the sides running across the binding only their flat half
    counts, since the bent strip is seen foreshortened.
    """
    t = np.linspace(0, 1, 101)
    h = surface.height
    half = np.linspace(0.5, 1, 51)
    lines = {
        "left": [((np.ones_like(t), t), h), ((half, np.zeros_like(half)), 0.5), ((half, np.ones_like(half)), 0.5)],
        "right": [((np.zeros_like(t), t), h), ((1 - half, np.zeros_like(half)), 0.5), ((1 - half, np.ones_like(half)), 0.5)],
        "top": [((t, np.ones_like(t)), 1.0), ((np.zeros_like(half), half), 0.5 * h), ((np.ones_like(half), half), 0.5 * h)],
        "bottom": [((t, np.zeros_like(t)), 1.0), ((np.zeros_like(half), 1 - half), 0.5 * h), ((np.ones_like(half), 1 - half), 0.5 * h)],
    }[surface.binding]
    density = 0.0
    for (fx, fy), length in lines:
        q = surface.project(fx, fy)
        density = max(density, float(np.linalg.norm(np.diff(q, axis=0), axis=1).sum()) / length)
    W, H = density, density * h
    k = min(1.0, max_side / max(W, H))
    return max(2, int(round(W * k))), max(2, int(round(H * k)))


def flatten_maps(surface: PageSurface, size: tuple[int, int], inset: float, H_ref_from_shot: list[np.ndarray], step: int = 8) -> list[np.ndarray]:
    """For each shot, where each coarse output node is in that shot: (rows, cols, 2) float32.

    Output pixel -> page fraction (with the inset) -> page surface ->
    reference photo -> shot, through the inverse of the shot's homography.
    """
    fx, fy = page_fractions(size, inset, step)
    ref = surface.project(fx.ravel(), fy.ravel()).reshape(-1, 1, 2)
    return [cv2.perspectiveTransform(ref, np.linalg.inv(H)).reshape(*fx.shape, 2).astype(np.float32) for H in H_ref_from_shot]


def shot_poses(surface: PageSurface, H_ref_from_shot: list[np.ndarray]) -> list[np.ndarray]:
    """Each shot's camera centre in the page plane's frame.

    The shot's homography maps the flat part of the page as the reference
    sees it, so with the reference pose and the same intrinsics it can be
    decomposed into the shot's own pose.
    """
    R, t = surface._camera()
    K = np.asarray(surface.K, np.float64)
    G_ref = K @ np.column_stack([R[:, 0], R[:, 1], t])
    centres = []
    for H in H_ref_from_shot:
        M = np.linalg.solve(K, np.linalg.inv(H) @ G_ref)
        lam = 2.0 / (np.linalg.norm(M[:, 0]) + np.linalg.norm(M[:, 1]))
        M = M * lam
        if M[2, 2] < 0:
            M = -M
        r1, r2, tt = M[:, 0], M[:, 1], M[:, 2]
        r3 = np.cross(r1, r2)
        U, _, Vt = np.linalg.svd(np.column_stack([r1, r2, r3]))
        Ri = U @ Vt
        centres.append(-Ri.T @ tt)
    return centres


def view_angles(surface: PageSurface, H_ref_from_shot: list[np.ndarray], grid: np.ndarray) -> np.ndarray:
    """Angle (degrees) between the surface normal and the ray to each shot's camera.

    ``grid`` is (n, 2) page fractions; returns (shots, n).
    """
    P = surface.plane_points(grid[:, 0], grid[:, 1])
    N = surface.normals(grid[:, 0], grid[:, 1])
    out = []
    for C in shot_poses(surface, H_ref_from_shot):
        ray = C[None, :] - P
        cos = np.abs((ray * N).sum(axis=1)) / np.maximum(np.linalg.norm(ray, axis=1), 1e-12)
        out.append(np.degrees(np.arccos(np.clip(cos, 0, 1))))
    return np.array(out)
