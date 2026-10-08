"""Train the learned single-shot glare detector on real pages' overlap labels.

Not installed with the package, and the only part of the project that needs
PyTorch (``pip install torch onnx``); the package runs the exported model
through OpenCV. Run from ``backend/`` on the output of
``tools/real_glare_data.py``:

    python tools/train_glare_net.py train DATA_DIR --init net5.pt --out glare_net.pt
    python tools/train_glare_net.py eval DATA_DIR --model glare_net.pt
    python tools/train_glare_net.py export glare_net.pt src/albumproc/models/glare_net.onnx

Pages are split by name (``--test``, ``--val``), never by shot, so that the
reported numbers come from pages the model never saw. Training draws random
crops of each shot as the pipeline sees it (warped into the page frame on the
fusion's grid, zero outside the shot), with flips, quarter turns and small
exposure and colour changes. Clean pixels are weighted up (``--clean-weight``):
a warning about glare that is not there sends the user back to re-shoot a
good page.

The evaluation scores the detector as the pipeline runs it (score, then
hysteresis from ``GlareParams``' seed and weak thresholds) against the overlap
labels: recall is the share of glare-labelled pixels detected, false alarms
the share of clean-labelled pixels detected.

Results (2026-10-08), the shipped ``models/glare_net.onnx``: fine-tuned from
the synthetic-page model (net5 in the project's glare-research folder) for
4000 steps, clean weight 10, on 15 real pages (``glare-01`` to ``-15``);
thresholds 0.9/0.5/3 chosen on a run that held ``glare-13`` to ``-15`` out.
On the 9 held-out pages (``glare-16`` to ``-19`` and the five 2-shot pages):
recall 0.096, false alarms 0.0001; the hand-made ``features`` detector with
its defaults: recall 0.002, false alarms 0.0000. Recall varies widely by page
(0.62 on ``glare-19``, 0.02 on ``glare-18``, whose glare is a veil over a
third of the page). Most of what is missed is such a wide, soft veil, which
one photo cannot tell from the page's lighting; the spec's 80% is not
reachable this way.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from albumproc.glare import glare_features, glare_map, hysteresis  # noqa: E402
from real_glare_data import LABEL_CLEAN, LABEL_GLARE, LABEL_UNKNOWN, clean_labels, overlap_labels  # noqa: E402

NET_SCALE = 0.5  # the network runs on the fusion grid shrunk by this
DEFAULT_TEST = ["glare-16", "glare-17", "glare-18", "glare-19"]
DEFAULT_VAL = ["glare-13", "glare-14", "glare-15"]


def _cbr(i, o, s=1, d=1):
    return nn.Sequential(nn.Conv2d(i, o, 3, s, padding=d, dilation=d), nn.ReLU(inplace=True))


class Net(nn.Module):
    """Small encoder-decoder (88k parameters) with a wide dilated middle, so
    that each output sees about a fifth of the page around it."""

    def __init__(self, c: int = 16):
        super().__init__()
        self.e1 = nn.Sequential(_cbr(3, c), _cbr(c, c))
        self.e2 = nn.Sequential(_cbr(c, 2 * c, 2), _cbr(2 * c, 2 * c))
        self.e3 = nn.Sequential(_cbr(2 * c, 2 * c, 2), _cbr(2 * c, 2 * c, 1, 2), _cbr(2 * c, 2 * c, 1, 4), _cbr(2 * c, 2 * c, 1, 8), _cbr(2 * c, 2 * c, 1, 16))
        self.d2 = _cbr(4 * c, 2 * c)
        self.d1 = _cbr(3 * c, c)
        self.out = nn.Conv2d(c, 1, 1)

    def forward(self, x):
        a = self.e1(x)
        b = self.e2(a)
        c = self.e3(b)
        c = F.interpolate(c, size=b.shape[2:], mode="bilinear", align_corners=False)
        b = self.d2(torch.cat([c, b], 1))
        b = F.interpolate(b, size=a.shape[2:], mode="bilinear", align_corners=False)
        return self.out(self.d1(torch.cat([b, a], 1)))


def load_pages(data: Path, names: list[str]) -> list[dict]:
    pages = []
    for n in names:
        d = np.load(data / f"{n}.npz")
        imgs = [np.where(m[..., None], s, 0).astype(np.uint8) for s, m in zip(d["small"], d["masks"])]
        labs = [clean_labels(lab, m) for lab, m in zip(overlap_labels(list(d["toned"]), list(d["masks"])), d["masks"])]
        small = [shrink(i, lab, NET_SCALE) for i, lab in zip(imgs, labs)]
        pages.append(dict(name=n, imgs=imgs, toned=list(d["toned"]), labels=labs, masks=list(d["masks"]), small=small))
    return pages


def shrink(img: np.ndarray, lab: np.ndarray, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """A shot and its labels at the scale the network runs at.

    A shrunk pixel is glare where at least half of it was, clean where at
    least three quarters of it was, and unknown otherwise.
    """
    h, w = lab.shape
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    g = cv2.resize((lab == LABEL_GLARE).astype(np.float32), size, interpolation=cv2.INTER_AREA)
    c = cv2.resize((lab == LABEL_CLEAN).astype(np.float32), size, interpolation=cv2.INTER_AREA)
    out = np.full(g.shape, LABEL_UNKNOWN, np.uint8)
    out[c >= 0.75] = LABEL_CLEAN
    out[g >= 0.5] = LABEL_GLARE
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA), out


def to_input(img: np.ndarray) -> np.ndarray:
    """BGR uint8 to the network's input: NCHW float32 in -0.5..0.5."""
    return (img.astype(np.float32).transpose(2, 0, 1)[None] / 255.0 - 0.5).astype(np.float32)


