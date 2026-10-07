"""Several photos of one album page in, one flat, cropped, glare-free page out."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .align import ShotAlignment, align_shots, apply_field
from .curvature import CurvatureFit, CurvatureParams, PageSurface, fit_page, flatten_maps, output_size, upsample_map, view_angles
from .fuse import WEIGHT_MAX_SIDE, FuseParams, fuse
from .glare import GlareParams, adapt_bias, combine, glare_features, glare_map, residual
from .page import PageQuad, detect_pages, estimate_aspect, focal_px_from_35mm
from .regions import QualityParams, glare_regions, page_warnings, uncovered_regions
from .register import register

SCHEMA_VERSION = 2

PREVIEW_MAX_SIDE = 3000
MAP_STEP = 8  # flattening maps of curved pages are computed every this many output px
MAP_BAND = 512  # and expanded to full resolution this many rows at a time


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
    glare: GlareParams = field(default_factory=GlareParams)
    quality: QualityParams = field(default_factory=QualityParams)
    curvature: CurvatureParams = field(default_factory=CurvatureParams)


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
    glare_fraction: dict[int, float]  # comparison with the other shots, as fuse() measures it
    gains: dict[int, list[float]]
    schema_version: int = SCHEMA_VERSION
    glare: dict = field(default_factory=lambda: {"fraction": 0.0, "per_shot": {}, "regions": []})  # residual glare
    uncovered: dict = field(default_factory=lambda: {"fraction": 0.0, "regions": []})
    warnings: list[dict] = field(default_factory=list)
    detector: dict = field(default_factory=dict)  # glare and quality parameters, bias used
    curvature: dict = field(default_factory=dict)  # see _curvature_report

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PageResult:
    image: np.ndarray  # uint8 BGR
    report: PageReport
    corners_ref: np.ndarray  # page corners in the reference photo's full-res pixels
    residual_glare: np.ndarray | None = None  # float32 0..1 on the detector's grid
    coverage: np.ndarray | None = None  # bool, full resolution: pixel covered by at least one shot
    surface: PageSurface | None = None  # the bent page model, when the page was treated as curved


def _warp_all(images, homographies, size):
    """Warp each image by its homography into a frame of ``size`` (w, h)."""
    warped, masks = [], []
    for img, H in zip(images, homographies):
        h, w = img.shape[:2]
        warped.append(cv2.warpPerspective(img, H, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT))
        m = cv2.warpPerspective(np.full((h, w), 255, np.uint8), H, size, flags=cv2.INTER_NEAREST)
        masks.append(cv2.erode(m, np.ones((5, 5), np.uint8)) > 0)
    return warped, masks


def _remap_all(images, maps, size, step: int = MAP_STEP, scale: float = 1.0):
    """Like ``_warp_all``, through coarse per-shot maps (see ``curvature.flatten_maps``).

    The maps are expanded bilinearly and applied in bands of rows, so memory
    stays at a band's worth of float maps. With ``scale`` below 1 the result
    is that much smaller, from a shot first shrunk to about that size.
    """
    W, H = max(1, round(size[0] * scale)), max(1, round(size[1] * scale))
    warped, masks = [], []
    for img, m in zip(images, maps):
        g = min(1.0, 2.0 * scale)
        src = img if g >= 1 else cv2.resize(img, (max(1, round(img.shape[1] * g)), max(1, round(img.shape[0] * g))), interpolation=cv2.INTER_AREA)
        sh, sw = src.shape[:2]
        out = np.zeros((H, W, 3), np.uint8)
        mask = np.zeros((H, W), np.uint8)
        for r0 in range(0, H, MAP_BAND):
            r1 = min(H, r0 + MAP_BAND)
            band = upsample_map(m, step, (W, H), (r0, r1), scale)
            mx = (band[..., 0] + 0.5) * g - 0.5 if g < 1 else band[..., 0]
            my = (band[..., 1] + 0.5) * g - 0.5 if g < 1 else band[..., 1]
            out[r0:r1] = cv2.remap(src, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            mask[r0:r1] = ((mx >= 0) & (my >= 0) & (mx <= sw - 1) & (my <= sh - 1)) * 255
        k = 5 if scale >= 1 else 3
        warped.append(out)
        masks.append(cv2.erode(mask, np.ones((k, k), np.uint8)) > 0)
    return warped, masks


def _neighbour_side(pool: list, chosen: PageQuad) -> str | None:
    """The side of the chosen page that another detected page lies against, if any.

    Pages of an open album lie left and right of each other, and the side
    facing the neighbour is where the album's spine is.
    """
    q = chosen.corners.astype(np.float32)
    x0, y0 = q.min(axis=0)
    x1, y1 = q.max(axis=0)
    area = cv2.contourArea(q)
    for cand, _ in pool:
        c = cand.corners.astype(np.float32)
        a = cv2.contourArea(c)
        if cand is chosen or a <= 0 or not 0.5 < a / area < 2.0 or not cv2.isContourConvex(c.reshape(-1, 1, 2)):
            continue
        cx0, cy0 = c.min(axis=0)
        cx1, cy1 = c.max(axis=0)
        overlap_y = min(y1, cy1) - max(y0, cy0)
        if overlap_y < 0.5 * (y1 - y0) or cv2.intersectConvexConvex(q, c)[0] > 0.1 * area:
            continue
        w = x1 - x0
        if abs(cx1 - x0) < 0.1 * w:
            return "left"
        if abs(cx0 - x1) < 0.1 * w:
            return "right"
    return None


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
    covered = float(preview.coverage.sum())
    for i, H in zip(used, pre_H):
        frame = np.ones(images[i].shape[:2], bool)
        for c in detect_pages(images[i]):
            if c.method == "fallback":
                continue
            mapped = cv2.perspectiveTransform(c.corners.reshape(-1, 1, 2), H).reshape(4, 2).astype(np.float32)
            # Scores grow with the share of the image a candidate fills; rate
            # it by its share of the mosaic instead, so that a close-up of
            # part of the page does not outweigh the whole page in the mosaic.
            own = cv2.contourArea(c.corners) / frame.size
            score = c.score * (cv2.contourArea(mapped) / covered) / own if own > 0 else 0.0
            pool.append((PageQuad(mapped, c.method, score), _touch(c.corners, frame)))
    quad = _choose_page(pool, [images[i].shape[:2] for i in used], pre_H)
    corners_ref = cv2.perspectiveTransform(quad.corners.reshape(-1, 1, 2), np.linalg.inv(to_canvas)).reshape(4, 2)

    # 2. Output rectangle: true aspect ratio, native resolution, capped.
    rh, rw = images[ref].shape[:2]
    focal = focal_px_from_35mm(opt.focal_35mm, rw, rh) if opt.focal_35mm else None
    aspect = estimate_aspect(corners_ref, (rw / 2, rh / 2), focal)
    cp = opt.curvature
    cp.validate()
    fit = None
    if cp.mode != "off":
        f_px = focal or focal_px_from_35mm(26.0, rw, rh)
        K = np.array([[f_px, 0, rw / 2], [0, f_px, rh / 2], [0, 0, 1]])
        fit = fit_page(preview.image, preview.coverage, quad.corners, to_canvas, K, aspect, cp, _neighbour_side(pool, quad))
    surface = fit.surface if fit is not None else None
    alignments: list[ShotAlignment] = []
    if surface is None:
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
    else:
        # A bent page: one resampling of each shot through the flattening
        # map, after a correction for the parallax in the bent strip.
        aspect_planar = aspect
        aspect = surface.aspect
        W, H = output_size(surface, opt.max_side)
        shot_H = [reg.homographies[i] for i in used]
        maps = flatten_maps(surface, (W, H), opt.inset, shot_H, MAP_STEP)
        f_small = min(1.0, WEIGHT_MAX_SIDE / max(W, H))
        small, small_m = _remap_all([images[i] for i in used], maps, (W, H), MAP_STEP, f_small)
        order = [used.index(i) for i in reg.tree_order if i in used]
        alignments = align_shots(small, small_m, order, cp.align, f_small)
        maps = [m if a.field is None else apply_field(m, a.field, MAP_STEP, (W, H)) for m, a in zip(maps, alignments)]
        out_w, out_m = _remap_all([images[i] for i in used], maps, (W, H), MAP_STEP)
        result = fuse(out_w, out_m, preview.tone_reference, opt.fuse)
        corners_ref = surface.project(np.array([0, 1, 1, 0.0]), np.array([0, 0, 1, 1.0])).astype(np.float32)

    quality = _assess(result, used, reg.dropped, (W, H), opt)
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
        glare=quality.glare,
        uncovered=quality.uncovered,
        warnings=quality.warnings,
        detector=quality.detector,
    )
    steep = None
    if surface is not None:
        steep = _steep(surface, [reg.homographies[i] for i in used], [images[i].shape[:2] for i in used], cp)
    report.curvature = _curvature_report(cp, fit, surface, aspect_planar if surface is not None else aspect, used, alignments, steep)
    report.warnings += _curvature_warnings(cp, fit, steep)

    if debug_dir is not None:
        _write_debug(Path(debug_dir), preview, quad, to_canvas, result, used, quality)
        if fit is not None:
            _write_curvature_debug(Path(debug_dir), preview.image, to_canvas, fit, images[ref], used, alignments, (W, H), opt.inset)

    return PageResult(result.image, report, corners_ref, quality.residual, result.coverage, surface)


def _steep(surface: PageSurface, shot_H: list[np.ndarray], shapes: list[tuple[int, int]], cp: CurvatureParams) -> dict:
    """Where every shot saw the page at more than the steep angle, and the resolution there.

    The share of the page seen only steeply, and the cosine of its best view
    angle against that of the flat part, which is how much resolution it has
    compared with the rest.
    """
    n = 64
    gx, gy = np.meshgrid((np.arange(n) + 0.5) / n, (np.arange(n) + 0.5) / n)
    grid = np.stack([gx.ravel(), gy.ravel()], axis=1)
    angles = view_angles(surface, shot_H, grid)
    ref_px = surface.project(grid[:, 0], grid[:, 1]).reshape(-1, 1, 2)
    for k, (H, (h, w)) in enumerate(zip(shot_H, shapes)):
        p = cv2.perspectiveTransform(ref_px, np.linalg.inv(H)).reshape(-1, 2)
        seen = (p[:, 0] >= 0) & (p[:, 1] >= 0) & (p[:, 0] < w) & (p[:, 1] < h)
        angles[k, ~seen] = 90.0
    best = angles.min(axis=0)
    steep = best > cp.steep_angle_deg
    u = {"left": grid[:, 0], "right": 1 - grid[:, 0], "top": grid[:, 1], "bottom": 1 - grid[:, 1]}[surface.binding]
    flat = u > 0.5
    cos = np.cos(np.radians(best))
    resolution = float(np.median(cos[steep]) / max(np.median(cos[flat]), 1e-9)) if steep.any() else 1.0
    return {"fraction": round(float(steep.mean()), 4), "resolution": round(resolution, 3)}


def _curvature_report(cp, fit: CurvatureFit | None, surface, aspect_planar: float, used, alignments, steep) -> dict:
    """The page metadata's ``curvature`` object, present on every page."""
    if fit is None:
        fit = CurvatureFit(None, 0.0, 0.0, 0.0, 0.0, "off", 0)
    best = fit.best
    rep = {
        "mode": cp.mode,
        "applied": surface is not None,
        "reason": fit.reason,
        "binding": surface.binding if surface is not None else None,
        "max_lift": round(best.max_lift(), 4) if best is not None else 0.0,
        "strip_width": round(surface.strip_width(), 4) if surface is not None else 0.0,
        "width_change": round(surface.aspect / aspect_planar - 1, 4) if surface is not None else 0.0,
        "outline_rms": {"planar": round(fit.outline_rms_planar, 2), "curved": round(fit.outline_rms_curved, 2)},
        "lines_used": fit.lines_used,
        "profile": [[round(u, 4), round(v, 4)] for u, v in surface.profile(at=PROFILE_AT)] if surface is not None else [],
        "alignment": {
            str(used[a.shot]): {"matches": a.matches, "offset_before": a.offset_before, "offset_after": a.offset_after, "skipped": a.skipped}
            for a in alignments
            if a.skipped != "reference"
        },
        "steep_fraction": steep["fraction"] if steep else 0.0,
    }
    return rep


