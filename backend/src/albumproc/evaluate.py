"""Compare a processed page against synthetic ground truth."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Score:
    mae: float  # mean absolute error (0..255) after aligning and matching colour
    glare_px: float  # share of pixels much brighter than the truth (residual glare)
    corner_err: float  # mean page-corner misplacement, % of page diagonal
    aspect_err: float  # relative error of width/height


def _align(out: np.ndarray, truth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Resize ``out`` to the truth's size and estimate where its corners were."""
    h, w = truth.shape[:2]
    res = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
    # Residual misplacement: fit a homography from truth to output by features.
    sift = cv2.SIFT_create(4000)
    g1 = cv2.cvtColor(truth, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(res, cv2.COLOR_BGR2GRAY)
    k1, d1 = sift.detectAndCompute(g1, None)
    k2, d2 = sift.detectAndCompute(g2, None)
    knn = cv2.BFMatcher().knnMatch(d1, d2, k=2)
    good = [m for m, *r in knn if r and m.distance < 0.75 * r[0].distance]
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    H, _ = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 3.0)
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    return res, cv2.perspectiveTransform(corners, H).reshape(4, 2)


@dataclass
class GeometryError:
    max: float  # largest distance of a matched feature from the best homography, fraction of the page width
    p95: float  # 95th percentile of the same
    matches: int


def geometry_error(out: np.ndarray, truth: np.ndarray, gross: float = 0.015, agree: float = 0.002) -> GeometryError:
    """Bend and squeeze left in ``out`` that no flat-page model could explain.

    SIFT features of the output (resized to the truth's size) are matched
    against the truth, the best homography between them is fitted, and the
    distances of the matches from it are measured as a fraction of the page
    width. A homography absorbs any error a plane-to-plane mapping could
    make (corners, proportions, perspective), so what remains is the error a
    curved page leaves behind. That error varies smoothly over the page, and
    a mismatched feature does not: a match is kept only when its error is
    within ``agree`` of the median error of its nearest neighbours, and
    under ``gross``.
    """
    h, w = truth.shape[:2]
    res = cv2.resize(out, (w, h), interpolation=cv2.INTER_AREA)
    sift = cv2.SIFT_create(6000)
    k1, d1 = sift.detectAndCompute(cv2.cvtColor(truth, cv2.COLOR_BGR2GRAY), None)
    k2, d2 = sift.detectAndCompute(cv2.cvtColor(res, cv2.COLOR_BGR2GRAY), None)
    knn = cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2)
    good = [m for m, *r in knn if r and m.distance < 0.7 * r[0].distance]
    src = np.float32([k1[m.queryIdx].pt for m in good])
    dst = np.float32([k2[m.trainIdx].pt for m in good])
    H, _ = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 0.003 * w, maxIters=10000, confidence=0.999)
    ok = np.ones(len(src), bool)
    for _ in range(3):  # refit on every match that agrees with its neighbours, not just the flat part's
        err = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2) - dst
        dist = np.linalg.norm(src[:, None] - src[None], axis=2)
        near = np.argsort(dist, axis=1)[:, 1:9]
        med = np.median(err[near], axis=1)
        ok = (np.linalg.norm(err - med, axis=1) < agree * w) & (np.linalg.norm(err, axis=1) < gross * w)
        H, _ = cv2.findHomography(src[ok], dst[ok], 0)
    d = np.linalg.norm(cv2.perspectiveTransform(src[ok].reshape(-1, 1, 2), H).reshape(-1, 2) - dst[ok], axis=1) / w
    return GeometryError(round(float(d.max()), 5), round(float(np.percentile(d, 95)), 5), int(len(d)))


def _strip_mask(shape: tuple[int, int], binding: str, strip: float) -> np.ndarray:
    """The ``strip`` of the page next to ``binding``, as a bool mask."""
    h, w = shape
    m = np.zeros((h, w), bool)
    k = int(round(strip * (w if binding in ("left", "right") else h)))
    if binding == "left":
        m[:, :k] = True
    elif binding == "right":
        m[:, w - k :] = True
    elif binding == "top":
        m[:k] = True
    else:
        m[h - k :] = True
    return m


