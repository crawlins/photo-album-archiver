"""Shadows cast on the page by the photographer's hand, arm, phone or head.

``fuse`` trusts the darker shot where shots disagree, because glare only adds
light. A shadow takes light away, so without help the shadowed shot wins and
the shadow is copied into the composed page. This module finds shadows so
that fusion can stop trusting the shot that has one, and so that the page
metadata can report the ones that are left.

Shadow and glare differ physically. A shadow scales the light reaching the
page: in linear light every channel of every pixel under it is multiplied by
the same ``k < 1``, so colour and relative texture are kept. Glare adds
roughly white light on top: about the same amount in every channel, which
raises dark channels proportionally more and washes colour out. Comparing
shot ``I`` with a reference ``R`` (the other covering shot, or the per-pixel
median of all covering shots) in small windows, ``I`` is called shadowed
where it is darker by at least the minimum depth and

- on coloured content, the per-channel ratios ``R_c / I_c`` are equal (as
  under a shadow) rather than spread the way added white light spreads them;
- on pale content with texture, the fit ``R = a*I + b`` has ``b ~ 0``
  (scaled) rather than ``b > 0`` (light added);
- on flat pale content, colour and texture cannot tell. With three or more
  shots the median is trusted, unless the soft edge of the difference lies
  in the other shots (glare in most of them) rather than in this one. With
  two, the edge alone decides, and only where it plainly lies in this shot.

That is the comparison cue (``comparison_shadow``). It also stays out of
dark content, where small errors of the tone match are large ratios.

Where only one shot covers the page, or every shot has the same shadow, the
single-image cue (``single_shadow``) looks for what a shadow does to one
photo: a dip in the page's illumination with a soft, achromatic edge that
keeps the texture of what lies under it. It is cautious and only reports.

Everything runs on the fusion grid (long side at most 1000 px).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np
from scipy.fft import dctn, idctn

from .glare import GlareParams, glare_features, glare_map


@dataclass
class ShadowParams:
    # The defaults were chosen with tools/fit_shadow.py (synthetic seeds 1-32
    # and the existing synthetic cases) and checked on the real pages of the
    # private data repository.
    # Off by default: on Clark's real album most shots carry the phone's
    # shadow somewhere, and pushing out the shots found shadowed lets in
    # another whose shadow comparison could not see, which made five of 19
    # pages worse (real/shadow-results.md in the data repository). Off gives
    # the composed page as without shadow handling, and an empty report.
    enabled: bool = False
    window_frac: float = 0.015  # comparison window, fraction of the grid's long side
    min_depth: float = 0.12  # shallower differences are not shadow
    depth_ramp: float = 0.10  # depth above min_depth for a full cue
    b_tol: float = 0.12  # allowed offset of the texture fit, fraction of mean(R)
    # Least spread of the per-channel log ratios that white light added to
    # the reference would give, for colour to tell shadow from glare; below
    # it the content counts as pale. An observed spread of half of it
    # already rules a shadow out.
    chroma_tol: float = 0.15
    # Least correlation of the two shots' texture in a window for the
    # texture fit to count; below it noise, not content, sets the slope.
    min_correlation: float = 0.6
    own_min: float = 0.3  # least share of a pale difference's soft edge lying in the shot itself (three or more shots)
    # The same with two shots, where no third says which of them is off: a
    # shadow's edge lies wholly in its shot (scores of 0.98 and more on the
    # synthetic pages), while sleeve glare in the other shot scored up to
    # 0.42 (tools/fit_shadow.py).
    own_min_two: float = 0.85
    own_local_min: float = 0.5  # with two shots, least share for the nearest stretch of edge too
    # With two shots, least depth of the area the edge test judges. At 0.12
    # a slight darkening of a portrait's backdrop on the real page page-two
    # was taken for a shadow once the tone was refitted.
    two_min_depth: float = 0.18
    # With two shots, how far (fraction of the long side) a shadow is kept
    # from an edge lying in the other shot: glare's soft tail. It covers the
    # glare veto's reach and fusion's halo around a shadow.
    two_edge_reach: float = 0.05
    # With two shots, how much brighter than this shot's own page just
    # outside the area the other shot may be: more is glare in it.
    two_level_tol: float = 0.25
    dark_level: float = 0.08  # luminance (linear) of the shot below which no shadow is called
    glare_reach: float = 0.03  # the glare veto reaches this far beyond plain glare, fraction of the long side
    seed: float = 0.7  # hysteresis thresholds on the cue, as for glare
    weak: float = 0.3
    min_seed_px: int = 5  # narrower seeds are dropped
    # Smaller shadows are dropped, fraction of the grid. A hand's or a phone's
    # shadow covers several percent of a page; on the real two-shot pages,
    # differences the size of the spec's 0.05% were sleeve sheen and tone.
    min_area: float = 0.002
    # Single-image cue.
    penumbra_frac: float = 0.005  # scale of a shadow's soft edge, fraction of the long side
    soft_ratio: float = 0.5  # coarse/fine gradient magnitude for a soft edge
    grad_chroma_tol: float = 0.35  # allowed difference of the channels' log gradients, fraction of the luminance's
    texture_tol: float = 0.5  # allowed log ratio of local texture across a soft edge
    edge_run: float = 0.6  # dips along this share of a page edge, deepest at it, are spine shading
    single_weight: float = 0.5  # single-image cue scale
    dip_min: float = 0.03  # least dip of the rebuilt log illumination below its envelope that is looked at
    # Share of a dip's rim with an accepted edge for a full cue. 0.12 finds
    # 23 of 30 single-shot shadows of depth 0.35 or more on the synthetic
    # pages (0.3 found 1) now that edges near glare are left out of the
    # rebuild (``glare_edge_reach``); in overlaps the report counts the cue
    # only where every covering shot shows it.
    edge_cover: float = 0.12
    # The same for a shot that alone covers part of the page, where the
    # report follows its cue without a second shot to agree. 0.12 there
    # warned on the real page page-two, over the darker backdrop of a
    # portrait; 0.15 does not.
    grow_edge_cover: float = 0.15
    # Edges with glare this near (fraction of the long side) on their bright
    # side are left out of the rebuilt illumination: the page around a glare
    # spot is no darker than elsewhere, but rebuilt with the spot's edges it
    # looks like a dip.
    glare_edge_reach: float = 0.03


@dataclass
class ComparisonShadow:
    """Per-shot shadow maps on the grid, float32 0..1, from comparing the shots."""

    cue: list[np.ndarray]  # the comparison cue with the glare veto (``s``)
    cue_raw: list[np.ndarray]  # without the veto (``s_raw``)
    shadow: list[np.ndarray]  # grown areas of ``cue``, 0 elsewhere: what fusion uses
    shadow_raw: list[np.ndarray]  # grown areas of ``cue_raw``: what the report counts

    def any(self) -> bool:
        return any(bool((s > 0).any()) for s in self.shadow)


_LIN = np.where(np.arange(256) / 255.0 <= 0.04045, np.arange(256) / 255.0 / 12.92, ((np.arange(256) / 255.0 + 0.055) / 1.055) ** 2.4).astype(np.float32)
_LUM = np.float32([0.0722, 0.7152, 0.2126])  # BGR weights of luminance, in linear light


def linear(img_bgr: np.ndarray) -> np.ndarray:
    """A uint8 sRGB image in linear light, float32 0..1."""
    return _LIN[img_bgr]


def _box(x: np.ndarray, k: int) -> np.ndarray:
    return cv2.boxFilter(x, -1, (k, k), normalize=True, borderType=cv2.BORDER_REFLECT)


def _ramp(depth: np.ndarray, start: float, p: ShadowParams) -> np.ndarray:
    return np.clip((depth - start) / p.depth_ramp, 0, 1)


def _reference(lin: list[np.ndarray], masks: list[np.ndarray], extra: list[np.ndarray] | None = None):
    """For each shot, what the other covering shots show: the other shot where
    one other covers, else the per-pixel median of every covering shot.

    The median includes the shot itself: with three or more covering it is
    still one of the clean values when one shot is shadowed or glared, and
    it is computed once for all shots. Of an even count's two middle values
    it takes the darker, not their mean: glare only adds light, so with two
    of four shots glared the mean is half glare and a clean shot looks
    shadowed against it, while a shadowed shot is still found against the
    darker of two clean ones. ``extra`` (one map, or a stack of maps, per
    shot) is combined as before. Returns, per shot, the reference image, the
    reference ``extra`` and the count of other covering shots.
    """
    n = len(lin)
    count = np.sum([m.astype(np.int32) for m in masks], axis=0)

    def median(stack: list[np.ndarray], lower: bool = False) -> np.ndarray:
        a = np.stack([np.where(m[(...,) + (None,) * (s.ndim - 2)], s, np.nan) for s, m in zip(stack, masks)]).astype(np.float32)
        a.sort(axis=0)  # NaNs last
        c = count[(...,) + (None,) * (a.ndim - 3)]
        lo = np.take_along_axis(a, np.maximum((c - 1) // 2, 0)[None], 0)[0]
        if lower:
            return np.nan_to_num(lo)
        hi = np.take_along_axis(a, np.maximum(c // 2, 0)[None], 0)[0]
        return np.nan_to_num((lo + hi) / 2)

    def total(stack: list[np.ndarray]) -> np.ndarray:
        return np.sum([np.where(m[(...,) + (None,) * (s.ndim - 2)], s, 0) for s, m in zip(stack, masks)], axis=0, dtype=np.float32)

    med = median(lin, lower=True) if n >= 3 else None
    tot = total(lin)
    med_x = median(extra) if extra is not None and n >= 3 else None
    tot_x = total(extra) if extra is not None else None
    out = []
    for i in range(n):
        others = count - masks[i]
        one = (others == 1)[..., None]
        ref = np.where(one, tot - np.where(masks[i][..., None], lin[i], 0), med if med is not None else 0)
        ref_x = None
        if extra is not None:
            ex = (...,) + (None,) * (extra[i].ndim - 2)
            ref_x = np.where((others == 1)[ex], tot_x - np.where(masks[i][ex], extra[i], 0), med_x if med_x is not None else 0)
        out.append((ref.astype(np.float32), ref_x, others))
    return out


def _edge_ownership(I: np.ndarray, R: np.ndarray, valid: np.ndarray, p: ShadowParams):
    """Where the soft edges of ``log(I / R)`` are, and how much of each lies in ``I``: (weight, own, sp).

    On flat pale content a darker ``I`` is either a shadow in ``I`` or glare
    in ``R``. The edge of the difference tells them apart: a shadow's soft
    edge is in ``I`` while ``R`` is flat there, and glare's edge is in ``R``
    while ``I`` is flat. ``own`` (0..1) is the share of the change carried
    by ``I``; ``weight`` is the change's strength, 0 where it does not count.
    """
    sp = max(1.0, p.penumbra_frac * max(I.shape[:2]) / 2)
    lI0 = cv2.log((I * _LUM).sum(axis=-1) + 0.005)
    lR0 = cv2.log((R * _LUM).sum(axis=-1) + 0.005)
    lI = cv2.GaussianBlur(lI0, (0, 0), sp)
    lR = cv2.GaussianBlur(lR0, (0, 0), sp)
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(np.ceil(2 * sp)) + 1,) * 2)

    def fine(x):  # the strongest gradient nearby at half the scale
        x = cv2.GaussianBlur(x, (0, 0), sp / 2)
        return cv2.dilate(np.hypot(cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)), disk)

    sharp = np.maximum(fine(lI0), fine(lR0))
    gIx, gIy = cv2.Sobel(lI, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(lI, cv2.CV_32F, 0, 1, ksize=3)
    gRx, gRy = cv2.Sobel(lR, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(lR, cv2.CV_32F, 0, 1, ksize=3)
    dx, dy = gIx - gRx, gIy - gRy
    w = dx * dx + dy * dy
    own = np.clip((gIx * dx + gIy * dy) / np.maximum(w, 1e-12), 0, 1)
    # Only where one of the shots is flat (at the content's own edges both
    # change, and a slight loss of contrast in either says nothing), and only
    # soft edges (glare in one shot can wash out a sharp edge of the content,
    # which then seems to belong to the other shot alone).
    flat_one = (np.minimum(np.hypot(gIx, gIy), np.hypot(gRx, gRy)) < 0.3 * np.sqrt(w)) & (np.sqrt(w) >= 0.75 * sharp)
    # Away from the edge of either shot's coverage, where the blur sees the black outside.
    inner = cv2.erode(valid.astype(np.uint8), np.ones((2 * int(np.ceil(3 * sp)) + 1,) * 2, np.uint8)) > 0
    return np.where(inner & flat_one, w, 0), own, sp


def _owned_by_shot(I: np.ndarray, R: np.ndarray, valid: np.ndarray, pending: np.ndarray, p: ShadowParams) -> np.ndarray:
    """1 over areas of ``pending`` whose edge lies in shot ``I`` rather than in ``R``, else 0.

    Each area of ``pending`` is scored by how much of the change of
    ``log(I / R)`` around it is carried by ``I`` (``_edge_ownership``),
    weighted by the change's strength.
    """
    w, own, sp = _edge_ownership(I, R, valid, p)
    return (_area_scores(pending, w, own, sp)[0] >= p.own_min).astype(np.float32)


def _area_scores(areas: np.ndarray, w: np.ndarray, own: np.ndarray, sp: float):
    """Each connected area's share of its surrounding edges lying in the shot: (score per pixel, labels)."""
    n, labels = cv2.connectedComponents(areas.astype(np.uint8), connectivity=8)
    r = int(np.ceil(4 * sp))
    band = cv2.dilate(labels.astype(np.float32), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))).astype(np.int32)
    num = np.bincount(band.ravel(), (w * own).ravel(), minlength=n)
    den = np.bincount(band.ravel(), w.ravel(), minlength=n)
    score = np.where(den > 0, num / np.maximum(den, 1e-12), 0)
    score[0] = 0
    return score[labels], labels


