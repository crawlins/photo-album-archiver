"""Glare found in one photo on its own, and the glare left in a composed page.

``fuse`` removes glare by comparing shots, which cannot see glare where only
one shot covers the page or where every shot has it in the same place. This
module finds glare in a single shot from what sleeve glare does to it.
Reflected light is white and adds on top of the print, so it

- raises the darkest colour channel above that of the surroundings (``veil``),
- washes colour out (``desat``),
- flattens the print's texture (``flat``), and
- clips the brightest part (``clip``).

The four features are combined by a logistic score with fixed weights (fitted
by ``tools/fit_glare.py``). Strong scores seed glare areas, which then grow
into connected weaker scores, so a soft veil is followed outward while sharp
white print borders, which never reach a seed, are left alone.

The weights are cautious. From one photo alone, faint sleeve sheen looks much
like pale print content, white borders and white paper, and a warning about
glare that is not there sends the user back to re-shoot a good page. So the
detector reports glare it is sure of and misses much of the faint kind; where
shots overlap, ``fuse`` already removes glare by comparison.

Everything runs on the grid ``fuse`` uses for its weights (long side at most
1000 px). Background estimates are medians over a window of a few percent of
the long side, computed so that what lies outside a shot's mask never leaks
in.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .fuse import WEIGHT_MAX_SIDE


NET_PATH = Path(__file__).parent / "models" / "glare_net.onnx"


@dataclass
class GlareParams:
    enabled: bool = True  # False skips detection; the report's fields stay, empty
    # "features": the hand-made features below with their logistic weights
    # (the default: it raises no false warnings on synthetic white pages);
    # "net": the learned detector (models/glare_net.onnx, trained by
    # tools/train_glare_net.py on real pages' overlap labels), which finds
    # more of the glare on real sleeved pages but flags white paper and
    # print borders on synthetic ones. Neither reaches the spec's targets.
    detector: str = "features"
    net_scale: float = 0.5  # the net runs on the detector's grid shrunk by this
    # Hysteresis on the net's score, chosen on three validation pages the net
    # was not trained on (0.9/0.5 kept false alarms under 0.1% there).
    net_seed: float = 0.9
    net_weak: float = 0.5
    net_min_seed_px: int = 3
    # Logistic weights from tools/fit_glare.py (synthetic cases with seeds
    # 101-112, clean class weighted 30:1 so that false alarms stay rare).
    # Flatness carried no evidence on its own once weights were held
    # non-negative, so it is computed for --debug but weighted 0.
    bias: float = -4.13
    w_veil: float = 7.5
    w_desat: float = 1.0
    w_flat: float = 0.0
    w_clip: float = 2.32
    background_frac: float = 0.06  # background window, fraction of the long side
    seed: float = 0.7  # hysteresis thresholds on the score
    weak: float = 0.3
    # Seeds narrower than this (grid px) are dropped. Wide enough to drop the
    # white border along a print (about 6 px on the grid), which otherwise
    # scores like glare against the dark print beside it.
    min_seed_px: int = 7
    adapt_bias: bool = True
    adapt_min_labels: int = 2000
    adapt_min_glare: int = 200
    adapt_max_shift: float = 1.5
    adapt_clean_weight: float = 30.0  # weight of clean labels over glare ones, as in the fit of the defaults


@dataclass
class GlareFeatures:
    veil: np.ndarray  # float32, 0..1 each
    desat: np.ndarray
    flat: np.ndarray
    clip: np.ndarray


@dataclass
class GlareMap:
    score: np.ndarray  # float32 0..1, raw score
    glare: np.ndarray  # float32 0..1, the score inside the grown areas, 0 elsewhere
    features: GlareFeatures | None  # None from the learned detector


def grid_scale(shape: tuple[int, ...]) -> float:
    """Scale from an image of ``shape`` to the detector's grid."""
    return min(1.0, WEIGHT_MAX_SIDE / max(shape[:2]))


def _masked_mean(x: np.ndarray, m: np.ndarray, k: int) -> np.ndarray:
    """Mean of ``x`` over a k x k box, counting only pixels inside ``m``."""
    mf = m.astype(np.float32)
    num = cv2.boxFilter(x * mf, -1, (k, k), normalize=True, borderType=cv2.BORDER_REFLECT)
    den = cv2.boxFilter(mf, -1, (k, k), normalize=True, borderType=cv2.BORDER_REFLECT)
    return num / np.maximum(den, 1e-6)


