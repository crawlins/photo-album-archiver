"""The learned single-shot glare detector and the overlap labels it is trained on.

The net is trained and scored on real album pages, which are not in the
repository (see ``tests/test_real.py``), so these tests cover the mechanics:
the model ships and loads, scores have the right shape and range, masks are
honoured, and the pipeline runs with it. Its accuracy on real pages is
recorded in ``tools/train_glare_net.py``'s docstring and the README.
"""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

from albumproc import detect_glare
from albumproc.glare import NET_PATH, GlareParams, hysteresis, net_map, net_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from real_glare_data import LABEL_CLEAN, LABEL_GLARE, LABEL_UNKNOWN, clean_labels, overlap_labels  # noqa: E402


def _print(h=300, w=400, seed=0):
    """A print-like shot: smooth colour fields with fine texture and sharp edges, BGR uint8."""
    rng = np.random.default_rng(seed)
    img = cv2.resize(rng.integers(30, 200, (6, 8, 3)).astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST).astype(np.float32)
    img += cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 1.0)[..., None] * 8
    return np.clip(img, 0, 255).astype(np.uint8)


def test_model_ships_with_the_package():
    assert NET_PATH.is_file()
    assert NET_PATH.stat().st_size < 2_000_000


def test_net_score_matches_the_shot_and_is_zero_outside_its_mask():
    img = _print(301, 457)
    mask = np.zeros(img.shape[:2], bool)
    mask[20:280, 40:400] = True
    s = net_score(img, mask)
    assert s.shape == img.shape[:2] and s.dtype == np.float32
    assert 0 <= s.min() and s.max() <= 1
    assert s[~mask].max() == 0


def test_net_score_ignores_what_lies_outside_the_mask():
    img = _print()
    mask = np.zeros(img.shape[:2], bool)
    mask[50:250, 50:350] = True
    bright = img.copy()
    bright[~mask] = 255
    assert np.array_equal(net_score(img, mask), net_score(bright, mask))


def test_net_score_is_deterministic_and_thread_safe():
    import threading

    img = _print(240, 320, seed=5)
    first = net_score(img)
    out = [None] * 4

    def run(k):
        out[k] = net_score(img)

    threads = [threading.Thread(target=run, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(np.array_equal(first, o) for o in out)


def test_net_map_grows_areas_from_its_score_only():
    img = _print(240, 320, seed=5)
    p = GlareParams(detector="net", net_seed=0.0, net_weak=0.0, net_min_seed_px=1)
    g = net_map(img, None, p)
    assert np.array_equal(g.glare, g.score)  # everything is a seed
    p = GlareParams(detector="net", net_seed=2.0, net_weak=0.0, net_min_seed_px=1)
    assert net_map(img, None, p).glare.max() == 0  # nothing is


def test_detect_glare_uses_features_by_default_and_the_net_on_request():
    img = _print()
    assert detect_glare(img).features is not None
    assert detect_glare(img, params=GlareParams(detector="net")).features is None


def test_hysteresis_grows_from_wide_seeds_only():
    score = np.zeros((60, 60), np.float32)
    score[10:30, 10:30] = 0.5  # weak area holding a wide seed
    score[18:22, 18:22] = 0.9
    score[40:50, 40:50] = 0.5  # weak area whose only seed is one pixel
    score[45, 45] = 0.9
    out = hysteresis(score, 0.7, 0.4, 3)
    assert out[10:30, 10:30].all()
    assert not out[40:50, 40:50].any()
    assert hysteresis(score, 0.7, 0.4, 1)[40:50, 40:50].all()


def _shots(n=3, h=120, w=160):
    rng = np.random.default_rng(1)
    base = np.clip(_print(h, w).astype(np.float32) * 0.7 + 30, 0, 255).astype(np.uint8)
    shots = [np.clip(base.astype(np.int16) + rng.integers(-2, 3, base.shape), 0, 255).astype(np.uint8) for _ in range(n)]
    return base, shots, [np.ones((h, w), bool) for _ in range(n)]


def test_overlap_labels_mark_glare_where_one_shot_is_brighter():
    _, shots, masks = _shots()
    shots[1][40:80, 60:110] = np.clip(shots[1][40:80, 60:110].astype(np.int16) + 60, 0, 255)
    lab = overlap_labels(shots, masks)
    assert (lab[1][50:70, 70:100] == LABEL_GLARE).all()
    assert (lab[0][50:70, 70:100] == LABEL_CLEAN).all()
    assert (lab[2] == LABEL_GLARE).sum() == 0


def test_overlap_labels_leave_a_shifted_edge_unlabelled_as_glare():
    base, shots, masks = _shots(2)
    edge = np.full_like(base, 40)
    edge[:, 80:] = 200
    shots = [edge, np.roll(edge, -2, axis=1)]  # the second shot's bright side starts 2 px earlier
    lab = overlap_labels(shots, masks)
    assert (lab[1] == LABEL_GLARE).sum() == 0
    assert (overlap_labels(shots, masks, shift_px=0)[1] == LABEL_GLARE).sum() > 0


def test_overlap_labels_need_two_shots():
    _, shots, masks = _shots(2)
    masks[1] = np.zeros_like(masks[1])
    masks[1][:, :80] = True
    lab = overlap_labels(shots, masks)
    assert (lab[0][:, 90:] == LABEL_UNKNOWN).all()
    assert (lab[1][:, 90:] == LABEL_UNKNOWN).all()


def test_clean_labels_drop_the_rim_and_thin_glare_lines():
    lab = np.zeros((50, 50), np.uint8)
    lab[20:30, 10:20] = LABEL_GLARE
    lab[5:45, 35] = LABEL_GLARE  # a one-pixel line
    mask = np.ones((50, 50), bool)
    mask[:, 45:] = False  # the shot ends here; the frame's own edge is not the shot's
    lab[~mask] = LABEL_UNKNOWN
    out = clean_labels(lab, mask, rim=3)
    assert (out[:, 42:] == LABEL_UNKNOWN).all()
    assert out[0, 20] == LABEL_CLEAN
    assert (out[20:30, 10:20] == LABEL_GLARE).all()
    assert (out[5:45, 35] == LABEL_UNKNOWN)[3:-3].all()
    assert out[25, 25] == LABEL_CLEAN


@pytest.mark.parametrize("detector", ["net", "features"])
def test_pipeline_runs_with_either_detector(detector):
    from albumproc import process_page
    from albumproc.pipeline import PageOptions
    from albumproc.synth import make_case

    case = make_case(10, "glare", 2)
    res = process_page(case.images, PageOptions(glare=GlareParams(detector=detector)))
    assert res.report.detector["glare"]["detector"] == detector
    assert res.residual_glare is not None and res.residual_glare.max() <= 1


def test_overlap_labels_ignore_light_falling_off_across_one_shot():
    _, shots, masks = _shots()
    ramp = np.linspace(1.0, 1.4, shots[0].shape[1], dtype=np.float32)[None, :, None]
    shots[0] = np.clip(shots[0] * ramp, 0, 255).astype(np.uint8)  # one shot lit more towards the right
    assert (overlap_labels(shots, masks, relight=False)[0] == LABEL_GLARE).mean() > 0.2
    assert (overlap_labels(shots, masks)[0] == LABEL_GLARE).mean() < 0.01
