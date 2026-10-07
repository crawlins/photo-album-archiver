"""Aligning shots of a curved page with smooth displacement fields."""

import cv2
import numpy as np
import pytest

from albumproc.align import align_shots, apply_field, field_at, fit_field
from albumproc.curvature import AlignParams
from albumproc.synth import _print_photo, make_page

SHAPE = (680, 880)  # h, w of the low-res composed page


def _page(seed=1):
    page = make_page(np.random.default_rng(seed), 2200, 1700)
    return cv2.resize(page, (SHAPE[1], SHAPE[0]), interpolation=cv2.INTER_AREA)


def _shift(img, d):
    """``img`` resampled so that its content for point x comes from x + d(x)."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return cv2.remap(img, xx + d[..., 0], yy + d[..., 1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _bump(amp=3.0):
    """A smooth displacement, largest near the left edge as parallax in a bent strip would be."""
    h, w = SHAPE
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    fall = np.exp(-xx / (0.15 * w))
    return np.stack([amp * fall * (0.5 + yy / h), -0.6 * amp * fall * np.sin(np.pi * yy / h)], axis=2)


def test_known_smooth_displacement_is_recovered():
    page = _print_photo(np.random.default_rng(2), SHAPE[1], SHAPE[0])  # detail everywhere, to measure everywhere
    d = _bump()
    full = np.ones(SHAPE, bool)
    (ref, shot) = align_shots([page, _shift(page, d)], [full, full], [0, 1], AlignParams(), scale=0.5)
    assert ref.skipped == "reference" and shot.skipped is None
    g = shot.field.shape[0]
    # At the control points the recovered field (output px) matches, in low-res px.
    gy, gx = np.meshgrid(np.linspace(0, SHAPE[0] - 1, g), np.linspace(0, SHAPE[1] - 1, g), indexing="ij")
    truth = d[np.round(gy).astype(int), np.round(gx).astype(int)]
    inner = (slice(1, -1), slice(1, -1))  # the outermost ring has few features to go by
    # The shot shows at x what belongs at x + d(x): it is corrected by reading it at x - d(x).
    assert np.abs(shot.field[inner] * 0.5 + truth[inner]).max() < 0.5
    assert shot.offset_after <= shot.offset_before  # medians: most of this page is not displaced


def test_shot_with_too_few_matches_is_left_alone():
    page = _page()
    blank = np.full_like(page, 200)
    full = np.ones(SHAPE, bool)
    out = align_shots([page, blank], [full, full], [0, 1], AlignParams(), scale=1.0)
    assert out[1].field is None and out[1].skipped == "too_few_matches"


def test_correction_is_clamped():
    page = _page()
    d = _bump(amp=20.0)
    full = np.ones(SHAPE, bool)
    p = AlignParams(max_shift=0.005)
    _, shot = align_shots([page, _shift(page, d)], [full, full], [0, 1], p, scale=1.0)
    assert shot.field is not None
    assert np.linalg.norm(shot.field, axis=2).max() <= p.max_shift * max(SHAPE) + 1e-3


def test_stitched_shot_is_aligned_through_its_neighbour():
    # Three overlapping strips of the page. The last does not overlap the
    # reference at all; it is aligned through the middle one.
    page = _page(3)
    h, w = SHAPE
    masks = [np.zeros(SHAPE, bool) for _ in range(3)]
    masks[0][:, : int(0.45 * w)] = True
    masks[1][:, int(0.3 * w) : int(0.75 * w)] = True
    masks[2][:, int(0.6 * w) :] = True
    d = np.zeros((h, w, 2), np.float32)
    d[..., 0], d[..., 1] = 2.0, -1.5
    shots = [page * masks[0][..., None], page * masks[1][..., None], _shift(page, d) * masks[2][..., None]]
    out = align_shots(shots, masks, [0, 1, 2], AlignParams(), scale=1.0)
    assert out[1].skipped is None and out[1].offset_before < 0.5
    assert out[2].skipped is None and out[2].matches >= AlignParams().min_matches
    np.testing.assert_allclose(field_at(out[2].field, np.array([[0.7 * w, 0.5 * h]]), SHAPE)[0], [-2.0, 1.5], atol=0.5)
    assert out[2].offset_before == pytest.approx(2.5, abs=0.5)
    assert out[2].offset_after < 0.5


def test_fit_field_fades_to_zero_without_matches():
    pts = np.array([[x, y] for x in np.linspace(50, 300, 12) for y in np.linspace(50, 600, 12)])
    f = fit_field(pts, np.tile([3.0, 0.0], (len(pts), 1)), SHAPE, AlignParams())
    near = field_at(f, np.array([[150.0, 300.0]]), SHAPE)[0]
    far = field_at(f, np.array([[860.0, 300.0]]), SHAPE)[0]
    assert near[0] == pytest.approx(3.0, abs=0.2)
    assert abs(far[0]) < 1.0


def test_field_is_folded_into_the_map():
    W, H, step = 801, 601, 8
    gh, gw = (H - 1) // step + 2, (W - 1) // step + 2
    U, V = np.meshgrid(np.arange(gw) * step, np.arange(gh) * step)
    coarse = np.stack([U * 1.5 + 10, V * 1.5 + 20], axis=2).astype(np.float32)  # a plain scaling map
    field = np.zeros((16, 16, 2), np.float32)
    field[..., 0] = 4.0
    m = apply_field(coarse, field, step, (W, H))
    # The node for x now reads where the old map had x + 4: 6 shot px further right.
    np.testing.assert_allclose(m[..., 0] - coarse[..., 0], 6.0, atol=1e-3)
    np.testing.assert_allclose(m[..., 1], coarse[..., 1], atol=1e-3)