PROFILE_AT = (0.0, 0.01, 0.02, 0.035, 0.05, 0.075, 0.1, 0.14, 0.18, 0.24, 0.3, 0.4, 0.55, 0.75, 1.0)


def _curvature_warnings(cp: CurvatureParams, fit: CurvatureFit | None, steep: dict | None) -> list[dict]:
    out = []
    if steep and steep["fraction"] > 0:
        out.append(
            {
                "code": "steep_binding",
                "fraction": steep["fraction"],
                "resolution": steep["resolution"],
                "message": f"{steep['fraction']:.1%} of the page next to the binding was only seen at a steep angle, at "
                f"{steep['resolution']:.0%} of the resolution of the rest. Add a shot aimed more squarely at the binding.",
            }
        )
    if fit is not None and fit.reason == "fit_failed" and fit.max_bow > cp.min_lift:
        out.append(
            {
                "code": "curvature_uncorrected",
                "max_bow": round(fit.max_bow, 4),
                "message": "The page looks bent near its binding, but its shape could not be fitted, so it was processed as flat. "
                "Lines near the binding may be bent.",
            }
        )
    return out


@dataclass
class _Quality:
    glare: dict
    uncovered: dict
    warnings: list[dict]
    detector: dict
    residual: np.ndarray
    maps: list  # per used shot: GlareMap, or None with the detector off


