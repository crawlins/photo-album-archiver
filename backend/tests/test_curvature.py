"""The curved page model and its fit, on pictures of known page surfaces."""

from dataclasses import replace

import cv2
import numpy as np
import pytest

import albumproc.curvature as curvature
from albumproc.curvature import SIDES, CurvatureParams, PageSurface, fit_page, flatten_maps, output_size, view_angles
from albumproc.page import estimate_aspect

from curved_render import ASPECT, CORNERS, K, bent, flat_surface, outline_quad, render


def _fit(surface: PageSurface, p: CurvatureParams | None = None, img=None, valid=None, neighbour=None):
    if img is None:
        img, valid = render(surface)
    q = outline_quad(surface)
    aspect = estimate_aspect(q, (K[0, 2], K[1, 2]), K[0, 0])
    return fit_page(img, valid, q, np.eye(3), K, aspect, p or CurvatureParams(), neighbour)


# --- The model ------------------------------------------------------------


def test_flat_model_is_the_solvepnp_homography():
    s = flat_surface()
    R, _ = cv2.Rodrigues(s.rvec)
    G = K @ np.column_stack([R[:, 0], R[:, 1], s.tvec])
    x, y = np.random.default_rng(0).random((2, 200))
    planar = cv2.perspectiveTransform(np.stack([x, y * s.height], axis=1).reshape(-1, 1, 2), G).reshape(-1, 2)
    for side in SIDES:
        np.testing.assert_allclose(replace(s, binding=side).project(x, y), planar, atol=0.01)


@pytest.mark.parametrize("side", SIDES)
def test_distance_across_the_page_is_its_true_length(side):
    s = bent(side, 0.05)
    u = np.linspace(0, 1, 4001)
    v = np.full_like(u, 0.4)
    P = s.plane_points(u, v) if side in ("left", "right") else s.plane_points(v, u)
    assert np.linalg.norm(np.diff(P, axis=0), axis=1).sum() == pytest.approx(s.across, rel=1e-4)
    # ...while its footprint in the plane is shorter: the bent strip is seen foreshortened.
    assert s.footprint_width() < 0.995


def test_lift_and_strip_width_of_a_hand_made_profile():
    theta = np.radians([30, 20, 10, 2.5, 0, 0, 0, 0])
    s = replace(flat_surface(), theta=theta)
    u = np.linspace(0, 1, 200001)
    th = s.theta_at(u)
    # Lift at the binding: the rise of the profile, integrated along the page.
    assert s.max_lift() == pytest.approx(float(np.sum(np.sin(th[:-1]) * np.diff(u))), rel=1e-3)
    assert s.max_lift() == pytest.approx(s.profile(5)[0][1], rel=1e-6)
    assert s.profile(5)[-1] == (1.0, 0.0)
    # The profile is steeper than 2 degrees up to just past the 0.1 knot.
    assert s.strip_width() == pytest.approx(float(u[np.abs(th) > np.radians(2)].max()), abs=0.003)
    assert 0.1 < s.strip_width() < 0.18
    assert flat_surface().max_lift() == 0 and flat_surface().strip_width() == 0


@pytest.mark.parametrize("side", SIDES)
def test_unproject_inverts_project(side):
    s = bent(side, 0.05)
    x, y = np.random.default_rng(1).random((2, 300))
    np.testing.assert_allclose(s.unproject(s.project(x, y)), np.stack([x, y], axis=1), atol=1e-9)


def test_normals_are_perpendicular_to_the_page():
    s = bent("left", 0.05)
    u = np.linspace(0, 0.3, 50)
    P = s.plane_points(u, np.full_like(u, 0.5))
    tangent = (P[2:] - P[:-2])[1:-1]
    N = s.normals(u, np.full_like(u, 0.5))[2:-2]
    cos = np.abs((tangent * N).sum(axis=1)) / np.linalg.norm(tangent, axis=1)
    assert cos.max() < 1e-2
    assert (N[:, 2] < 0).all()  # towards the camera


def test_flattening_map_puts_the_corners_at_the_output_corners():
    s = bent("left", 0.03)
    W, H = output_size(s, 8000)
    assert W / H == pytest.approx(s.aspect, rel=2e-3)
    (m,) = flatten_maps(s, (W, H), 0.0, [np.eye(3)], step=8)
    ref = s.project(np.array([0, 1, 1, 0.0]), np.array([0, 0, 1, 1.0]))
    gh, gw = m.shape[:2]
    corners = curvature.bilinear(m, np.array([0, (W - 1) / 8, (W - 1) / 8, 0]), np.array([0, 0, (H - 1) / 8, (H - 1) / 8]))
    np.testing.assert_allclose(corners, ref, atol=0.05)


def test_view_angles_grow_where_the_page_bends_away():
    s = bent("left", 0.08)
    grid = np.array([[0.0, 0.5], [0.6, 0.5]])
    (a,) = view_angles(s, [np.eye(3)], grid)
    assert a[0] > a[1] + 20


# --- The fit --------------------------------------------------------------


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("lift,tol", [(0.005, 0.3), (0.02, 0.1), (0.05, 0.1)])
def test_fit_finds_the_binding_and_the_lift(side, lift, tol):
    s = bent(side, lift)
    f = _fit(s)
    assert f.best.binding == side
    assert f.best.max_lift() == pytest.approx(s.max_lift(), rel=tol)
    if lift >= 0.02:  # a 0.5% lift is a few pixels here: found, but not always clearly enough to apply
        assert f.reason is None and f.surface is f.best
        assert f.outline_rms_curved < f.outline_rms_planar


