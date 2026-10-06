import numpy as np
import pytest

from albumproc.regions import QualityParams, find_regions, glare_regions, page_warnings, uncovered_regions

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
        "regions": 1,
        "single_shot": 1,
        "location": "bottom-left",
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
    assert w["single_shot"] == 2
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
    assert w[0]["regions"] == 3 and w[0]["locations"] == ["top-left", "right", "centre"]
    assert w[1] == {"code": "photos_dropped", "photos": [2], "message": "Photo 2 matched no other photo and was not used."}
    assert page_warnings({"fraction": 0.0, "regions": []}, NONE, [1, 4])[0]["message"] == "Photos 1, 4 matched no other photo and were not used."


def test_no_warnings_for_a_clean_page():
    assert page_warnings({"fraction": 0.0, "per_shot": {}, "regions": []}, NONE, []) == []
