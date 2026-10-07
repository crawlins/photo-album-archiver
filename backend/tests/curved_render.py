"""Pictures of a known page surface, for testing the curvature fit on its own."""

from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from albumproc.curvature import KNOTS, PageSurface, initial_surface, upsample_map

K = np.array([[1500.0, 0, 800], [0, 1500.0, 600], [0, 0, 1]])
SIZE = (1600, 1200)
CORNERS = np.array([[330, 250], [1290, 230], [1330, 990], [300, 960]], np.float64)
ASPECT = 1.3


def flat_surface() -> PageSurface:
    return initial_surface(CORNERS, K, ASPECT)


def bent(binding: str, lift: float, strip: float = 0.12) -> PageSurface:
    """The flat page bent at ``binding``, rising to about ``lift`` (fraction across) over ``strip``.

    The profile angle falls linearly from the binding to ``strip``, which the
    spline knots represent only approximately.
    """
    s = replace(flat_surface(), binding=binding)
    u = np.asarray(KNOTS)
    shape = np.clip(1 - u / strip, 0, None)
    # A linear fall of theta over the strip lifts the binding by about theta0 * strip / 2.
    theta0 = np.arctan(2 * lift / strip)
    return replace(s, theta=theta0 * shape)


def render(surface: PageSurface, lines: bool = True, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """The page (cream, with dark lines across the binding when ``lines``) on a dark table.

    Returns the image and its valid mask. The table has a little texture and
    the image some noise, so edges are found as in a photo.
    """
    rng = np.random.default_rng(seed)
    w, h = SIZE
    step = 2
    gy, gx = np.mgrid[0 : h + step : step, 0 : w + step : step].astype(np.float64)
    f = surface.unproject(np.stack([gx.ravel(), gy.ravel()], axis=1)).reshape(gx.shape + (2,)).astype(np.float32)
    f = upsample_map(np.nan_to_num(f, nan=-1.0), step, (w, h))
    fx, fy = f[..., 0], f[..., 1]
    inside = (fx >= 0) & (fx <= 1) & (fy >= 0) & (fy <= 1)
    img = np.empty((h, w, 3), np.float32)
    img[:] = (60, 80, 110)  # brown table
    img += rng.normal(0, 6, (h, w, 1)).astype(np.float32)
    page = np.array([205, 225, 235], np.float32)
    img[inside] = page
    if lines:
        # Print borders running across the binding, at several heights, from
        # just inside the binding to past the middle of the page.
        across, along = (fx, fy) if surface.binding in ("left", "right") else (fy, fx)
        u = across if surface.binding in ("left", "top") else 1 - across
        for k in np.linspace(0.15, 0.85, 6):
            band = inside & (np.abs(along - k) < 0.004) & (u > 0.015) & (u < 0.6)
            img[band] = (60, 50, 40)
    img = cv2.GaussianBlur(img, (0, 0), 0.8) + rng.normal(0, 2, (h, w, 3)).astype(np.float32)
    return np.clip(img, 0, 255).astype(np.uint8), np.ones((h, w), bool)


def outline_quad(surface: PageSurface) -> np.ndarray:
    return surface.project(np.array([0, 1, 1, 0.0]), np.array([0, 0, 1, 1.0])).astype(np.float32)