def _batch(shots, rng, bs=16, cs=128):
    xs, ys = [], []
    while len(xs) < bs:
        img, lab = shots[rng.integers(len(shots))]
        h, w = lab.shape
        y0, x0 = rng.integers(0, h - cs + 1), rng.integers(0, w - cs + 1)
        b = lab[y0 : y0 + cs, x0 : x0 + cs]
        if (b != LABEL_UNKNOWN).mean() < 0.1:
            continue
        a = img[y0 : y0 + cs, x0 : x0 + cs].astype(np.float32)
        inside = a.sum(axis=2, keepdims=True) > 0
        a = np.where(inside, np.clip(a * rng.uniform(0.85, 1.12) * rng.uniform(0.95, 1.05, 3), 0, 255), 0)
        r = int(rng.integers(4))
        a, b = np.rot90(a, r), np.rot90(b, r)
        if rng.random() < 0.5:
            a, b = a[:, ::-1], b[:, ::-1]
        xs.append(a.transpose(2, 0, 1) / 255.0 - 0.5)
        ys.append(b)
    return torch.tensor(np.array(xs), dtype=torch.float32), torch.tensor(np.array(ys).astype(np.int64))


def train(net: Net, pages: list[dict], its: int, lr: float, clean_weight: float, seed: int = 0, log=print) -> Net:
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    shots = [(i, lab) for p in pages for i, lab in p["small"] if min(lab.shape) >= 128]
    opt = torch.optim.AdamW(net.parameters(), lr / 4, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.OneCycleLR(opt, lr, total_steps=its)
    net.train()
    run, t = 0.0, time.time()
    for i in range(its):
        x, y = _batch(shots, rng)
        out = net(x)[:, 0]
        valid = (y != LABEL_UNKNOWN).float()
        tgt = (y == LABEL_GLARE).float()
        w = valid * torch.where(tgt > 0, 1.0, clean_weight)
        loss = (F.binary_cross_entropy_with_logits(out, tgt, reduction="none") * w).sum() / w.sum().clamp(min=1)
        opt.zero_grad()
        loss.backward()
        opt.step()
        sch.step()
        run = 0.98 * run + 0.02 * loss.item()
        if i % 200 == 0:
            log(f"{i} loss {run:.4f} {time.time() - t:.0f}s")
    net.eval()
    return net


def predict(net: Net, img: np.ndarray) -> np.ndarray:
    """Glare probability of a grid-sized shot, run at ``NET_SCALE`` as the package runs it."""
    h, w = img.shape[:2]
    s = cv2.resize(img, (max(1, round(w * NET_SCALE)), max(1, round(h * NET_SCALE))), interpolation=cv2.INTER_AREA)
    with torch.no_grad():
        p = torch.sigmoid(net(torch.from_numpy(to_input(s))))[0, 0].numpy()
    return cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR)


