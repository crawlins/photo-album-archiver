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
