import numpy as np
import pytest

from albumproc.regions import QualityParams, attach_shot_boxes, find_regions, glare_regions, page_warnings, shot_box, shot_outline, single_coverage_regions, uncovered_regions

GRID = (100, 200)  # h, w
PAGE = (2000, 1000)  # composed page w, h


def _mask(*boxes):
    m = np.zeros(GRID, bool)
    for x0, y0, x1, y1 in boxes:
        m[y0:y1, x0:x1] = True
    return m


def test_regions_filtered_sorted_boxed_and_located():
    m = _mask((10, 10, 30, 20), (150, 70, 190, 95), (100, 45, 101, 46))  # 200 px, 1000 px, 1 px
    found = find_regions(m, PAGE, min_area=0.0005)  # 0.0005 * 20000 = 10 px
    assert [r.area for r, _ in found] == [0.05, 0.01]
    big, small = found[0][0], found[1][0]
    assert big.bbox == (0.75, 0.7, 0.95, 0.95)
    assert big.bbox_px == (1500, 700, 1900, 950)
    assert big.location == "bottom-right"
    assert small.location == "top-left"
    assert found[0][1].sum() == 1000


@pytest.mark.parametrize(
    "box,location",
    [
        ((90, 40, 110, 60), "centre"),
        ((90, 2, 110, 12), "top"),
        ((2, 40, 12, 60), "left"),
        ((185, 85, 195, 95), "bottom-right"),
        ((90, 85, 110, 95), "bottom"),
    ],
)
def test_location_words(box, location):
    (r, _), = find_regions(_mask(box), PAGE, 0.0005)
    assert r.location == location


def test_closing_joins_a_region_split_by_a_hairline():
    m = _mask((10, 10, 60, 40))
    m[:, 35] = False
    assert len(find_regions(m, PAGE, 0.0005)) == 1


@pytest.mark.parametrize(
    "box,edges",
    [
        ((0, 0, 20, 30), ["top", "left"]),  # corner
        ((180, 30, 200, 60), ["right"]),  # edge
        ((50, 80, 120, 100), ["bottom"]),
        ((80, 40, 120, 60), []),  # a hole inside the page
        ((0, 0, 200, 10), ["top", "right", "left"]),
    ],
)
def test_uncovered_edges(box, edges):
    coverage = ~np.kron(_mask(box), np.ones((10, 10), bool))  # full resolution, 10x the grid
    (r,) = uncovered_regions(coverage, PAGE, QualityParams(), GRID)
    assert r.edges == edges
    assert "cause" not in r.to_dict() and "shots" not in r.to_dict()


def test_uncovered_ignores_slivers_thinner_than_half_a_grid_cell():
    coverage = np.ones((1000, 2000), bool)
    coverage[:, :3] = False  # 3 px of 10 per grid cell
    assert uncovered_regions(coverage, PAGE, QualityParams(), GRID) == []


def _shots():
    left, right = np.zeros(GRID, bool), np.zeros(GRID, bool)
    left[:, :120] = True
    right[:, 80:] = True
    return left, right


def test_glare_region_causes_and_shots():
    left, right = _shots()
    count = left.astype(int) + right
    res = np.zeros(GRID, np.float32)
    res[10:30, 20:60] = 0.8  # seen by the left shot only
    res[60:80, 90:110] = 0.6  # in the overlap: both shots had glare
    res[60:70, 150:170] = 0.9  # right shot only
    regions = glare_regions(res, count, [3, 5], [left, right], PAGE, QualityParams())
    assert [r.cause for r in regions] == ["single_shot", "all_shots_glared", "single_shot"]
    assert [r.shots for r in regions] == [[3], [3, 5], [5]]
    assert regions[0].severity == pytest.approx(0.8)
    assert "edges" not in regions[0].to_dict()


def test_cause_follows_the_larger_share_of_the_region():
    left, right = _shots()
    count = left.astype(int) + right
    res = np.zeros(GRID, np.float32)
    res[40:60, 60:110] = 0.7  # 20 of 50 columns in one shot only, 30 in both
    (r,) = glare_regions(res, count, [0, 1], [left, right], PAGE, QualityParams())
    assert r.cause == "all_shots_glared"
    res[40:60, 40:60] = 0.7  # now 40 of 70 are single-shot
    (r,) = glare_regions(res, count, [0, 1], [left, right], PAGE, QualityParams())
    assert r.cause == "single_shot"


