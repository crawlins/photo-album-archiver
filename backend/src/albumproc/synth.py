"""Synthetic album-page photos for testing without real captures.

A fake album page (prints, captions, paper texture) lies on a fake table. A
pinhole camera looks at it from varied angles, so perspective is physically
correct, and each shot gets its own glare spots, exposure, noise and JPEG
compression. The page image itself is kept as ground truth.
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


def make_page(rng: np.random.Generator, w: int = 2200, h: int = 1700) -> np.ndarray:
    paper = np.array([215, 230, 238], np.float32)  # cream, BGR
    page = np.empty((h, w, 3), np.float32)
    page[:] = paper
    page += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 3)[..., None] * 6
    page = np.clip(page, 0, 255).astype(np.uint8)
    # 2x2 grid of prints with white borders and a caption under each.
    mx, my = int(w * 0.06), int(h * 0.07)
    cw, ch = (w - 3 * mx) // 2, (h - 3 * my) // 2
    for r in range(2):
        for c in range(2):
            x, y = mx + c * (cw + mx), my + r * (ch + my)
            pw, ph = int(cw * rng.uniform(0.75, 0.95)), int(ch * rng.uniform(0.7, 0.85))
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


def _look_at_homography(target_xy, dist, tilt_deg, tilt_dir_deg, roll_deg, f, img_size):
    """Homography from scene plane (z=0, units = scene px) to image pixels."""
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
    H = K @ np.column_stack([R[:, 0], R[:, 1], -R @ C])
    return H / H[2, 2]


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


def make_shots(
    rng: np.random.Generator,
    scene: np.ndarray,
    page_rect: tuple[int, int, int, int],
    targets: list[tuple[float, float]],
    fill: float,
    img_size=(2000, 1500),
    glare: bool = True,
) -> list[SynthShot]:
    """Photograph ``scene``; each target is a point in page-relative coords (0..1).

    ``fill`` is the fraction of the image width that the page's width spans;
    above 1 each shot only sees part of the page.
    """
    px, py, pw, ph = page_rect
    w, h = img_size
    f = 26 / 43.27 * np.hypot(w, h)  # 26 mm-equivalent, a typical phone main camera
    shots = []
    for tx, ty in targets:
        dist = f * pw / (fill * w)
        H = _look_at_homography(
            (px + tx * pw, py + ty * ph),
            dist,
            tilt_deg=rng.uniform(5, 22),
            tilt_dir_deg=rng.uniform(0, 360),
            roll_deg=rng.uniform(-8, 8),
            f=f,
            img_size=img_size,
        )
        img = cv2.warpPerspective(scene, H, img_size, flags=cv2.INTER_AREA if fill < 1 else cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        page_poly = cv2.perspectiveTransform(np.float32([[px, py], [px + pw, py], [px + pw, py + ph], [px, py + ph]]).reshape(-1, 1, 2), H).reshape(4, 2)
        page_poly = np.clip(page_poly, 0, [w - 1, h - 1])
        gmask = np.zeros((h, w), bool)
        if glare:
            img, gmask = _add_glare(rng, img, page_poly)
        # Exposure/white-balance drift, sensor noise, mild blur, JPEG.
        gain = rng.uniform(0.9, 1.1) * rng.uniform(0.97, 1.03, 3)
        img = np.clip(img.astype(np.float32) * gain + rng.normal(0, 2.0, img.shape), 0, 255).astype(np.uint8)
        img = cv2.GaussianBlur(img, (0, 0), 0.6)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        shots.append(SynthShot(img, H, gmask))
    return shots


def make_case(seed: int, kind: str = "glare", n: int = 4) -> SynthPage:
    """Build a test case.

    kind="glare": n full-page shots from different angles, each with glare.
    kind="stitch": an oversized page shot in overlapping parts (left/right,
    or a 2x2 grid when n >= 4), each with glare.
    """
    rng = np.random.default_rng(seed)
    if kind == "stitch" and n >= 4:
        page = make_page(rng, 3200, 2200)
    elif kind == "stitch":
        page = make_page(rng, 4000, 2000)  # wide, like a two-page spread
    else:
        page = make_page(rng)
    ph, pw = page.shape[:2]
    margin = int(0.6 * max(pw, ph))
    scene = make_table(rng, pw + 2 * margin, ph + 2 * margin)
    scene[margin : margin + ph, margin : margin + pw] = page
    rect = (margin, margin, pw, ph)
    if kind == "glare":
        targets = [(0.5 + rng.uniform(-0.04, 0.04), 0.5 + rng.uniform(-0.04, 0.04)) for _ in range(n)]
        shots = make_shots(rng, scene, rect, targets, fill=0.72)
    elif kind == "stitch":
        if n >= 4:
            targets = [(0.3, 0.3), (0.7, 0.3), (0.3, 0.7), (0.7, 0.7)][:n]
            fill = 1.35
        else:
            targets = [(0.3, 0.5), (0.7, 0.5)]
            fill = 1.2
        targets = [(x + rng.uniform(-0.03, 0.03), y + rng.uniform(-0.03, 0.03)) for x, y in targets]
        shots = make_shots(rng, scene, rect, targets, fill=fill)
    else:
        raise ValueError(kind)
    return SynthPage(page, scene, (margin, margin), shots)
