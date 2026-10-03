import cv2
import numpy as np
import pytest

from albumproc.page import detect_page, estimate_aspect, focal_px_from_35mm, order_corners
from albumproc.synth import make_case


def test_order_corners_any_input_order():
    pts = np.float32([[90, 80], [10, 5], [100, 0], [0, 90]])
    q = order_corners(pts)
    np.testing.assert_allclose(q, [[10, 5], [100, 0], [90, 80], [0, 90]])


def _project(rect_w, rect_h, f, tilt_deg, size=(2000, 1500)):
    """Corners of a w x h rectangle seen by a tilted pinhole camera."""
    t = np.radians(tilt_deg)
    R = np.array([[1, 0, 0], [0, np.cos(t), -np.sin(t)], [0, np.sin(t), np.cos(t)]])
    K = np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1]])
    pts = []
    for x, y in [(-rect_w / 2, -rect_h / 2), (rect_w / 2, -rect_h / 2), (rect_w / 2, rect_h / 2), (-rect_w / 2, rect_h / 2)]:
        p = K @ (R @ np.array([x, y, 0.0]) + np.array([0, 0, 3 * f]))
        pts.append(p[:2] / p[2])
    return np.array(pts)


@pytest.mark.parametrize("tilt", [10, 25, 35])
def test_aspect_recovers_true_shape_under_perspective(tilt):
    f = focal_px_from_35mm(26, 2000, 1500)
    corners = _project(1400, 1000, f, tilt)
    assert estimate_aspect(corners, (1000, 750), f) == pytest.approx(1.4, rel=0.005)
    # The naive side-length ratio is visibly wrong at these angles.
    naive = np.linalg.norm(corners[1] - corners[0]) / np.linalg.norm(corners[3] - corners[0])
    assert abs(naive - 1.4) > 0.01


def test_detects_page_in_single_shot_despite_glare():
    case = make_case(1, "glare", 4)
    for shot in case.shots:
        q = detect_page(shot.image)
        mx, my = case.page_origin
        ph, pw = case.page.shape[:2]
        truth = cv2.perspectiveTransform(
            np.float32([[mx, my], [mx + pw, my], [mx + pw, my + ph], [mx, my + ph]]).reshape(-1, 1, 2),
            shot.H_scene_to_image,
        ).reshape(4, 2)
        err = np.linalg.norm(q.corners - truth, axis=1).max()
        assert err < 15, (q.method, err)
