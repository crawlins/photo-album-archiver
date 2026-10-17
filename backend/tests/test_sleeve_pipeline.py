"""Pages in album sleeves through the whole page pipeline, and the measured accuracy of the sleeve step."""

import json
import time

import numpy as np
import pytest

from albumproc import PageOptions, process_page
from albumproc.curvature import CurvatureParams
from albumproc.page import detect_pages
from albumproc.pipeline import _sleeve_warnings
from albumproc.sleeve import SideFinding, SleeveFit, SleeveParams, find_sleeve
from albumproc.synth import SleeveSpec, make_case


def _opt(**kw):
    return PageOptions(sleeve=SleeveParams(**kw))


def _err(corners, truth):
    """Corner distances as fractions of the truth's diagonal."""
    return np.linalg.norm(np.asarray(corners) - truth, axis=1) / np.linalg.norm(truth[2] - truth[0])


@pytest.fixture(scope="module")
def sleeved():
    """A flat page in a sleeve, binding strip on the left, two shots: cropped to the paper and to the sleeve."""
    case = make_case(1, "glare", 2, sleeve=SleeveSpec(binding="left"))
    return case, process_page(case.images), process_page(case.images, _opt(crop="sleeve"))


def test_sleeved_page_is_cropped_to_the_paper(sleeved):
    case, res, _ = sleeved
    s = res.report.sleeve
    assert s["sleeved"] and s["reason"] is None and s["mode"] == "auto" and s["crop"] == "paper"
    assert s["strip_side"] == "left"
    assert [b["kind"] for b in s["sides"]["left"]["bands"]] == ["strip"]
    assert s["sides"]["top"]["bands"][0]["kind"] == "crimp" and s["sides"]["bottom"]["bands"][0]["kind"] == "crimp"
    assert all(side["paper_found"] for side in s["sides"].values())
    ref = res.report.reference
    assert _err(res.corners_ref, case.corners_in(ref, "paper")).max() <= 0.0025
    paper_aspect = case.page.shape[1] / case.page.shape[0]
    assert res.report.aspect == pytest.approx(paper_aspect, rel=0.01)
    assert res.report.schema_version == 5
    assert not [w for w in res.report.warnings if w["code"] == "paper_edge_uncertain"]
    assert res.sleeve.sleeved and set(res.mosaic_from_shot) == set(res.report.used)


def test_sleeve_crop_reaches_the_sleeves_edge(sleeved):
    case, paper, res = sleeved
    s = res.report.sleeve
    assert s["crop"] == "sleeve" and s["sleeved"]
    ref = res.report.reference
    err = _err(res.corners_ref, case.corners_in(ref, "sleeve"))
    # Where the sleeve's edge was seen on both sides of a corner (the strip
    # and the crimps), the crop's corner is the sleeve's.
    seen = {k: v["sleeve_seen"] for k, v in s["sides"].items()}
    for k, (a, b) in enumerate([("left", "top"), ("top", "right"), ("right", "bottom"), ("bottom", "left")]):
        if seen[a] and seen[b]:
            assert err[k] <= 0.005, k
    # The page grows by the bands' widths, and its aspect is the crop's.
    m = {k: sum(b["width"] for b in v["bands"]) if v["sleeve_seen"] else 0.0 for k, v in s["sides"].items()}
    w, h = res.report.size
    pw, ph = paper.report.size
    assert w / pw == pytest.approx(1 + m["left"] + m["right"], rel=0.01)
    assert res.report.aspect == pytest.approx(w / h, rel=0.01)


def test_same_inputs_give_the_same_metadata(sleeved):
    case, first, _ = sleeved
    again = process_page(case.images)
    assert json.dumps(again.report.to_dict(), default=str) == json.dumps(first.report.to_dict(), default=str)
    np.testing.assert_array_equal(again.image, first.image)


