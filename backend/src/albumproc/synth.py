"""Synthetic album-page photos for testing without real captures.

A fake album page (prints, captions, paper texture) lies on a fake table. A
pinhole camera looks at it from varied angles, so perspective is physically
correct, and each shot gets its own glare spots, exposure, noise and JPEG
compression. The page image itself is kept as ground truth.

Curved pages (``kind="curved"``) are bent near one side, as an album page
lifts off the table next to its binding: the page is cut into thin strips
parallel to the binding, each placed in 3D along the bend and drawn on its
own, farthest first.

Glare comes in two styles: ``spot`` (soft, flat-topped reflections of a lamp)
and ``sleeve`` (long, thin, wavy streaks with a clipped core inside a soft
veil, as a crinkled plastic sleeve reflects a ceiling light).

Sleeves (``sleeve=``) put the page in a clear plastic album sleeve: crimped
seams on the two sides across the binding, a gap of clear plastic inside
them, an opaque binding strip on the binding side and a clear margin on the
far side, the paper shifted and turned slightly inside it. The paper's and
the sleeve's corners and the bands on each side are kept as ground truth.

Shadows (``shadow=``) are the soft silhouette of an arm, a hand or a phone
held between the light and the page, entering from a page edge. They lie on
the page, so a shadow in several shots falls on the same part of it. They
are drawn from their own random stream, so a case with a shadow has the same
page, shots and glare as the same case without one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import cv2
import numpy as np


@dataclass
class SynthShot:
    image: np.ndarray
    H_scene_to_image: np.ndarray
    glare_mask: np.ndarray  # bool, where glare was added
    shadow_mask: np.ndarray | None = None  # bool, where a shadow is at least half its depth; None without one
    camera: tuple | None = None  # (K, R, C) of the shot, see _look_at_camera


SHADOWS = ("none", "one", "all", "part")
SHADOW_SHAPES = ("arm", "hand", "phone")
EDGES = ("left", "right", "top", "bottom")


@dataclass
class Shadow:
    """A cast shadow on the page, in scene coordinates."""

    silhouette: np.ndarray  # float32 0..1, penumbra included, on the scene shrunk by ``scale``
    scale: float  # silhouette px per scene px
    depth: float  # share of the light taken away at full shadow, 0..1
    tint: np.ndarray  # (3,) BGR: the shadow's slight colour from ambient light of another colour
    shape: str
    edge: str  # the page edge it enters from


@dataclass
class SleeveSpec:
    """An album sleeve around the page. Fractions are of the paper's width; None draws a value."""

    binding: str = "left"  # the strip's side; the crimps run along the two sides across it
    crimp: float = 0.015  # crimped seam's width
    period: float = 0.0055  # its ridges' spacing along the seam
    gap: float | None = None  # clear plastic between a crimp and the paper, 0 to 1%
    strip: float = 0.025  # opaque binding strip's width
    clear: float | None = None  # clear margin on the far side, 0 to 5.5%
    radius: float | None = None  # rounded outer corners: 0 or 2%
    shift: float | None = None  # the paper's shift inside the sleeve, up to 0.5% each way
    turn: float | None = None  # and its turn, degrees, up to 0.5
    print_inset: float | None = None  # the prints' borders this far inside the paper's edge, 1 to 3%


@dataclass
class SleeveTruth:
    """Where the paper and the sleeve are, in the sleeve's own pixels (the drawn canvas)."""

    spec: SleeveSpec  # with every value drawn
    paper_corners: np.ndarray  # (4, 2) TL TR BR BL
    sleeve_corners: np.ndarray  # (4, 2): the sleeve's outer rectangle
    bands: dict[str, list[str]]  # per side of the page, the sleeve's bands from the outside in
    project: object = None  # project(k, pts) -> shot k's pixels, for canvas pixels pts (n, 2)


@dataclass
class SynthPage:
    page: np.ndarray  # ground-truth page, BGR
    scene: np.ndarray  # page on the table
    page_origin: tuple[int, int]  # page's top-left inside the scene
    shots: list[SynthShot]
    sleeve: SleeveTruth | None = None

    @property
    def images(self) -> list[np.ndarray]:
        return [s.image for s in self.shots]

    def outline_in(self, k: int, which: str = "paper", n: int = 60) -> list[np.ndarray]:
        """The paper's (or the sleeve's) sides, top, right, bottom, left, as points in shot ``k``'s pixels."""
        sl = self.sleeve
        q = sl.paper_corners if which == "paper" else sl.sleeve_corners
        t = np.linspace(0, 1, n)[:, None]
        return [sl.project(k, q[i] + t * (q[(i + 1) % 4] - q[i])) for i in range(4)]

    def corners_in(self, k: int, which: str = "paper") -> np.ndarray:
        sl = self.sleeve
        return sl.project(k, sl.paper_corners if which == "paper" else sl.sleeve_corners)


