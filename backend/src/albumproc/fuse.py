"""Glare-aware fusion of photos that are already warped into one frame.

Glare is light added on top of the print, so where shots disagree the darker
one is the more truthful. Each shot gets a per-pixel weight that falls off as
its (smoothed) brightness rises above the darkest shot covering that pixel,
times a feather that fades out towards the shot's edges so seams between
partial shots blend instead of showing a step.

Weights are computed at reduced resolution (they are smooth anyway) and then
upsampled, which keeps memory and time modest for 12 MP inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

WEIGHT_MAX_SIDE = 1000


@dataclass
class FuseParams:
    glare_tau: float = 0.03  # brightness excess (0..1) at which weight drops to 1/e
    glare_flag: float = 0.08  # excess counted as glare in the report
    feather_frac: float = 0.04  # feather width as a fraction of the frame's long side
    sharpness: float = 2.0  # weights ** sharpness: higher picks one shot, lower averages
    halo_px: int = 5  # widen each glare spot by this many (low-res) pixels
    glare_floor: float = 0.02  # excess below this is noise or shading, not glare
    smooth_frac: float = 0.008  # blur of the weight maps, fraction of the long side


@dataclass
class FuseResult:
    image: np.ndarray  # uint8 BGR
    coverage: np.ndarray  # bool, pixel covered by at least one shot
    gains: np.ndarray  # (n, 3) per-shot colour change at mid-grey (tone curve at 128 / 128)
    tone_reference: int  # index of the shot whose colours the others were matched to
    glare_fraction: list[float]  # share of each shot's covered area judged to be glare
    weights_small: list[np.ndarray]  # per-shot weights at reduced resolution
    # Intermediate values at the weights' resolution, for the glare detector.
    toned_small: list[np.ndarray] | None = None  # tone-matched shots, uint8 BGR
    small: list[np.ndarray] | None = None  # shots as taken, uint8 BGR
    masks_small: list[np.ndarray] | None = None  # eroded coverage masks
    excess_small: list[np.ndarray] | None = None  # brightness excess over the darkest shot, 0..1, before the halo


def _small(a: np.ndarray, f: float, interp=cv2.INTER_AREA) -> np.ndarray:
    if f >= 1:
        return a
    h, w = a.shape[:2]
    return cv2.resize(a, (max(1, round(w * f)), max(1, round(h * f))), interpolation=interp)


def _mode_ratio(r: np.ndarray) -> float:
    """Most common ratio, robust to glare (a one-sided tail) unlike the median."""
    lr = np.log(np.clip(r, 0.25, 4.0))
    hist, edges = np.histogram(lr, bins=280, range=(np.log(0.25), np.log(4.0)))
    hist = cv2.GaussianBlur(hist.astype(np.float32).reshape(1, -1), (0, 0), 2.0).ravel()
    k = int(np.argmax(hist))
    return float(np.exp((edges[k] + edges[k + 1]) / 2))


def _estimate_gains(imgs: list[np.ndarray], masks: list[np.ndarray], ref: int, iters: int = 3) -> np.ndarray:
    """Per-shot, per-channel gains that bring every shot to a common exposure."""
    n = len(imgs)
    g = np.ones((n, 3), np.float32)
    bright = [cv2.cvtColor(im, cv2.COLOR_BGR2HSV)[..., 2] > 235 for im in imgs]
    usable = [masks[i] & ~bright[i] for i in range(n)]
    acc = np.zeros(imgs[0].shape, np.float32)
    cnt = np.zeros(imgs[0].shape[:2], np.float32)
    for i in range(n):
        acc[usable[i]] += imgs[i][usable[i]].astype(np.float32)
        cnt[usable[i]] += 1
    # Gauss-Seidel: the reference stays at gain 1; each other shot is matched
    # to the current state of the *other* shots (comparing with itself would
    # pull its gain towards 1), and the running sum is updated at once.
    for _ in range(iters):
        for i in range(n):
            if i == ref:
                continue
            own = np.where(usable[i][..., None], imgs[i].astype(np.float32) * g[i], 0)
            others_cnt = cnt - usable[i]
            others = (acc - own) / np.maximum(others_cnt, 1)[..., None]
            m = usable[i] & (others_cnt > 0) & (others.min(axis=2) > 20) & (others.max(axis=2) < 235)
            if m.sum() < 500:
                continue
            ratio = others[m] / np.maximum(imgs[i][m].astype(np.float32), 1)
            g[i] = np.clip([_mode_ratio(ratio[:, ch]) for ch in range(3)], 0.6, 1.6)
            acc += np.where(usable[i][..., None], imgs[i].astype(np.float32) * g[i], 0) - own
    return g


def _fit_curve(x: np.ndarray, y: np.ndarray, gain: float) -> np.ndarray:
    """Monotone 256-entry lookup table mapping x to y, falling back to ``gain``."""
    lut = np.arange(256, dtype=np.float32) * gain
    edges = np.arange(0, 257, 8)
    idx = np.digitize(x, edges) - 1
    centers, values = [], []
    for b in range(len(edges) - 1):
        sel = idx == b
        if sel.sum() >= 50:
            centers.append(float(np.median(x[sel])))
            values.append(float(np.median(y[sel])))
    if len(centers) >= 3:
        fitted = np.interp(np.arange(256), centers, values)
        lo, hi = centers[0], centers[-1]
        # Outside the observed range keep the global gain's slope.
        ramp = np.arange(256)
        fitted[ramp < lo] = ramp[ramp < lo] * (values[0] / max(lo, 1))
        fitted[ramp > hi] = values[-1] + (ramp[ramp > hi] - hi) * gain
        lut = np.maximum.accumulate(fitted).astype(np.float32)
    return np.clip(lut, 0, 255)


def _estimate_tone(imgs: list[np.ndarray], masks: list[np.ndarray], ref: int) -> np.ndarray:
    """Per-shot, per-channel tone curves (n, 3, 256) matching every shot to ``ref``.

    A global gain first; then a curve fitted on pixels that agree with the
    other shots under that gain, which leaves glare out and absorbs the
    camera's non-linear tone mapping and highlight clipping.
    """
    n = len(imgs)
    g = _estimate_gains(imgs, masks, ref)
    luts = np.stack([np.stack([np.arange(256, dtype=np.float32) * g[i, ch] for ch in range(3)]) for i in range(n)])
    for i in range(n):
        if i == ref:
            continue
        acc = np.zeros(imgs[0].shape, np.float32)
        cnt = np.zeros(imgs[0].shape[:2], np.float32)
        for j in range(n):
            if j != i:
                acc[masks[j]] += imgs[j][masks[j]].astype(np.float32) * g[j]
                cnt[masks[j]] += 1
        others = acc / np.maximum(cnt, 1)[..., None]
        mine = imgs[i].astype(np.float32) * g[i]
        agree = masks[i] & (cnt > 0) & (np.abs(mine - others).max(axis=2) < 0.08 * 255 + 0.08 * others.max(axis=2))
        if agree.sum() < 2000:
            continue
        for ch in range(3):
            luts[i, ch] = _fit_curve(imgs[i][agree][:, ch].astype(np.float32), others[agree][:, ch], g[i, ch])
    return luts


def _apply_tone(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Apply a (3, 256) float LUT to a uint8 BGR image, returning float32."""
    return np.stack([lut[ch][img[..., ch]] for ch in range(3)], axis=-1)


