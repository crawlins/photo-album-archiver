import cv2
import numpy as np

from albumproc.fuse import fuse


def _scene(h=400, w=600):
    rng = np.random.default_rng(3)
    base = rng.integers(40, 200, (h // 20, w // 20, 3)).astype(np.uint8)
    return np.kron(base, np.ones((20, 20, 1), np.uint8))


def test_glare_spot_replaced_and_exposure_matched():
    truth = _scene()
    h, w = truth.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    a = truth.astype(np.float32)
    b = truth.astype(np.float32) * 0.85  # second shot under-exposed
    spot = np.exp(-(((xx - 150) / 40.0) ** 2 + ((yy - 200) / 40.0) ** 2))
    a = np.clip(a + 200 * spot[..., None], 0, 255).astype(np.uint8)  # glare in shot a only
    b = np.clip(b, 0, 255).astype(np.uint8)
    masks = [np.ones((h, w), bool)] * 2
    res = fuse([a, b], masks, reference=0)
    np.testing.assert_allclose(res.gains[1], 1 / 0.85, rtol=0.04)
    inside = spot > 0.5
    err = np.abs(res.image.astype(int) - truth.astype(int))[inside].mean()
    assert err < 6
    assert res.glare_fraction[0] > res.glare_fraction[1]


def test_handover_between_shots_is_gradual():
    # Two glare-free shots that differ by shading and a pixel of
    # misregistration, as real photos of a page in a sleeve do. "Darker wins"
    # alone flips between them at every texture edge, leaving a patchwork of
    # tones; the weights must instead hand over gradually.
    rng = np.random.default_rng(4)
    h, w = 600, 800
    truth = np.clip(cv2.GaussianBlur(rng.normal(0, 1, (h, w, 3)).astype(np.float32), (0, 0), 6) * 400 + 128, 30, 220)
    shade = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 25)[..., None] * 6
    shade += 0.05 * (np.mgrid[0:h, 0:w][1] / w - 0.5)[..., None]
    moved = cv2.warpAffine(truth, np.float32([[1, 0, 1.5], [0, 1, 0.8]]), (w, h), borderMode=cv2.BORDER_REFLECT)
    a = np.clip(truth * (1 + shade) + rng.normal(0, 3, truth.shape), 0, 255).astype(np.uint8)
    b = np.clip(moved * (1 - shade) + rng.normal(0, 3, truth.shape), 0, 255).astype(np.uint8)
    res = fuse([a, b], [np.ones((h, w), bool)] * 2, reference=0)
    share = res.weights_small[0] / (res.weights_small[0] + res.weights_small[1])
    gy, gx = np.gradient(share)
    assert np.hypot(gx, gy).max() < 0.1  # was 0.26 with per-pixel switching
