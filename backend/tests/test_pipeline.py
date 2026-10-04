import numpy as np
import pytest

from albumproc import process_page
from albumproc.evaluate import score
from albumproc.synth import make_case


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