def _nearest(where: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Each pixel gets ``values`` at the nearest pixel of ``where`` (which must not be empty)."""
    _, near = cv2.distanceTransformWithLabels((~where).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(where)
    lut = np.zeros(near.max() + 1, values.dtype)
    lut[near[ys, xs]] = values[ys, xs]
    return lut[near]


def _owned_by_shot_two(I: np.ndarray, R: np.ndarray, valid: np.ndarray, pending: np.ndarray, core: np.ndarray, p: ShadowParams) -> np.ndarray:
    """``_owned_by_shot`` where only one other shot covers: stricter, and judged area by area and edge by edge.

    With two shots nothing else says which one is off, so an area counts
    only where its edges lie almost wholly in ``I`` (``own_min_two``).
    Areas are scored on their ``core`` (the difference deep enough to seed
    a shadow), and the shallower rim around it takes the verdict of the
    nearest core: scored on the rim, a faint wide difference (a sleeve's
    glare in ``R``) would borrow the verdict of a strong edge somewhere
    along it. Where a shadow in ``I`` runs into glare in ``R`` both are one
    area, so each pixel must also have its nearest stretch of edge mostly
    in ``I`` (``own_local_min``), lie beyond ``two_edge_reach`` of an edge
    in ``R``, and show ``R`` no brighter than ``I`` is where the two agree
    nearby (``two_level_tol``): taking ``R`` there must not let glare in.
    """
    w, own, sp = _edge_ownership(I, R, valid, p)
    cores = pending & core
    if not cores.any():
        return np.zeros(pending.shape, np.float32)
    score, _ = _area_scores(cores, w, own, sp)
    by_area = _nearest(cores, score >= p.own_min_two)
    sig = 2 * int(np.ceil(4 * sp))
    num, den = cv2.GaussianBlur(w * own, (0, 0), sig), cv2.GaussianBlur(w, (0, 0), sig)
    edges = (den > 0) & (den >= 0.05 * float(den[pending].max()))  # where a soft edge is near enough to judge by
    if not edges.any():
        return np.zeros(pending.shape, np.float32)
    local = np.where(edges, num / np.maximum(den, 1e-12), 0).astype(np.float32)
    by_edge = _nearest(edges, local) >= p.own_local_min
    # Nor near an edge that lies in ``R``: glare's soft tail there darkens
    # ``I`` by comparison, and taking ``R`` lets the glare in.
    theirs = edges & (local < p.own_local_min)
    if theirs.any():
        reach = p.two_edge_reach * max(pending.shape)
        by_edge &= cv2.distanceTransform((~theirs).astype(np.uint8), cv2.DIST_L2, 5) > reach
    # And no brighter in ``R`` than ``I`` is just outside the area: on flat
    # content a shadow takes ``I`` below the page's level and ``R`` shows
    # that level, while glare in ``R`` lifts it above.
    area = pending & by_area & by_edge
    if area.any() and p.two_level_tol > 0 and (~area & valid).any():
        yI = cv2.GaussianBlur((I * _LUM).sum(axis=-1), (0, 0), sp)
        yR = cv2.GaussianBlur((R * _LUM).sum(axis=-1), (0, 0), sp)
        ring = (cv2.dilate(pending.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * sig + 1,) * 2)) > 0) & ~pending & valid
        ring &= yI >= (1 - p.min_depth / 3) * yR  # where the shots agree: the page's own level
        if ring.any():
            area &= yR <= (1 + p.two_level_tol) * _nearest(ring, yI)
    return area.astype(np.float32)


def _window_stats(I: np.ndarray, R: np.ndarray, valid: np.ndarray, k: int):
    """Per-channel means of ``I`` and ``R`` over k x k windows of ``valid`` pixels, and their texture's variances and covariance summed over channels."""
    v = valid.astype(np.float32)
    den = np.maximum(_box(v, k), 1e-6)[..., None]
    v3 = v[..., None]

    def mean(x):
        return _box(x * v3, k) / den

    mI_c, mR_c = mean(I), mean(R)
    varI = np.maximum((mean(I * I) - mI_c * mI_c).sum(axis=-1), 0)
    varR = np.maximum((mean(R * R) - mR_c * mR_c).sum(axis=-1), 0)
    cov = (mean(I * R) - mI_c * mR_c).sum(axis=-1)
    return mI_c, mR_c, varI, varR, cov


def window_fit(I: np.ndarray, R: np.ndarray, valid: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The fit ``R = a*I + b`` in each k x k window, from the texture: (a, b, mean(R)).

    Each channel is centred on its own mean, so ``a`` comes from the
    content's texture and not from its colour, and ``a`` is the ratio of the
    texture's contrast in the two shots (a reduced major axis), which noise
    in both does not pull towards 0 as a least-squares slope would.
    ``b = mean(R) - a*mean(I)``: about 0 when ``I`` is ``R`` scaled (a
    shadow), positive when light was added to ``R`` (glare).
    """
    mI_c, mR_c, varI, varR, cov = _window_stats(I, R, valid, k)
    a = np.where(cov > 0, np.sqrt(varR / np.maximum(varI, 1e-6)), 0)
    mI, mR = mI_c.mean(axis=-1), mR_c.mean(axis=-1)
    return a, mR - a * mI, mR


def _spread(x: np.ndarray) -> np.ndarray:
    """Largest less smallest of the three channels."""
    return np.maximum(np.maximum(x[..., 0], x[..., 1]), x[..., 2]) - np.minimum(np.minimum(x[..., 0], x[..., 1]), x[..., 2])


def _cues(I: np.ndarray, R: np.ndarray, valid: np.ndarray, others: np.ndarray, glare_ref: np.ndarray | None, p: ShadowParams, k: int):
    """The comparison cue of shot ``I`` against reference ``R`` (both linear BGR): (s_raw, s_grow, veto).

    ``others`` counts the other shots covering each pixel; ``glare_ref`` is
    the reference's own glare score, widened by ``glare_reach`` and as is
    (two channels; None skips that check). ``s_grow`` starts at a third of
    the minimum depth, to follow the penumbra out to where the shadow
    fades. ``veto`` is the reference's glare score that holds back fusion
    (None without ``glare_ref``). See the module's docstring for the tests.
    """
    eps = 1e-6
    mI_c, mR_c, varI, varR, cov = _window_stats(I, R, valid, k)
    mI, mR = mI_c.mean(axis=-1), mR_c.mean(axis=-1)
    a = np.where(cov > 0, np.sqrt(varR / np.maximum(varI, eps)), 0)
    fit_lin = np.clip(1 - np.abs(mR - a * mI) / (p.b_tol * np.maximum(mR, eps)), 0, 1)
    textured = (cov > 0) & (cov >= p.min_correlation * np.sqrt(varI * varR))

    yI = mI_c[..., 0] * _LUM[0] + mI_c[..., 1] * _LUM[1] + mI_c[..., 2] * _LUM[2]
    yR = mR_c[..., 0] * _LUM[0] + mR_c[..., 1] * _LUM[1] + mR_c[..., 2] * _LUM[2]
    depth = 1 - yI / np.maximum(yR, eps)

    # Per-channel ratios: equal under a shadow (give or take the ambient
    # light's tint), larger in darker channels under glare. ``expected`` is
    # their spread if the whole difference were white light added to ``R``.
    spread = _spread(cv2.log(np.maximum(mR_c, eps)) - cv2.log(np.maximum(mI_c, eps)))
    expected = _spread(cv2.log(1 + np.maximum(yR - yI, 0)[..., None] / np.maximum(mI_c, eps)))
    informative = expected >= p.chroma_tol
    fit_colour = np.clip(1 - spread / np.maximum(0.5 * expected, eps), 0, 1)
    # Where colour cannot tell them apart (grey, white, pale content): the
    # texture's fit where there is texture enough, and in any case no glare
    # near the spot in the reference by its own look.
    fit_pale = np.where(textured, fit_lin, 0.0)
    # In dark content a small error of the tone match is a large ratio, and
    # phones brighten shadows of the scene differently from shot to shot.
    # Judged on this shot, not the reference: a sleeve's reflection over a
    # dark print in the other shot looks like a deep, colour-true shadow in
    # this one (real pages page-four and grandma-wilson), so a deep shadow
    # over mid-tone content is missed too.
    lit = yI >= p.dark_level
    # On flat pale content the edge of the difference decides. With three
    # or more shots the median of the others is right unless most of them
    # have glare there, and then the edge is in them, not in this shot.
    # With one other shot only a plain edge in this shot counts.
    pending = valid & ~informative & lit & (depth >= p.min_depth / 3)
    owned_two = np.zeros(depth.shape, bool)
    if (pending & (others >= 2)).any():
        many = pending & (others >= 2)
        fit_pale = np.maximum(fit_pale, np.where(many, _owned_by_shot(I, R, valid, many, p), 0))
    if (pending & (others == 1)).any():
        two = pending & (others == 1)
        owned_two = _owned_by_shot_two(I, R, valid, two, depth >= p.two_min_depth, p) > 0
        fit_pale = np.maximum(fit_pale, owned_two.astype(np.float32))
    veto = None
    if glare_ref is not None:
        # The widened score stands for glare's fading tail, which looks alike
        # from either shot. Where two shots' edge test has placed the edge in
        # this shot, the tail is already ruled out, and widening would only
        # let a print's bright border, which the glare score marks, hide a
        # shadow next to it.
        veto = np.where(owned_two, glare_ref[..., 1], glare_ref[..., 0])
        fit_pale = np.where(veto >= p.weak, 0, fit_pale)
    fit = np.where(informative, fit_colour, fit_pale)
    fit = np.where(lit, fit, 0)

    s_raw = np.where(valid, _ramp(depth, p.min_depth, p) * fit, 0).astype(np.float32)
    s_grow = np.where(valid, _ramp(depth, p.min_depth / 3, p) * fit, 0).astype(np.float32)
    return s_raw, s_grow, veto


def grow_regions(seed_cue: np.ndarray, grow_cue: np.ndarray, p: ShadowParams) -> np.ndarray:
    """Areas of ``grow_cue >= weak`` holding a seed of ``seed_cue``, at least ``min_area`` of the grid; the grow cue there."""
    seeds = (seed_cue >= p.seed).astype(np.uint8)
    if p.min_seed_px > 1:
        seeds = cv2.morphologyEx(seeds, cv2.MORPH_OPEN, np.ones((p.min_seed_px, p.min_seed_px), np.uint8))
    if not seeds.any():
        return np.zeros(seed_cue.shape, np.float32)
    wk = ((grow_cue >= p.weak) | (seeds > 0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(wk, connectivity=8)
    keep = np.unique(labels[seeds > 0])
    keep = [k for k in keep if k > 0 and stats[k, cv2.CC_STAT_AREA] >= p.min_area * seed_cue.size]
    return np.where(np.isin(labels, keep), np.maximum(grow_cue, seed_cue), 0).astype(np.float32)


def glare_scores(toned: Sequence[np.ndarray], masks: Sequence[np.ndarray], small: Sequence[np.ndarray] | None = None, gp: GlareParams | None = None) -> list[np.ndarray]:
    """Each shot's single-image glare score (``glare.glare_map``'s), for the glare veto."""
    gp = gp or GlareParams()
    small = small if small is not None else [None] * len(toned)
    return [glare_map(glare_features(t, m, s, gp), m, gp).score for t, m, s in zip(toned, masks, small)]


def comparison_shadow(
    toned: Sequence[np.ndarray],
    masks: Sequence[np.ndarray],
    p: ShadowParams | None = None,
    glare: Sequence[np.ndarray] | Callable[[], Sequence[np.ndarray]] | None = None,
) -> ComparisonShadow:
    """Each shot's shadow where it overlaps other shots, from comparing it with them.

    ``toned`` are the tone-matched shots on the grid (uint8 BGR) and
    ``masks`` their coverage. ``glare`` is each shot's single-image glare
    score (0..1), or a function giving them, called only when some shadow is
    found (it is the costly part); None skips the glare veto.
    """
    p = p or ShadowParams()
    n = len(toned)
    shape = masks[0].shape
    empty = [np.zeros(shape, np.float32) for _ in range(n)]
    if n < 2:
        return ComparisonShadow(empty, empty, empty, empty)
    k = max(3, int(round(p.window_frac * max(shape))) | 1)
    lin = [linear(t) for t in toned]
    masks = [m.astype(bool) for m in masks]

    # First without the glare score: when nothing is found at all, as on most
    # pages, the glare features are not needed.
    refs = _reference(lin, masks)
    first = [_cues(lin[i], ref, masks[i] & (others > 0), others, None, p, k) for i, (ref, _, others) in enumerate(refs)]
    if not any(grow_regions(s, g, p).any() for s, g, _ in first):
        return ComparisonShadow([s for s, _, _ in first], [s for s, _, _ in first], empty, empty)

    scores = glare() if callable(glare) else glare
    if scores is None:
        cues = first
    else:
        # Glare fades out softly around its core, and the fading tail, darker
        # than the core, looks alike from either shot: let the veto reach
        # out from where glare is plain.
        r = max(1, int(round(p.glare_reach * max(shape))))
        disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        refs = _reference(lin, masks, [np.dstack([cv2.dilate(np.asarray(s, np.float32), disk), np.asarray(s, np.float32)]) for s in scores])
        cues = [_cues(lin[i], ref, masks[i] & (others > 0), others, rg, p, k) for i, (ref, rg, others) in enumerate(refs)]
    cue, cue_raw, shadow, shadow_raw = [], [], [], []
    for s_raw, s_grow, rg in cues:
        veto = 1 - np.clip(rg, 0, 1) if rg is not None else 1
        s, g = (s_raw * veto).astype(np.float32), (s_grow * veto).astype(np.float32)
        cue.append(s)
        cue_raw.append(s_raw)
        shadow.append(grow_regions(s, g, p))
        shadow_raw.append(grow_regions(s_raw, s_grow, p))
    return ComparisonShadow(cue, cue_raw, shadow, shadow_raw)


def _poisson(gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """The image whose forward differences best match (gx, gy), by a DCT solve (Neumann boundary)."""
    h, w = gx.shape
    div = np.zeros((h, w), np.float64)
    div[:, 1:] += gx[:, 1:] - gx[:, :-1]
    div[:, 0] += gx[:, 0]
    div[1:, :] += gy[1:, :] - gy[:-1, :]
    div[0, :] += gy[0, :]
    d = dctn(div, type=2, norm="ortho")
    ky = 2 * np.cos(np.pi * np.arange(h) / h) - 2
    kx = 2 * np.cos(np.pi * np.arange(w) / w) - 2
    den = ky[:, None] + kx[None, :]
    den[0, 0] = 1.0
    d /= den
    d[0, 0] = 0.0
    return idctn(d, type=2, norm="ortho").astype(np.float32)


def _envelope(u: np.ndarray, mask: np.ndarray, iters: int = 4) -> np.ndarray:
    """A quadratic surface along the top of ``u`` inside ``mask``.

    It absorbs slow changes of the light over the page (vignetting, the
    lamp's falloff), so that only dips below it remain. Each pass refits on
    the points between the 40th and 95th percentile of the residual, which
    leaves out dips and the odd bright bump (a glare spot).
    """
    h, w = u.shape
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return np.full_like(u, float(np.median(u[mask])) if mask.any() else 0.0)
    step = max(1, len(xs) // 20000)
    xs, ys = xs[::step], ys[::step]
    x, y = xs / w - 0.5, ys / h - 0.5
    A = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    z = u[ys, xs].astype(np.float64)
    keep = np.ones(len(z), bool)
    for _ in range(iters):
        coef, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        res = z - A @ coef
        lo, hi = np.percentile(res, [40, 95])
        keep = (res >= lo) & (res <= hi)
    gx, gy = np.meshgrid(np.arange(w) / w - 0.5, np.arange(h) / h - 0.5)
    return (coef[0] + coef[1] * gx + coef[2] * gy + coef[3] * gx * gx + coef[4] * gx * gy + coef[5] * gy * gy).astype(np.float32)


def _drop_spine(cue: np.ndarray, depth: np.ndarray, p: ShadowParams) -> np.ndarray:
    """Remove dips that run along most of one edge of the grid and are deepest at it: spine shading, not a cast shadow."""
    h, w = cue.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats((cue >= p.weak).astype(np.uint8), connectivity=8)
    out = cue.copy()
    for k in range(1, n):
        x, y, bw, bh, _ = (int(v) for v in stats[k])
        m = labels == k
        for edge, touches, run, dist in (
            ("left", x <= 1, bh / h, np.arange(w)[None, :].repeat(h, 0)),
            ("right", x + bw >= w - 1, bh / h, (w - 1 - np.arange(w))[None, :].repeat(h, 0)),
            ("top", y <= 1, bw / w, np.arange(h)[:, None].repeat(w, 1)),
            ("bottom", y + bh >= h - 1, bw / w, (h - 1 - np.arange(h))[:, None].repeat(w, 1)),
        ):
            if not touches or run < p.edge_run:
                continue
            d = dist[m]
            mid = np.median(d)
            if depth[m][d <= mid].mean() > depth[m][d > mid].mean():
                out[m] = 0
                break
    return out


def single_shadow(toned: np.ndarray, mask: np.ndarray | None = None, p: ShadowParams | None = None, glare: np.ndarray | None = None) -> np.ndarray:
    """Shadow cue (0..1) of one shot on its own, on the grid.

    The shot's log-luminance gradients are split into illumination and
    reflectance. A gradient counts as illumination when it is soft (about as
    strong at the penumbra's scale as the strongest finer-scale gradient
    near it), achromatic (every channel's log changes alike) and keeps the
    texture (the local contrast of the log is about the same on both sides).
    The illumination is rebuilt from those gradients alone by a Poisson
    solve, and each dip below its smooth upper envelope is a candidate. The
    rebuilt dip is shallower than the real one wherever the edge was not
    accepted all round, so a candidate's depth is measured on the image
    across its accepted edges; its cue is that depth, mapped like the
    comparison cue and scaled by ``single_weight``, times the share of its
    rim with an accepted edge (full at ``edge_cover``). ``glare`` is
    the shot's glare score (0..1, on the grid), when known: the soft edge of
    a glare veil is not a shadow's, and the page between two veils is not a
    dip, so edges with glare within ``glare_edge_reach`` of their bright
    side are left out of the rebuild, and edges whose bright side has glare
    are not taken.
    """
    p = p or ShadowParams()
    h, w = toned.shape[:2]
    m = np.ones((h, w), bool) if mask is None else mask.astype(bool)
    if m.sum() < 100:
        return np.zeros((h, w), np.float32)
    g = None if glare is None else glare.astype(np.float32)
    mf = m.astype(np.float32)
    lin = linear(toned)
    logc = cv2.log(lin + 0.01)
    logl = cv2.log((lin * _LUM).sum(axis=-1) + 0.01)
    sp = max(1.0, p.penumbra_frac * max(h, w))

    def blur(x, s):
        # Normalised by the mask, so that what lies outside it does not leak in.
        num = cv2.GaussianBlur(x * (mf if x.ndim == 2 else mf[..., None]), (0, 0), s)
        den = cv2.GaussianBlur(mf, (0, 0), s)
        return num / np.maximum(den if x.ndim == 2 else den[..., None], 1e-6)

    def grad(x):
        gx = np.zeros_like(x)
        gy = np.zeros_like(x)
        gx[:, :-1] = x[:, 1:] - x[:, :-1]
        gy[:-1] = x[1:] - x[:-1]
        return gx, gy

    lc = blur(logl, sp)
    gx, gy = grad(lc)
    mag = np.hypot(gx, gy)
    fx, fy = grad(blur(logl, max(0.7, sp / 3)))
    fine = cv2.dilate(np.hypot(fx, fy), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * int(sp) + 1, 2 * int(sp) + 1)))
    soft = mag >= p.soft_ratio * fine

    cc = blur(logc, sp)
    worst = np.zeros_like(mag)
    for ch in range(3):
        cx, cy = grad(np.ascontiguousarray(cc[..., ch]))
        worst = np.maximum(worst, np.hypot(cx - gx, cy - gy))
    achromatic = worst <= p.grad_chroma_tol * mag

    # Local contrast of the log on either side of the edge, 2 sigma away.
    mean = cv2.blur(logl, (5, 5))
    tex = cv2.log(np.sqrt(np.maximum(cv2.blur(logl * logl, (5, 5)) - mean * mean, 0)) + 0.005)
    ux, uy = gx / np.maximum(mag, 1e-9), gy / np.maximum(mag, 1e-9)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = 2 * sp
    t_hi = cv2.remap(tex, xx + d * ux, yy + d * uy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    t_lo = cv2.remap(tex, xx - d * ux, yy - d * uy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    keeps_texture = np.abs(t_hi - t_lo) <= p.texture_tol

    inner = cv2.erode(m.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    ok = soft & achromatic & keeps_texture & inner & (mag > 1e-4)
    if g is not None and p.glare_edge_reach > 0:
        r = int(round(p.glare_edge_reach * max(h, w)))
        g_near = cv2.dilate(g, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        bright = cv2.remap(g_near, xx + 2 * sp * gx / np.maximum(mag, 1e-9), yy + 2 * sp * gy / np.maximum(mag, 1e-9), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        ok = ok & (bright < p.weak)
    u = _poisson(np.where(ok, gx, 0), np.where(ok, gy, 0))
    env = _envelope(u, inner)
    dip = np.where(m, env - u, 0)
    n, labels, stats, _ = cv2.connectedComponentsWithStats((dip >= p.dip_min).astype(np.uint8), connectivity=8)
    cue = np.zeros((h, w), np.float32)
    depth = np.zeros((h, w), np.float32)
    if n > 1:
        # The log-luminance outside less inside, across the whole penumbra:
        # the most over a few distances, since penumbrae differ in width.
        step = np.zeros((h, w), np.float32)
        for d in (2 * sp, 4 * sp, 8 * sp):
            hi = cv2.remap(lc, xx + d * ux, yy + d * uy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            lo = cv2.remap(lc, xx - d * ux, yy - d * uy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            step = np.maximum(step, hi - lo)
        d = 2 * sp
        if g is not None:
            g_hi = cv2.remap(g, xx + d * ux, yy + d * uy, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            ok = ok & (g_hi < p.weak)
        r = max(1, int(round(2 * sp)))
        ring = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        for k in range(1, n):
            if stats[k, cv2.CC_STAT_AREA] < p.min_area * h * w:
                continue
            comp = labels == k
            band = (cv2.dilate(comp.astype(np.uint8), ring) > 0) & ~(cv2.erode(comp.astype(np.uint8), ring) > 0) & inner
            if not band.any():
                continue
            # Accepted edges whose bright side faces out of the dip.
            cy, cx = np.nonzero(comp)
            ccx, ccy = cx.mean(), cy.mean()
            by, bx = np.nonzero(band)
            outward = (ux[by, bx] * (bx - ccx) + uy[by, bx] * (by - ccy)) > 0
            acc = ok[by, bx] & outward
            coverage = acc.mean()
            if acc.sum() < 5:
                continue
            dk = 1 - float(np.exp(-max(float(np.median(step[by[acc], bx[acc]])), 0)))
            depth[comp] = dk
            cue[comp] = float(np.clip(p.single_weight * (dk - p.min_depth) / p.depth_ramp, 0, 1) * np.clip(coverage / p.edge_cover, 0, 1))
    return np.where(m, _drop_spine(cue, dip, p), 0).astype(np.float32)


def grow(comparison: np.ndarray, single: np.ndarray, single_area: np.ndarray, p: ShadowParams) -> np.ndarray:
    """One shot's shadow for the report, beyond what comparison sees (0 or 1 on the grid).

    Shadows found by comparison (``comparison`` > 0) are grown into the
    part of the shot no other shot covers (``single_area``), following the
    single-image cue at ``weak``, so that a shadow crossing out of an
    overlap is reported in full. The single-image cue on its own counts only
    from strong seeds (at ``seed``).
    """
    seeds = comparison > 0
    weak = ((single >= p.weak) & single_area) | seeds
    n, labels = cv2.connectedComponents(weak.astype(np.uint8), connectivity=8)
    keep = np.unique(labels[seeds])
    grown = np.isin(labels, keep[keep > 0]) & ~seeds
    alone = grow_regions(single, single, p) > 0
    return (grown | alone).astype(np.float32)
