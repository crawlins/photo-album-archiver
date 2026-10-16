"""The sleeve step on its own: ridges, bands, paths, tracing and the paper's outline."""

import cv2
import numpy as np
import pytest

from albumproc.page import detect_pages
from albumproc.sleeve import SideFinding, SleeveParams, candidate_paths, classify_bands, find_sleeve, measure_band, paper_candidates, refine_path, ridge_score, trace_path
from albumproc.synth import SleeveSpec, _print_photo, _resolve_sleeve, make_case, make_page, make_sleeve, make_table

W = 1800  # page width in px for the hand-built strips
COLS = 1800

OUTSIDE = (40, 60, 90)  # BGR: a dark table
CLEAR = (70, 85, 110)  # the table through clear plastic
PAPER = (215, 230, 238)  # cream album paper
STRIP = (188, 222, 240)  # the binding strip's warmer cream


def _strip(bands, seed=0):
    """A straightened corridor (Lab float32, as ``side_strip`` gives) from (kind, colour, rows) bands, outside first."""
    rng = np.random.default_rng(seed)
    rows = sum(n for _, _, n in bands)
    img = np.zeros((rows, COLS, 3), np.float32)
    r = 0
    cuts = []
    for kind, colour, n in bands:
        img[r : r + n] = colour
        if kind == "crimp":  # ridged across: brighter and darker every 10 px along the side
            img[r : r + n] += (35 * np.sin(2 * np.pi * np.arange(COLS) / 10.0))[None, :, None]
        r += n
        cuts.append(r)
    img += rng.normal(0, 2.0, img.shape).astype(np.float32)
    lab = cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)
    paths = [np.full(COLS, c - 1.0) for c in cuts[:-1]]  # the change between a band's last row and the next's first
    return lab, paths


def test_ridge_score_finds_a_seams_period_and_not_a_smooth_band():
    rng = np.random.default_rng(1)
    x = np.arange(1800)
    for period in (6, 10, 20):
        ridged = np.sin(2 * np.pi * x / period) + rng.normal(0, 0.3, len(x))
        assert ridge_score(ridged, (4, 27)) >= 0.5, period
    smooth = cv2.GaussianBlur(rng.normal(0, 1, (1, len(x))), (0, 0), 8).ravel()
    assert ridge_score(smooth, (4, 27)) < 0.3
    assert ridge_score(np.zeros(len(x)), (4, 27)) == 0.0


def test_bands_on_a_crimped_side():
    # Beyond the sleeve, the crimped seam, the clear gap inside it, the paper.
    lab, paths = _strip([("outside", OUTSIDE, 70), ("crimp", (150, 150, 150), 27), ("clear", CLEAR, 14), ("paper", PAPER, 120)])
    kinds = [b.kind for b in classify_bands(lab, paths, None, SleeveParams(), W, strip_ok=False)]
    assert kinds == ["outside", "crimp", "clear", "paper"]


def test_bands_on_the_binding_side():
    lab, paths = _strip([("outside", OUTSIDE, 70), ("strip", STRIP, 45), ("paper", PAPER, 120)])
    paper = measure_band(lab[-100:], np.ones(lab[-100:].shape[:2], bool), np.ones(lab[-100:].shape[:2], bool), W, SleeveParams())
    kinds = [b.kind for b in classify_bands(lab, paths, None, SleeveParams(), W, strip_ok=True, paper=paper)]
    assert kinds == ["outside", "strip", "paper"]
    # Only on a side across a crimped one.
    kinds = [b.kind for b in classify_bands(lab, paths, None, SleeveParams(), W, strip_ok=False, paper=paper)]
    assert "strip" not in kinds


def test_paper_like_band_outside_is_not_a_strip():
    # A print's white border inside the paper is a plain light band too, but
    # paper lies outside it.
    lab, paths = _strip([("outside", OUTSIDE, 70), ("paper", PAPER, 40), ("border", (245, 248, 250), 25), ("print", (90, 140, 60), 80)])
    paper = measure_band(lab[75:105], np.ones((30, COLS), bool), np.ones((30, COLS), bool), W, SleeveParams())
    kinds = [b.kind for b in classify_bands(lab, paths, None, SleeveParams(), W, strip_ok=True, paper=paper)]
    assert "strip" not in kinds


def _ridges(rows, centres, sigma=1.5):
    r = np.arange(rows)[:, None]
    return sum(np.exp(-0.5 * ((r - c[None, :]) / sigma) ** 2) for c in centres)


def test_paths_follow_a_bow_without_hopping_to_an_edge_beside_it():
    # Two edges 1% of the width apart, both bowed by 1.5% of the width over the side.
    c = np.arange(COLS)
    bow = 0.015 * W * np.sin(np.pi * c / (COLS - 1))
    first = 60 + bow
    second = first + 0.01 * W
    strength = _ridges(200, [first, second]) * 20 + np.random.default_rng(2).normal(0, 0.5, (200, COLS))
    paths = candidate_paths(np.abs(strength), SleeveParams(), W)
    assert len(paths) == 2
    (p1, s1), (p2, s2) = paths
    assert s1 >= 0.9 and s2 >= 0.9
    # Placed segment by segment, each stays with its own edge...
    assert np.abs(p1 - first).max() <= 0.002 * W
    assert np.abs(p2 - second).max() <= 0.002 * W
    # ...and followed column by column, it lies on it.
    band = int(round(2 * SleeveParams().max_step * W))
    assert np.abs(refine_path(np.abs(strength), p1, band) - first).max() <= 1.0
    assert np.abs(refine_path(np.abs(strength), p2, band) - second).max() <= 1.0


