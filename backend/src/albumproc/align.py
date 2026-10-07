"""Aligning shots of a curved page where one homography per shot cannot.

Each shot's homography is fitted to the whole page, which on a page bent
near its binding means mostly to the flat part, so in the bent strip two
shots taken from different angles disagree by their parallax. After every
shot has been flattened through the page model, the remaining misalignment
is measured with matched features against the shots already aligned and
corrected with a smooth displacement field, which is folded into the shot's
flattening map so that the shot is still resampled once.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .curvature import AlignParams, bilinear


@dataclass
class ShotAlignment:
    shot: int  # position in the list of shots given to align_shots
    field: np.ndarray | None  # (grid, grid, 2), output px; None when skipped
    matches: int
    offset_before: float  # median misalignment against the aligned shots, output px
    offset_after: float
    skipped: str | None  # too_few_matches | reference


def _grid_pos(pts: np.ndarray, shape: tuple[int, int], grid: int) -> tuple[np.ndarray, np.ndarray]:
    """Fractional control-grid positions of image points (n, 2) in an image of ``shape``."""
    h, w = shape
    return pts[:, 0] / max(w - 1, 1) * (grid - 1), pts[:, 1] / max(h - 1, 1) * (grid - 1)


def field_at(field: np.ndarray, pts: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A displacement field (grid, grid, 2) over an image of ``shape``, sampled at points (n, 2)."""
    g = field.shape[0]
    gx, gy = _grid_pos(pts, shape, g)
    return bilinear(field, np.clip(gx, 0, g - 1), np.clip(gy, 0, g - 1)).astype(np.float64)


def _regulariser(grid: int) -> np.ndarray:
    """Thin-plate smoothness rows over the control grid: d2/dx2, d2/dy2 and the cross term."""
    rows = []
    idx = np.arange(grid * grid).reshape(grid, grid)
    for i in range(grid):
        for j in range(grid):
            if j + 2 < grid:
                r = np.zeros(grid * grid)
                r[[idx[i, j], idx[i, j + 1], idx[i, j + 2]]] = [1, -2, 1]
                rows.append(r)
            if i + 2 < grid:
                r = np.zeros(grid * grid)
                r[[idx[i, j], idx[i + 1, j], idx[i + 2, j]]] = [1, -2, 1]
                rows.append(r)
            if i + 1 < grid and j + 1 < grid:
                r = np.zeros(grid * grid)
                r[[idx[i, j], idx[i, j + 1], idx[i + 1, j], idx[i + 1, j + 1]]] = np.array([1, -1, -1, 1]) * np.sqrt(2)
                rows.append(r)
    return np.array(rows)


def fit_field(pts: np.ndarray, disp: np.ndarray, shape: tuple[int, int], p: AlignParams) -> np.ndarray:
    """Smooth field (grid, grid, 2) whose value at ``pts`` best matches ``disp``.

    Regularised least squares on bilinear control points: a thin-plate
    smoothness term, and a weak pull to zero that keeps the field at zero
    where there are no matches.
    """
    g = p.grid
    gx, gy = _grid_pos(pts, shape, g)
    x0 = np.clip(np.floor(gx).astype(int), 0, g - 2)
    y0 = np.clip(np.floor(gy).astype(int), 0, g - 2)
    fx, fy = np.clip(gx - x0, 0, 1), np.clip(gy - y0, 0, 1)
    A = np.zeros((len(pts), g * g))
    rows = np.arange(len(pts))
    for dy, dx, wgt in ((0, 0, (1 - fx) * (1 - fy)), (0, 1, fx * (1 - fy)), (1, 0, (1 - fx) * fy), (1, 1, fx * fy)):
        np.add.at(A, (rows, (y0 + dy) * g + x0 + dx), wgt)
    Rg = _regulariser(g)
    M = A.T @ A + p.smooth**2 * Rg.T @ Rg + p.zero**2 * np.eye(g * g)
    sol = np.linalg.solve(M, A.T @ disp)
    return sol.reshape(g, g, 2)


def _features(img: np.ndarray, mask: np.ndarray, sift) -> tuple[np.ndarray, np.ndarray | None]:
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    m = cv2.erode(mask.astype(np.uint8), np.ones((7, 7), np.uint8))
    kps, desc = sift.detectAndCompute(gray, m)
    return np.array([k.pt for k in kps], np.float64).reshape(-1, 2), desc


