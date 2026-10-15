"""Synthetic shots warped into the page frame with their true homographies.

Tests of the shadow detector and of fusion use these instead of the full
pipeline, so that registration error plays no part and each shot's shadow
mask is known in the page frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Framed:
    warped: list[np.ndarray]  # uint8 BGR, page frame
    masks: list[np.ndarray]  # bool, where each shot covers the page
    shadow: list[np.ndarray]  # bool, each shot's true shadow (all False without one)
    glare: list[np.ndarray]  # bool, each shot's true glare
    truth: np.ndarray  # the page, at the same scale


def page_frame(case, scale: float = 0.5) -> Framed:
    ph, pw = case.page.shape[:2]
    ox, oy = case.page_origin
    size = (round(pw * scale), round(ph * scale))
    S = np.array([[scale, 0, 0], [0, scale, 0], [0, 0, 1.0]])
    T = np.array([[1, 0, ox], [0, 1, oy], [0, 0, 1.0]])  # page to scene
    warped, masks, shadow, glare = [], [], [], []
    for s in case.shots:
        H = S @ np.linalg.inv(s.H_scene_to_image @ T)  # shot to page frame
        h, w = s.image.shape[:2]
        warped.append(cv2.warpPerspective(s.image, H, size, flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR))
        m = cv2.warpPerspective(np.full((h, w), 255, np.uint8), H, size, flags=cv2.INTER_NEAREST)
        masks.append(cv2.erode(m, np.ones((5, 5), np.uint8)) > 0)
        sm = s.shadow_mask if s.shadow_mask is not None else np.zeros((h, w), bool)
        shadow.append((cv2.warpPerspective(sm.astype(np.uint8) * 255, H, size, flags=cv2.INTER_LINEAR) >= 128) & masks[-1])
        glare.append((cv2.warpPerspective(s.glare_mask.astype(np.uint8) * 255, H, size, flags=cv2.INTER_LINEAR) >= 128) & masks[-1])
    return Framed(warped, masks, shadow, glare, cv2.resize(case.page, size, interpolation=cv2.INTER_AREA))


def to_grid(mask: np.ndarray, grid_shape: tuple[int, int]) -> np.ndarray:
    """A full-size page-frame mask on the fusion grid (True where mostly set)."""
    gh, gw = grid_shape
    return cv2.resize(mask.astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA) >= 0.5