def score_pages(net: Net | None, pages: list[dict]) -> list[dict]:
    """Each shot's glare score: the net's, or with ``net`` None the package's hand-made features' (``GlareParams`` defaults)."""
    for p in pages:
        if net is None:
            p["probs"] = [glare_map(glare_features(t, m, s), m).score for t, m, s in zip(p["toned"], p["masks"], p["imgs"])]
        else:
            p["probs"] = [np.where(m, predict(net, i), 0).astype(np.float32) for i, m in zip(p["imgs"], p["masks"])]
    return pages


def rates(pages: list[dict], seed: float, weak: float, min_seed_px: int) -> tuple[float, float, list]:
    """Pooled recall and false-alarm rate, and the same per page."""
    tp = gl = fp = cl = 0
    per = []
    for p in pages:
        a = b = c = d = 0
        for prob, lab in zip(p["probs"], p["labels"]):
            det = hysteresis(prob, seed, weak, min_seed_px)
            g, k = lab == LABEL_GLARE, lab == LABEL_CLEAN
            a += int((det & g).sum()); b += int(g.sum()); c += int((det & k).sum()); d += int(k.sum())  # noqa: E702
        per.append((p["name"], a / max(b, 1), c / max(d, 1), b / max(b + d, 1)))
        tp += a; gl += b; fp += c; cl += d  # noqa: E702
    return tp / max(gl, 1), fp / max(cl, 1), per


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("data", type=Path)
    t.add_argument("--init", type=Path)
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--its", type=int, default=3000)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--clean-weight", type=float, default=10.0)
    t.add_argument("--test", nargs="*", default=DEFAULT_TEST)
    t.add_argument("--val", nargs="*", default=DEFAULT_VAL)
    t.add_argument("--all", action="store_true", help="train on every page except --test (validation pages too)")
    e = sub.add_parser("eval")
    e.add_argument("data", type=Path)
    e.add_argument("--model", type=Path, required=True, help="a .pt file, or 'features' for the hand-made detector")
    e.add_argument("--pages", nargs="*", default=DEFAULT_VAL)
    e.add_argument("--th", nargs="*", default=["0.7,0.4,3"], help="seed,weak,min_seed_px")
    x = sub.add_parser("export")
    x.add_argument("model", type=Path)
    x.add_argument("onnx", type=Path)
    a = ap.parse_args()
    torch.set_num_threads(max(1, (torch.get_num_threads())))

    if a.cmd == "train":
        names = sorted(f.stem for f in a.data.glob("*.npz"))
        held = set(a.test) | (set() if a.all else set(a.val))
        tr = [n for n in names if n not in held]
        print("training on", tr, flush=True)
        net = Net()
        if a.init:
            net.load_state_dict(torch.load(a.init))
        train(net, load_pages(a.data, tr), a.its, a.lr, a.clean_weight, log=lambda s: print(s, flush=True))
        torch.save(net.state_dict(), a.out)
    elif a.cmd == "eval":
        net = None
        if str(a.model) != "features":
            net = Net()
            net.load_state_dict(torch.load(a.model))
            net.eval()
        pages = score_pages(net, load_pages(a.data, a.pages))
        for th in a.th:
            s, w, m = th.split(",")
            rec, fa, per = rates(pages, float(s), float(w), int(m))
            print(f"seed {s} weak {w} min {m}: recall {rec:.3f} false alarms {fa:.4f}")
            for n, r, f, share in per:
                print(f"    {n:15s} recall {r:.3f} false alarms {f:.4f} glare share {share:.3f}")
    else:
        net = Net()
        net.load_state_dict(torch.load(a.model))
        net.eval()
        a.onnx.parent.mkdir(parents=True, exist_ok=True)
        torch.onnx.export(
            net, torch.zeros(1, 3, 256, 256), str(a.onnx), input_names=["image"], output_names=["logit"],
            dynamic_axes={"image": {2: "h", 3: "w"}, "logit": {2: "h", 3: "w"}}, opset_version=17, dynamo=False,
        )
        print("wrote", a.onnx, a.onnx.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