def _print_photo(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """A made-up snapshot: soft gradient sky/ground plus blobs, shapes, detail."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    c1, c2 = rng.integers(40, 230, 3), rng.integers(40, 230, 3)
    t = (yy / h)[..., None]
    img = (c1 * (1 - t) + c2 * t).astype(np.float32)
    for _ in range(rng.integers(6, 14)):
        col = rng.integers(0, 255, 3).tolist()
        kind = rng.integers(0, 3)
        if kind == 0:
            cv2.circle(img, (int(rng.integers(0, w)), int(rng.integers(0, h))), int(rng.integers(h // 20, h // 4)), col, -1)
        elif kind == 1:
            pts = rng.integers(0, [w, h], (int(rng.integers(3, 7)), 2)).astype(np.int32)
            cv2.fillPoly(img, [pts], col)
        else:
            p1 = tuple(int(v) for v in rng.integers(0, [w, h]))
            p2 = tuple(int(v) for v in rng.integers(0, [w, h]))
            cv2.line(img, p1, p2, col, int(rng.integers(2, 8)))
    img = cv2.GaussianBlur(img, (0, 0), 1.5)
    # Fine texture, like grass or fabric, so there is detail to match on.
    tex = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2) * 18
    img += tex[..., None]
    return np.clip(img, 0, 255).astype(np.uint8)


def make_page(rng: np.random.Generator, w: int = 2200, h: int = 1700, margin: float | None = None) -> np.ndarray:
    """A cream page with a 2x2 grid of prints, each captioned.

    ``margin`` is the paper left around and between the prints, as a
    fraction of the width, with the prints as wide as they fit; by default
    the margins are 6% of the width and 7% of the height, and the prints
    vary in width.
    """
    paper = np.array([215, 230, 238], np.float32)  # cream, BGR
    page = np.empty((h, w, 3), np.float32)
    page[:] = paper
    page += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 3)[..., None] * 6
    page = np.clip(page, 0, 255).astype(np.uint8)
    # 2x2 grid of prints with white borders and a caption under each.
    mx, my = (int(w * 0.06), int(h * 0.07)) if margin is None else (int(w * margin), int(w * margin))
    cw, ch = (w - 3 * mx) // 2, (h - 3 * my) // 2
    for r in range(2):
        for c in range(2):
            x, y = mx + c * (cw + mx), my + r * (ch + my)
            pw, ph = int(cw * rng.uniform(0.75, 0.95)), int(ch * rng.uniform(0.7, 0.85))
            if margin is not None:  # prints as wide as their cell, so they reach the margin
                pw = cw - 2 * max(6, cw // 40)
            px, py = x + (cw - pw) // 2, y
            b = max(6, pw // 40)
            page[py - b : py + ph + b, px - b : px + pw + b] = (245, 248, 250)
            page[py : py + ph, px : px + pw] = _print_photo(rng, pw, ph)
            words = ["Summer", "Lake", "Grandma", "1974", "Picnic", "Beach", "Road trip", "Birthday"]
            text = f"{rng.choice(words)} {rng.choice(words)}"
            cv2.putText(page, text, (px, py + ph + int(my * 0.6)), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, h / 1200, (60, 50, 40), max(1, h // 600), cv2.LINE_AA)
    return page


def make_table(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    """Wood-ish background with grain, distinct from the cream page."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    phase = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 25) * 40
    grain = np.sin(yy / 9.0 + phase) * 0.5 + 0.5
    base = np.array([40, 75, 120], np.float32)  # brown, BGR
    img = base * (0.75 + 0.35 * grain[..., None])
    img += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.0)[..., None] * 8
    return np.clip(img, 0, 255).astype(np.uint8)


def _look_at_camera(target_xy, dist, tilt_deg, tilt_dir_deg, roll_deg, f, img_size):
    """Pinhole camera looking at a point of the scene plane: (K, R, C).

    ``R``'s rows are the camera axes in scene coordinates and ``C`` is the
    camera centre; a scene point X projects to K @ R @ (X - C).
    """
    tilt, tdir, roll = np.radians([tilt_deg, tilt_dir_deg, roll_deg])
    target = np.array([target_xy[0], target_xy[1], 0.0])
    # Camera sits above the plane (negative z looks down onto it), offset by tilt.
    offset = np.array([np.sin(tilt) * np.cos(tdir), np.sin(tilt) * np.sin(tdir), -np.cos(tilt)]) * dist
    C = target + offset
    z = target - C
    z /= np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0])  # image y follows scene y
    x = np.cross(up, z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R0 = np.stack([x, y, z])  # rows: camera axes in world coords
    cr, sr = np.cos(roll), np.sin(roll)
    R = np.array([[cr, -sr, 0], [sr, cr, 0], [0, 0, 1]]) @ R0
    w, h = img_size
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]])
    return K, R, C


def _plane_homography(K, R, C) -> np.ndarray:
    """Homography from the scene plane (z=0, units = scene px) to image pixels."""
    H = K @ np.column_stack([R[:, 0], R[:, 1], -R @ C])
    return H / H[2, 2]


def _look_at_homography(target_xy, dist, tilt_deg, tilt_dir_deg, roll_deg, f, img_size):
    """Homography from scene plane (z=0, units = scene px) to image pixels."""
    return _plane_homography(*_look_at_camera(target_xy, dist, tilt_deg, tilt_dir_deg, roll_deg, f, img_size))


def _add_glare(rng, img, page_poly) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape[:2]
    out = img.astype(np.float32) / 255.0
    total = np.zeros((h, w), np.float32)
    x0, y0 = page_poly.min(axis=0)
    x1, y1 = page_poly.max(axis=0)
    for _ in range(int(rng.integers(1, 3))):
        cx, cy = rng.uniform(x0, x1), rng.uniform(y0, y1)
        sx, sy = rng.uniform(0.04, 0.12) * w, rng.uniform(0.03, 0.08) * h
        ang = rng.uniform(0, np.pi)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        u = (xx - cx) * np.cos(ang) + (yy - cy) * np.sin(ang)
        v = -(xx - cx) * np.sin(ang) + (yy - cy) * np.cos(ang)
        g = np.exp(-((u / sx) ** 2 + (v / sy) ** 2) ** 1.5)  # flat-topped, like a reflection
        total = np.maximum(total, g * rng.uniform(0.7, 1.1))
    # Specular light is white and adds on top, washing colours out.
    out = out + total[..., None] * (1.05 - out * 0.3)
    return np.clip(out * 255, 0, 255).astype(np.uint8), total > 0.15


