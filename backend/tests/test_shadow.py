"""Shadow detection and removal (``shadow.py``, its use in ``fuse`` and the page report)."""

import cv2
import numpy as np
import pytest

from albumproc import PageOptions, process_page
from albumproc.evaluate import residual_truth
from albumproc.fuse import FuseParams, fuse
from albumproc.regions import _location
from albumproc.shadow import ShadowParams, comparison_shadow, grow, grow_regions, linear, single_shadow, window_fit
from albumproc.synth import SHADOWS, _add_shadow, make_case, make_shadow
from shadow_cases import page_frame, to_grid

H, W = 300, 400
ON = ShadowParams(enabled=True)  # shadow handling is off by default
ON_PAGE = PageOptions(fuse=FuseParams(shadow=ON))


def _srgb(lin: np.ndarray) -> np.ndarray:
    v = np.clip(lin, 0, 1)
    return (np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055) * 255 + 0.5).astype(np.uint8)


def _content(kind: str, seed: int = 0) -> np.ndarray:
    """Linear BGR content: ``print`` (coloured and textured), ``colour`` (flat coloured patches) or ``grey`` (flat pale grey)."""
    rng = np.random.default_rng(seed)
    if kind == "grey":
        return np.full((H, W, 3), 0.55, np.float32)
    # Saturated colours: one or two channels high, the rest low.
    cells = np.where(rng.random((H // 50, W // 50, 3)) < 0.5, 0.75, 0.2)
    cells[cells.max(axis=2) < 0.5, 0] = 0.75
    cells[cells.min(axis=2) > 0.5, 1] = 0.2  # no grey
    patches = np.kron(cells, np.ones((50, 50, 1))).astype(np.float32)
    if kind == "colour":
        return patches
    tex = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 1.5)
    return np.clip(patches * (1 + 0.6 * tex[..., None]), 0.02, 1).astype(np.float32)


def _disc(soft: float = 6.0) -> np.ndarray:
    """A soft disc in the middle, 0..1."""
    m = np.zeros((H, W), np.float32)
    cv2.circle(m, (W // 2, H // 2), 70, 1.0, -1)
    return cv2.GaussianBlur(m, (0, 0), soft)


def _pair(content: np.ndarray, shadow: float = 0.0, glare: float = 0.0, seed: int = 1):
    """Two shots of ``content``: the first with a shadow of depth ``shadow`` in the disc, the second with white light ``glare`` added there."""
    rng = np.random.default_rng(seed)
    d = _disc()[..., None]
    a = content * (1 - shadow * d)
    b = content + glare * d
    noise = [rng.normal(0, 0.004, content.shape).astype(np.float32) for _ in range(2)]
    return [_srgb(a + noise[0]), _srgb(b + noise[1])], [np.ones((H, W), bool)] * 2


INSIDE = (slice(H // 2 - 40, H // 2 + 40), slice(W // 2 - 40, W // 2 + 40))


# Synthetic shadows


def test_shadow_scales_linear_light_inside_its_mask():
    rng = np.random.default_rng(3)
    s = make_shadow(rng, (W, H), (50, 40, 300, 220), shape="arm", edge="left", depth=0.5, penumbra=0.01, along=0.5, reach=0.5)
    img = np.full((H, W, 3), 180, np.uint8)
    out, mask = _add_shadow(img, np.eye(3), s)
    assert 0.02 < mask.mean() < 0.5
    lin_in = linear(out.astype(np.uint8))[mask].mean(axis=0) / linear(img)[0, 0]
    # Half depth at the mask's edge, so on average a little less than the full depth inside.
    assert np.all((lin_in > 0.45) & (lin_in < 0.7))
    far = cv2.dilate(mask.astype(np.uint8), np.ones((31, 31), np.uint8)) == 0
    assert np.abs(out[far] - 180).max() < 1


@pytest.mark.parametrize("shape", ["arm", "hand", "phone"])
@pytest.mark.parametrize("edge", ["left", "right", "top", "bottom"])
def test_shadow_enters_from_its_edge(shape, edge):
    rng = np.random.default_rng(5)
    rect = (50, 40, 300, 220)
    s = make_shadow(rng, (W, H), rect, shape=shape, edge=edge, depth=0.4, penumbra=0.005)
    sil = cv2.resize(s.silhouette, (W, H)) >= 0.5
    x, y, w, h = rect
    band = {"left": sil[y : y + h, x : x + 5], "right": sil[y : y + h, x + w - 5 : x + w], "top": sil[y : y + 5, x : x + w], "bottom": sil[y + h - 5 : y + h, x : x + w]}[edge]
    assert band.any()
    assert s.shape == shape and s.edge == edge


def test_shadow_options_are_checked():
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError):
        make_shadow(rng, (W, H), (0, 0, W, H), shape="cat")
    with pytest.raises(ValueError):
        make_shadow(rng, (W, H), (0, 0, W, H), edge="middle")
    with pytest.raises(ValueError):
        make_case(1, "glare", 2, shadow="some")
    assert SHADOWS == ("none", "one", "all", "part")


def test_a_shadow_leaves_the_rest_of_the_case_unchanged():
    plain, none, one = make_case(7, "glare", 2), make_case(7, "glare", 2, shadow="none"), make_case(7, "glare", 2, shadow="one")
    for a, b in zip(plain.images, none.images):
        assert np.array_equal(a, b)
    assert not np.array_equal(plain.images[0], one.images[0]) and one.shots[0].shadow_mask.any()
    assert np.array_equal(plain.images[1], one.images[1]) and one.shots[1].shadow_mask is None
    assert all(s.shadow_mask is None for s in plain.shots)


# The comparison cue


def test_window_fit_tells_a_scale_from_an_offset():
    rng = np.random.default_rng(2)
    R = _content("print")
    valid = np.ones((H, W), bool)
    a, b, mR = window_fit(R * 0.6, R, valid, 15)  # the shot is R in a shadow of depth 0.4
    assert np.median(a) == pytest.approx(1 / 0.6, rel=0.05)
    assert np.median(np.abs(b) / mR) < 0.02
    a, b, mR = window_fit(R, R + 0.15, valid, 15)  # the reference has white light added
    assert np.median(a) == pytest.approx(1.0, abs=0.05)
    assert np.median(b) == pytest.approx(0.15, abs=0.02)
    del rng


@pytest.mark.parametrize("kind", ["print", "colour"])
def test_shadow_in_one_shot_is_found(kind):
    shots, masks = _pair(_content(kind), shadow=0.45)
    found = comparison_shadow(shots, masks)
    assert (found.shadow[0][INSIDE] > 0).mean() > 0.95
    assert (found.shadow_raw[0][INSIDE] > 0).mean() > 0.95
    assert not found.shadow[1].any()
    assert (found.shadow[0][_disc() < 0.01] > 0).mean() < 0.02


@pytest.mark.parametrize("kind", ["print", "colour"])
def test_glare_in_the_other_shot_is_not_a_shadow(kind):
    shots, masks = _pair(_content(kind), glare=0.25)
    found = comparison_shadow(shots, masks)
    assert not found.any()
    assert not any(s.any() for s in found.shadow_raw)


def test_two_shots_on_flat_grey_keep_darker_wins():
    """On flat grey content a shadow in one shot and glare in the other look alike; with two shots nothing is called."""
    for shots, masks in (_pair(_content("grey"), shadow=0.4), _pair(_content("grey"), glare=0.3)):
        found = comparison_shadow(shots, masks)
        assert not any(s.any() for s in found.shadow_raw)


def test_third_shot_settles_flat_grey():
    content = _content("grey")
    shots, masks = _pair(content, shadow=0.4)
    third = _srgb(content + np.random.default_rng(9).normal(0, 0.004, content.shape))
    found = comparison_shadow(shots + [third], masks + [np.ones((H, W), bool)])
    assert (found.shadow[0][INSIDE] > 0).mean() > 0.95
    assert not found.shadow[1].any() and not found.shadow[2].any()


def test_glare_in_most_other_shots_is_not_a_shadow_on_flat_grey():
    content = _content("grey")
    shots, masks = _pair(content, glare=0.3)
    found = comparison_shadow([_srgb(content), shots[1], shots[1].copy()], masks + [np.ones((H, W), bool)])
    assert not any(s.any() for s in found.shadow_raw)


def test_glare_veto():
    """A shot darker than a reference with glare of its own is not shadowed for fusion, but is still counted for the report."""
    shots, masks = _pair(_content("print"), shadow=0.45)
    veto = [np.zeros((H, W), np.float32), np.where(_disc() > 0.05, 0.9, 0.0).astype(np.float32)]
    found = comparison_shadow(shots, masks, glare=veto)
    assert not found.shadow[0][INSIDE].any()
    assert (found.shadow_raw[0][INSIDE] > 0).mean() > 0.5
    calls = []
    comparison_shadow(*_pair(_content("print"), glare=0.25), glare=lambda: calls.append(1) or veto)
    assert calls == []  # the glare score is only computed when a shadow is in sight


def test_shallow_and_dark_differences_are_not_shadow():
    shots, masks = _pair(_content("print"), shadow=0.08)  # below the minimum depth
    assert not comparison_shadow(shots, masks).any()
    shots, masks = _pair(_content("print") * 0.08, shadow=0.45)  # dark content: tone errors, not shadow
    assert not comparison_shadow(shots, masks).any()


def test_small_shadows_are_dropped():
    p = ShadowParams()
    cue = np.zeros((H, W), np.float32)
    cue[10:14, 10:14] = 1.0  # 16 px, under min_seed_px wide
    cue[100:130, 100:130] = 1.0  # 900 px, 0.75% of the grid
    assert grow_regions(cue, cue, p)[100:130, 100:130].min() == 1.0
    assert grow_regions(cue, cue, p)[10:14, 10:14].max() == 0
    assert not grow_regions(cue, cue, ShadowParams(min_area=0.01)).any()


def test_growth_follows_the_penumbra():
    seed = np.zeros((H, W), np.float32)
    seed[100:200, 100:200] = 1.0
    grow_cue = np.zeros((H, W), np.float32)
    grow_cue[90:210, 90:210] = 0.5  # weak, but joined to the seed
    grow_cue[10:30, 10:30] = 0.5  # weak and alone
    g = grow_regions(seed, grow_cue, ShadowParams())
    assert g[95, 95] == 0.5 and g[150, 150] == 1.0 and g[20, 20] == 0


def test_disabled_finds_nothing_and_one_shot_finds_nothing():
    shots, masks = _pair(_content("print"), shadow=0.45)
    assert not comparison_shadow(shots[:1], masks[:1]).any()


# Fusion


def _fuse(shots, masks, **kw):
    full = [cv2.resize(s, (W * 2, H * 2), interpolation=cv2.INTER_LINEAR) for s in shots]
    fm = [cv2.resize(m.astype(np.uint8), (W * 2, H * 2), interpolation=cv2.INTER_NEAREST) > 0 for m in masks]
    kw.setdefault("shadow", ON)
    return fuse(full, fm, reference=1, params=FuseParams(**kw))


def test_fusion_takes_the_unshadowed_shot():
    content = _content("print")
    shots, masks = _pair(content, shadow=0.45)
    on, off = _fuse(shots, masks), _fuse(shots, masks, shadow=ShadowParams(enabled=False))
    truth = cv2.resize(_srgb(content), (W * 2, H * 2))
    inside = cv2.resize((_disc() > 0.99).astype(np.uint8), (W * 2, H * 2)) > 0
    assert np.abs(on.image.astype(int) - truth.astype(int))[inside].mean() < 3
    assert np.abs(off.image.astype(int) - truth.astype(int))[inside].mean() > 20
    assert on.shadow_small[0].max() > 0.5 and not on.shadow_small[1].any()
    assert off.shadow_small is None and off.shadow_raw_small is None


def test_clean_shot_is_not_counted_as_glare_beside_a_shadow():
    shots, masks = _pair(_content("print"), shadow=0.45)
    on, off = _fuse(shots, masks), _fuse(shots, masks, shadow=ShadowParams(enabled=False))
    assert off.glare_fraction[1] > 0.05  # against the shadow, the clean shot looked glared
    assert on.glare_fraction[1] < 0.01


def test_where_every_shot_is_shadowed_darker_wins(monkeypatch):
    import albumproc.fuse as F
    from albumproc.shadow import ComparisonShadow

    content = _content("print")
    shots, masks = _pair(content, shadow=0.45)
    shots[1] = _srgb(content * (1 - 0.3 * _disc()[..., None]))  # both shadowed, the first more
    real = F.comparison_shadow

    def both(toned, m, p, glare):  # as if comparison had found a shadow in both shots, in the disc
        r = real(toned, m, p, glare)
        s = [cv2.resize(np.where(_disc() > 0.5, 1.0, 0.0).astype(np.float32), m[0].shape[::-1], interpolation=cv2.INTER_NEAREST)] * 2
        return ComparisonShadow(r.cue, r.cue_raw, s, s)

    monkeypatch.setattr(F, "comparison_shadow", both)
    on = _fuse(shots, masks)
    monkeypatch.undo()
    off = _fuse(shots, masks, shadow=ShadowParams(enabled=False))
    inside = cv2.resize((_disc() > 0.99).astype(np.uint8), (W * 2, H * 2)) > 0
    darker, lighter = (cv2.resize(s, (W * 2, H * 2)).astype(int) for s in shots)
    assert np.abs(on.image.astype(int) - darker)[inside].mean() < 3  # the darker shot, as without shadow handling
    assert np.abs(on.image.astype(int) - lighter)[inside].mean() > 10
    del off


def test_shadow_on_a_large_pale_area_does_not_bend_the_tone():
    """A shallow shadow over much of a pale page passes for agreement in the tone fit, which would then hide it."""
    rng = np.random.default_rng(4)
    paper = (_content("grey") * np.float32([0.85, 0.95, 1.0]) + cv2.GaussianBlur(rng.normal(0, 0.01, (H, W)).astype(np.float32), (0, 0), 1)[..., None]).astype(np.float32)
    big = np.zeros((H, W), np.float32)
    cv2.rectangle(big, (0, 0), (W // 3, H), 1.0, -1)  # a third of the page
    big = cv2.GaussianBlur(big, (0, 0), 8)[..., None]
    clean = _srgb(paper)
    shots = [_srgb(paper * (1 - 0.3 * big)), clean, clean.copy()]
    masks = [np.ones((H, W), bool)] * 3
    r = _fuse(shots, masks)
    truth = cv2.resize(clean, (W * 2, H * 2))
    assert np.abs(r.image.astype(int) - truth.astype(int)).mean() < 3


def test_no_shadow_gives_the_same_page():
    shots, masks = _pair(_content("print"), glare=0.25)
    on, off = _fuse(shots, masks), _fuse(shots, masks, shadow=ShadowParams(enabled=False))
    assert np.array_equal(on.image, off.image)
    assert [s.any() for s in on.shadow_small] == [False, False]


# The single-image cue


def test_single_cue_finds_a_soft_shadow_on_paper():
    paper = _content("grey") * np.float32([0.95, 1.0, 1.05])
    shot = _srgb(paper * (1 - 0.45 * _disc(8)[..., None]) + np.random.default_rng(0).normal(0, 0.003, paper.shape))
    cue = single_shadow(shot)
    assert (cue[INSIDE] >= ShadowParams().seed).mean() > 0.9
    assert cue[_disc(8) < 0.01].max() < ShadowParams().weak


def test_single_cue_ignores_dark_prints_with_sharp_edges():
    paper = _content("grey")
    img = paper.copy()
    img[80:220, 100:300] = 0.02  # a black mat, sharp-edged
    img[120:180, 140:260] = _content("print")[120:180, 140:260] * 0.3  # a dark print in it
    assert single_shadow(_srgb(img)).max() < ShadowParams().weak


def test_single_cue_ignores_spine_shading():
    paper = _content("grey")
    ramp = np.clip(1 - np.arange(W) / (0.15 * W), 0, 1).astype(np.float32)  # darkest at the left edge, the whole height
    assert single_shadow(_srgb(paper * (1 - 0.5 * ramp)[None, :, None])).max() < ShadowParams().weak


def test_growth_into_the_single_covered_part():
    p = ShadowParams()
    comparison = np.zeros((H, W), np.float32)
    comparison[100:200, 200:260] = 1.0  # found by comparison in the overlap (x >= 200)
    single = np.zeros((H, W), np.float32)
    single[100:200, 120:260] = 0.5  # the shot alone sees it run on to the left
    single[20:40, 20:40] = 0.5  # weak and unconnected
    single[250:290, 20:60] = 0.9  # strong on its own
    area = np.zeros((H, W), bool)
    area[:, :200] = True
    g = grow(comparison, single, area, p)
    assert g[150, 150] == 1 and g[150, 230] == 0  # grown; the overlap is comparison's
    assert g[30, 30] == 0 and g[270, 40] == 1


# Synthetic pages, end to end


def _framed_fuse(F, **kw):
    kw.setdefault("shadow", ON)
    return fuse(F.warped, F.masks, params=FuseParams(**kw))


@pytest.mark.parametrize(
    "seed,kind,n,style",
    [(1, "glare", 2, "spot"), (3, "glare", 4, "spot"), (4, "glare", 3, "sleeve"), (10, "stitch", 2, "spot"), (40, "stitch", 4, "sleeve"), (30, "clean_white", 3, "spot"), (21, "pair", 3, "spot")],
)
def test_pages_without_shadow_come_out_as_before(seed, kind, n, style):
    F = page_frame(make_case(seed, kind, n, glare_style=style))
    on, off = _framed_fuse(F), _framed_fuse(F, shadow=ShadowParams(enabled=False))
    np.testing.assert_array_equal(on.image, off.image)
    assert not any(s.any() for s in on.shadow_small)


@pytest.mark.parametrize("seed", [21, 30, 32])
def test_shadow_in_one_of_three_shots_is_removed(seed):
    shadowed, clean = page_frame(make_case(seed, "glare", 3, shadow="one", glare=False)), page_frame(make_case(seed, "glare", 3, glare=False))
    r = _framed_fuse(shadowed)
    r0 = fuse(clean.warped, clean.masks, reference=r.tone_reference, params=FuseParams(shadow=ShadowParams(enabled=False)))
    off = fuse(shadowed.warped, shadowed.masks, reference=r.tone_reference, params=FuseParams(shadow=ShadowParams(enabled=False)))
    inside = shadowed.shadow[0] & np.all(shadowed.masks, axis=0)
    err = np.abs(r.image.astype(int) - r0.image.astype(int))[inside].mean()
    assert err < 3
    assert np.abs(off.image.astype(int) - r0.image.astype(int))[inside].mean() > 5 * err
    grid = r.masks_small[0].shape
    truth = to_grid(shadowed.shadow[0], grid)
    found = r.shadow_raw_small[0] > 0
    assert (found & truth).sum() / truth.sum() >= 0.8
    near = cv2.dilate(truth.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    assert ((found & ~near).sum() + sum((s > 0).sum() for s in r.shadow_raw_small[1:])) / found.size <= 0.005


@pytest.mark.parametrize("seed", [1, 5])
def test_glare_removal_no_worse_with_a_shadow_in_another_shot(seed):
    """Glare in every shot, and a shadow in the first: glare left in the page is no more than without shadow handling."""
    F = page_frame(make_case(seed, "glare", 3, shadow="one"))
    on, off = _framed_fuse(F), _framed_fuse(F, shadow=ShadowParams(enabled=False))
    g_on, g_off = residual_truth(on.image, F.truth).mean(), residual_truth(off.image, F.truth).mean()
    assert g_on <= g_off + 0.001


@pytest.fixture(scope="module")
def part_page():
    case = make_case(10, "stitch", 2, shadow="part", glare=False)
    return case, process_page(case.images, ON_PAGE)


def test_shadow_where_only_one_shot_covers_is_reported(part_page):
    _, res = part_page
    (w,) = [w for w in res.report.warnings if w["code"] == "shadow"]
    assert w["single_shot"] == len(w["regions"]) >= 1 and w["location"] == "left"
    assert w["message"].endswith("Only one photo covers it; add a shot of that area without the shadow.")
    assert res.report.shadow["fraction"] > 0.05 and res.report.shadow["per_shot"][0] > 0
    assert res.report.detector["shadow"]["min_depth"] == ShadowParams().min_depth
    assert res.residual_shadow is not None and res.residual_shadow.max() > 0.5


def test_same_shadow_in_every_shot_is_reported_by_a_looser_single_cue():
    """Every shot has the same shadow: only the single-image cue can see it.

    The default rim (``edge_cover``) is strict enough that the real two-shot
    pages without a shadow get no warning, and this page's shadow is missed
    with it; a looser rim reports it, as ``all_shots_shadowed``, in the
    right place.
    """
    case = make_case(21, "glare", 3, shadow="all", glare=False)
    strict = process_page(case.images, ON_PAGE)
    assert "shadow" not in [w["code"] for w in strict.report.warnings]
    loose = PageOptions(fuse=FuseParams(shadow=ShadowParams(enabled=True, edge_cover=0.15)))
    res = process_page(case.images, loose)
    (w,) = [w for w in res.report.warnings if w["code"] == "shadow"]
    assert w["all_shots_shadowed"] >= 1 and w["regions"][0]["cause"] == "all_shots_shadowed"
    F = page_frame(case)
    ys, xs = np.nonzero(F.shadow[0])
    h, wd = F.shadow[0].shape
    assert w["location"] == _location(xs.mean() / wd, ys.mean() / h)
    assert "hold the phone so that you and it are not between the light and the page" in w["message"]


def test_shadow_handling_off_reports_nothing(part_page):
    case, on = part_page
    assert PageOptions().fuse.shadow.enabled is False  # off unless asked for
    off = process_page(case.images)
    assert off.report.shadow == {"fraction": 0.0, "per_shot": {i: 0.0 for i in off.report.used}, "regions": []}
    assert "shadow" not in [w["code"] for w in off.report.warnings]
    assert on.report.coverage == off.report.coverage
