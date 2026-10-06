import cv2
import numpy as np
import pytest

from albumproc import detect_glare
from albumproc.glare import GlareFeatures, GlareMap, GlareParams, adapt_bias, combine, glare_features, glare_map, residual
from albumproc.synth import make_case


def _textured(colour, h=750, w=1000, seed=0):
    """A flat print colour with fine texture, as BGR uint8."""
    rng = np.random.default_rng(seed)
    tex = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.2) * 12
    return np.clip(np.float32(colour) + tex[..., None], 0, 255).astype(np.uint8)


def _veiled(img, a, box):
    """Add a white veil of strength ``a`` (0..1) inside ``box``: I = T(1 - a/2) + a."""
    out = img.astype(np.float32) / 255
    x0, y0, x1, y1 = box
    out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * (1 - a / 2) + a
    return np.clip(out * 255, 0, 255).astype(np.uint8)


BOX = (480, 350, 520, 390)  # smaller than the 60 px background window, as a sleeve streak is


def test_veil_and_desaturation_rise_with_veil_strength():
    base = _textured((40, 120, 60))
    veil, desat, flat = [], [], []
    for a in (0.0, 0.15, 0.3, 0.5):
        f = glare_features(_veiled(base, a, BOX))
        inside = (slice(360, 380), slice(490, 510))
        veil.append(f.veil[inside].mean())
        desat.append(f.desat[inside].mean())
        flat.append(f.flat[inside].mean())
    assert veil[0] < 0.01 and desat[0] < 0.05
    assert all(b > a for a, b in zip(veil, veil[1:]))
    assert all(b > a for a, b in zip(desat, desat[1:]))
    # Texture shrinks by about (1 - a/2) under this veil.
    assert flat[3] == pytest.approx(0.25, abs=0.1)


def test_clip_only_where_the_photo_clipped():
    img = _textured((90, 90, 90))
    img[50:80, 50:120] = 255
    f = glare_features(img)
    assert f.clip[60:70, 70:100].min() > 0.95
    assert f.clip[150:, 200:].max() == 0
    # Clipped in the camera's photo, not in the tone-matched one: still clipped.
    toned = (img.astype(np.float32) * 0.9).astype(np.uint8)
    assert glare_features(toned, None, img).clip[60:70, 70:100].min() > 0.95


def test_masked_out_border_changes_no_feature_inside():
    img = _veiled(_textured((40, 120, 60)), 0.4, BOX)
    mask = np.zeros(img.shape[:2], bool)
    mask[300:450, 420:600] = True
    black, white = img.copy(), img.copy()
    black[~mask] = 0
    white[~mask] = 255
    fb, fw = glare_features(black, mask), glare_features(white, mask)
    for name in ("veil", "desat", "flat", "clip"):
        np.testing.assert_array_equal(getattr(fb, name)[mask], getattr(fw, name)[mask])
        assert getattr(fb, name)[~mask].max() == 0


def test_strong_veil_is_found_and_its_surroundings_left_alone():
    # The default weights are cautious: a veil that nearly washes the print
    # out is found; fainter sheen is left to the comparison between shots.
    img = _veiled(_textured((40, 120, 60)), 0.8, BOX)
    g = detect_glare(img)
    inside = np.zeros(img.shape[:2], bool)
    x0, y0, x1, y1 = BOX
    inside[y0:y1, x0:x1] = True
    assert (g.glare[inside] > 0).mean() > 0.8
    assert (g.glare[~cv2.dilate(inside.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)] > 0).sum() == 0


def test_full_size_photo_is_checked_on_the_small_grid():
    img = np.zeros((2400, 3200, 3), np.uint8) + 120
    g = detect_glare(img)
    assert g.score.shape == (750, 1000)
    assert g.glare.shape == (750, 1000)


@pytest.mark.parametrize("seed", [30, 31])
def test_no_glare_on_white_borders_paper_and_sky(seed):
    case = make_case(seed, "clean_white", 3)
    ph, pw = case.page.shape[:2]
    x, y = case.page_origin
    d = 0.03 * pw  # the page's corners against the dark table are left out
    inner = np.float32([[x + d, y + d], [x + pw - d, y + d], [x + pw - d, y + ph - d], [x + d, y + ph - d]]).reshape(-1, 1, 2)
    for shot in case.shots:
        g = detect_glare(shot.image).glare
        f = g.shape[1] / shot.image.shape[1]
        poly = cv2.perspectiveTransform(inner, shot.H_scene_to_image).reshape(-1, 2) * f
        page = np.zeros(g.shape, np.uint8)
        cv2.fillPoly(page, [np.round(poly).astype(np.int32)], 1)
        assert (g[page > 0] > 0).sum() == 0