def test_user_thresholds_apply():
    left, right = _shots()
    count = left.astype(int) + right
    res = np.zeros(GRID, np.float32)
    res[10:20, 10:20] = 0.4  # 100 px = 0.5% of the page
    assert glare_regions(res, count, [0, 1], [left, right], PAGE, QualityParams()) == []
    assert len(glare_regions(res, count, [0, 1], [left, right], PAGE, QualityParams(residual_threshold=0.3))) == 1
    assert glare_regions(res, count, [0, 1], [left, right], PAGE, QualityParams(0.3, min_region_area=0.01)) == []


def _g(*causes, fraction=0.0412):
    locs = ["bottom-left", "top", "right"]
    return {"fraction": fraction, "per_shot": {}, "regions": [{"location": locs[k], "cause": c} for k, c in enumerate(causes)]}


NONE = {"fraction": 0.0, "regions": []}


def test_glare_warning_for_one_single_shot_region():
    (w,) = page_warnings(_g("single_shot"), NONE, [])
    assert w == {
        "code": "glare",
        "fraction": 0.0412,
        "single_shot": 1,
        "all_shots_glared": 0,
        "location": "bottom-left",
        "regions": [{"location": "bottom-left", "cause": "single_shot"}],
        "message": "Glare remains on 4.1% of the page in 1 area (bottom-left). It was seen by only one photo; add a shot of that area from a different angle.",
    }


def test_glare_warning_advice_by_cause():
    (w,) = page_warnings(_g("all_shots_glared", "all_shots_glared"), NONE, [])
    assert w["message"] == (
        "Glare remains on 4.1% of the page in 2 areas (largest bottom-left)."
        " Every photo had glare there; change the light or the camera angle for all the shots."
    )
    (w,) = page_warnings(_g("single_shot", "single_shot"), NONE, [])
    assert "Each was seen by only one photo; add shots of those areas from a different angle." in w["message"]
    (w,) = page_warnings(_g("all_shots_glared", "single_shot", "single_shot"), NONE, [])
    assert w["single_shot"] == 2 and w["all_shots_glared"] == 1
    assert "2 of them were seen by only one photo" in w["message"]
    assert "change the light or the camera angle for those" in w["message"]


def test_coverage_and_dropped_warnings():
    unc = {
        "fraction": 0.0127,
        "regions": [{"location": "top-left", "edges": ["top", "left"]}, {"location": "right", "edges": ["right"]}, {"location": "centre", "edges": []}],
    }
    w = page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, unc, [2])
    assert [x["code"] for x in w] == ["incomplete_coverage", "photos_dropped"]
    assert w[0]["message"] == "1.3% of the page is not in any photo (top-left corner, right edge, centre)."
    assert len(w[0]["regions"]) == 3 and w[0]["locations"] == ["top-left", "right", "centre"]
    assert w[0]["regions"][0] == {"location": "top-left", "edges": ["top", "left"]}
    assert w[1] == {"code": "photos_dropped", "photos": [2], "message": "Photo 2 matched no other photo and was not used."}
    assert page_warnings({"fraction": 0.0, "regions": []}, NONE, [1, 4])[0]["message"] == "Photos 1, 4 matched no other photo and were not used."


def test_no_warnings_for_a_clean_page():
    assert page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, NONE, []) == []


def test_single_coverage_regions_are_each_shot_s_own_part():
    left, right = _shots()  # overlap in columns 80..120
    count = left.astype(np.uint8) + right
    regions = single_coverage_regions(count, [3, 5], [left, right], PAGE, QualityParams())
    assert [r.shots for r in regions] == [[3], [5]]  # equal areas: top-left first
    assert regions[0].bbox == (0.0, 0.0, 0.4, 1.0) and regions[1].bbox == (0.6, 0.0, 1.0, 1.0)
    assert regions[0].edges == ["top", "bottom", "left"] and regions[1].edges == ["top", "right", "bottom"]
    assert all(r.cause is None and r.severity is None for r in regions)


def test_single_coverage_ignores_the_seam_and_small_patches():
    whole = np.ones(GRID, bool)
    left, right = np.zeros(GRID, bool), np.zeros(GRID, bool)
    left[:, :98], right[:, 100:] = True, True
    count = whole.astype(np.uint8) + left + right  # 1 only along columns 98-99: a seam
    assert single_coverage_regions(count, [0, 1, 2], [whole, left, right], PAGE, QualityParams()) == []
    small = np.zeros(GRID, bool)
    small[10:20, 10:25] = True  # 150 px = 0.75% of the page: under min_single_area, over min_region_area
    assert single_coverage_regions(small.astype(np.uint8), [0], [small], PAGE, QualityParams()) == []
    assert len(single_coverage_regions(small.astype(np.uint8), [0], [small], PAGE, QualityParams(min_single_area=0.005))) == 1


