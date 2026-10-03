"""Feature-based registration of several photos of the same album page.

Every photo is mapped into the pixel frame of one *reference* photo with a
planar homography. Photos do not all need to overlap the reference: they are
chained through whichever neighbours they overlap best, which is what lets an
oversized page be stitched from partial shots.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

WORK_MAX_SIDE = 1600
MIN_INLIERS = 25


@dataclass
class _Features:
    scale: float  # full-res -> work-res
    size: tuple[int, int]  # (w, h) at work resolution
    keypoints: np.ndarray  # (n, 2) float32, work-res coords
    descriptors: np.ndarray | None


@dataclass
class Registration:
    reference: int
    # Maps full-res pixels of photo i to full-res pixels of the reference
    # photo; None for photos that could not be registered.
    homographies: list[np.ndarray | None]
    # Same mapping at work resolution (photo i work-res -> reference work-res).
    work_homographies: list[np.ndarray | None]
    work_scales: list[float]
    pair_inliers: dict[tuple[int, int], int] = field(default_factory=dict)

    @property
    def registered(self) -> list[int]:
        return [i for i, h in enumerate(self.homographies) if h is not None]

    @property
    def dropped(self) -> list[int]:
        return [i for i, h in enumerate(self.homographies) if h is None]


def _scale_matrix(s: float) -> np.ndarray:
    return np.diag([s, s, 1.0])


def _extract(img: np.ndarray, sift: cv2.SIFT) -> _Features:
    h, w = img.shape[:2]
    s = min(1.0, WORK_MAX_SIDE / max(h, w))
    small = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    # Local contrast boost helps on faded prints and inside glare halos.
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    kps, desc = sift.detectAndCompute(gray, None)
    pts = np.array([k.pt for k in kps], dtype=np.float32).reshape(-1, 2)
    return _Features(s, (small.shape[1], small.shape[0]), pts, desc)


def _plausible(H: np.ndarray, src_size: tuple[int, int], dst_size: tuple[int, int]) -> bool:
    """Reject homographies that fold, flip or wildly rescale the photo."""
    w, h = src_size
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    warped = cv2.perspectiveTransform(corners, H).reshape(-1, 2)
    if not cv2.isContourConvex(warped.astype(np.float32)):
        return False
    area = cv2.contourArea(warped)
    ratio = area / float(dst_size[0] * dst_size[1])
    return 0.02 < ratio < 50


def _match_pair(a: _Features, b: _Features, matcher) -> tuple[np.ndarray | None, int]:
    """Homography mapping a -> b at work resolution, and its inlier count."""
    if a.descriptors is None or b.descriptors is None or len(a.keypoints) < 8 or len(b.keypoints) < 8:
        return None, 0
    knn = matcher.knnMatch(a.descriptors, b.descriptors, k=2)
    good = [m for m, *rest in knn if rest and m.distance < 0.75 * rest[0].distance]
    if len(good) < MIN_INLIERS:
        return None, 0
    src = a.keypoints[[m.queryIdx for m in good]]
    dst = b.keypoints[[m.trainIdx for m in good]]
    H, mask = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 3.0, maxIters=10000, confidence=0.999)
    if H is None:
        return None, 0
    n = int(mask.sum())
    if n < MIN_INLIERS or not _plausible(H, a.size, b.size):
        return None, 0
    return H, n


def register(images: list[np.ndarray]) -> Registration:
    n = len(images)
    if n == 0:
        raise ValueError("no images given")
    sift = cv2.SIFT_create(nfeatures=8000)
    feats = [_extract(img, sift) for img in images]
    matcher = cv2.BFMatcher(cv2.NORM_L2)

    # Pairwise homographies (both directions) for every pair that overlaps.
    H_pair: dict[tuple[int, int], np.ndarray] = {}
    inliers: dict[tuple[int, int], int] = {}
    for i in range(n):
        for j in range(i + 1, n):
            H, k = _match_pair(feats[i], feats[j], matcher)
            if H is not None:
                H_pair[(i, j)] = H
                H_pair[(j, i)] = np.linalg.inv(H)
                inliers[(i, j)] = inliers[(j, i)] = k

    # Reference: the best-connected photo (for a single full-page shot set,
    # this is the one that overlaps the others most).
    strength = [sum(k for (a, _), k in inliers.items() if a == i) for i in range(n)]
    ref = int(np.argmax(strength))

    # Maximum spanning tree from the reference (Prim), chaining homographies.
    work_H: list[np.ndarray | None] = [None] * n
    work_H[ref] = np.eye(3)
    placed = {ref}
    while True:
        best = None
        for (a, b), k in inliers.items():
            if a not in placed and b in placed and (best is None or k > best[2]):
                best = (a, b, k)
        if best is None:
            break
        a, b, _ = best
        work_H[a] = work_H[b] @ H_pair[(a, b)]
        placed.add(a)

    full_H: list[np.ndarray | None] = []
    s_ref = feats[ref].scale
    for i in range(n):
        if work_H[i] is None:
            full_H.append(None)
            continue
        H = np.linalg.inv(_scale_matrix(s_ref)) @ work_H[i] @ _scale_matrix(feats[i].scale)
        full_H.append(H / H[2, 2])

    pairs = {k: v for k, v in inliers.items() if k[0] < k[1]}
    return Registration(ref, full_H, work_H, [f.scale for f in feats], pairs)