def test_thin_white_lines_do_not_seed_glare():
    img = _textured((70, 60, 120))
    for x in range(40, 380, 40):
        img[:, x : x + 2] = 250  # white border lines, 2 px wide
    assert detect_glare(img).glare.max() == 0


def _labelled(n_glare, n_clean, shift=0.0, seed=0):
    """Two shots whose single-image score is off by ``shift`` from the labels."""
    rng = np.random.default_rng(seed)
    h, w = 100, 100
    feats, excess, masks = [], [], []
    for _ in range(2):
        veil = np.zeros((h, w), np.float32)
        ex = np.zeros((h, w), np.float32)
        idx = rng.permutation(h * w)
        g, c = idx[:n_glare], idx[n_glare : n_glare + n_clean]
        veil.flat[g] = rng.normal(0.6 + shift, 0.08, len(g))
        veil.flat[c] = rng.normal(0.3 + shift, 0.08, len(c))
        ex.flat[g] = 0.2
        ex.flat[c] = 0.0
        ex.flat[idx[n_glare + n_clean :]] = 0.05  # neither label
        z = np.zeros((h, w), np.float32)
        feats.append(GlareFeatures(np.clip(veil, 0, 1), z, z, z))
        excess.append(ex)
        masks.append(np.ones((h, w), bool))
    return feats, excess, masks


def test_bias_adapts_towards_the_best_separating_value():
    p = GlareParams(bias=-3.0, w_veil=7.5, w_desat=0, w_flat=0, w_clip=0, adapt_clean_weight=1.0)
    # Glare at veil 0.6 and clean at 0.3 are best split at veil 0.45, so the
    # best bias is -7.5 * 0.45 = -3.4; with the features shifted down by 0.1
    # it moves up to -2.6.
    for shift, best in ((0.0, -3.375), (-0.1, -2.625)):
        bias, adapted = adapt_bias(*_labelled(1500, 3000, shift), p)
        assert adapted
        assert abs(bias - best) < 0.3
    # Weighting clean labels more, as the defaults do, keeps the page cautious.
    cautious, _ = adapt_bias(*_labelled(1500, 3000), GlareParams(bias=-3.0, w_veil=7.5, w_desat=0, w_flat=0, w_clip=0))
    assert cautious < -3.375 - 0.3


def test_bias_kept_without_enough_labels():
    p = GlareParams()
    assert adapt_bias(*_labelled(80, 3000), p) == (p.bias, False)  # too few glare labels (2 x 80 < 200)
    assert adapt_bias(*_labelled(300, 600), p) == (p.bias, False)  # too few labels in all (2 x 900 < 2000)
    f, e, m = _labelled(1500, 3000)
    assert adapt_bias(f[:1], e[:1], m[:1], p) == (p.bias, False)  # one shot: nothing to compare
    assert adapt_bias(f, e, m, GlareParams(adapt_bias=False)) == (p.bias, False)


def test_comparison_counts_only_where_the_shot_itself_looks_glared():
    score = np.array([[0.1, 0.35, 0.9]], np.float32)
    single = GlareMap(score, np.array([[0, 0, 0.9]], np.float32), None)
    excess = np.array([[0.2, 0.04, 0.0]], np.float32)
    np.testing.assert_allclose(combine(single, excess, 0.08, 0.3), [[0.0, 0.5, 0.9]])


def test_residual_is_the_weighted_mean():
    g = [np.array([[1.0, 0.0, 0.5]], np.float32), np.array([[0.0, 0.0, 1.0]], np.float32)]
    w = [np.array([[3.0, 1.0, 1.0]], np.float32), np.array([[1.0, 0.0, 3.0]], np.float32)]
    np.testing.assert_allclose(residual(g, w), [[0.75, 0.0, 0.875]])
    assert residual(g, [np.zeros((1, 3), np.float32)] * 2).max() == 0


def test_glare_map_is_zero_outside_its_mask():
    f = glare_features(_veiled(_textured((40, 120, 60)), 0.8, BOX))
    mask = np.zeros(f.veil.shape, bool)
    mask[:, :500] = True
    g = glare_map(f, mask)
    assert g.score[~mask].max() == 0 and g.glare[~mask].max() == 0
    assert g.glare[mask].max() > 0
