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
