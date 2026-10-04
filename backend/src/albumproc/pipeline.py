"""Several photos of one album page in, one flat, cropped, glare-free page out."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .fuse import FuseParams, fuse
from .page import PageQuad, detect_pages, estimate_aspect, focal_px_from_35mm
from .register import register

PREVIEW_MAX_SIDE = 3000


@dataclass
class PageOptions:
    max_side: int = 8000  # cap on the output's long side, in pixels
    inset: float = 0.003  # trim this fraction from each edge to hide background slivers
    # 35 mm-equivalent focal length of the reference photo (from EXIF when
    # known). Used to recover the page's true proportions; 26 mm is typical of
    # a phone's main camera. None solves it from the page corners instead,
    # which is much noisier.
    focal_35mm: float | None = 26.0
    fuse: FuseParams = field(default_factory=FuseParams)


@dataclass
class PageReport:
    n_inputs: int
    reference: int
    used: list[int]
    dropped: list[int]
    pair_inliers: dict[str, int]
    page_method: str
    page_score: float
    aspect: float
    size: tuple[int, int]  # (w, h)
    coverage: float  # share of the page covered by at least one photo
    glare_fraction: dict[int, float]
    gains: dict[int, list[float]]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageResult:
    image: np.ndarray  # uint8 BGR
    report: PageReport
    corners_ref: np.ndarray  # page corners in the reference photo's full-res pixels


def _warp_all(images, homographies, size):
    """Warp each image by its homography into a frame of ``size`` (w, h)."""
    warped, masks = [], []
    for img, H in zip(images, homographies):
        h, w = img.shape[:2]
        warped.append(cv2.warpPerspective(img, H, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT))
        m = cv2.warpPerspective(np.full((h, w), 255, np.uint8), H, size, flags=cv2.INTER_NEAREST)
        masks.append(cv2.erode(m, np.ones((5, 5), np.uint8)) > 0)
    return warped, masks


def process_page(images: list[np.ndarray], options: PageOptions | None = None, debug_dir: str | Path | None = None) -> PageResult:
    opt = options or PageOptions()
    reg = register(images)
    used = reg.registered
    ref = reg.reference

    # 1. Low-res mosaic in the reference photo's frame, used to find the page.
    s_ref = reg.work_scales[ref]
    corners = []
    for i in used:
        h, w = images[i].shape[:2]
        c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        corners.append(cv2.perspectiveTransform(c, reg.homographies[i]).reshape(-1, 2))
    allc = np.concatenate(corners)
    x0, y0 = allc.min(axis=0)
    x1, y1 = allc.max(axis=0)
    c = min(1.0, PREVIEW_MAX_SIDE / max(x1 - x0, y1 - y0), s_ref * 1.5)
    to_canvas = np.array([[c, 0, -c * x0], [0, c, -c * y0], [0, 0, 1]])
    canvas_size = (int(np.ceil((x1 - x0) * c)), int(np.ceil((y1 - y0) * c)))
    pre_H = [to_canvas @ reg.homographies[i] for i in used]
    pre_w, pre_m = _warp_all([images[i] for i in used], pre_H, canvas_size)
    preview = fuse(pre_w, pre_m, params=opt.fuse)

    # Page candidates from the mosaic (the only view of a stitched page) and
    # from each photo on its own (sharper when one page fills the photo and a
    # neighbouring page is also in view), all in mosaic coordinates.
    pool = [(c, _touch(c.corners, preview.coverage)) for c in detect_pages(preview.image, preview.coverage)]
    for i, H in zip(used, pre_H):
        frame = np.ones(images[i].shape[:2], bool)
        for c in detect_pages(images[i]):
            if c.method == "fallback":
                continue
            mapped = cv2.perspectiveTransform(c.corners.reshape(-1, 1, 2), H).reshape(4, 2).astype(np.float32)
            pool.append((PageQuad(mapped, c.method, c.score), _touch(c.corners, frame)))
    quad = _choose_page(pool, [images[i].shape[:2] for i in used], pre_H)
    corners_ref = cv2.perspectiveTransform(quad.corners.reshape(-1, 1, 2), np.linalg.inv(to_canvas)).reshape(4, 2)

    # 2. Output rectangle: true aspect ratio, native resolution, capped.
    rh, rw = images[ref].shape[:2]
    focal = focal_px_from_35mm(opt.focal_35mm, rw, rh) if opt.focal_35mm else None
    aspect = estimate_aspect(corners_ref, (rw / 2, rh / 2), focal)
    tl, tr, br, bl = corners_ref
    long_px = max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl), np.linalg.norm(bl - tl), np.linalg.norm(br - tr))
    long_px = min(long_px, opt.max_side)
    if aspect >= 1:
        W, H = long_px, long_px / aspect
    else:
        W, H = long_px * aspect, long_px
    W, H = int(round(W)), int(round(H))
    dx, dy = opt.inset * W, opt.inset * H
    rect = np.float32([[-dx, -dy], [W - 1 + dx, -dy], [W - 1 + dx, H - 1 + dy], [-dx, H - 1 + dy]])
    P = cv2.getPerspectiveTransform(corners_ref.astype(np.float32), rect)

    # 3. Warp every full-res photo straight into the page rectangle (one
    #    resampling step), then fuse there.
    out_w, out_m = _warp_all([images[i] for i in used], [P @ reg.homographies[i] for i in used], (W, H))
    result = fuse(out_w, out_m, preview.tone_reference, opt.fuse)

    report = PageReport(
        n_inputs=len(images),
        reference=ref,
        used=used,
        dropped=reg.dropped,
        pair_inliers={f"{a}-{b}": k for (a, b), k in reg.pair_inliers.items()},
        page_method=quad.method,
        page_score=round(quad.score, 3),
        aspect=round(aspect, 4),
        size=(W, H),
        coverage=round(float(result.coverage.mean()), 4),
        glare_fraction={i: round(g, 4) for i, g in zip(used, result.glare_fraction)},
        gains={i: [round(float(x), 3) for x in g] for i, g in zip(used, result.gains)},
    )

    if debug_dir is not None:
        _write_debug(Path(debug_dir), preview, quad, to_canvas, result, used)

    return PageResult(result.image, report, corners_ref)


def _touch(q: np.ndarray, valid: np.ndarray) -> float:
    """Share of a quad's outline lying on or beyond the edge of ``valid``.

    A page cut off by the edge of what was photographed has a side there, so
    it looks like a smaller page unless this is taken into account.
    """
    border = cv2.distanceTransform(np.pad(valid, 1).astype(np.uint8), cv2.DIST_L2, 3)[1:-1, 1:-1]
    pts = np.concatenate([q[k] + np.linspace(0, 1, 50, endpoint=False)[:, None] * (q[(k + 1) % 4] - q[k]) for k in range(4)])
    xy = np.round(pts).astype(int)
    inside = (xy[:, 0] >= 0) & (xy[:, 1] >= 0) & (xy[:, 0] < valid.shape[1]) & (xy[:, 1] < valid.shape[0])
    d = np.zeros(len(pts))
    d[inside] = border[xy[inside, 1], xy[inside, 0]]
    return float((d < 0.01 * max(valid.shape)).mean())


def _choose_page(pool: list[tuple[PageQuad, float]], shapes: list[tuple[int, int]], to_canvas_H: list[np.ndarray]) -> PageQuad:
    """Pick the page the photos are of when more than one page is in view.

    Each candidate's detection score is weighted by how well it fits the
    photos: how much of it each photo shows (averaged over photos, cubed so
    that "shown whole in every photo" beats sheer size, since two pages side
    by side outscore one on area alone), and whether it is cut off by the
    edge of what was photographed.
    """
    frames = [
        cv2.perspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2), H).reshape(4, 2).astype(np.float32)
        for (h, w), H in zip(shapes, to_canvas_H)
    ]
    best, best_score = pool[0][0], -1.0
    for cand, touch in pool:
        q = cand.corners.astype(np.float32)
        area = cv2.contourArea(q)
        # A quad mapped from another photo can fold over under strong
        # perspective, and the overlap below is only defined for convex ones.
        if area <= 0 or not cv2.isContourConvex(q.reshape(-1, 1, 2)):
            continue
        visible = [min(1.0, cv2.intersectConvexConvex(f, q)[0] / area) for f in frames]
        score = cand.score * (1 - touch) ** 2 * float(np.mean(visible)) ** 3
        if score > best_score:
            best, best_score = cand, score
    return best


def _write_debug(d: Path, preview, quad, to_canvas, result, used):
    d.mkdir(parents=True, exist_ok=True)
    vis = preview.image.copy()
    cv2.polylines(vis, [quad.corners.round().astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), 3)
    cv2.imwrite(str(d / "preview_mosaic.jpg"), vis)
    for i, w in zip(used, result.weights_small):
        wn = w / max(float(w.max()), 1e-9)
        cv2.imwrite(str(d / f"weight_{i:02d}.png"), (wn * 255).astype(np.uint8))