def _assess(result, used: list[int], dropped: list[int], size: tuple[int, int], opt: PageOptions) -> _Quality:
    """Glare left in the composed page, uncovered area, and the warnings.

    Runs on the grid of ``fuse``'s weights. Each shot's own glare map is
    combined with the comparison against the other shots, then weighted by
    the share each shot had in every composed pixel.
    """
    gp, q = opt.glare, opt.quality
    masks = result.masks_small
    grid = masks[0].shape
    count = np.sum([m.astype(np.uint8) for m in masks], axis=0)
    bias, adapted, maps = gp.bias, False, [None] * len(used)
    if gp.enabled:
        feats = [glare_features(t, m, s, gp) for t, m, s in zip(result.toned_small, masks, result.small)]
        bias, adapted = adapt_bias(feats, result.excess_small, masks, gp, opt.fuse.glare_flag)
        maps = [glare_map(f, m, gp, bias) for f, m in zip(feats, masks)]
        per = [combine(g, ex, opt.fuse.glare_flag, gp.weak) for g, ex in zip(maps, result.excess_small)]
        res = residual(per, result.weights_small)
    else:
        per = [np.zeros(grid, np.float32) for _ in used]
        res = np.zeros(grid, np.float32)
    covered = count > 0
    glare = {
        "fraction": round(float((res[covered] >= q.residual_threshold).mean()) if covered.any() else 0.0, 4),
        "per_shot": {i: round(float((g[m] >= q.residual_threshold).mean()) if m.any() else 0.0, 4) for i, g, m in zip(used, per, masks)},
        "regions": [r.to_dict() for r in glare_regions(res, count, used, masks, size, q)],
    }
    uncovered = {
        "fraction": round(1.0 - float(result.coverage.mean()), 4),
        "regions": [r.to_dict() for r in uncovered_regions(result.coverage, size, q, grid)],
    }
    detector = {"bias": round(float(bias), 4), "bias_adapted": adapted, "glare": asdict(gp), "quality": asdict(q)}
    return _Quality(glare, uncovered, page_warnings(glare, uncovered, dropped), detector, res, maps)


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
    return float((d < 0.004 * max(valid.shape)).mean())


