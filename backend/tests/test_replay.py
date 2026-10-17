"""The single-shot glare replay (``tools/replay_glare.py``), on synthetic pages only.

The real replay runs by hand on the private photos. Here a wide page is shot
in two halves twice over, each shot with glare of its own, and the
generator's own page stands in for the reference composition.
"""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import replay_glare as rg  # noqa: E402
from albumproc.synth import make_page, make_shots, make_table  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _box(shape, x0, y0, x1, y1):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_output_inside_the_repository_is_refused(tmp_path):
    for inside in (REPO, REPO / "backend", REPO / "backend" / "tools" / ".." / "out"):
        with pytest.raises(SystemExit, match="inside the repository"):
            rg.check_out(inside)
    assert rg.check_out(tmp_path / "replay") == (tmp_path / "replay").resolve()


def test_stitch_pairs_cover_the_page_overlap_little_and_each_add_their_part():
    shape = (40, 100)
    masks = {
        0: _box(shape, 0, 0, 60, 40),  # left
        1: _box(shape, 45, 0, 100, 40),  # right: with 0, overlap 15%
        2: _box(shape, 0, 0, 100, 40),  # whole page
        3: _box(shape, 30, 0, 100, 40),  # right, wide: with 0, overlap 30%
        4: _box(shape, 95, 0, 100, 40),  # a sliver
    }
    ref = rg.Ref("t", [], list(masks), np.zeros(shape + (3,), np.uint8), np.ones(shape, bool), masks)
    # (2, 4) covers the page but is a whole shot and a sliver, not a stitch;
    # (0, 3) overlaps more than (0, 1), so comes after it.
    assert rg.stitch_pairs(ref) == [(0, 1), (0, 3)]
    assert rg.stitch_pairs(ref, limit=1) == [(0, 1)]
    assert rg.stitch_pairs(ref, max_overlap=0.2) == [(0, 1)]


def test_prompted_shot_is_the_best_or_the_worst_cover_of_the_rectangle():
    shape = (40, 100)
    masks = {5: _box(shape, 50, 0, 100, 40), 6: _box(shape, 70, 0, 100, 40), 7: _box(shape, 90, 0, 100, 40), 8: _box(shape, 0, 0, 40, 40)}
    ref = rg.Ref("t", [], list(masks), np.zeros(shape + (3,), np.uint8), np.ones(shape, bool), masks)
    box = _box(shape, 60, 10, 100, 30)
    assert rg.prompted_shot(ref, box, [5, 6, 7, 8], "best") == 5  # ties on cover go to the lower index
    assert rg.prompted_shot(ref, box, [6, 7, 8], "best") == 6
    assert rg.prompted_shot(ref, box, [5, 6, 7, 8], "worst") == 6  # three quarters; 7 covers a quarter only
    assert rg.prompted_shot(ref, box, [7, 8], "worst") == 7  # nothing covers half: the best there is
    assert rg.prompted_shot(ref, box, [8], "best") is None
    assert rg.prompted_shot(ref, box, [], "best") is None


def _result(page, glare, single, removed, warned, unwarned_single, flagged=0, n_flagged=0, clean=0, rule="best"):
    return {
        "rule": rule,
        "page_px": page,
        "glare_px": glare,
        "single_glare_px": single,
        "removed_px": removed,
        "detectors": {
            "features": {
                "glare_in_warned_px": warned,
                "single_glare_unwarned_px": unwarned_single,
                "overlap_glare_unwarned_px": glare - warned - unwarned_single,
                "single_glare_flagged_px": flagged,
                "regions_flagged": n_flagged,
                "regions_flagged_clean": clean,
            }
        },
    }


def test_summary_weights_by_area_and_its_shares_add_up():
    a = _result(1000, 100, 80, [40, 76], 85, 5, flagged=60, n_flagged=1)
    b = _result(1000, 20, 20, [20, 20], 20, 0, n_flagged=2, clean=1)
    s = rg.summarise([a, b])
    assert s["replays"] == 2
    assert s["glare"] == 0.06 and s["single_glare"] == 0.05
    assert s["removed_1"] == 0.6 and s["removed_2"] == 0.96
    d = s["detectors"]["features"]
    assert d["glare_in_warned"] + d["single_glare_unwarned"] + d["overlap_glare_unwarned"] == pytest.approx(1.0, abs=1e-3)
    assert d["glare_in_warned"] == round(105 / 120, 4) and d["single_glare_unwarned"] == round(5 / 120, 4)
    assert d["single_glare_flagged"] == 0.6 and d["regions_flagged"] == 3 and d["flagged_clean"] == round(1 / 3, 4)
    assert rg.gate(s) == {"removed": 0.96, "single_glare_unwarned": round(5 / 120, 4), "pass": True}
    # Too little removed, or too much glare outside every warning, stops the spec.
    assert not rg.gate(rg.summarise([_result(1000, 100, 80, [40, 60], 85, 5)]))["pass"]
    assert not rg.gate(rg.summarise([_result(1000, 100, 80, [80, 80], 70, 15)]))["pass"]
    # Nothing to measure is reported as such, not as 0.
    empty = rg.summarise([])
    assert empty["single_glare"] is None and empty["removed_2"] is None and not rg.gate(empty)["pass"]


