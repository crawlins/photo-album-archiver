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
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class SynthShot:
    image: np.ndarray
    H_scene_to_image: np.ndarray
    glare_mask: np.ndarray  # bool, where glare was added


@dataclass
class SynthPage:
    page: np.ndarray  # ground-truth page, BGR
    scene: np.ndarray  # page on the table
    page_origin: tuple[int, int]  # page's top-left inside the scene
    shots: list[SynthShot]

    @property
    def images(self) -> list[np.ndarray]:
        return [s.image for s in self.shots]


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
) -> list[SynthShot]:
    """Photograph ``scene``; each target is a point in page-relative coords (0..1).

    ``fill`` is the fraction of the image width that the page's width spans;
    above 1 each shot only sees part of the page. ``glare_style`` is ``spot``
    or ``sleeve``. ``render(K, R, C, img_size, interp)``, when given, draws
    the shot instead of warping the flat ``scene`` and returns the image and
    the page's outline in it.
    """
    if glare_style not in ("spot", "sleeve"):
        raise ValueError(glare_style)
    px, py, pw, ph = page_rect
    w, h = img_size
    f = 26 / 43.27 * np.hypot(w, h)  # 26 mm-equivalent, a typical phone main camera
    shots = []
    for tx, ty in targets:
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
        shots.append(SynthShot(img, H, gmask))
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

    return render


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
    """
    rng = np.random.default_rng(seed)
    if kind in ("curved", "curved_stitch"):
        return _curved_case(rng, kind, n, glare_style, lift, strip, binding)
    if kind == "stitch" and n >= 4:
        page = make_page(rng, 3200, 2200)
    elif kind == "stitch":
        page = make_page(rng, 4000, 2000)  # wide, like a two-page spread
    elif kind == "clean_white":
        page = make_clean_white_page(rng)
    else:
        page = make_page(rng)
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
        shots = make_shots(rng, scene, rect, targets, fill=0.62, glare_style=glare_style)
        if wide and n >= 2:
            # A wide shot of the whole row, aimed at its middle.
            mid = (len(row) * pw + (len(row) - 1) * gap) / 2 - k_page * (pw + gap)
            shots += make_shots(rng, scene, rect, [(mid / pw, 0.5)], fill=0.36 if len(row) == 2 else 0.25, glare_style=glare_style)
        return SynthPage(page, scene, (x_page, margin), shots)
    scene = make_table(rng, pw + 2 * margin, ph + 2 * margin)
    scene[margin : margin + ph, margin : margin + pw] = page
    rect = (margin, margin, pw, ph)
    if kind == "glare":
        targets = [(0.5 + rng.uniform(-0.04, 0.04), 0.5 + rng.uniform(-0.04, 0.04)) for _ in range(n)]
        shots = make_shots(rng, scene, rect, targets, fill=0.72, glare_style=glare_style)
    elif kind == "clean_white":
        targets = [(0.5 + rng.uniform(-0.04, 0.04), 0.5 + rng.uniform(-0.04, 0.04)) for _ in range(n)]
        shots = make_shots(rng, scene, rect, targets, fill=0.72, glare=False)
    elif kind == "stitch":
        if n >= 4:
            targets = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)][:n]
            fill = 1.35
        else:
            targets = [(0.3, 0.5), (0.7, 0.5)]
            fill = 1.2
        targets = [(x + rng.uniform(-0.03, 0.03), y + rng.uniform(-0.03, 0.03)) for x, y in targets]
        shots = make_shots(rng, scene, rect, targets, fill=fill, glare_style=glare_style)
    else:
        raise ValueError(kind)
    return SynthPage(page, scene, (margin, margin), shots)


def _curved_case(rng, kind: str, n: int, glare_style: str, lift: float, strip: float, binding: str) -> SynthPage:
    if binding not in BINDINGS:
        raise ValueError(binding)
    stitch = kind == "curved_stitch"
    # Narrow margins, so that prints reach well into the bent strip, where
    # the bend shows in them.
    if stitch and binding in ("left", "right"):
        page = make_page(rng, 4000, 2000, margin=0.015)  # wide: halves side by side
    elif stitch:
        page = make_page(rng, 2200, 3000, margin=0.015)  # tall: halves above and below each other
    else:
        page = make_page(rng, margin=0.02)
    ph, pw = page.shape[:2]
    margin = int(0.6 * max(pw, ph))
    table = make_table(rng, pw + 2 * margin, ph + 2 * margin)
    scene = table.copy()
    scene[margin : margin + ph, margin : margin + pw] = page
    rect = (margin, margin, pw, ph)
    render = _curved_renderer(page, table, (margin, margin), binding, lift, strip)
    if stitch and binding in ("left", "right"):
        targets, fill = [(0.3, 0.5), (0.7, 0.5)], 1.2
    elif stitch:
        targets, fill = [(0.5, 0.32), (0.5, 0.68)], 0.85
    else:
        targets, fill = [(0.5, 0.5)] * max(1, n), 0.72
    targets = [(x + rng.uniform(-0.03, 0.03), y + rng.uniform(-0.03, 0.03)) for x, y in targets]
    shots = make_shots(rng, scene, rect, targets, fill=fill, glare_style=glare_style, render=render)
    return SynthPage(page, scene, (margin, margin), shots)
