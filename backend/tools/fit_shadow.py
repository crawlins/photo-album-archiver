"""Score shadow-detector settings on synthetic pages, to choose ``ShadowParams``.

Not installed with the package. Run from ``backend/``:

    python tools/fit_shadow.py                                  # the defaults
    python tools/fit_shadow.py '{"depth_ramp": 0.08}' '{"chroma_tol": 0.2}'   # each setting, against the defaults

Each argument is a set of ``ShadowParams`` fields to change. For each, three
groups of synthetic pages are fused in the page frame with their true
homographies (``tests/shadow_cases.py``), so registration plays no part:

- ``one``: glare-free pages, two and three shots, a shadow in the first shot
  (seeds 21-32). Reported: the share of the true shadow found where shots
  overlap (``shadow_raw``), false alarms as a share of the overlap, and the
  mean error of the composed page inside the shadow against the same page
  shot without it.
- ``glare``: the same with glare in every shot (seeds 1-12), where some of
  the shadow is under another shot's glare and stays (the conflict case).
- ``none``: the existing cases without a shadow (glare, stitch, clean white,
  pairs). Reported: how many come out different from fusion without shadow
  handling, and how many show any shadow for the report.

The single-image cue is scored on every shot of the first two groups:
shadows of depth 0.35 or more found (half of them or more), and shots with a
false seed.

The defaults in ``ShadowParams`` were chosen with this script; the real
pages in the private data repository decided between settings that scored
alike here.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from albumproc.fuse import FuseParams, fuse  # noqa: E402
from albumproc.shadow import ShadowParams, grow_regions, single_shadow  # noqa: E402
from albumproc.synth import _shadow_plan, make_case  # noqa: E402
from shadow_cases import page_frame, to_grid  # noqa: E402

ONE = [(s, "glare", n, None, "one") for s in range(21, 33) for n in (2, 3)]
GLARE = [(s, "glare", n, "spot" if s % 2 else "sleeve", "one") for s in range(1, 13) for n in (2, 3)]
NONE = (
    [(s, "glare", n, "spot" if s % 2 else "sleeve", "none") for s in range(1, 7) for n in (2, 3, 4)]
    + [(s, "stitch", n, "sleeve" if s >= 40 else "spot", "none") for s in (10, 40, 41) for n in (2, 4)]
    + [(s, "clean_white", 3, "spot", "none") for s in (30, 31, 32)]
    + [(s, "pair", 3, "spot", "none") for s in (20, 21, 22)]
)
_frames: dict = {}


def _frame(spec):
    if spec not in _frames:
        seed, kind, n, style, shadow = spec
        _frames[spec] = page_frame(make_case(seed, kind, n, glare_style=style or "spot", shadow=shadow, glare=style is not None))
    return _frames[spec]


def _depth(seed: int) -> float:
    """The depth of the first shot's shadow in a ``glare``-kind case of ``seed`` (as ``make_case`` draws it)."""
    return _shadow_plan(np.random.default_rng([seed, 7]), "one", (4840, 4340), (1320, 1320, 2200, 1700), [(0.5, 0.5)])[0].depth


def _score(args):
    spec, kw = args
    p = replace(ShadowParams(), **kw)
    F = _frame(spec)
    r = fuse(F.warped, F.masks, params=FuseParams(shadow=p))
    if spec[4] == "none":
        off = fuse(F.warped, F.masks, params=FuseParams(shadow=ShadowParams(enabled=False)))
        return spec, {"changed": not np.array_equal(r.image, off.image), "reported": any(s.any() for s in r.shadow_raw_small)}
    grid = r.masks_small[0].shape
    overlap = np.sum(r.masks_small, axis=0) >= 2
    near_glare = np.zeros(grid, bool)
    for g in F.glare:
        near_glare |= cv2.dilate(to_grid(g, grid).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    truth = to_grid(F.shadow[0], grid) & overlap & ~near_glare
    near = cv2.dilate(to_grid(F.shadow[0], grid).astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
    found = r.shadow_raw_small[0] > 0
    false = ((found & ~near & overlap).sum() + sum(((s > 0) & overlap).sum() for s in r.shadow_raw_small[1:])) / max(overlap.sum(), 1)
    clean = _frame(spec[:4] + ("none",))
    r0 = fuse(clean.warped, clean.masks, reference=r.tone_reference, params=FuseParams(shadow=ShadowParams(enabled=False)))
    inside = F.shadow[0] & (np.sum(F.masks, axis=0) >= 2)
    single = []
    for i, (t, m) in enumerate(zip(r.toned_small, r.masks_small)):
        cue = single_shadow(t, m, p)
        seeds = grow_regions(cue, cue, p) > 0
        gt = to_grid(F.shadow[i], grid)
        far = ~(cv2.dilate(gt.astype(np.uint8), np.ones((21, 21), np.uint8)) > 0)
        single.append(((seeds & gt).sum() / gt.sum() if gt.any() else None, bool((seeds & far & m).any())))
    return spec, {
        "recall": float((found & truth).sum() / max(truth.sum(), 1)),
        "false": float(false),
        "error": float(np.abs(r.image.astype(int) - r0.image.astype(int))[inside].mean()),
        "depth": _depth(spec[0]),
        "single": single,
    }


def main(argv: list[str]) -> None:
    settings = [json.loads(a) for a in argv] or [{}]
    with ProcessPoolExecutor() as ex:
        for kw in settings:
            res = dict(ex.map(_score, [(s, kw) for s in ONE + GLARE + NONE]))
            print(json.dumps(kw) or "{}")
            for name, group in (("one", ONE), ("glare", GLARE)):
                rows = [res[s] for s in group]
                for n in (2, 3):
                    sub = [r for s, r in zip(group, rows) if s[2] == n]
                    print(
                        f"  {name:5s} {n} shots: recall {np.mean([r['recall'] for r in sub]):.2f}"
                        f" (min {min(r['recall'] for r in sub):.2f}), false {max(r['false'] for r in sub):.4f},"
                        f" error {np.mean([r['error'] for r in sub]):.1f} (max {max(r['error'] for r in sub):.1f})"
                    )
            deep = [found for s in ONE + GLARE for found, _ in res[s]["single"][:1] if res[s]["depth"] >= 0.35]
            false_shots = sum(f for s in ONE + GLARE for _, f in res[s]["single"])
            print(f"  single: {sum(d >= 0.5 for d in deep)}/{len(deep)} shadows of depth >= 0.35 found; false seeds on {false_shots} shots")
            none = [res[s] for s in NONE]
            print(f"  none: {sum(r['changed'] for r in none)}/{len(none)} changed, {sum(r['reported'] for r in none)} with a shadow reported")


if __name__ == "__main__":
    main(sys.argv[1:])