@pytest.fixture(scope="module")
def twice_stitched():
    """A wide page shot in left and right halves twice, every shot with glare; its reference is the page itself."""
    rng = np.random.default_rng(5)
    page = make_page(rng, 4000, 2000)
    ph, pw = page.shape[:2]
    m = int(0.6 * pw)
    scene = make_table(rng, pw + 2 * m, ph + 2 * m)
    scene[m : m + ph, m : m + pw] = page
    targets = [(0.3, 0.5), (0.7, 0.5), (0.28, 0.48), (0.72, 0.52)]
    shots = make_shots(rng, scene, (m, m, pw, ph), targets, fill=1.2)
    ref = rg.reference("synthetic", [s.image for s in shots])
    # The generator's page, laid onto the reference grid, in place of the
    # composition of all the shots.
    small = cv2.resize(page, (ref.page.shape[1], round(ref.page.shape[1] * ph / pw)), interpolation=cv2.INTER_AREA)
    H, _ = rg.frame_homography(small, ref.page)
    ref.page = cv2.warpPerspective(small, H, ref.page.shape[1::-1], flags=cv2.INTER_LINEAR)
    # Shot pixels -> the reference grid, for the generator's glare masks.
    to_grid = [H @ np.diag([small.shape[1] / pw, small.shape[0] / ph, 1.0]) @ np.array([[1, 0, -m], [0, 1, -m], [0, 0, 1.0]]) @ np.linalg.inv(s.H_scene_to_image) for s in shots]
    cache = {}

    def run(subset):
        if subset not in cache:
            cache[subset] = rg.compose(ref, subset)
        return cache[subset]

    return shots, ref, to_grid, run


def test_truth_is_the_generator_s_glare_where_one_shot_covers_the_page(twice_stitched):
    shots, ref, to_grid, run = twice_stitched
    assert rg.stitch_pairs(ref)[:1] in ([(0, 1)], [(0, 3)], [(1, 2)], [(2, 3)])
    c = run((0, 1))
    assert c.dropped == [] and c.page is not None
    found = rg.truth(c.page, c.covered, ref)
    inner = cv2.erode(c.single.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    drawn = np.zeros_like(found)
    for k in (0, 1):
        g = cv2.warpPerspective(shots[k].glare_mask.astype(np.uint8), to_grid[k], found.shape[::-1], flags=cv2.INTER_NEAREST) > 0
        own = rg._onto(np.ones(shots[k].image.shape[:2], np.uint8), to_grid[k], found.shape)
        drawn |= g & own
    near = cv2.dilate(drawn.astype(np.uint8), np.ones((2 * rg.SHIFT_PX + 3,) * 2, np.uint8)) > 0
    f, d = found & inner, drawn & inner
    assert d.sum() > 500
    # Nearly all the truth is drawn glare, and most of the drawn glare (all
    # but its faint fringe, under the label threshold) is found.
    assert (f & near).sum() >= 0.9 * f.sum()
    assert (f & d).sum() >= 0.5 * d.sum()


def test_a_prompted_shot_over_the_region_removes_its_glare(twice_stitched):
    _, ref, _, run = twice_stitched
    tr = rg.replay(ref, (0, 1), "best", run)
    assert tr.skipped is None
    assert len(tr.steps) >= 2 and tr.steps[1].shots in ((0, 1, 2), (0, 1, 3))
    r = rg.measure(ref, tr)
    assert r["single_glare_px"] > 500
    assert r["removed_px"][0] >= 0.3 * r["single_glare_px"]
    assert r["removed_px"][1] >= 0.8 * r["single_glare_px"]
    for d in r["detectors"].values():
        assert d["glare_in_warned_px"] + d["single_glare_unwarned_px"] + d["overlap_glare_unwarned_px"] == r["glare_px"]
        # Both halves are warned single coverage: none of their glare is unwarned.
        assert d["single_glare_unwarned_px"] <= 0.05 * r["single_glare_px"]


def test_whole_shots_are_the_one_photo_starts_spread_over_the_capture_order():
    shape = (40, 100)
    masks = {i: _box(shape, 0, 0, 100, 40) for i in range(6)}
    masks[2] = _box(shape, 0, 0, 60, 40)
    ref = rg.Ref("t", [], list(masks), np.zeros(shape + (3,), np.uint8), np.ones(shape, bool), masks)
    assert rg.whole_shots(ref) == [(0,), (1,), (4,), (5,)]
    assert rg.whole_shots(ref, limit=2) == [(0,), (5,)]
    assert rg.whole_shots(ref, min_cover=0.5) == [(0,), (2,), (3,), (5,)]