def _choose_page(pool: list[tuple[PageQuad, float]], shapes: list[tuple[int, int]], to_canvas_H: list[np.ndarray]) -> PageQuad:
    """Pick the page the photos are of when more than one page is in view.

    Each candidate's detection score is weighted by how well it fits the
    photos: how much of it each photo shows (averaged over photos, cubed so
    that "shown whole in every photo" beats sheer size, since two pages side
    by side outscore one on area alone, and a wide shot may be too coarse to
    show the line between them), and whether it is cut off by the edge of
    what was photographed.
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


def _write_debug(d: Path, preview, quad, to_canvas, result, used, quality: _Quality):
    d.mkdir(parents=True, exist_ok=True)
    vis = preview.image.copy()
    cv2.polylines(vis, [quad.corners.round().astype(np.int32).reshape(-1, 1, 2)], True, (0, 0, 255), 3)
    cv2.imwrite(str(d / "preview_mosaic.jpg"), vis)
    for i, w in zip(used, result.weights_small):
        wn = w / max(float(w.max()), 1e-9)
        cv2.imwrite(str(d / f"weight_{i:02d}.png"), (wn * 255).astype(np.uint8))
    # Each shot's glare score in grey, its grown glare areas in red.
    for i, g in zip(used, quality.maps):
        if g is None:
            continue
        img = cv2.cvtColor((g.score * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        img[g.glare > 0] = (0, 0, 255)
        cv2.imwrite(str(d / f"glare_{i:02d}.png"), img)
    cv2.imwrite(str(d / "quality_overlay.jpg"), quality_overlay(result.image, quality.glare["regions"], quality.uncovered["regions"]))


def _write_curvature_debug(d: Path, mosaic, to_canvas, fit: CurvatureFit, reference, used, alignments, size, inset):
    """The traced and fitted outlines, the profile, the page grid on the reference photo, the shots' fields."""
    vis = mosaic.copy()
    t = max(1, round(max(vis.shape[:2]) / 1000))
    for pts in fit.outline:
        if pts is not None:
            cv2.polylines(vis, [np.round(pts).astype(np.int32).reshape(-1, 1, 2)], False, (255, 255, 255), t)
    u = np.linspace(0, 1, 101)
    border = (np.concatenate([u, np.ones_like(u), u[::-1], np.zeros_like(u)]), np.concatenate([np.zeros_like(u), u, np.ones_like(u), u[::-1]]))
    for s, colour in ((fit.planar, (0, 0, 255)), (fit.best, (0, 200, 0))):
        if s is not None:
            q = cv2.perspectiveTransform(s.project(*border).reshape(-1, 1, 2), to_canvas)
            cv2.polylines(vis, [np.round(q).astype(np.int32)], True, colour, t)
    cv2.imwrite(str(d / "curvature_outline.jpg"), vis)
    s = fit.best
    if s is None:
        return
    # Lift against position, both as fractions of the page across the binding.
    pw, ph, m = 800, 300, 40
    plot = np.full((ph, pw, 3), 255, np.uint8)
    prof = np.array(s.profile(200))
    top = max(float(np.abs(prof[:, 1]).max()), 1e-4)
    pts = np.stack([m + prof[:, 0] * (pw - 2 * m), ph / 2 - prof[:, 1] / top * (ph / 2 - m)], axis=1)
    cv2.line(plot, (m, ph // 2), (pw - m, ph // 2), (200, 200, 200), 1)
    cv2.polylines(plot, [np.round(pts).astype(np.int32).reshape(-1, 1, 2)], False, (0, 120, 0), 2)
    cv2.putText(plot, f"{s.binding} binding, max lift {top:.2%} of the page", (m, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
    cv2.imwrite(str(d / "curvature_profile.png"), plot)
    # Lines of constant x and y every 5% of the page, through the surface onto the reference photo.
    grid = reference.copy()
    t = max(1, round(max(grid.shape[:2]) / 1000))
    for k in np.linspace(0, 1, 21):
        for xy in ((np.full_like(u, k), u), (u, np.full_like(u, k))):
            cv2.polylines(grid, [np.round(s.project(*xy)).astype(np.int32).reshape(-1, 1, 2)], False, (0, 255, 255), t)
    cv2.imwrite(str(d / "curvature_grid.jpg"), grid)
    # Each shot's correction: hue for direction, brightness for size.
    for i, a in zip(used, alignments):
        if a.field is None:
            continue
        f = cv2.resize(a.field, (max(2, size[0] // 4), max(2, size[1] // 4)), interpolation=cv2.INTER_LINEAR)
        mag, ang = cv2.cartToPolar(np.ascontiguousarray(f[..., 0]), np.ascontiguousarray(f[..., 1]), angleInDegrees=True)
        hsv = np.stack([ang / 2, np.full_like(mag, 255), np.clip(mag / max(float(mag.max()), 1e-6) * 255, 0, 255)], axis=2).astype(np.uint8)
        cv2.imwrite(str(d / f"align_{i:02d}.png"), cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR))


def quality_overlay(image: np.ndarray, glare_regions: list[dict], uncovered_regions: list[dict]) -> np.ndarray:
    """The composed page with glare regions boxed in magenta and uncovered ones in cyan, numbered."""
    vis = image.copy()
    t = max(2, round(max(image.shape[:2]) / 600))
    for regions, colour in ((glare_regions, (255, 0, 255)), (uncovered_regions, (255, 255, 0))):
        for k, r in enumerate(regions):
            x0, y0, x1, y1 = r["bbox_px"]
            cv2.rectangle(vis, (x0, y0), (x1, y1), colour, t)
            cv2.putText(vis, str(k), (x0 + 2 * t, y0 + 12 * t), cv2.FONT_HERSHEY_SIMPLEX, 0.4 * t, colour, t)
    return vis