def _edge_strip(fade, seed=3):
    """One edge along the side, dark outside and light inside, its contrast gone over the middle ``fade`` of the side."""
    rng = np.random.default_rng(seed)
    img = np.zeros((60, COLS, 3), np.float32)
    img[:30] = (60, 70, 80)
    img[30:] = PAPER
    a, b = int(COLS * (0.5 - fade / 2)), int(COLS * (0.5 + fade / 2))
    img[30:, a:b] = (60, 70, 80)
    img += rng.normal(0, 2.0, img.shape).astype(np.float32)
    return cv2.cvtColor(np.clip(img, 0, 255).astype(np.uint8), cv2.COLOR_BGR2LAB).astype(np.float32)


def test_trace_bridges_a_short_gap_and_gives_up_on_a_long_one():
    p = SleeveParams()
    path = np.full(COLS, 29.0)  # the change from row 29 to row 30
    off = trace_path(_edge_strip(0.15), path, 1, p, W)
    assert off is not None
    assert np.abs(off - 29.0).max() <= 0.3
    assert trace_path(_edge_strip(0.25), path, 1, p, W) is None


def _flat_sleeve(page, spec, seed=0):
    """The page in its sleeve on a table, seen straight on: (image, outer outline, true paper corners)."""
    rng = np.random.default_rng(seed)
    held = {}

    def backdrop(cw, ch):
        m = 150
        held["table"], held["m"] = make_table(rng, cw + 2 * m, ch + 2 * m), m
        return held["table"][m : m + ch, m : m + cw]

    canvas, truth = make_sleeve(rng, page, spec, backdrop)
    img, m = held["table"], held["m"]
    ch, cw = canvas.shape[:2]
    img[m : m + ch, m : m + cw] = canvas
    img = cv2.resize(img, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
    quad = detect_pages(img)[0].corners
    return img, quad, (truth.paper_corners + m) * 0.75


def test_print_border_near_the_edge_is_not_taken_for_it():
    print_inset = 0.02
    rng = np.random.default_rng(4)
    spec = _resolve_sleeve(rng, SleeveSpec(binding="left", gap=0.006, clear=0.03, radius=0.0, shift=0.002, turn=0.2, print_inset=print_inset))
    page = make_page(rng, margin=print_inset)
    img, quad, truth = _flat_sleeve(page, spec)
    fit = find_sleeve(img, np.ones(img.shape[:2], bool), quad, SleeveParams())
    assert fit.sleeved and fit.strip_side == "left"
    diag = np.linalg.norm(truth[2] - truth[0])
    assert np.linalg.norm(fit.corners - truth, axis=1).max() <= 0.0025 * diag


def test_full_bleed_print_edge_is_the_papers():
    rng = np.random.default_rng(5)
    spec = _resolve_sleeve(rng, SleeveSpec(binding="left", gap=0.006, clear=0.03, radius=0.0, shift=0.002, turn=0.2, print_inset=0.0))
    page = _print_photo(rng, 2200, 1700)
    img, quad, truth = _flat_sleeve(page, spec)
    fit = find_sleeve(img, np.ones(img.shape[:2], bool), quad, SleeveParams())
    assert fit.sleeved
    diag = np.linalg.norm(truth[2] - truth[0])
    assert np.linalg.norm(fit.corners - truth, axis=1).max() <= 0.0025 * diag


def test_no_sleeve_on_the_existing_synthetic_pages():
    for seed, kind, kw in [(10, "glare", {}), (10, "stitch", {}), (21, "pair", {"gap": 0}), (3, "curved", {"lift": 0.05}), (1, "clean_white", {})]:
        img = make_case(seed, kind, 1, **kw).images[0]
        fit = find_sleeve(img, np.ones(img.shape[:2], bool), detect_pages(img)[0].corners, SleeveParams())
        assert not fit.sleeved and fit.reason == "no_sleeve_band", (seed, kind)
        assert fit.outline == [] and fit.report()["sleeved"] is False


def test_off_returns_the_outer_outline():
    img = make_case(1, "glare", 1, sleeve=SleeveSpec()).images[0]
    quad = detect_pages(img)[0].corners
    fit = find_sleeve(img, np.ones(img.shape[:2], bool), quad, SleeveParams(mode="off"))
    assert not fit.sleeved and fit.reason == "off"
    np.testing.assert_array_equal(fit.corners, quad)


def test_params_are_checked():
    with pytest.raises(ValueError):
        SleeveParams(mode="on").validate()
    with pytest.raises(ValueError):
        SleeveParams(crop="page").validate()


def test_candidates_on_a_crimped_side_are_the_seams_inner_edge_and_the_next():
    lab, paths = _strip([("outside", OUTSIDE, 70), ("crimp", (150, 150, 150), 27), ("clear", CLEAR, 14), ("paper", PAPER, 30), ("print", (90, 140, 60), 90)])
    bands = classify_bands(lab, paths, None, SleeveParams(), W, strip_ok=False)
    side = SideFinding("top", [np.zeros((COLS, 2))] * len(paths), bands, None, False, [np.zeros(COLS)] * len(paths), width=W)
    # The edge inside the clear gap first, then the seam's own inner edge;
    # never the print's border.
    assert paper_candidates(side) == [2, 1]