def test_flat_page_is_reported_flat():
    f = _fit(flat_surface())
    assert f.surface is None and f.reason == "flat"
    assert f.best.max_lift() < CurvatureParams().min_lift


def test_lines_carry_the_fit_when_the_binding_side_is_hidden():
    s = bent("left", 0.03)
    img, valid = render(s)
    # The binding edge hidden along most of its length (under a ring, or the
    # facing page), so that side of the outline cannot be traced.
    band = np.zeros(valid.shape, np.uint8)
    edge = s.project(np.zeros(50), np.linspace(0.1, 0.9, 50)).astype(np.int32)
    cv2.polylines(band, [edge.reshape(-1, 1, 2)], False, 1, 90)
    valid &= band == 0
    f = _fit(s, img=img, valid=valid)
    assert f.outline[3] is None  # the left side
    assert f.lines_used >= 2
    assert f.surface is not None and f.surface.binding == "left"
    assert f.surface.max_lift() == pytest.approx(s.max_lift(), rel=0.25)


def test_named_binding_is_the_only_side_tried(monkeypatch):
    tried = []
    real = curvature.fit_surface

    def spy(outline, lines, init, *a, **kw):
        tried.append(init.binding)
        return real(outline, lines, init, *a, **kw)

    monkeypatch.setattr(curvature, "fit_surface", spy)
    f = _fit(bent("left", 0.03), CurvatureParams(binding="right"))
    assert set(tried) == {"left", "right"}  # the flat fit is made with the default side
    assert tried.count("left") == 1
    assert f.best.binding == "right"


def test_side_facing_a_neighbouring_page_is_preferred():
    # A flat page is explained equally well with a bend at any side; the
    # neighbour tips the choice.
    assert _fit(flat_surface(), neighbour="right").best.binding == "right"
    assert _fit(flat_surface(), neighbour="left").best.binding == "left"


def test_off_fits_nothing(monkeypatch):
    monkeypatch.setattr(curvature, "trace_outline", lambda *a: pytest.fail("traced with curvature off"))
    f = _fit(bent("left", 0.05), CurvatureParams(mode="off"))
    assert f.surface is None and f.reason == "off"


def test_force_applies_a_converged_fit_below_the_thresholds():
    f = _fit(flat_surface(), CurvatureParams(mode="force"))
    assert f.surface is not None and f.reason is None


def test_fit_that_does_not_converge_falls_back(monkeypatch):
    def broken(*a, **kw):
        raise ValueError("did not converge")

    monkeypatch.setattr(curvature, "least_squares", broken)
    f = _fit(bent("left", 0.05))
    assert f.surface is None and f.reason == "fit_failed"
    assert f.max_bow > CurvatureParams().min_lift  # so the page gets a curvature_uncorrected warning


def test_profile_folding_back_falls_back():
    s = replace(bent("left", 0.05), theta=np.radians([85, 60, 30, 0, 0, 0, 0, 0]))
    assert not curvature._sane(s, [None] * 4, np.eye(3), 1000.0, CurvatureParams())
    assert curvature._sane(bent("left", 0.05), [None] * 4, np.eye(3), 1000.0, CurvatureParams())


def test_corners_off_the_traced_outline_fall_back():
    s = bent("left", 0.02)
    img, valid = render(s)
    outline = curvature.trace_outline(img, valid, outline_quad(s), 0.03)
    traced = curvature._traced_corners(outline)
    diag = float(np.linalg.norm(CORNERS[2] - CORNERS[0]))
    assert curvature._sane(s, traced, np.eye(3), diag, CurvatureParams())
    moved = replace(s, tvec=s.tvec * 1.05)  # the page 5% farther away: corners pulled in by about 3%
    assert not curvature._sane(moved, traced, np.eye(3), diag, CurvatureParams())


def test_outline_not_found_falls_back():
    # The quad lies on bare paper, so no side of the outline is traced and
    # there are no lines to make up for it.
    s = bent("left", 0.05)
    img = np.full((1200, 1600, 3), 200, np.uint8)
    f = fit_page(img, np.ones((1200, 1600), bool), outline_quad(s), np.eye(3), K, ASPECT, CurvatureParams())
    assert f.surface is None and f.reason == "fit_failed" and f.max_bow == 0


def test_traced_outline_follows_the_bow():
    s = bent("top", 0.05)
    img, valid = render(s)
    outline = curvature.trace_outline(img, valid, outline_quad(s), 0.03)
    assert all(o is not None for o in outline)
    truth = s.project(np.linspace(0.0, 1.0, 20001), np.zeros(20001))
    pts = outline[0]  # distance from each traced point of the top side to the true top edge
    dist = np.min(np.linalg.norm(pts[:, None] - truth[None], axis=2), axis=1)
    assert np.median(dist) < 0.5 and dist.max() < 2.0
    bows = curvature.side_bows(outline, 1000.0)
    assert bows["left"] > 0.003 and bows["right"] > 0.003  # a top binding bows the sides
    assert bows["bottom"] < 0.001