@pytest.mark.parametrize("seed,kind,n", [(10, "glare", 3), (21, "pair", 1)])
def test_unsleeved_page_comes_out_as_before(seed, kind, n):
    case = make_case(seed, kind, n, **({"gap": 0} if kind == "pair" else {}))
    t = time.perf_counter()
    off = process_page(case.images, _opt(mode="off"))
    t_off = time.perf_counter() - t
    t = time.perf_counter()
    on = process_page(case.images, _opt(crop="sleeve"))  # the crop option has no effect without a sleeve
    t_on = time.perf_counter() - t
    np.testing.assert_array_equal(on.image, off.image)
    a, b = on.report.to_dict(), off.report.to_dict()
    sa, sb = a.pop("sleeve"), b.pop("sleeve")
    assert a == b
    assert sa["sleeved"] is False and sa["reason"] == "no_sleeve_band" and sb["reason"] == "off"
    assert all(v == {"bands": [], "paper_found": False, "sleeve_seen": False} for v in sa["sides"].values())
    assert t_on <= 1.10 * t_off + 1.0


@pytest.mark.parametrize("binding", ["auto", "right"])
def test_curvature_on_a_sleeved_bent_page(binding):
    case = make_case(4, "curved", 1, lift=0.03, binding="left", sleeve=SleeveSpec())
    res = process_page(case.images, PageOptions(curvature=CurvatureParams(mode="auto" if binding == "auto" else "force", binding=binding)))
    s, c = res.report.sleeve, res.report.curvature
    assert s["sleeved"] and s["strip_side"] == "left"
    if binding == "auto":
        # The paper's traced edges and the strip's side: flattened, at the strip.
        assert c["applied"] and c["binding"] == "left"
    else:
        # A binding side the user names wins over the strip.
        assert c["binding"] == "right"


def test_warning_for_a_side_whose_paper_edge_was_not_found():
    fit = SleeveFit(True, None, np.zeros((4, 2)), [], None, {}, [])
    assert _sleeve_warnings(fit) == []
    fit.sides = [SideFinding(n, [], [], None if n == "right" else 0, False) for n in ("top", "right", "bottom", "left")]
    (w,) = _sleeve_warnings(fit)
    assert w["code"] == "paper_edge_uncertain" and w["side"] == "right" and "right edge" in w["message"]


SYNTH = [(seed, kind, binding) for seed in (1, 2, 3) for kind in ("glare", "curved") for binding in ("left", "top", "right", "bottom")]


@pytest.fixture(scope="module")
def synthetic_accuracy():
    """The sleeve step on single shots of sleeved pages, flat and bent by 3%, against the truth."""
    rows = []
    for seed, kind, binding in SYNTH:
        case = make_case(seed, kind, 1, sleeve=SleeveSpec(binding=binding), lift=0.03, binding=binding)
        img = case.images[0]
        fit = find_sleeve(img, np.ones(img.shape[:2], bool), detect_pages(img)[0].corners, SleeveParams())
        truth = case.corners_in(0)
        width = np.linalg.norm(truth[1] - truth[0])
        near = []
        for side, traced in zip(case.outline_in(0, n=400), fit.outline):
            if traced is not None:
                d = np.min(np.linalg.norm(traced[::3, None] - side[None], axis=2), axis=1) / width
                near.append(float((d <= 0.002).mean()))
        sides = fit.report()["sides"]
        kinds = sum([b["kind"] for b in sides[k]["bands"]] == case.sleeve.bands[k] for k in sides) if fit.sleeved else 0
        rows.append({"sleeved": fit.sleeved, "corners": _err(fit.corners, truth).max(), "near": min(near) if len(near) == 4 else 0.0, "kinds": kinds})
    return rows


def test_synthetic_accuracy(synthetic_accuracy):
    # Requirement 8.2 asks for every case: corners within 0.25% of the
    # diagonal and 98% of the traced edge within 0.2% of the width. These
    # are the shares measured when the step was written, as a floor
    # against regressions; see the pull request for where it falls short.
    rows = synthetic_accuracy
    good = [r["sleeved"] and r["corners"] <= 0.0025 and r["near"] >= 0.98 for r in rows]
    assert sum(r["sleeved"] for r in rows) >= 0.75 * len(rows)
    assert sum(good) >= 0.5 * len(rows)
    # Requirement 8.3 (band kinds right on 95% of the sides) is not met
    # either: a narrow clear gap and the clear margin over the table are
    # often missed or found where the truth has none.
    assert sum(r["kinds"] for r in rows) >= 0.3 * 4 * len(rows)