def _photometric_reference(imgs: list[np.ndarray], masks: list[np.ndarray]) -> int:
    """Shot whose colours the others are matched to.

    Among the shots with (nearly) the fewest clipped highlights, the one of
    middling brightness: the darkest would make the whole page dull, the
    brightest has lost detail in the paper and white borders.
    """
    clipped = np.array([float((im[m].max(axis=1) >= 250).mean()) if m.any() else 1.0 for im, m in zip(imgs, masks)])
    ok = np.flatnonzero(clipped <= clipped.min() + 0.02)
    bright = [float(imgs[i][masks[i]].mean()) for i in ok]
    return int(ok[np.argsort(bright)[len(ok) // 2]])


def _smooth_weights(weights: list[np.ndarray], masks: list[np.ndarray], sigma: float) -> list[np.ndarray]:
    """Blur each shot's share of the total weight, so shots hand over gradually.

    Picking the darker shot pixel by pixel switches between shots wherever
    they differ by a little noise or shading, which leaves a blocky patchwork
    of slightly different tones. Blurring the shares (not the raw weights,
    whose scale varies by orders of magnitude) turns every switch into a
    ramp. Each shot's blur is normalised by its own coverage so it does not
    fade towards the edge of the photo.
    """
    total = np.maximum(np.sum(weights, axis=0), 1e-30)
    out = []
    for w, m in zip(weights, masks):
        mf = m.astype(np.float32)
        share = cv2.GaussianBlur(w / total * mf, (0, 0), sigma)
        cover = cv2.GaussianBlur(mf, (0, 0), sigma)
        out.append(np.where(m, share / np.maximum(cover, 1e-6) + 1e-8, 0).astype(np.float32))
    return out


def fuse(
    warped: list[np.ndarray],
    masks: list[np.ndarray],
    reference: int | None = None,
    params: FuseParams | None = None,
) -> FuseResult:
    p = params or FuseParams()
    n = len(warped)
    H, W = warped[0].shape[:2]
    f = min(1.0, WEIGHT_MAX_SIDE / max(H, W))

    small = [_small(im, f) for im in warped]
    small_m = [_small(m.astype(np.uint8), f, cv2.INTER_NEAREST) > 0 for m in masks]
    # Ignore a thin rim: interpolation smears the black outside into it.
    small_m = [cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0 for m in small_m]

    if reference is None:
        reference = _photometric_reference(small, small_m)
    luts = _estimate_tone(small, small_m, reference)

    # Smoothed luminance of each tone-matched shot; +inf where not covered.
    lum, toned_small = [], []
    for i in range(n):
        toned_small.append(np.clip(_apply_tone(small[i], luts[i]), 0, 255).astype(np.uint8))
        g = cv2.cvtColor(toned_small[i], cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g.astype(np.float32) / 255.0, (0, 0), 2.0)
        lum.append(np.where(small_m[i], g, np.inf))
    lum_min = np.min(lum, axis=0)

    sh, sw = small_m[0].shape
    feather_px = max(2.0, p.feather_frac * max(sh, sw))
    sigma = p.smooth_frac * max(sh, sw)
    # The halo also covers the reach of the blur below, so that smoothing a
    # weight map does not let a glare spot back in at its rim.
    r = p.halo_px + int(np.ceil(2 * sigma))
    halo = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    weights_small, glare_fraction, excess_small = [], [], []
    for i in range(n):
        m = small_m[i]
        excess = np.where(m, lum[i] - np.where(np.isfinite(lum_min), lum_min, 0), 0).astype(np.float32)
        excess_small.append(np.maximum(excess, 0))
        excess = cv2.dilate(np.maximum(excess, 0), halo)
        glare_w = np.exp(-np.maximum(excess - p.glare_floor, 0) / p.glare_tau)
        dist = cv2.distanceTransform(np.pad(m, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
        feather = np.clip(dist / feather_px, 1e-3, 1.0)
        w = np.where(m, (feather * glare_w) ** p.sharpness + 1e-8, 0).astype(np.float32)
        weights_small.append(w)
        glare_fraction.append(float((excess[m] > p.glare_flag).mean()) if m.any() else 0.0)
    if sigma > 0.5:
        weights_small = _smooth_weights(weights_small, small_m, sigma)

    acc = np.zeros((H, W, 3), np.float32)
    wsum = np.zeros((H, W), np.float32)
    for i in range(n):
        w = cv2.resize(weights_small[i], (W, H), interpolation=cv2.INTER_LINEAR) * masks[i]
        acc += _apply_tone(warped[i], luts[i]) * w[..., None]
        wsum += w
    coverage = wsum > 0
    out = acc / np.maximum(wsum, 1e-12)[..., None]
    out[~coverage] = 0
    gains = luts[:, :, 128] / 128.0
    return FuseResult(
        np.clip(out + 0.5, 0, 255).astype(np.uint8),
        coverage,
        gains,
        reference,
        glare_fraction,
        weights_small,
        toned_small,
        small,
        small_m,
        excess_small,
    )