def test_one_shot_is_one_single_coverage_region_of_the_whole_page():
    whole = np.ones(GRID, bool)
    (r,) = single_coverage_regions(whole.astype(np.uint8), [7], [whole], PAGE, QualityParams())
    assert r.bbox == (0.0, 0.0, 1.0, 1.0) and r.area == 1.0 and r.shots == [7]
    assert r.edges == ["top", "right", "bottom", "left"]


def test_shot_box_under_identity_and_affine_maps_and_outside_the_shot():
    identity = lambda pts: pts  # noqa: E731
    assert shot_box((100, 200, 300, 400), identity, (1000, 1000)) == (100, 200, 300, 400)
    half = lambda pts: np.asarray(pts) * 0.5 + 10  # noqa: E731
    assert shot_box((100, 200, 300, 400), half, (1000, 1000)) == (60, 110, 160, 210)
    assert shot_box((100, 200, 300, 400), half, (100, 100)) == (60, 99, 99, 99) or shot_box((100, 200, 300, 400), half, (100, 100)) is None
    assert shot_box((100, 200, 300, 400), identity, (50, 50)) is None  # wholly outside
    assert shot_box((100, 200, 300, 400), identity, (200, 1000)) == (100, 200, 199, 400)  # clipped
    nan = lambda pts: np.full_like(np.asarray(pts, float), np.nan)  # noqa: E731
    assert shot_box((100, 200, 300, 400), nan, (1000, 1000)) is None


def test_attach_shot_boxes_keys_by_input_index_and_skips_shots_the_box_misses():
    (r, _), = find_regions(_mask((10, 10, 30, 20)), PAGE, 0.0005)  # bbox_px (100, 100, 300, 200)
    shift = lambda pts: np.asarray(pts) - [250, 0]  # noqa: E731
    attach_shot_boxes([r], [2, 9], [lambda pts: pts, shift], [(2000, 1000), (2000, 1000)])
    assert r.shot_boxes == {2: (100, 100, 300, 200), 9: (0, 100, 50, 200)}
    attach_shot_boxes([r], [2], [lambda pts: np.asarray(pts) - [400, 0]], [(2000, 1000)])
    assert r.shot_boxes == {}
    assert r.to_dict()["shot_boxes"] == {}


def test_single_coverage_warning_messages_and_regions():
    one = {"fraction": 0.3104, "regions": [{"location": "right", "edges": ["right"], "shots": [1], "shot_boxes": {1: (2210, 0, 4080, 3072)}, "area": 0.31}]}
    (w,) = page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, NONE, [], one)
    assert w["code"] == "single_coverage" and w["fraction"] == 0.3104 and w["locations"] == ["right"]
    assert w["regions"] == [{"location": "right", "edges": ["right"], "shots": [1], "shot_boxes": {1: (2210, 0, 4080, 3072)}}]
    assert w["message"] == (
        "Only one photo covers the right edge (31.0% of the page)."
        " Take another shot of that part from a different angle so that glare there can be removed."
    )
    three = {"fraction": 0.5, "regions": [{"location": "top-left", "edges": ["top", "left"]}, {"location": "centre", "edges": []}, {"location": "bottom", "edges": ["bottom"]}]}
    (w,) = page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, NONE, [], three)
    assert w["message"].startswith("Only one photo covers the top-left corner and 2 other areas (50.0% of the page). Take another shot of those parts")
    assert page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, NONE, [], {"fraction": 0.004, "regions": []}) == []


def test_shot_outline_is_the_mask_s_contour_in_page_fractions():
    m = _mask((20, 10, 120, 60))  # columns 20..119, rows 10..59 of a 200 x 100 grid
    pts = shot_outline(m)
    assert len(pts) == 4
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert min(xs) == pytest.approx(20 / 199, abs=1e-3) and max(xs) == pytest.approx(119 / 199, abs=1e-3)
    assert min(ys) == pytest.approx(10 / 99, abs=1e-3) and max(ys) == pytest.approx(59 / 99, abs=1e-3)
    assert shot_outline(np.zeros(GRID, bool)) == []
    whole = shot_outline(np.ones(GRID, bool))
    assert len(whole) == 4 and all(0 <= x <= 1 and 0 <= y <= 1 for x, y in whole)


def test_shot_outline_follows_a_slanted_shot_with_few_points():
    yy, xx = np.mgrid[0:100, 0:200]
    m = (xx + yy * 0.5 > 60) & (xx + yy * 0.5 < 160)  # a parallelogram
    pts = shot_outline(m)
    assert 4 <= len(pts) <= 64
    # The point in the parallelogram's corner region, not a bounding box.
    assert any(abs(x - 60 / 199) < 0.02 and y < 0.05 for x, y in pts)
    assert any(abs(x - (160 - 50) / 199) < 0.03 and y > 0.95 for x, y in pts)
