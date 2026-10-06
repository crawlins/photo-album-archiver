"""Fit the glare detector's logistic weights on synthetic pages.

Not installed with the package. Run from ``backend/``:

    python tools/fit_glare.py            # fit and print GlareParams values
    python tools/fit_glare.py --eval     # also score each case with the fitted weights

Each case is processed by ``process_page``; the detector's features are taken
from the final fusion, on the page grid, for every shot. Labels:

- where one shot covers the page, the residual glare truth
  (``evaluate.residual_truth``: at least 20 levels brighter than the
  ground-truth page) says glare or clean;
- where shots overlap, the comparison cue does (excess over the darkest
  shot above ``glare_flag`` is glare, below 0.02 clean), as in the
  detector's own bias adaptation.

The fit is weighted towards precision (``--neg-weight``): a warning about
glare that is not there sends the user back to re-shoot a good page, so false
alarms cost more than misses. Weights are kept non-negative, so each feature
can only add evidence of glare, as its physical meaning says.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from albumproc import pipeline  # noqa: E402
from albumproc.evaluate import residual_truth  # noqa: E402
from albumproc.glare import GlareParams, combine, glare_features, glare_map, residual  # noqa: E402
from albumproc.synth import make_case  # noqa: E402

NAMES = ["veil", "desat", "flat", "clip"]

# (kind, glare style, shots, seed). Seeds differ from those the tests use.
CASES = [
    ("stitch", "sleeve", 2, 101),
    ("stitch", "sleeve", 2, 102),
    ("stitch", "sleeve", 2, 103),
    ("stitch", "sleeve", 4, 104),
    ("stitch", "spot", 2, 105),
    ("stitch", "spot", 2, 106),
    ("glare", "sleeve", 2, 107),
    ("glare", "sleeve", 3, 108),
    ("glare", "spot", 3, 109),
    ("glare", "spot", 3, 110),
    ("clean_white", "spot", 3, 111),
    ("clean_white", "spot", 3, 112),
]


def _case(spec):
    kind, style, n, seed = spec
    captured = {}
    fuse = pipeline.fuse

    def keep(*a, **k):
        captured["r"] = fuse(*a, **k)
        return captured["r"]

    pipeline.fuse = keep
    case = make_case(seed, kind, n, glare_style=style)
    res = pipeline.process_page(case.images, pipeline.PageOptions(glare=GlareParams(enabled=False)))
    r = captured["r"]
    grid = r.masks_small[0].shape
    truth = cv2.resize(residual_truth(res.image, case.page).astype(np.float32), grid[::-1], interpolation=cv2.INTER_AREA)
    feats = [glare_features(t, m, s) for t, m, s in zip(r.toned_small, r.masks_small, r.small)]
    return dict(spec=spec, feats=feats, masks=r.masks_small, excess=r.excess_small, weights=r.weights_small, truth=truth)


def _samples(case, glare_flag=0.08):
    count = np.sum([m.astype(np.uint8) for m in case["masks"]], axis=0)
    xs, ys = [], []
    for f, m, ex in zip(case["feats"], case["masks"], case["excess"]):
        X = np.stack([getattr(f, n) for n in NAMES], -1)
        one, both = m & (count == 1), m & (count >= 2)
        pos = (one & (case["truth"] > 0.5)) | (both & (ex > glare_flag))
        neg = (one & (case["truth"] < 0.01)) | (both & (ex < 0.02))
        xs += [X[pos][::3], X[neg][::15]]
        ys += [np.ones(len(X[pos][::3])), np.zeros(len(X[neg][::15]))]
    return np.concatenate(xs), np.concatenate(ys)


def fit(X: np.ndarray, y: np.ndarray, neg_weight: float, l2: float = 1e-3) -> np.ndarray:
    """Class-weighted logistic regression with non-negative feature weights."""
    npos = max(float(y.sum()), 1.0)
    wt = np.where(y == 1, 1 / npos, neg_weight / max(len(y) - npos, 1.0))
    wt *= len(y) / wt.sum()
    active = list(range(X.shape[1]))
    while True:
        X1 = np.hstack([np.ones((len(X), 1)), X[:, active]])
        b = np.zeros(X1.shape[1])
        for _ in range(50):
            p = 1 / (1 + np.exp(-np.clip(X1 @ b, -30, 30)))
            g = X1.T @ (wt * (y - p)) - l2 * b
            H = (X1 * (wt * p * (1 - p))[:, None]).T @ X1 + l2 * np.eye(len(b))
            step = np.linalg.solve(H, g)
            b += step
            if np.abs(step).max() < 1e-6:
                break
        neg = [a for a, w in zip(active, b[1:]) if w < 0]
        if not neg:
            break
        active.remove(min(neg, key=lambda a: b[1 + active.index(a)]))
    out = np.zeros(X.shape[1] + 1)
    out[0] = b[0]
    for a, w in zip(active, b[1:]):
        out[1 + a] = w
    return out


def _params(b: np.ndarray) -> GlareParams:
    return GlareParams(bias=round(float(b[0]), 2), **{f"w_{n}": round(float(w), 2) for n, w in zip(NAMES, b[1:])})


def evaluate(case, p: GlareParams):
    maps = [glare_map(f, m, p) for f, m in zip(case["feats"], case["masks"])]
    per = [combine(g, ex, 0.08, p.weak) for g, ex in zip(maps, case["excess"])]
    count = np.sum([m.astype(np.uint8) for m in case["masks"]], axis=0)
    covered = count > 0
    det = (residual(per, case["weights"]) >= 0.5) & covered
    truth, clean = (case["truth"] > 0.5) & covered, (case["truth"] < 0.01) & covered
    return (det & truth).sum() / max(truth.sum(), 1), (det & clean).sum() / max(clean.sum(), 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--neg-weight", type=float, default=30.0, help="weight of the clean class relative to glare")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    with ProcessPoolExecutor(a.jobs) as ex:
        cases = list(ex.map(_case, CASES))
    X, y = zip(*(_samples(c) for c in cases))
    b = fit(np.concatenate(X), np.concatenate(y), a.neg_weight)
    p = _params(b)
    print(f"bias={p.bias}, w_veil={p.w_veil}, w_desat={p.w_desat}, w_flat={p.w_flat}, w_clip={p.w_clip}")
    if a.eval:
        for c in cases:
            rec, fp = evaluate(c, p)
            print(f"{c['spec']}: residual recall {rec:.3f}, false positives {fp:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