def align_shots(small: list[np.ndarray], masks: list[np.ndarray], order: list[int], p: AlignParams, scale: float) -> list[ShotAlignment]:
    """Displacement fields that align each shot with those before it in ``order``.

    ``small`` are the shots flattened into the composed page at low
    resolution (``scale`` low-res px per output px), ``masks`` their
    coverage, and ``order`` the order in which registration placed them,
    the reference first. Each shot is matched against every shot already
    aligned where they overlap, so a stitched shot that does not overlap the
    reference is aligned through its neighbours. A field ``d`` means the
    shot's content for output point ``x`` is taken from where it lies now,
    at ``x + d(x)``.
    """
    shape = small[0].shape[:2]
    long = max(shape)
    max_shift = p.max_shift * long
    sift = cv2.SIFT_create(nfeatures=6000)
    feats = [_features(im, m, sift) for im, m in zip(small, masks)]
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    out: list[ShotAlignment | None] = [None] * len(small)
    fields: dict[int, np.ndarray] = {}
    ref = order[0]
    out[ref] = ShotAlignment(ref, None, 0, 0.0, 0.0, "reference")
    aligned = [ref]
    for i in order[1:]:
        ki, di = feats[i]
        src, dst = [], []
        for j in aligned:
            kj, dj = feats[j]
            if di is None or dj is None or len(ki) < 2 or len(kj) < 2:
                continue
            good = [m for m, *r in matcher.knnMatch(di, dj, k=2) if r and m.distance < 0.75 * r[0].distance]
            if not good:
                continue
            a = ki[[m.queryIdx for m in good]]
            b = kj[[m.trainIdx for m in good]]
            if j in fields:  # where shot j's feature lies once j is corrected: b' + d_j(b') = b
                bb = b.copy()
                for _ in range(3):
                    bb = b - field_at(fields[j], bb, shape)
                b = bb
            close = np.linalg.norm(a - b, axis=1) < 2 * max_shift
            src.append(a[close])
            dst.append(b[close])
        a = np.concatenate(src) if src else np.zeros((0, 2))
        b = np.concatenate(dst) if dst else np.zeros((0, 2))
        if len(a) < p.min_matches:
            out[i] = ShotAlignment(i, None, int(len(a)), 0.0, 0.0, "too_few_matches")
            aligned.append(i)
            continue
        disp = a - b  # the shot shows what belongs at b at a
        f = fit_field(b, disp, shape, p)
        keep = np.linalg.norm(field_at(f, b, shape) - disp, axis=1) <= p.outlier_px
        if keep.sum() >= p.min_matches:
            a, b, disp = a[keep], b[keep], disp[keep]
            f = fit_field(b, disp, shape, p)
        mag = np.linalg.norm(f, axis=2, keepdims=True)
        f = f * np.minimum(1.0, max_shift / np.maximum(mag, 1e-12))
        fields[i] = f
        before = float(np.median(np.linalg.norm(disp, axis=1))) / scale
        after = float(np.median(np.linalg.norm(disp - field_at(f, b, shape), axis=1))) / scale
        out[i] = ShotAlignment(i, (f / scale).astype(np.float32), int(len(a)), round(before, 2), round(after, 2), None)
        aligned.append(i)
    return out


def apply_field(coarse_map: np.ndarray, field: np.ndarray, step: int, size: tuple[int, int]) -> np.ndarray:
    """Fold a displacement field (output px) into a shot's coarse flattening map.

    The map's node for output point ``x`` then reads the shot where the old
    map had ``x + d(x)``, so the correction costs no extra resampling.
    """
    W, H = size
    gh, gw = coarse_map.shape[:2]
    U, V = np.meshgrid(np.arange(gw, dtype=np.float64) * step, np.arange(gh, dtype=np.float64) * step)
    pts = np.stack([U.ravel(), V.ravel()], axis=1)
    d = field_at(field, pts, (H, W)).reshape(gh, gw, 2)
    return bilinear(coarse_map, (U + d[..., 0]) / step, (V + d[..., 1]) / step)