def _sleeve_glare(rng, img, page_poly) -> tuple[np.ndarray, np.ndarray]:
    """Two to four long, wavy streaks: a clipped core inside a soft veil.

    Returns the image as float32 in 0..255 *without* clipping, so the camera's
    exposure is applied before the sensor clips, as in a real photo, and the
    ground-truth glare mask.
    """
    h, w = img.shape[:2]
    pw = float(np.linalg.norm(page_poly[1] - page_poly[0]))  # page width in this shot
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    total = np.zeros((h, w), np.float32)
    for _ in range(int(rng.integers(2, 5))):
        cx, cy = (rng.dirichlet([2, 2, 2, 2])[:, None] * page_poly).sum(axis=0)  # somewhere on the page
        ang = rng.uniform(0, np.pi)
        length = rng.uniform(0.4, 0.9) * pw
        amp, wavelength, phase = rng.uniform(0.005, 0.02) * pw, rng.uniform(0.15, 0.3) * pw, rng.uniform(0, 2 * np.pi)
        u = (xx - cx) * np.cos(ang) + (yy - cy) * np.sin(ang)
        v = -(xx - cx) * np.sin(ang) + (yy - cy) * np.cos(ang)
        d = np.abs(v - amp * np.sin(2 * np.pi * u / wavelength + phase))
        along = np.exp(-((np.abs(u) / (length / 2)) ** 6))  # flat along the streak, tapering at its ends
        core = np.exp(-((d / (0.005 * pw)) ** 4)) * rng.uniform(1.0, 1.3)  # about 1% of the page wide
        veil = np.exp(-((d / (0.025 * pw)) ** 2)) * rng.uniform(0.35, 0.55)  # about 5% wide above 0.15
        total = np.maximum(total, along * np.maximum(core, veil))
    out = img.astype(np.float32) / 255.0
    # Reflected light is white and adds on top; the camera's tone curve
    # compresses what lies under it, so the print's contrast fades too.
    out = out * (1 - 0.5 * np.minimum(total, 1))[..., None] + total[..., None]
    return out * 255, total > 0.15


def make_shadow(
    rng: np.random.Generator,
    scene_size: tuple[int, int],
    page_rect: tuple[int, int, int, int],
    shape: str | None = None,
    edge: str | None = None,
    depth: float | None = None,
    penumbra: float | None = None,
    along: float | None = None,
    reach: float | None = None,
) -> Shadow:
    """An arm, hand or phone shadow entering the page from ``edge``.

    ``penumbra`` is the width of the soft edge (10-90% of the depth) as a
    fraction of the page's long side, 0.5-3% by default; ``depth`` is 0.2-0.6;
    ``along`` is where on the edge it enters (0..1) and ``reach`` how far in
    it goes, as a fraction of the page's long side. Unset values are drawn
    from ``rng``.
    """
    shape = shape or str(rng.choice(SHADOW_SHAPES))
    edge = edge or str(rng.choice(EDGES))
    if shape not in SHADOW_SHAPES:
        raise ValueError(shape)
    if edge not in EDGES:
        raise ValueError(edge)
    depth = float(rng.uniform(0.2, 0.6)) if depth is None else depth
    penumbra = float(rng.uniform(0.005, 0.03)) if penumbra is None else penumbra
    along = float(rng.uniform(0.2, 0.8)) if along is None else along
    reach = float(rng.uniform(0.25, 0.45) if shape == "phone" else rng.uniform(0.3, 0.55)) if reach is None else reach
    tint = rng.uniform(-0.03, 0.03, 3)
    turn = np.radians(rng.uniform(-25, 25))

    px, py, pw, ph = page_rect
    L = max(pw, ph)
    k = 0.25  # drawn on a quarter-size grid, then blurred: the shadow has no fine detail
    sw, sh = scene_size
    sil = np.zeros((max(1, round(sh * k)), max(1, round(sw * k))), np.uint8)
    entry = {"left": (px, py + along * ph), "right": (px + pw, py + along * ph), "top": (px + along * pw, py), "bottom": (px + along * pw, py + ph)}[edge]
    inward = {"left": 0.0, "right": np.pi, "top": np.pi / 2, "bottom": -np.pi / 2}[edge] + turn
    d = np.array([np.cos(inward), np.sin(inward)])
    nrm = np.array([-d[1], d[0]])
    start = np.array(entry) - d * 0.15 * L  # from beyond the page edge
    end = np.array(entry) + d * reach * L

    def pt(p):
        return tuple(int(round(v * k * 16)) for v in p)

    def capsule(a, b, width):
        r = int(round(width / 2 * k * 16))
        cv2.line(sil, pt(a), pt(b), 255, max(1, int(round(width * k))), cv2.LINE_AA, 4)
        cv2.circle(sil, pt(a), r, 255, -1, cv2.LINE_AA, 4)
        cv2.circle(sil, pt(b), r, 255, -1, cv2.LINE_AA, 4)

    if shape == "phone":
        # A phone held from the edge's side: a slab with rounded corners,
        # the hand that holds it hidden in its shadow.
        width = rng.uniform(0.12, 0.18) * L
        r = 0.12 * width
        half = width / 2
        for p0, p1, side in ((start, end, half - r), (start + d * r, end - d * r, half)):
            quad = [p0 + nrm * side, p1 + nrm * side, p1 - nrm * side, p0 - nrm * side]
            cv2.fillConvexPoly(sil, np.array([pt(c) for c in quad], np.int32), 255, cv2.LINE_AA, 4)
        for c in (start + d * r + nrm * (half - r), end - d * r + nrm * (half - r), end - d * r - nrm * (half - r), start + d * r - nrm * (half - r)):
            cv2.circle(sil, pt(c), int(round(r * k * 16)), 255, -1, cv2.LINE_AA, 4)
    else:
        arm_w = rng.uniform(0.08, 0.13) * L
        wrist = end if shape == "arm" else end - d * 0.08 * L
        capsule(start, wrist, arm_w if shape == "arm" else 0.8 * arm_w)
        if shape == "hand":
            palm = wrist + d * 0.05 * L
            axes = (int(round(0.06 * L * k * 16)), int(round(0.05 * L * k * 16)))
            cv2.ellipse(sil, pt(palm), axes, float(np.degrees(inward)), 0, 360, 255, -1, cv2.LINE_AA, 4)
            for a in np.radians([-25, -9, 7, 22]) + rng.normal(0, 0.05, 4):
                f = np.array([np.cos(inward + a), np.sin(inward + a)])
                capsule(palm + f * 0.04 * L, palm + f * rng.uniform(0.1, 0.13) * L, 0.018 * L)
            thumb = np.array([np.cos(inward - 1.1), np.sin(inward - 1.1)])
            capsule(palm, palm + thumb * 0.09 * L, 0.022 * L)
    sigma = penumbra * L * k / 2.56  # a Gaussian edge rises from 10% to 90% over 2.56 sigma
    out = cv2.GaussianBlur(sil.astype(np.float32) / 255.0, (0, 0), max(sigma, 0.3))
    return Shadow(out, k, depth, tint.astype(np.float32), shape, edge)