def strip_mae(out: np.ndarray, truth: np.ndarray, binding: str, strip: float) -> tuple[float, float]:
    """The aligned mean absolute error of ``score``, in the bent strip and on the rest of the page."""
    a, b, m = _aligned(out, truth)
    err = np.abs(a - b).mean(axis=2)
    s = _strip_mask(truth.shape[:2], binding, strip)[m:-m, m:-m]
    return round(float(err[s].mean()), 2), round(float(err[~s].mean()), 2)


def _aligned(out: np.ndarray, truth: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Output and truth aligned, blurred, gain-matched and cropped by the returned margin."""
    h, w = truth.shape[:2]
    res, corners = _align(out, truth)
    ideal = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    # Undo small misplacement before measuring pixel error, so this measures
    # cleanliness, not geometry (geometry is reported separately).
    Hfix = cv2.getPerspectiveTransform(corners.astype(np.float32), ideal)
    res = cv2.warpPerspective(res, Hfix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    a = cv2.GaussianBlur(res, (0, 0), 1.5).astype(np.float32)
    b = cv2.GaussianBlur(truth, (0, 0), 1.5).astype(np.float32)
    m = int(0.02 * min(w, h))
    a, b = a[m:-m, m:-m], b[m:-m, m:-m]
    gain = (a * b).sum(axis=(0, 1)) / np.maximum((a * a).sum(axis=(0, 1)), 1)
    return a * gain, b, m


def score(out: np.ndarray, truth: np.ndarray) -> Score:
    h, w = truth.shape[:2]
    res, corners = _align(out, truth)
    ideal = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    corner_err = float(np.linalg.norm(corners - ideal, axis=1).mean() / np.hypot(w, h) * 100)
    oh, ow = out.shape[:2]
    aspect_err = abs((ow / oh) / (w / h) - 1)

    # Undo small misplacement before measuring pixel error, so this measures
    # cleanliness, not geometry (geometry is reported separately above).
    Hfix = cv2.getPerspectiveTransform(corners.astype(np.float32), ideal)
    res = cv2.warpPerspective(res, Hfix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    a = cv2.GaussianBlur(res, (0, 0), 1.5).astype(np.float32)
    b = cv2.GaussianBlur(truth, (0, 0), 1.5).astype(np.float32)
    m = int(0.02 * min(w, h))
    a, b = a[m:-m, m:-m], b[m:-m, m:-m]
    gain = (a * b).sum(axis=(0, 1)) / np.maximum((a * a).sum(axis=(0, 1)), 1)
    a *= gain
    mae = float(np.abs(a - b).mean())
    la, lb = a.mean(axis=2), b.mean(axis=2)
    glare_px = float(((la - lb) > 35).mean())
    return Score(round(mae, 2), round(glare_px, 4), round(corner_err, 3), round(aspect_err, 4))


def residual_truth(out: np.ndarray, truth: np.ndarray, levels: float = 20) -> np.ndarray:
    """Pixels of ``out`` at least ``levels`` brighter than the ground truth.

    The truth is aligned to the output as ``score`` does it and its colours
    matched by a robust per-channel gain, so what remains brighter is glare
    left in the composed page. Returns a bool mask the size of ``out``.
    """
    oh, ow = out.shape[:2]
    h, w = truth.shape[:2]
    res, corners = _align(out, truth)
    ideal = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    # Truth warped into the (resized) output's frame.
    Hm = cv2.getPerspectiveTransform(ideal, corners.astype(np.float32))
    tw = cv2.warpPerspective(truth, Hm, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    a = cv2.GaussianBlur(res, (0, 0), 1.5).astype(np.float32)
    b = cv2.GaussianBlur(tw, (0, 0), 1.5).astype(np.float32)
    ok = (a.min(axis=2) > 5) & (b.min(axis=2) > 5)
    gain = np.array([np.median(b[..., c][ok] / a[..., c][ok]) for c in range(3)], np.float32)
    diff = (a * gain).mean(axis=2) - b.mean(axis=2)
    mask = (diff >= levels).astype(np.uint8)
    return cv2.resize(mask, (ow, oh), interpolation=cv2.INTER_NEAREST) > 0
