"""Glare labels for real album pages, from the overlap between shots.

Not installed with the package. Run from ``backend/``:

    python tools/real_glare_data.py PAGE_DIR... --out DIR

Each ``PAGE_DIR`` holds the shots of one page (``*.jpg``, as in ``real/``
of the private photo-album-archiver-data repository). The page goes
through ``process_page`` with the glare detector off; every shot, warped
into the page frame on the fusion's grid, is saved to ``DIR/<page>.npz`` with a label per pixel (``LABEL_*``).

Sleeve glare is light added on top of the print and it moves when the phone
moves, so where several shots cover a pixel the darker ones show the print.
A shot is labelled glare where it is clearly brighter than a low percentile
of the covering shots, and clean where it is close to it. A low percentile
rather than the darkest shot keeps one shot's shadow or misalignment from
making every other shot look glared. Pixels covered by one shot, or in
between the two thresholds, are left unlabelled.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from albumproc import pipeline  # noqa: E402
from albumproc.glare import GlareParams  # noqa: E402

LABEL_CLEAN, LABEL_GLARE, LABEL_UNKNOWN = 0, 1, 255


def overlap_labels(
    toned: list[np.ndarray],
    masks: list[np.ndarray],
    glare: float = 0.08,
    clean: float = 0.02,
    percentile: float = 25.0,
    min_shots: int = 2,
    shift_px: int = 3,
    relight: bool = True,
) -> list[np.ndarray]:
    """Per-shot glare labels (uint8, ``LABEL_*``) from the comparison of overlapping shots.

    ``toned`` are the tone-matched shots on one grid, ``masks`` where each
    covers the page. A shot's luminance (0..1, lightly smoothed) is compared
    with a low ``percentile`` of the luminance of all shots covering that
    pixel; with two shots this is the darker one.

    Shots never line up exactly (a page is not flat, registration is not
    perfect), so beside every sharp print edge one shot looks brighter than
    the others by a few pixels' shift. A shot is therefore only labelled
    glare where it is brighter than the brightest reference within
    ``shift_px``; a veil of glare is wider than that, an edge's shift is not.

    Tone matching is one curve per shot, but the light falls off across the
    page differently in each shot (the phone moves, the lamp does not), which
    leaves one shot a little brighter over a whole side of the page. With
    ``relight`` each shot's brightness is first brought to the reference by
    a smooth (quadratic) gain fitted where it is not much brighter, so that
    only light added locally, as glare is, remains.
    """
    lum = []
    for t, m in zip(toned, masks):
        g = cv2.cvtColor(t, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        lum.append(cv2.GaussianBlur(g, (0, 0), 2.0))
    masks = [m.astype(bool) for m in masks]
    count = np.sum([m.astype(np.uint8) for m in masks], axis=0)
    many = count >= min_shots
    ref = _low_percentile(lum, masks, count, percentile)
    if relight and many.any():
        for _ in range(2):
            lum = [_relight(li, ref, m & many, glare) for li, m in zip(lum, masks)]
            ref = _low_percentile(lum, masks, count, percentile)
    k = 2 * shift_px + 1
    ref_hi = cv2.dilate(ref, np.ones((k, k), np.uint8)) if shift_px > 0 else ref
    labels = []
    for li, m in zip(lum, masks):
        lab = np.full(m.shape, LABEL_UNKNOWN, np.uint8)
        sel = m & many
        lab[sel & (li - ref_hi >= glare)] = LABEL_GLARE
        lab[sel & (li - ref <= clean)] = LABEL_CLEAN
        labels.append(lab)
    return labels


def _low_percentile(lum: list[np.ndarray], masks: list[np.ndarray], count: np.ndarray, q: float) -> np.ndarray:
    """Per pixel, the ``q``-th percentile (lower value, no interpolation) of the shots covering it; 0 where none does."""
    stack = np.sort(np.stack([np.where(m, li, np.inf) for li, m in zip(lum, masks)]), axis=0)
    n = count.astype(np.intp)  # uint8 arithmetic wraps where no shot covers a pixel
    idx = np.clip(np.floor(q / 100.0 * (n - 1)), 0, len(lum) - 1).astype(np.intp)
    out = np.take_along_axis(stack, idx[None], axis=0)[0]
    return np.where(count > 0, out, 0).astype(np.float32)


def _relight(li: np.ndarray, ref: np.ndarray, sel: np.ndarray, glare: float, step: int = 4) -> np.ndarray:
    """``li`` times a smooth gain (exp of a quadratic in x, y) that brings it to ``ref`` over ``sel``.

    Fitted on a sparse grid of pixels by least squares on the log ratio,
    leaving out pixels much brighter than the reference (glare) and dark
    ones (noisy ratios), refitted once without the outliers of the first fit.
    """
    h, w = li.shape
    yy, xx = np.mgrid[0:h:step, 0:w:step]
    a, r, s = li[::step, ::step], ref[::step, ::step], sel[::step, ::step]
    use = s & (r > 0.1) & (a - r < glare)
    if use.sum() < 200:
        return li
    x, y = xx[use] / w - 0.5, yy[use] / h - 0.5
    A = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], 1)
    z = np.log((a[use] + 0.02) / (r[use] + 0.02))
    keep = np.ones(len(z), bool)
    for _ in range(2):
        c, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        res = z - A @ c
        keep = np.abs(res) < max(3 * float(np.median(np.abs(res[keep]))), 0.01)
    Y, X = np.mgrid[0:h, 0:w]
    X, Y = X / w - 0.5, Y / h - 0.5
    # Clipped: far outside the fitted area a quadratic runs away.
    gain = np.exp(-np.clip(c[0] + c[1] * X + c[2] * Y + c[3] * X * X + c[4] * X * Y + c[5] * Y * Y, -0.7, 0.7))
    return ((li + 0.02) * gain - 0.02).astype(np.float32)


def clean_labels(labels: np.ndarray, mask: np.ndarray, rim: int = 4) -> np.ndarray:
    """Overlap labels with the unreliable ones set to unknown.

    Near the edge of a shot the warp smears the black outside in, and thin
    glare lines are mostly a shot slightly misaligned against a sharp print
    edge, not glare; both are dropped.
    """
    out = labels.copy()
    inner = cv2.erode(mask.astype(np.uint8), np.ones((2 * rim + 1, 2 * rim + 1), np.uint8)) > 0
    out[~inner] = LABEL_UNKNOWN
    glare = (labels == LABEL_GLARE).astype(np.uint8)
    solid = cv2.morphologyEx(glare, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
    out[(glare > 0) & ~solid] = LABEL_UNKNOWN
    return out


def page_data(images: list[np.ndarray]) -> dict:
    """The shots of one page on the fusion's grid, with their overlap labels."""
    captured = {}
    fuse = pipeline.fuse

    def keep(*a, **k):
        captured["r"] = fuse(*a, **k)
        return captured["r"]

    pipeline.fuse = keep
    try:
        res = pipeline.process_page(images, pipeline.PageOptions(glare=GlareParams(enabled=False)))
    finally:
        pipeline.fuse = fuse
    r = captured["r"]
    labels = overlap_labels(r.toned_small, r.masks_small)
    grid = r.masks_small[0].shape
    page = cv2.resize(res.image, grid[::-1], interpolation=cv2.INTER_AREA)
    return dict(
        small=np.stack(r.small),
        toned=np.stack(r.toned_small),
        masks=np.stack(r.masks_small),
        excess=np.stack(r.excess_small),
        weights=np.stack(r.weights_small),
        labels=np.stack(labels),
        used=np.array(res.report.used),
        size=np.array(res.report.size),
        page=page,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pages", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for d in a.pages:
        dst = a.out / f"{d.name}.npz"
        if dst.exists():
            print(d.name, "cached")
            continue
        images = [cv2.imread(str(f)) for f in sorted(d.glob("*.jpg"))]
        data = page_data(images)
        np.savez_compressed(dst, **data)
        lab = data["labels"]
        print(d.name, f"{len(data['used'])}/{len(images)} shots", f"glare {(lab == LABEL_GLARE).mean():.4f}", f"clean {(lab == LABEL_CLEAN).mean():.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