def _srgb_to_linear(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.maximum(v, 0) ** (1 / 2.4) - 0.055)


def _add_shadow(img: np.ndarray, H_scene_to_image: np.ndarray, shadow: Shadow) -> tuple[np.ndarray, np.ndarray]:
    """Darken ``img`` (as drawn, before the camera) under ``shadow``.

    Light is taken away, so in linear light every channel is scaled by the
    same ``1 - depth`` at full shadow (give or take the slight tint). Returns
    the image as float32 in 0..255 and the ground-truth mask, where the
    shadow is at least half its depth.
    """
    h, w = img.shape[:2]
    k = shadow.scale
    H = H_scene_to_image @ np.diag([1 / k, 1 / k, 1.0])
    sil = cv2.warpPerspective(shadow.silhouette, H, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    factor = (1 - shadow.depth * sil)[..., None] * (1 + shadow.tint * sil[..., None])
    lin = _srgb_to_linear(img.astype(np.float32) / 255.0) * factor
    return (_linear_to_srgb(lin) * 255).astype(np.float32), sil >= 0.5


def _shadow_plan(rng: np.random.Generator, shadow: str, scene_size, page_rect, targets) -> list[Shadow | None]:
    """Which shots get which shadow: ``one`` (the first shot), ``all`` (the same in every shot), ``part``.

    ``part`` shadows the first shot from the page edge on its own side, away
    from the other shots, reaching into the part they also cover: the shadow
    lies partly where only that shot sees the page.
    """
    n = len(targets)
    if shadow not in SHADOWS:
        raise ValueError(shadow)
    if shadow == "none" or n == 0:
        return [None] * n
    if shadow == "part":
        t0 = np.array(targets[0])
        rest = np.mean(targets[1:], axis=0) if n > 1 else np.array([0.5, 0.5])
        dx, dy = t0 - rest
        edge = ("left" if dx <= 0 else "right") if abs(dx) >= abs(dy) else ("top" if dy <= 0 else "bottom")
        s = make_shadow(rng, scene_size, page_rect, edge=edge, along=float(rng.uniform(0.35, 0.65)), reach=float(rng.uniform(0.3, 0.4)))
        return [s] + [None] * (n - 1)
    s = make_shadow(rng, scene_size, page_rect)
    return [s] * n if shadow == "all" else [s] + [None] * (n - 1)


def make_shots(
    rng: np.random.Generator,
    scene: np.ndarray,
    page_rect: tuple[int, int, int, int],
    targets: list[tuple[float, float]],
    fill: float,
    img_size=(2000, 1500),
    glare: bool = True,
    glare_style: str = "spot",
    render=None,
    shadow: str | list[Shadow | None] = "none",
    shadow_rng: np.random.Generator | None = None,
) -> list[SynthShot]:
    """Photograph ``scene``; each target is a point in page-relative coords (0..1).

    ``fill`` is the fraction of the image width that the page's width spans;
    above 1 each shot only sees part of the page. ``glare_style`` is ``spot``
    or ``sleeve``. ``render(K, R, C, img_size, interp)``, when given, draws
    the shot instead of warping the flat ``scene`` and returns the image and
    the page's outline in it. ``shadow`` is one of ``SHADOWS`` (see
    ``_shadow_plan``), drawn from ``shadow_rng``, or a shadow (or None) for
    each target.
    """
    if glare_style not in ("spot", "sleeve"):
        raise ValueError(glare_style)
    if isinstance(shadow, str):
        shadow = _shadow_plan(shadow_rng or np.random.default_rng(0), shadow, scene.shape[1::-1], page_rect, targets)
    px, py, pw, ph = page_rect
    w, h = img_size
    f = 26 / 43.27 * np.hypot(w, h)  # 26 mm-equivalent, a typical phone main camera
    shots = []
    for (tx, ty), sh in zip(targets, shadow):
        dist = f * pw / (fill * w)
        K, R, C = _look_at_camera(
            (px + tx * pw, py + ty * ph),
            dist,
            tilt_deg=rng.uniform(5, 22),
            tilt_dir_deg=rng.uniform(0, 360),
            roll_deg=rng.uniform(-8, 8),
            f=f,
            img_size=img_size,
        )
        H = _plane_homography(K, R, C)
        interp = cv2.INTER_AREA if fill < 1 else cv2.INTER_LINEAR
        if render is not None:
            img, page_poly = render(K, R, C, img_size, interp)
        else:
            img = cv2.warpPerspective(scene, H, img_size, flags=interp, borderMode=cv2.BORDER_REFLECT)
            page_poly = cv2.perspectiveTransform(np.float32([[px, py], [px + pw, py], [px + pw, py + ph], [px, py + ph]]).reshape(-1, 1, 2), H).reshape(4, 2)
        page_poly = np.clip(page_poly, 0, [w - 1, h - 1])
        smask = None
        if sh is not None:  # the shadow darkens the print; glare reflects off the sleeve on top
            img, smask = _add_shadow(img, H, sh)
        gmask = np.zeros((h, w), bool)
        if glare and glare_style == "sleeve":
            img, gmask = _sleeve_glare(rng, img, page_poly)
        elif glare:
            img, gmask = _add_glare(rng, img, page_poly)
        # Exposure/white-balance drift, sensor noise, mild blur, JPEG.
        gain = rng.uniform(0.9, 1.1) * rng.uniform(0.97, 1.03, 3)
        img = np.clip(img.astype(np.float32) * gain + rng.normal(0, 2.0, img.shape), 0, 255).astype(np.uint8)
        img = cv2.GaussianBlur(img, (0, 0), 0.6)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        shots.append(SynthShot(img, H, gmask, smask, (K, R, C)))
    return shots


def make_clean_white_page(rng: np.random.Generator, w: int = 2200, h: int = 1700) -> np.ndarray:
    """A glare-free page of bright, unsaturated content that is not glare.

    White paper, prints with wide white borders, and one print whose upper
    part is a blown-out sky. A print cannot be whiter than its own paper, so
    the sky is the white of the border, flat apart from the paper's grain.
    Whites are those of diffusely lit paper, below what the camera clips.
    """
    grain = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2)[..., None]
    page = np.empty((h, w, 3), np.float32)
    page[:] = (212, 222, 228)  # off-white album paper, BGR
    page += grain * 3
    mx, my = int(w * 0.06), int(h * 0.07)
    cw, ch = (w - 3 * mx) // 2, (h - 3 * my) // 2
    sky = int(rng.integers(0, 4))
    white = np.array([226, 229, 230], np.float32)  # photo paper
    for k in range(4):
        r, c = divmod(k, 2)
        x, y = mx + c * (cw + mx), my + r * (ch + my)
        b = cw // 12  # wide white border
        pw, ph = cw - 2 * b, ch - 2 * b
        page[y : y + ch, x : x + cw] = white + grain[y : y + ch, x : x + cw] * 2
        photo = _print_photo(rng, pw, ph).astype(np.float32)
        if k == sky:
            top = int(ph * 0.4)
            ramp = np.clip((np.arange(ph) - top) / 12.0, 0, 1)[:, None, None]  # the horizon, slightly soft
            photo = white * (1 - ramp) + photo * ramp + grain[y + b : y + b + ph, x + b : x + b + pw] * 2 * (1 - ramp)
        page[y + b : y + b + ph, x + b : x + b + pw] = photo
    return np.clip(page, 0, 255).astype(np.uint8)


