import time

import cv2
import numpy as np
import pytest

from albumproc import GlareParams, PageOptions, process_page
from albumproc.evaluate import residual_truth, score
from albumproc.fuse import fuse
from albumproc.pipeline import _assess
from albumproc.synth import make_case


def _codes(report):
    return [w["code"] for w in report.warnings]


@pytest.mark.parametrize("seed", [10, 13])
def test_glare_removed_by_merging_shots(seed):
    case = make_case(seed, "glare", 3)
    res = process_page(case.images)
    s = score(res.image, case.page)
    assert res.report.dropped == []
    assert res.report.coverage > 0.99
    assert s.glare_px < 0.001  # residual glare: under 0.1% of the page
    assert s.corner_err < 1.0  # page edges within 1% of the diagonal
    assert s.aspect_err < 0.02

    single = process_page([case.images[res.report.reference]])
    assert score(single.image, case.page).glare_px > 5 * max(s.glare_px, 1e-4)
    # Merging removed the glare, and the detector agrees.
    assert "glare" not in _codes(res.report)


@pytest.mark.parametrize("n", [2, 4])
def test_oversized_page_stitched_from_parts(n):
    case = make_case(10, "stitch", n)
    res = process_page(case.images)
    s = score(res.image, case.page)
    assert res.report.coverage > 0.99
    assert s.corner_err < 1.0
    assert s.aspect_err < 0.03
    assert s.mae < 10


def test_unrelated_photo_is_dropped():
    case = make_case(11, "glare", 3)
    rng = np.random.default_rng(0)
    junk = (rng.random((1500, 2000, 3)) * 255).astype(np.uint8)
    res = process_page(case.images + [junk])
    assert res.report.dropped == [3]
    assert score(res.image, case.page).corner_err < 1.0


@pytest.mark.parametrize("seed,n", [(20, 4), (25, 3), (27, 4)])
def test_two_pages_side_by_side_keeps_the_photographed_page(seed, n):
    # Close-ups of one page with its neighbour partly in view, plus one wide
    # shot showing both pages whole. The result must be the page the photos
    # are of, not the neighbour and not both pages as one.
    case = make_case(seed, "pair", n)
    res = process_page(case.images)
    s = score(res.image, case.page)
    assert res.report.dropped == []
    assert s.corner_err < 1.0
    assert s.aspect_err < 0.02
    assert s.glare_px < 0.002


def test_single_close_up_with_neighbour_partly_in_view():
    case = make_case(21, "pair", 1)
    assert len(case.images) == 1
    res = process_page(case.images)
    s = score(res.image, case.page)
    assert s.corner_err < 1.0
    assert s.aspect_err < 0.02


@pytest.mark.parametrize(
    "seed,n,layout",
    [
        (20, 4, {}),  # close-ups plus a wide shot of both pages
        (25, 3, {}),
        (21, 1, {}),  # a single close-up
        (31, 2, {"sides": 2, "wide": False}),  # neighbours on both sides, close-ups only
        (42, 2, {"sides": 2, "wide": False}),
    ],
)
def test_touching_pages_keeps_the_photographed_page(seed, n, layout):
    # Pages of an open album touch with no table between them, so colour and
    # outline alone merge them into one; only the join line separates them.
    case = make_case(seed, "pair", n, gap=0, **layout)
    res = process_page(case.images)
    s = score(res.image, case.page)
    assert res.report.dropped == []
    assert s.corner_err < 1.0
    assert s.aspect_err < 0.02


@pytest.fixture(scope="module")
def sleeve_stitch():
    """An oversized page in two halves with sleeve glare, with and without the detector."""
    case = make_case(40, "stitch", 2, glare_style="sleeve")
    t = time.perf_counter()
    on = process_page(case.images)
    t_on = time.perf_counter() - t
    t = time.perf_counter()
    off = process_page(case.images, PageOptions(glare=GlareParams(enabled=False)))
    t_off = time.perf_counter() - t
    return case, on, off, t_on, t_off