def _background(x: np.ndarray, m: np.ndarray, win: float, scale: float) -> np.ndarray:
    """Masked median of ``x`` over a window of ``win`` px.

    Computed on a coarser grid (a median over a large window is costly), on
    values quantized to ``x * scale`` in 0..255. Pixels outside ``m`` are
    first filled from the inside by normalized convolution, so the median
    never sees what is outside the mask.
    """
    h, w = x.shape
    k = max(1, int(win // 15))
    size = (max(1, -(-w // k)), max(1, -(-h // k)))
    mf = m.astype(np.float32)
    ms = cv2.resize(mf, size, interpolation=cv2.INTER_AREA)
    xs = cv2.resize(x * mf, size, interpolation=cv2.INTER_AREA) / np.maximum(ms, 1e-6)
    inside = ms > 0.5
    if not inside.any():
        return np.zeros_like(x)
    sigma = max(1.0, win / k)
    num = cv2.GaussianBlur(np.where(inside, xs, 0).astype(np.float32), (0, 0), sigma)
    den = cv2.GaussianBlur(inside.astype(np.float32), (0, 0), sigma)
    far = float(np.median(xs[inside]))
    filled = np.where(inside, xs, np.where(den > 1e-3, num / np.maximum(den, 1e-6), far))
    q = np.clip(filled * scale + 0.5, 0, 255).astype(np.uint8)
    ksize = max(3, int(round(win / k)) | 1)
    med = cv2.medianBlur(q, ksize).astype(np.float32) / scale
    return cv2.resize(med, (w, h), interpolation=cv2.INTER_LINEAR)


def glare_features(
    img_bgr: np.ndarray,
    mask: np.ndarray | None = None,
    untoned_bgr: np.ndarray | None = None,
    params: GlareParams | None = None,
) -> GlareFeatures:
    """The four glare features of a grid-sized image, 0 outside ``mask``.

    ``img_bgr`` is the tone-matched shot; ``untoned_bgr``, the shot as the
    camera wrote it, gives the clipping feature (the same image if omitted).
    """
    p = params or GlareParams()
    h, w = img_bgr.shape[:2]
    m = np.ones((h, w), bool) if mask is None else mask.astype(bool)
    mf = m.astype(np.float32)
    win = max(3.0, p.background_frac * max(h, w))

    mn = img_bgr.min(axis=2).astype(np.float32)
    veil = np.clip((mn - _background(mn, m, win, 1.0)) / 255.0, 0, 1)

    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    desat = np.clip(1 - (chroma + 2) / (_background(chroma, m, win, 1.0) + 2), 0, 1)

    L = lab[..., 0]
    mean = _masked_mean(L, m, 5)
    tex = np.sqrt(np.maximum(_masked_mean(L * L, m, 5) - mean * mean, 0))
    flat = np.clip(1 - (tex + 0.5) / (_background(tex, m, win, 4.0) + 0.5), 0, 1)

    raw = img_bgr if untoned_bgr is None else untoned_bgr
    clipped = (raw >= 250).sum(axis=2).astype(np.float32) / 3
    num = cv2.GaussianBlur(clipped * mf, (0, 0), 1.5)
    den = cv2.GaussianBlur(mf, (0, 0), 1.5)
    clip = np.clip(num / np.maximum(den, 1e-6), 0, 1)

    return GlareFeatures(*(np.where(m, f, 0).astype(np.float32) for f in (veil, desat, flat, clip)))


def _linear(f: GlareFeatures, p: GlareParams) -> np.ndarray:
    """The logistic score's argument without the bias."""
    return p.w_veil * f.veil + p.w_desat * f.desat + p.w_flat * f.flat + p.w_clip * f.clip


def glare_map(features: GlareFeatures, mask: np.ndarray | None, params: GlareParams | None = None, bias: float | None = None) -> GlareMap:
    """Score each pixel, then grow glare areas from strong seeds into weak scores."""
    p = params or GlareParams()
    b = p.bias if bias is None else bias
    m = np.ones(features.veil.shape, bool) if mask is None else mask.astype(bool)
    score = np.where(m, 1 / (1 + np.exp(-(b + _linear(features, p)))), 0).astype(np.float32)
    grown = hysteresis(score, p.seed, p.weak, p.min_seed_px)
    return GlareMap(score, np.where(grown, score, 0).astype(np.float32), features)


def hysteresis(score: np.ndarray, seed: float, weak: float, min_seed_px: int) -> np.ndarray:
    """Areas of ``score >= weak`` that hold a seed (``score >= seed``) at least ``min_seed_px`` wide."""
    seeds = (score >= seed).astype(np.uint8)
    if min_seed_px > 1:
        seeds = cv2.morphologyEx(seeds, cv2.MORPH_OPEN, np.ones((min_seed_px, min_seed_px), np.uint8))
    wk = (score >= weak).astype(np.uint8)
    _, labels = cv2.connectedComponents(wk, connectivity=8)
    keep = np.unique(labels[(seeds > 0) & (wk > 0)])
    return np.isin(labels, keep[keep > 0])


def detect_glare(img_bgr: np.ndarray, mask: np.ndarray | None = None, params: GlareParams | None = None, bias: float | None = None) -> GlareMap:
    """Glare in one photo on its own, on the detector's grid.

    A full-size photo is first shrunk so that its long side is at most 1000
    px; the maps come back at that size (see ``grid_scale``).
    """
    p = params or GlareParams()
    f = grid_scale(img_bgr.shape)
    if f < 1:
        h, w = img_bgr.shape[:2]
        size = (max(1, round(w * f)), max(1, round(h * f)))
        img_bgr = cv2.resize(img_bgr, size, interpolation=cv2.INTER_AREA)
        if mask is not None:
            mask = cv2.resize(mask.astype(np.float32), size, interpolation=cv2.INTER_AREA) >= 0.5
    if p.detector == "net":
        return net_map(img_bgr, mask, p)
    feats = glare_features(img_bgr, mask, None, p)
    return glare_map(feats, mask, p, bias)


_net_lock = threading.Lock()


@lru_cache(maxsize=None)
def _load_net(path: str):
    return cv2.dnn.readNetFromONNX(path)


def net_score(img_bgr: np.ndarray, mask: np.ndarray | None = None, params: GlareParams | None = None) -> np.ndarray:
    """The learned detector's glare probability for each pixel of a grid-sized shot, 0 outside ``mask``.

    The shot is taken as the camera wrote it (not tone-matched), black
    outside its mask as the net was trained, and is shrunk by ``net_scale``
    for the net; the score comes back at the shot's size.
    """
    p = params or GlareParams()
    h, w = img_bgr.shape[:2]
    m = np.ones((h, w), bool) if mask is None else mask.astype(bool)
    img = np.where(m[..., None], img_bgr, 0).astype(np.uint8)
    size = (max(1, round(w * p.net_scale)), max(1, round(h * p.net_scale)))
    small = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
    blob = small.astype(np.float32).transpose(2, 0, 1)[None] / 255.0 - 0.5
    net = _load_net(str(NET_PATH))
    with _net_lock:  # one net object, shared by every thread
        net.setInput(np.ascontiguousarray(blob, np.float32))
        logit = net.forward()[0, 0]
    prob = 1 / (1 + np.exp(-np.clip(logit, -30, 30)))
    prob = cv2.resize(prob.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    return np.where(m, prob, 0).astype(np.float32)


def net_map(img_bgr: np.ndarray, mask: np.ndarray | None = None, params: GlareParams | None = None) -> GlareMap:
    """The learned detector's score, and its glare areas grown from strong seeds into weak scores."""
    p = params or GlareParams()
    score = net_score(img_bgr, mask, p)
    grown = hysteresis(score, p.net_seed, p.net_weak, p.net_min_seed_px)
    return GlareMap(score, np.where(grown, score, 0).astype(np.float32), None)


def weak_threshold(p: GlareParams) -> float:
    """The score at which a shot counts as weakly glared, for the detector in use."""
    return p.net_weak if p.detector == "net" else p.weak


def adapt_bias(
    features: list[GlareFeatures],
    excess: list[np.ndarray],
    masks: list[np.ndarray],
    params: GlareParams,
    glare_flag: float = 0.08,
    clean: float = 0.02,
) -> tuple[float, bool]:
    """Refit the score's bias to this page's own glare, where shots overlap.

    Where two or more shots cover a pixel, the comparison with the darkest
    of them labels each shot's pixel as glare (excess above ``glare_flag``)
    or clean (below ``clean``). With enough labels the bias is moved, within
    ``adapt_max_shift`` of the default, to the value that best separates
    them (lowest logistic loss, clean labels weighted as in the fit of the
    defaults so that the page keeps the same caution); otherwise the default
    stays.
    Returns the bias and whether it was adapted.
    """
    p = params
    if not p.adapt_bias or len(features) < 2:
        return p.bias, False
    count = np.sum([m.astype(np.uint8) for m in masks], axis=0)
    zg, zc = [], []
    for f, ex, m in zip(features, excess, masks):
        both = m & (count >= 2)
        z = _linear(f, p)
        zg.append(z[both & (ex > glare_flag)])
        zc.append(z[both & (ex < clean)])
    zg, zc = np.concatenate(zg), np.concatenate(zc)
    if len(zg) + len(zc) < p.adapt_min_labels or len(zg) < p.adapt_min_glare or len(zc) == 0:
        return p.bias, False
    # Subsample deterministically so large overlaps stay cheap.
    zg, zc = zg[:: max(1, len(zg) // 20000)], zc[:: max(1, len(zc) // 20000)]
    grid = p.bias + np.linspace(-p.adapt_max_shift, p.adapt_max_shift, 61)
    loss = [np.logaddexp(0, -(b + zg)).mean() + p.adapt_clean_weight * np.logaddexp(0, b + zc).mean() for b in grid]
    return float(grid[int(np.argmin(loss))]), True


def combine(single: GlareMap, excess: np.ndarray, glare_flag: float, weak: float) -> np.ndarray:
    """One shot's glare map from its own score and the comparison with other shots.

    The comparison counts only where the shot's own score is at least weakly
    positive, so a shadow or a slight misalignment in another shot (which
    makes this one look relatively bright) is not taken for glare.
    """
    c = np.clip(excess / glare_flag, 0, 1)
    return np.maximum(single.glare, np.where(single.score >= weak, c, 0)).astype(np.float32)


def residual(glare: list[np.ndarray], weights: list[np.ndarray]) -> np.ndarray:
    """Share of each composed pixel that came from glared light: sum(w*g) / sum(w)."""
    num = np.zeros(weights[0].shape, np.float64)
    den = np.zeros(weights[0].shape, np.float64)
    for g, w in zip(glare, weights):
        num += w.astype(np.float64) * g
        den += w
    return np.where(den > 0, num / np.where(den > 0, den, 1), 0).astype(np.float32)
