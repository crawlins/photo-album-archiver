"""Real photos of album pages, when the test data is checked out.

The photos are family photos, kept in the private repository
crawlins/photo-album-archiver-data. Clone it beside this one and link its
``real`` folder into the repository root:

    git clone git@github.com:crawlins/photo-album-archiver-data.git ../photo-album-archiver-data
    ln -s ../photo-album-archiver-data/real real

Each two-shot page is a sleeved album page photographed twice, with
neighbouring pages (the facing page, the stack of pages beside it) in view.
``real/<page>/paper.json`` holds the paper's corners, points along its edges
and the binding side, marked by hand (see ``real/paper-edges.md`` there).
The expected aspect ratio is the paper's, from those corners, so the test
catches a neighbour's edge, a page stack, the open album or the sleeve being
taken in, or a print being taken for the page.

The 19 ``glare-*`` pages of the same album are only run with
``PAA_SLOW=1``: each has nine shots.
"""

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from albumproc import PageOptions, process_page
from albumproc.curvature import CurvatureParams
from albumproc.page import estimate_aspect, focal_px_from_35mm

REAL = Path(__file__).resolve().parents[2] / "real"
PAGES = ["grandma-wilson", "page-two", "page-three", "page-four", "page-five"]

# Where the sleeve step falls short of the spec on the labelled pages, as
# measured when it was written. Requirement 8.5: page-four's sleeve is not
# recognised, so it keeps the outline found before; grandma-wilson's left
# edge and top-left corner and page-two's bottom-right corner are not traced
# to within the tolerance. Requirement 8.6: curvature `force` picks the
# bottom for both page-four's and page-two's binding (right).
SHORT_OF_8_5 = {"grandma-wilson", "page-two", "page-four"}
SHORT_OF_8_6 = {"page-two", "page-four"}
SHORT_OF_4_3: set[str] = set()

needs_data = pytest.mark.skipif(not REAL.is_dir(), reason="real test photos not checked out (see module docstring)")


def _shots(page):
    shots = sorted((REAL / page).glob("shot_*.jpg"))
    assert shots, f"no shots in {REAL / page}"
    return shots


def _labels(page):
    path = REAL / page / "paper.json"
    if not path.exists():
        pytest.skip(f"no paper-edge labels for {page}")
    return json.loads(path.read_text())


def _to_mosaic(res, names, shot, pts):
    """Label points marked in ``shot`` into the preview mosaic."""
    H = res.mosaic_from_shot[names.index(shot)]
    return cv2.perspectiveTransform(np.asarray(pts, np.float64).reshape(-1, 1, 2), H).reshape(-1, 2)


def _miss(page, short):
    return pytest.param(page, marks=pytest.mark.xfail(reason="short of the spec's target, see SHORT_OF_* above", strict=False)) if page in short else page


@pytest.fixture(scope="module")
def processed():
    cache = {}

    def get(page, mode="auto"):
        if (page, mode) not in cache:
            images = [cv2.imread(str(p), cv2.IMREAD_COLOR) for p in _shots(page)]
            cache[page, mode] = process_page(images, PageOptions(curvature=CurvatureParams(mode=mode)))
        return cache[page, mode]

    return get


@needs_data
@pytest.mark.parametrize("page", [_miss(p, SHORT_OF_4_3) for p in PAGES])
def test_real_page_is_the_single_sleeved_paper(processed, page):
    res = processed(page)
    lab = _labels(page)
    names = [p.name for p in _shots(page)]
    assert res.report.dropped == []
    assert res.report.coverage > 0.99
    # The paper's aspect from the labelled corners, measured as the pipeline measures it.
    to_canvas = res.mosaic_from_shot[res.report.reference]
    corners = np.array([_to_mosaic(res, names, c["shot"], [c["point"]])[0] for c in lab["corners"]])
    corners_ref = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), np.linalg.inv(to_canvas)).reshape(4, 2)
    h, w = cv2.imread(str(_shots(page)[res.report.reference])).shape[:2]
    aspect = estimate_aspect(corners_ref, (w / 2, h / 2), focal_px_from_35mm(26.0, w, h))
    assert res.report.aspect == pytest.approx(aspect, rel=0.015)


@needs_data
@pytest.mark.parametrize("page", [_miss(p, SHORT_OF_8_5) for p in PAGES])
def test_real_paper_outline_matches_the_labels(processed, page):
    res = processed(page)
    lab = _labels(page)
    names = [p.name for p in _shots(page)]
    fit = res.sleeve
    assert fit.sleeved
    corners = np.array([_to_mosaic(res, names, c["shot"], [c["point"]])[0] for c in lab["corners"]])
    diag = np.linalg.norm(corners[2] - corners[0])
    width = (np.linalg.norm(corners[1] - corners[0]) + np.linalg.norm(corners[2] - corners[3])) / 2
    assert np.linalg.norm(fit.corners - corners, axis=1).max() <= 0.005 * diag
    for k, name in enumerate(["top", "right", "bottom", "left"]):
        traced = fit.outline[k]
        if traced is None:  # the side the outer outline stood in for
            traced = np.linspace(fit.corners[k], fit.corners[(k + 1) % 4], 400)
        for marked in lab["edges"][name]:
            pts = _to_mosaic(res, names, marked["shot"], marked["points"])
            d = np.min(np.linalg.norm(pts[:, None] - traced[None], axis=2), axis=1)
            assert (d <= 0.004 * width).mean() >= 0.9, name


@needs_data
@pytest.mark.parametrize("page", [_miss(p, SHORT_OF_8_6) for p in PAGES])
def test_real_binding_side(processed, page):
    lab = _labels(page)
    forced = processed(page, "force").report.curvature
    auto = processed(page).report.curvature
    assert auto["reason"] != "fit_failed"
    assert forced["binding"] == lab["binding"]


GLARE_PAGES = sorted(p.name for p in REAL.glob("glare-*")) if REAL.is_dir() else []


@needs_data
@pytest.mark.skipif(os.environ.get("PAA_SLOW") != "1", reason="slow: set PAA_SLOW=1")
@pytest.mark.parametrize("page", GLARE_PAGES)
def test_glare_pages_are_all_sleeved(page):
    images = [cv2.imread(str(p), cv2.IMREAD_COLOR) for p in _shots(page)]
    res = process_page(images)
    assert res.report.sleeve["sleeved"], res.report.sleeve["reason"]