BINDINGS = ("left", "right", "top", "bottom")


def _bend(across: float, lift: float, strip: float, n: int = 400) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A page bent near its binding, cut into ``n`` strips parallel to it.

    The page rises to ``lift * across`` at the binding along the quadratic
    ``lift * across * (1 - x / (strip * across)) ** 2`` over the first
    ``strip`` of its footprint ``x`` (measured from the binding, which stays
    put) and lies flat beyond. Returns, for the strip edges, their distance
    from the binding along the page (page px), and their footprint and height
    in the scene.
    """
    xs = np.linspace(0.0, across, 20001)
    s = max(strip * across, 1e-9)
    slope = np.where(xs < s, -2 * lift * across / s * (1 - xs / s), 0.0)
    height = np.where(xs < s, lift * across * (1 - xs / s) ** 2, 0.0)
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(xs) * (slope[1:] + slope[:-1]) / 2))])
    u = np.linspace(0.0, across, n + 1)
    foot = np.interp(u, arc, xs)  # the page is shorter than its footprint's span only where it bends
    return u, foot, np.interp(foot, xs, height)


def _curved_renderer(page: np.ndarray, table: np.ndarray, origin: tuple[int, int], binding: str, lift: float, strip: float):
    """``make_shots`` renderer for ``page`` bent near ``binding`` on ``table``."""
    if binding not in BINDINGS:
        raise ValueError(binding)
    ph, pw = page.shape[:2]
    ox, oy = origin
    across = pw if binding in ("left", "right") else ph
    u, foot, height = _bend(across, lift, strip)
    # Neighbouring strips that both lie flat are one plane: draw them as one.
    keep = [0] + [k for k in range(1, len(u) - 1) if height[k] != 0 or height[k - 1] != 0] + [len(u) - 1]
    u, foot, height = u[keep], foot[keep], height[keep]

    def scene_xyz(a, b, z):
        """Scene point at footprint ``a`` from the binding and ``b`` along it, ``z`` towards the camera."""
        if binding == "left":
            x, y = ox + a, oy + b
        elif binding == "right":
            x, y = ox + pw - a, oy + b
        elif binding == "top":
            x, y = ox + b, oy + a
        else:
            x, y = ox + b, oy + ph - a
        return np.array([x, y, -z])  # the camera looks down from negative z

    def page_xy(a, b):
        """Page pixel at ``a`` along the page from the binding and ``b`` along it."""
        return {"left": (a, b), "right": (pw - a, b), "top": (b, a), "bottom": (b, ph - a)}[binding]

    along = ph if binding in ("left", "right") else pw
    u_all, foot_all, height_all = _bend(across, lift, strip)

    def locate(K, R, C, pts):
        """Image pixels of page pixels ``pts`` (n, 2), on the bent page."""
        x, y = np.asarray(pts, np.float64).T
        a, b = {"left": (x, y), "right": (pw - x, y), "top": (y, x), "bottom": (ph - y, x)}[binding]
        X = np.stack([scene_xyz(f, bb, z) for f, bb, z in zip(np.interp(a, u_all, foot_all), b, np.interp(a, u_all, height_all))])
        c = (X - C) @ R.T
        q = c @ K.T
        return q[:, :2] / q[:, 2:3]

    def render(K, R, C, img_size, interp):
        w, h = img_size
        img = cv2.warpPerspective(table, _plane_homography(K, R, C), img_size, flags=interp, borderMode=cv2.BORDER_REFLECT)

        def project(X):
            c = R @ (X - C)
            q = K @ c
            return q[:2] / q[2], c[2]

        strips = []
        for k in range(len(u) - 1):
            src, dst, depth = [], [], 0.0
            for a, b, z, uu in ((foot[k], 0, height[k], u[k]), (foot[k + 1], 0, height[k + 1], u[k + 1]), (foot[k + 1], along, height[k + 1], u[k + 1]), (foot[k], along, height[k], u[k])):
                q, d = project(scene_xyz(a, b, z))
                src.append(page_xy(uu, b))
                dst.append(q)
                depth += d
            strips.append((depth, np.float32(src), np.float32(dst)))
        for _, src, dst in sorted(strips, key=lambda t: -t[0]):  # farthest first, nearer ones drawn over them
            x0, y0 = np.floor(dst.min(axis=0)).astype(int) - 2
            x1, y1 = np.ceil(dst.max(axis=0)).astype(int) + 2
            x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
            if x1 <= x0 or y1 <= y0:
                continue
            Hs = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64) @ cv2.getPerspectiveTransform(src, dst)
            patch = cv2.warpPerspective(page, Hs, (x1 - x0, y1 - y0), flags=interp, borderMode=cv2.BORDER_REPLICATE)
            mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
            poly = np.round((dst - [x0, y0]) * 16).astype(np.int32)
            cv2.fillConvexPoly(mask, poly, 1, cv2.LINE_8, 4)
            # Strips overlap by a pixel so no seam of table shows between them.
            mask = cv2.dilate(mask, np.ones((2, 2), np.uint8)) if len(strips) > 1 else mask
            roi = img[y0:y1, x0:x1]
            roi[mask > 0] = patch[mask > 0]
        corners = [scene_xyz(foot[0], 0, height[0]), scene_xyz(foot[-1], 0, height[-1]), scene_xyz(foot[-1], along, height[-1]), scene_xyz(foot[0], along, height[0])]
        poly = np.array([project(X)[0] for X in corners], np.float32)
        return img, poly

    render.locate = locate
    return render


def _resolve_sleeve(rng: np.random.Generator, spec: SleeveSpec) -> SleeveSpec:
    """``spec`` with every unset value drawn from ``rng``."""
    if spec.binding not in BINDINGS:
        raise ValueError(spec.binding)
    return SleeveSpec(
        binding=spec.binding,
        crimp=spec.crimp,
        period=spec.period,
        gap=float(rng.uniform(0.0, 0.01)) if spec.gap is None else spec.gap,
        strip=spec.strip,
        clear=float(rng.uniform(0.0, 0.055)) if spec.clear is None else spec.clear,
        radius=float(rng.choice([0.0, 0.02])) if spec.radius is None else spec.radius,
        shift=float(rng.uniform(0.0, 0.005)) if spec.shift is None else spec.shift,
        turn=float(rng.uniform(-0.5, 0.5)) if spec.turn is None else spec.turn,
        print_inset=float(rng.uniform(0.01, 0.03)) if spec.print_inset is None else spec.print_inset,
    )


def _sleeve_layout(spec: SleeveSpec, pw: int, ph: int, rng: np.random.Generator):
    """Band widths per side (px) and the canvas size for a resolved sleeve around a ``pw`` x ``ph`` paper.

    Returns ({side: (kind of outermost band, band widths outside the paper's bounding box)}, (cw, ch), paper affine (2, 3)).
    """
    W = float(pw)
    across = ("top", "bottom") if spec.binding in ("left", "right") else ("left", "right")
    free = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}[spec.binding]
    widths = {}
    for side in ("top", "right", "bottom", "left"):
        if side in across:
            widths[side] = [("crimp", spec.crimp * W), ("clear", (spec.gap + rng.uniform(0, spec.shift)) * W)]
        elif side == spec.binding:
            widths[side] = [("strip", spec.strip * W)]
        else:
            widths[side] = [("clear", (spec.clear + rng.uniform(0, spec.shift)) * W)]
    th = np.radians(spec.turn)
    bw = pw * abs(np.cos(th)) + ph * abs(np.sin(th))
    bh = pw * abs(np.sin(th)) + ph * abs(np.cos(th))
    total = {k: sum(w for _, w in v) for k, v in widths.items()}
    cw = int(round(total["left"] + bw + total["right"]))
    ch = int(round(total["top"] + bh + total["bottom"]))
    # Paper turned about its centre, its bounding box placed inside the bands.
    cx, cy = total["left"] + bw / 2, total["top"] + bh / 2
    c, sn = np.cos(th), np.sin(th)
    A = np.array([[c, -sn, cx - (c * pw / 2 - sn * ph / 2)], [sn, c, cy - (sn * pw / 2 + c * ph / 2)]])
    return widths, (cw, ch), A, free


def make_sleeve(rng: np.random.Generator, paper: np.ndarray, spec: SleeveSpec, backdrop) -> tuple[np.ndarray, SleeveTruth]:
    """The paper in its sleeve, drawn on a canvas the size of the sleeve.

    ``backdrop(cw, ch)`` gives what lies under the sleeve (BGR uint8), seen
    through its clear plastic with a 10% to 20% white haze, and outside its
    rounded corners as it is. ``spec`` is resolved (see ``_resolve_sleeve``).
    """
    ph, pw = paper.shape[:2]
    W = float(pw)
    widths, (cw, ch), A, free = _sleeve_layout(spec, pw, ph, rng)
    back = backdrop(cw, ch).astype(np.float32)
    haze = float(rng.uniform(0.1, 0.2))
    canvas = back * (1 - haze) + 255 * haze
    # The paper, turned, over the clear plastic.
    warped = cv2.warpAffine(paper, A, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    mask = cv2.warpAffine(np.full((ph, pw), 255, np.uint8), A, (cw, ch), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT).astype(np.float32)[..., None] / 255
    canvas = canvas * (1 - mask) + warped.astype(np.float32) * mask
    yy, xx = np.mgrid[0:ch, 0:cw].astype(np.float32)
    dist = {"top": yy, "bottom": ch - 1 - yy, "left": xx, "right": cw - 1 - xx}
    along = {"top": xx, "bottom": xx, "left": yy, "right": yy}
    # Crimped seams: grey, ridged across, with a bright line along their middle.
    for side, bands in widths.items():
        kind, w = bands[0]
        d = dist[side]
        if kind == "crimp":
            inside = d < w
            ridge = 0.5 + 0.5 * np.sin(2 * np.pi * along[side] / (spec.period * W))
            shade = 145 + 55 * ridge + 15 * np.exp(-(((d - 0.5 * w) / (0.25 * w)) ** 2))
            canvas[inside] = shade[inside][:, None] * np.array([1.0, 1.0, 0.98], np.float32)
        elif kind == "strip":
            inside = d < w
            canvas[inside] = np.array([188, 222, 240], np.float32) + 0 * canvas[inside]  # opaque warm cream, BGR
    # Rounded outer corners: beyond them is what lies under the sleeve.
    r = spec.radius * W
    if r > 0:
        for cx, cy in ((r, r), (cw - 1 - r, r), (cw - 1 - r, ch - 1 - r), (r, ch - 1 - r)):
            corner = ((xx - cx) * np.sign(cx - cw / 2) > 0) & ((yy - cy) * np.sign(cy - ch / 2) > 0) & (np.hypot(xx - cx, yy - cy) > r)
            canvas[corner] = back[corner]
    canvas = cv2.GaussianBlur(np.clip(canvas, 0, 255), (0, 0), 0.7)
    paper_corners = (np.float64([[0, 0, 1], [pw, 0, 1], [pw, ph, 1], [0, ph, 1]]) @ A.T).astype(np.float64)
    sleeve_corners = np.float64([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    # The bands as seen: the seam or strip, then whatever clear plastic is
    # left between it and the (turned) paper, if it is wide enough to see.
    edge_dist = {"top": paper_corners[:, 1], "bottom": ch - paper_corners[:, 1], "left": paper_corners[:, 0], "right": cw - paper_corners[:, 0]}
    side_corners = {"top": (0, 1), "right": (1, 2), "bottom": (2, 3), "left": (3, 0)}
    bands = {}
    for side, bs in widths.items():
        kind, w = bs[0]
        seam = w if kind in ("crimp", "strip") else 0.0
        gap = float(np.mean(edge_dist[side][list(side_corners[side])])) - seam
        bands[side] = ([kind] if seam else []) + (["clear"] if gap >= 0.004 * W else [])
    return canvas.astype(np.uint8), SleeveTruth(spec, paper_corners, sleeve_corners, bands)


def make_case(
    seed: int,
    kind: str = "glare",
    n: int = 4,
    gap: float = 0.03,
    sides: int = 1,
    wide: bool = True,
    glare_style: str = "spot",
    lift: float = 0.03,
    strip: float = 0.12,
    binding: str = "left",
    shadow: str = "none",
    glare: bool = True,
    sleeve: SleeveSpec | None = None,
) -> SynthPage:
    """Build a test case.

    kind="glare": n full-page shots from different angles, each with glare.
    kind="stitch": an oversized page shot in overlapping parts (left/right,
    or a 2x2 grid when n >= 4), each with glare.
    kind="clean_white": n full-page shots of a page of white paper, prints
    with wide white borders and a blown-out sky, with no glare at all.
    kind="pair": pages lying flat side by side; the shots are of one of them
    (the ground truth) and a neighbouring page is partly in view, on one
    side or, with ``sides=2``, on both. With ``wide`` the last shot is wider
    and shows every page in full; without it all shots are close-ups.
    ``gap`` is the strip of table between pages as a fraction of the page
    width; at 0 they touch, as in an open album, and only a thin shadow marks
    the join.
    ``glare_style`` is ``spot`` or ``sleeve`` (see ``make_shots``).
    kind="curved": n full-page shots of a page bent near its ``binding``
    side, rising to ``lift`` (a fraction of the page's size across the
    binding) over the ``strip`` of the page next to it (see ``_bend``); a
    negative ``lift`` bends it away from the camera, into the gutter.
    kind="curved_stitch": an oversized page, bent the same way, shot in two
    overlapping halves side by side across the binding: one half holds the
    bent strip, the other the far side of the page.
    ``shadow`` is one of ``SHADOWS``: ``none``, ``one`` (the first shot has
    a shadow), ``all`` (every shot has the same shadow) or ``part`` (the
    first shot has a shadow reaching in from a part of the page only it
    covers; meant for ``stitch``). ``glare=False`` leaves the glare out of
    ``glare`` and ``stitch`` shots.
    ``sleeve`` puts the page in an album sleeve (``glare``, ``stitch`` and
    the curved kinds; see ``make_sleeve``), its unset values drawn from a
    stream of their own. The prints then lie ``print_inset`` inside the
    paper's edges, and on a curved page the strip is on the bent side.
    """
    if shadow not in SHADOWS:
        raise ValueError(shadow)
    if sleeve is not None and kind not in ("glare", "stitch", "curved", "curved_stitch"):
        raise ValueError(f"no sleeve for {kind} pages")
    rng = np.random.default_rng(seed)
    srng = np.random.default_rng([seed, 7])  # shadows have their own stream, so the rest of the case is unchanged
    lrng = np.random.default_rng([seed, 11])  # and so do sleeves
    spec = None
    if sleeve is not None:
        if kind in ("curved", "curved_stitch"):
            sleeve = replace(sleeve, binding=binding)
        spec = _resolve_sleeve(lrng, sleeve)
    if kind in ("curved", "curved_stitch"):
        return _curved_case(rng, kind, n, glare_style, lift, strip, binding, shadow, srng, spec, lrng)
    inset = spec.print_inset if spec is not None else None
    if kind == "stitch" and n >= 4:
        page = make_page(rng, 3200, 2200, margin=inset)
    elif kind == "stitch":
        page = make_page(rng, 4000, 2000, margin=inset)  # wide, like a two-page spread
    elif kind == "clean_white":
        page = make_clean_white_page(rng)
    else:
        page = make_page(rng, margin=inset)
    ph, pw = page.shape[:2]
    margin = int(0.6 * max(pw, ph))
    if kind == "pair":
        # Neighbouring pages in a row with the page, with a strip of table
        # between them or touching.
        others = [make_page(rng, pw, ph)]
        gap = int(round(gap * pw))
        left = bool(rng.integers(0, 2))
        if sides == 2:
            others.append(make_page(rng, pw, ph))
        k_page = 1 if left or sides == 2 else 0  # the page's slot in the row
        row = others[:k_page] + [page] + others[k_page:]
        scene = make_table(rng, len(row) * pw + (len(row) - 1) * gap + 2 * margin, ph + 2 * margin)
        for k, pg in enumerate(row):
            x = margin + k * (pw + gap)
            scene[margin : margin + ph, x : x + pw] = pg
            if gap == 0 and k > 0:
                # Abutting page edges throw a hairline shadow, darkest at the join.
                xs = np.arange(x - 8, x + 8)
                shade = 1 - 0.45 * np.exp(-(((xs - x + 0.5) / 2.5) ** 2))
                band = scene[margin : margin + ph, xs].astype(np.float32) * shade[None, :, None]
                scene[margin : margin + ph, xs] = np.clip(band, 0, 255).astype(np.uint8)
        x_page = margin + k_page * (pw + gap)
        rect = (x_page, margin, pw, ph)
        n_close = n - 1 if wide and n >= 2 else n
        targets = [(0.5 + rng.uniform(-0.05, 0.05), 0.5 + rng.uniform(-0.05, 0.05)) for _ in range(max(1, n_close))]
        mid = (len(row) * pw + (len(row) - 1) * gap) / 2 - k_page * (pw + gap)
        wide_targets = [(mid / pw, 0.5)] if wide and n >= 2 else []
        plan = _shadow_plan(srng, shadow, scene.shape[1::-1], rect, targets + wide_targets)
        shots = make_shots(rng, scene, rect, targets, fill=0.62, glare_style=glare_style, shadow=plan[: len(targets)])
        if wide_targets:
            # A wide shot of the whole row, aimed at its middle.
            shots += make_shots(rng, scene, rect, wide_targets, fill=0.36 if len(row) == 2 else 0.25, glare_style=glare_style, shadow=plan[len(targets) :])
        return SynthPage(page, scene, (x_page, margin), shots)
    truth = None
    if spec is not None:
        held = {}

        def backdrop(cw, ch):
            m = int(0.6 * max(cw, ch))
            held["table"], held["m"] = make_table(rng, cw + 2 * m, ch + 2 * m), m
            return held["table"][m : m + ch, m : m + cw]

        canvas, truth = make_sleeve(lrng, page, spec, backdrop)
        margin = held["m"]
        scene = held["table"]
        ch, cw = canvas.shape[:2]
        scene[margin : margin + ch, margin : margin + cw] = canvas
        rect = (margin, margin, cw, ch)
    else:
        scene = make_table(rng, pw + 2 * margin, ph + 2 * margin)
        scene[margin : margin + ph, margin : margin + pw] = page
        rect = (margin, margin, pw, ph)
    if kind == "glare":
        targets = [(0.5 + rng.uniform(-0.04, 0.04), 0.5 + rng.uniform(-0.04, 0.04)) for _ in range(n)]
        shots = make_shots(rng, scene, rect, targets, fill=0.72, glare=glare, glare_style=glare_style, shadow=shadow, shadow_rng=srng)
    elif kind == "clean_white":
        targets = [(0.5 + rng.uniform(-0.04, 0.04), 0.5 + rng.uniform(-0.04, 0.04)) for _ in range(n)]
        shots = make_shots(rng, scene, rect, targets, fill=0.72, glare=False, shadow=shadow, shadow_rng=srng)
    elif kind == "stitch":
        if n >= 4:
            targets = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)][:n]
            fill = 1.35
        else:
            targets = [(0.3, 0.5), (0.7, 0.5)]
            fill = 1.2
        targets = [(x + rng.uniform(-0.03, 0.03), y + rng.uniform(-0.03, 0.03)) for x, y in targets]
        shots = make_shots(rng, scene, rect, targets, fill=fill, glare=glare, glare_style=glare_style, shadow=shadow, shadow_rng=srng)
    else:
        raise ValueError(kind)
    if truth is None:
        return SynthPage(page, scene, (margin, margin), shots)
    m = margin

    def project(k, pts):
        return cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(-1, 1, 2) + m, shots[k].H_scene_to_image).reshape(-1, 2)

    truth.project = project
    ox, oy = np.round(truth.paper_corners[0]).astype(int) + m
    return SynthPage(page, scene, (int(ox), int(oy)), shots, truth)


def _curved_case(rng, kind: str, n: int, glare_style: str, lift: float, strip: float, binding: str, shadow: str = "none", srng=None, spec: SleeveSpec | None = None, lrng=None) -> SynthPage:
    if binding not in BINDINGS:
        raise ValueError(binding)
    stitch = kind == "curved_stitch"
    # Narrow margins, so that prints reach well into the bent strip, where
    # the bend shows in them.
    if stitch and binding in ("left", "right"):
        page = make_page(rng, 4000, 2000, margin=0.015 if spec is None else spec.print_inset)  # wide: halves side by side
    elif stitch:
        page = make_page(rng, 2200, 3000, margin=0.015 if spec is None else spec.print_inset)  # tall: halves above and below each other
    else:
        page = make_page(rng, margin=0.02 if spec is None else spec.print_inset)
    ph, pw = page.shape[:2]
    truth = None
    if spec is not None:
        # The sleeve is drawn around the paper, and the two bend together.
        held = {}

        def backdrop(cw, ch):
            m = int(0.6 * max(cw, ch))
            held["table"], held["m"] = make_table(rng, cw + 2 * m, ch + 2 * m), m
            return held["table"][m : m + ch, m : m + cw]

        drawn, truth = make_sleeve(lrng, page, spec, backdrop)
        margin, table = held["m"], held["table"]
        ph, pw = drawn.shape[:2]
    else:
        drawn = page
        margin = int(0.6 * max(pw, ph))
        table = make_table(rng, pw + 2 * margin, ph + 2 * margin)
    scene = table.copy()
    scene[margin : margin + ph, margin : margin + pw] = drawn
    rect = (margin, margin, pw, ph)
    render = _curved_renderer(drawn, table, (margin, margin), binding, lift, strip)
    if stitch and binding in ("left", "right"):
        targets, fill = [(0.3, 0.5), (0.7, 0.5)], 1.2
    elif stitch:
        targets, fill = [(0.5, 0.32), (0.5, 0.68)], 0.85
    else:
        targets, fill = [(0.5, 0.5)] * max(1, n), 0.72
    targets = [(x + rng.uniform(-0.03, 0.03), y + rng.uniform(-0.03, 0.03)) for x, y in targets]
    shots = make_shots(rng, scene, rect, targets, fill=fill, glare_style=glare_style, render=render, shadow=shadow, shadow_rng=srng)
    if truth is None:
        return SynthPage(page, scene, (margin, margin), shots)
    truth.project = lambda k, pts: render.locate(*shots[k].camera, pts)
    ox, oy = np.round(truth.paper_corners[0]).astype(int) + margin
    return SynthPage(page, scene, (int(ox), int(oy)), shots, truth)