def test_glare_left_in_a_stitched_page_is_reported(sleeve_stitch):
    case, res, _, _, _ = sleeve_stitch
    r = res.report
    assert r.schema_version == 2
    (w,) = [w for w in r.warnings if w["code"] == "glare"]
    assert w["single_shot"] >= 1
    assert any(g["cause"] == "single_shot" for g in r.glare["regions"])
    assert set(r.glare["per_shot"]) == set(r.used)
    # Against the glare actually left in the page: what is reported is
    # really there. The cautious detector finds only part of it (see
    # glare.py), so recall is held to what it reaches, to catch regressions.
    h, w_ = res.residual_glare.shape
    truth = cv2.resize(residual_truth(res.image, case.page).astype(np.float32), (w_, h), interpolation=cv2.INTER_AREA)
    found = res.residual_glare >= 0.5
    real, clean = truth > 0.5, truth < 0.01
    assert (found & clean).sum() / clean.sum() <= 0.002
    assert (found & real).sum() / real.sum() >= 0.02


def test_detector_leaves_the_page_image_unchanged_and_costs_little(sleeve_stitch):
    _, on, off, t_on, t_off = sleeve_stitch
    np.testing.assert_array_equal(on.image, off.image)
    assert off.report.glare == {"fraction": 0.0, "per_shot": {i: 0.0 for i in off.report.used}, "regions": []}
    assert off.report.coverage == on.report.coverage and off.report.glare_fraction == on.report.glare_fraction
    assert t_on - t_off <= 0.10 * t_off + 1.0


def test_clean_white_page_gets_no_glare_warning():
    case = make_case(30, "clean_white", 3)
    res = process_page(case.images)
    assert "glare" not in _codes(res.report)
    assert res.report.glare["regions"] == []


def _two_shots(h=600, w=900, seed=5):
    """Two partly overlapping shots of one scene, already in the page frame; one has glare."""
    rng = np.random.default_rng(seed)
    truth = np.clip(cv2.GaussianBlur(rng.normal(0, 1, (h, w, 3)).astype(np.float32), (0, 0), 6) * 300 + 120, 20, 230)
    yy, xx = np.mgrid[0:h, 0:w]
    spot = np.exp(-(((xx - 300) / 40.0) ** 2 + ((yy - 300) / 40.0) ** 2))[..., None]
    a = np.clip(truth + 200 * spot, 0, 255).astype(np.uint8)
    b = truth.astype(np.uint8)
    ma, mb = np.zeros((h, w), bool), np.zeros((h, w), bool)
    ma[:, :600], mb[:, 300:] = True, True
    return [a, b], [ma, mb]


def test_missing_part_of_the_page_is_located():
    # The page finder crops a page to what was photographed, so a gap is
    # made here directly: no shot covers the bottom-right corner.
    imgs, masks = _two_shots()
    masks[1][400:, 750:] = False
    for im, m in zip(imgs, masks):
        im[~m] = 0
    opt = PageOptions()
    res = fuse(imgs, masks, 0, opt.fuse)
    q = _assess(res, [0, 1], [], (1800, 1200), opt)
    (w,) = [w for w in q.warnings if w["code"] == "incomplete_coverage"]
    (u,) = q.uncovered["regions"]
    assert w["regions"] == 1 and w["message"].endswith("(bottom-right corner).")
    assert u["location"] == "bottom-right" and u["edges"] == ["right", "bottom"]
    assert u["bbox"] == pytest.approx([0.8333, 0.6667, 1.0, 1.0], abs=0.01)
    assert u["bbox_px"][0] == pytest.approx(1500, abs=20)
    assert round(float(res.coverage.mean()), 4) == pytest.approx(1 - q.uncovered["fraction"], abs=1e-4)


def test_page_quality_is_deterministic():
    imgs, masks = _two_shots()
    for im, m in zip(imgs, masks):
        im[~m] = 0
    opt = PageOptions()
    q1 = _assess(fuse(imgs, masks, 0, opt.fuse), [0, 1], [], (900, 600), opt)
    q2 = _assess(fuse(imgs, masks, 0, opt.fuse), [0, 1], [], (900, 600), opt)
    assert (q1.glare, q1.uncovered, q1.warnings, q1.detector) == (q2.glare, q2.uncovered, q2.warnings, q2.detector)
    np.testing.assert_array_equal(q1.residual, q2.residual)
