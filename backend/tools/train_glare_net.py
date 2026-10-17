"""Train the learned single-shot glare detector on real pages' overlap labels.

Not installed with the package, and the only part of the project that needs
PyTorch (``pip install torch onnx``); the package runs the exported model
through OpenCV. Run from ``backend/`` on the output of
``tools/real_glare_data.py``:

    python tools/train_glare_net.py train DATA --init net5.pt --out glare_net.pt
    python tools/train_glare_net.py eval DATA --model glare_net.pt
    python tools/train_glare_net.py export glare_net.pt src/albumproc/models/glare_net.onnx
    python tools/train_glare_net.py synth OUT --seeds 300 320      # synthetic negatives

``DATA`` is a folder of ``<page>.npz`` files, or a folder of album folders
of them (``DATA/<album>/<page>.npz``); pages are then named
``<album>/<page>``. Pages are held out by name (``--test``, ``--val``) or
by album (``--holdout-album``, ``--val-album``), never by shot, so that the
reported numbers come from pages the model never saw. Training draws random
crops of each shot as the pipeline sees it (warped into the page frame on
the fusion's grid, zero outside the shot), with flips, quarter turns and
small exposure and colour changes. Clean pixels are weighted up
(``--clean-weight``): a warning about glare that is not there sends the user
back to re-shoot a good page.

Two models (glare-model-round-2 spec, Requirement 3):

- ``Net`` (round 1): a local encoder-decoder on the BGR shot; each output
  sees about a fifth of the page.
- ``Net2`` (round 2, ``--arch net2``): the same local branch on four
  channels (BGR plus the shot's low-frequency brightness, its lighting)
  and a whole-page branch on the shot shrunk by four, fused at the
  decoder's coarsest level, so that a veil over half the page can be told
  from the page being lit from one side. It is exported with two inputs,
  ``image`` and ``page``.

The evaluation scores the detector as the pipeline runs it (score, then
hysteresis from ``GlareParams``' seed and weak thresholds) against the overlap
labels: recall is the share of glare-labelled pixels detected, false alarms
the share of clean-labelled pixels detected; pooled, per page, and by the
kind of glare a page has (``veiled``: one connected glare area over a tenth
of the page; ``clipped``: the rest).

Results (2026-10-08), the shipped ``models/glare_net.onnx`` (round 1,
``Net``): fine-tuned from the synthetic-page model (net5 in the project's
glare-research folder) for 4000 steps, clean weight 10, on 15 real pages
(``glare-01`` to ``-15``); thresholds 0.9/0.5/3 chosen on a run that held
``glare-13`` to ``-15`` out. On the 9 held-out pages (``glare-16`` to ``-19``
and the five 2-shot pages): recall 0.096, false alarms 0.0001; the hand-made
``features`` detector with its defaults: recall 0.002, false alarms 0.0000.
Recall varies widely by page (0.62 on ``glare-19``, 0.02 on ``glare-18``,
whose glare is a veil over a third of the page). Round 2's numbers go here
when its album has been shot.
"""

from __future__ import annotations

import argparse
import json
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

from albumproc.glare import glare_features, glare_map, hysteresis, net_input  # noqa: E402
from real_glare_data import LABEL_CLEAN, LABEL_GLARE, LABEL_UNKNOWN, clean_labels, label_shares, overlap_labels, page_data  # noqa: E402

NET_SCALE = 0.5  # the network runs on the fusion grid shrunk by this
PAGE_SCALE = 0.25  # the whole-page branch sees the net's input shrunk by this again
LOWPASS_FRAC = 0.1  # sigma of the lighting channel's blur, as a fraction of the long side
VEIL_SHARE = 0.10  # a page is "veiled" when one connected glare area covers this much of a shot
DEFAULT_TEST = ["glare-16", "glare-17", "glare-18", "glare-19"]
DEFAULT_VAL = ["glare-13", "glare-14", "glare-15"]


def _cbr(i, o, s=1, d=1):
    return nn.Sequential(nn.Conv2d(i, o, 3, s, padding=d, dilation=d), nn.ReLU(inplace=True))


class Net(nn.Module):
    """Small encoder-decoder (88k parameters) with a wide dilated middle, so
    that each output sees about a fifth of the page around it."""

    def __init__(self, c: int = 16, channels: int = 3):
        super().__init__()
        self.channels = channels
        self.e1 = nn.Sequential(_cbr(channels, c), _cbr(c, c))
        self.e2 = nn.Sequential(_cbr(c, 2 * c, 2), _cbr(2 * c, 2 * c))
        self.e3 = nn.Sequential(_cbr(2 * c, 2 * c, 2), _cbr(2 * c, 2 * c, 1, 2), _cbr(2 * c, 2 * c, 1, 4), _cbr(2 * c, 2 * c, 1, 8), _cbr(2 * c, 2 * c, 1, 16))
        self.d2 = _cbr(4 * c, 2 * c)
        self.d1 = _cbr(3 * c, c)
        self.out = nn.Conv2d(c, 1, 1)

    def forward(self, x):
        a = self.e1(x)
        b = self.e2(a)
        c = self.e3(b)
        return self.decode(a, b, c)

    def decode(self, a, b, c):
        c = F.interpolate(c, size=b.shape[2:], mode="bilinear", align_corners=False)
        b = self.d2(torch.cat([c, b], 1))
        b = F.interpolate(b, size=a.shape[2:], mode="bilinear", align_corners=False)
        return self.out(self.d1(torch.cat([b, a], 1)))


class Net2(Net):
    """``Net`` on four channels plus a whole-page branch (glare-model-round-2 spec, 3.1 and 3.2).

    The page branch sees the shot shrunk by ``PAGE_SCALE`` (about 125 px
    across on the fusion grid), through three strided convolutions, and its
    features are upsampled to the local branch's coarsest level and
    concatenated there, so every output pixel knows where it sits on the
    page and how the page as a whole is lit.
    """

    def __init__(self, c: int = 16):
        super().__init__(c, channels=4)
        self.g = nn.Sequential(_cbr(4, c, 2), _cbr(c, 2 * c, 2), _cbr(2 * c, 2 * c, 2), _cbr(2 * c, 2 * c))
        self.fuse = _cbr(4 * c, 2 * c)

    def forward(self, x, page):  # noqa: D102
        a = self.e1(x)
        b = self.e2(a)
        c = self.e3(b)
        g = F.interpolate(self.g(page), size=c.shape[2:], mode="bilinear", align_corners=False)
        c = self.fuse(torch.cat([c, g], 1))
        return self.decode(a, b, c)


def make_net(arch: str) -> Net:
    return Net2() if arch == "net2" else Net()


def arch_of(net: Net) -> str:
    return "net2" if isinstance(net, Net2) else "net"


def load_net(path: Path) -> Net:
    """A saved model; ``Net2`` when its state has the page branch."""
    state = torch.load(path)
    net = Net2() if any(k.startswith("g.") for k in state) else Net()
    net.load_state_dict(state)
    net.eval()
    return net


# ---- data ---------------------------------------------------------------


def page_names(data: Path) -> list[str]:
    """Every page under ``data``: ``<page>`` for a flat folder, ``<album>/<page>`` for album folders."""
    names = sorted(f.stem for f in data.glob("*.npz"))
    for album in sorted(d for d in data.iterdir() if d.is_dir()):
        names += sorted(f"{album.name}/{f.stem}" for f in album.glob("*.npz"))
    return names


def album_of(name: str) -> str | None:
    return name.split("/", 1)[0] if "/" in name else None


def load_pages(data: Path, names: list[str]) -> list[dict]:
    pages = []
    for n in names:
        d = np.load(data / f"{n}.npz")
        imgs = [np.where(m[..., None], s, 0).astype(np.uint8) for s, m in zip(d["small"], d["masks"])]
        labs = [clean_labels(lab, m) for lab, m in zip(overlap_labels(list(d["toned"]), list(d["masks"])), d["masks"])]
        small = [shrink(i, lab, NET_SCALE) for i, lab in zip(imgs, labs)]
        pages.append(dict(name=n, imgs=imgs, toned=list(d["toned"]), labels=labs, masks=list(d["masks"]), small=small, veiled=is_veiled(labs)))
    return pages


def is_veiled(labels: list[np.ndarray]) -> bool:
    """True when one connected glare area of some shot covers more than ``VEIL_SHARE`` of the page (Requirement 4.1)."""
    for lab in labels:
        n, _, stats, _ = cv2.connectedComponentsWithStats((lab == LABEL_GLARE).astype(np.uint8), connectivity=8)
        if n > 1 and stats[1:, cv2.CC_STAT_AREA].max() > VEIL_SHARE * lab.size:
            return True
    return False


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


def to_input(img: np.ndarray, channels: int = 3) -> np.ndarray:
    """BGR uint8 to the network's input: NCHW float32 in -0.5..0.5, with the lighting channel for ``Net2``."""
    return net_input(img, channels, LOWPASS_FRAC)


def page_input(small: np.ndarray) -> np.ndarray:
    """The whole-page branch's input: the net's input shrunk by ``PAGE_SCALE``, four channels."""
    h, w = small.shape[:2]
    tiny = cv2.resize(small, (max(8, round(w * PAGE_SCALE)), max(8, round(h * PAGE_SCALE))), interpolation=cv2.INTER_AREA)
    return to_input(tiny, 4)


def _augment(a: np.ndarray, rng) -> np.ndarray:
    """A float BGR shot with exposure and colour changed a little, black kept black."""
    inside = a.sum(axis=2, keepdims=True) > 0
    return np.where(inside, np.clip(a * rng.uniform(0.85, 1.12) * rng.uniform(0.95, 1.05, 3), 0, 255), 0)


def _batch(shots, rng, arch: str, bs=16, cs=128):
    """Random crops with their labels; for ``net2`` the lighting channel and the whole page come with each crop."""
    xs, ys, gs = [], [], []
    while len(xs) < bs:
        img, lab = shots[rng.integers(len(shots))]
        h, w = lab.shape
        y0, x0 = rng.integers(0, h - cs + 1), rng.integers(0, w - cs + 1)
        b = lab[y0 : y0 + cs, x0 : x0 + cs]
        if (b != LABEL_UNKNOWN).mean() < 0.1:
            continue
        whole = _augment(img.astype(np.float32), rng).astype(np.uint8)
        r = int(rng.integers(4))
        flip = rng.random() < 0.5
        if arch == "net2":
            full = to_input(whole, 4)[0]  # the lighting channel is computed on the whole shot, then cropped
            a = full[:, y0 : y0 + cs, x0 : x0 + cs]
            g = page_input(whole)[0]
            a, g = np.rot90(a, r, axes=(1, 2)), np.rot90(g, r, axes=(1, 2))
            if flip:
                a, g = a[:, :, ::-1], g[:, :, ::-1]
            gs.append(np.ascontiguousarray(g))
        else:
            a = to_input(whole[y0 : y0 + cs, x0 : x0 + cs], 3)[0]
            a = np.rot90(a, r, axes=(1, 2))
            if flip:
                a = a[:, :, ::-1]
        b = np.rot90(b, r)
        if flip:
            b = b[:, ::-1]
        xs.append(np.ascontiguousarray(a))
        ys.append(b.copy())
    x = torch.tensor(np.array(xs), dtype=torch.float32)
    y = torch.tensor(np.array(ys).astype(np.int64))
    if arch == "net2":
        # Pages differ in size: pad the whole-page inputs to the largest in the batch.
        gh, gw = max(g.shape[1] for g in gs), max(g.shape[2] for g in gs)
        padded = np.full((len(gs), 4, gh, gw), -0.5, np.float32)
        for k, g in enumerate(gs):
            padded[k, :, : g.shape[1], : g.shape[2]] = g
        return x, y, torch.tensor(padded)
    return x, y, None


def train(net: Net, pages: list[dict], its: int, lr: float, clean_weight: float, seed: int = 0, log=print) -> Net:
    arch = arch_of(net)
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    shots = [(i, lab) for p in pages for i, lab in p["small"] if min(lab.shape) >= 128]
    opt = torch.optim.AdamW(net.parameters(), lr / 4, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.OneCycleLR(opt, lr, total_steps=its)
    net.train()
    run, t = 0.0, time.time()
    for i in range(its):
        x, y, g = _batch(shots, rng, arch)
        out = (net(x, g) if arch == "net2" else net(x))[:, 0]
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
        if arch_of(net) == "net2":
            p = torch.sigmoid(net(torch.from_numpy(to_input(s, 4)), torch.from_numpy(page_input(s))))[0, 0].numpy()
        else:
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


def rates(pages: list[dict], seed: float, weak: float, min_seed_px: int) -> tuple[float, float, list, dict]:
    """Pooled recall and false-alarm rate, the same per page, and pooled by the page's kind of glare."""
    tp = gl = fp = cl = 0
    per = []
    kinds: dict[str, list[int]] = {"veiled": [0, 0, 0, 0], "clipped": [0, 0, 0, 0]}
    for p in pages:
        a = b = c = d = 0
        for prob, lab in zip(p["probs"], p["labels"]):
            det = hysteresis(prob, seed, weak, min_seed_px)
            g, k = lab == LABEL_GLARE, lab == LABEL_CLEAN
            a += int((det & g).sum()); b += int(g.sum()); c += int((det & k).sum()); d += int(k.sum())  # noqa: E702
        kind_name = "veiled" if p.get("veiled") else "clipped"
        per.append((p["name"], a / max(b, 1), c / max(d, 1), b / max(b + d, 1), kind_name))
        kind = kinds[kind_name]
        kind[0] += a; kind[1] += b; kind[2] += c; kind[3] += d  # noqa: E702
        tp += a; gl += b; fp += c; cl += d  # noqa: E702
    by_kind = {k: (v[0] / max(v[1], 1), v[2] / max(v[3], 1), sum(1 for x in per if x[4] == k)) for k, v in kinds.items()}
    return tp / max(gl, 1), fp / max(cl, 1), per, by_kind


def export(net: Net, onnx: Path) -> None:
    onnx.parent.mkdir(parents=True, exist_ok=True)
    if arch_of(net) == "net2":
        torch.onnx.export(
            net, (torch.zeros(1, 4, 256, 256), torch.zeros(1, 4, 64, 64)), str(onnx), input_names=["image", "page"], output_names=["logit"],
            dynamic_axes={"image": {2: "h", 3: "w"}, "page": {2: "ph", 3: "pw"}, "logit": {2: "h", 3: "w"}}, opset_version=17, dynamo=False,
        )
    else:
        torch.onnx.export(
            net, torch.zeros(1, 3, 256, 256), str(onnx), input_names=["image"], output_names=["logit"],
            dynamic_axes={"image": {2: "h", 3: "w"}, "logit": {2: "h", 3: "w"}}, opset_version=17, dynamo=False,
        )


def synth_pages(out: Path, seeds: range, kinds: tuple[str, ...] = ("clean_white", "glare")) -> None:
    """Synthetic pages as the pipeline sees them, labelled by overlap like real ones (Requirement 3.3).

    ``clean_white`` pages are the hard negatives: white paper and print
    borders with no glare. ``glare`` pages add clipped spots whose labels
    come from the comparison, as on real pages.
    """
    from albumproc.synth import make_case

    out.mkdir(parents=True, exist_ok=True)
    shares = {}
    for kind in kinds:
        for seed in seeds:
            name = f"{kind}-{seed}"
            dst = out / f"{name}.npz"
            if not dst.exists():
                data = page_data(make_case(seed, kind, 3).images)
                np.savez_compressed(dst, **data)
            shares[name] = label_shares(np.load(dst)["labels"])
            print(name, shares[name], flush=True)
    (out / "labels.json").write_text(json.dumps(shares, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("data", type=Path)
    t.add_argument("--arch", choices=["net", "net2"], default="net")
    t.add_argument("--init", type=Path, help="a saved model of the same architecture to start from")
    t.add_argument("--out", type=Path, required=True)
    t.add_argument("--its", type=int, default=3000)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--clean-weight", type=float, default=10.0)
    t.add_argument("--test", nargs="*", default=DEFAULT_TEST, help="page names held out")
    t.add_argument("--val", nargs="*", default=DEFAULT_VAL, help="page names held out for choosing thresholds")
    t.add_argument("--holdout-album", nargs="*", default=[], help="albums (folders under DATA) held out")
    t.add_argument("--val-album", nargs="*", default=[], help="albums held out for choosing thresholds")
    t.add_argument("--extra", nargs="*", type=Path, default=[], help="more folders of pages to train on, e.g. synthetic negatives")
    t.add_argument("--all", action="store_true", help="train on every page except the test ones (validation pages too)")
    e = sub.add_parser("eval")
    e.add_argument("data", type=Path)
    e.add_argument("--model", type=Path, required=True, help="a .pt file, or 'features' for the hand-made detector")
    e.add_argument("--pages", nargs="*", default=None, help="page names (default: the validation pages)")
    e.add_argument("--album", nargs="*", default=[], help="every page of these albums")
    e.add_argument("--th", nargs="*", default=["0.7,0.4,3"], help="seed,weak,min_seed_px")
    x = sub.add_parser("export")
    x.add_argument("model", type=Path)
    x.add_argument("onnx", type=Path)
    sy = sub.add_parser("synth")
    sy.add_argument("out", type=Path)
    sy.add_argument("--seeds", nargs=2, type=int, default=[300, 320], metavar=("FIRST", "LAST"))
    a = ap.parse_args()
    torch.set_num_threads(max(1, (torch.get_num_threads())))

    if a.cmd == "train":
        names = page_names(a.data)
        held = set(a.test) | (set() if a.all else set(a.val))
        held_albums = set(a.holdout_album) | (set() if a.all else set(a.val_album))
        tr = [n for n in names if n not in held and album_of(n) not in held_albums]
        pages = load_pages(a.data, tr)
        for extra in a.extra:
            pages += load_pages(extra, page_names(extra))
        print("training on", len(pages), "pages:", tr, [str(x) for x in a.extra], flush=True)
        net = load_net(a.init) if a.init else make_net(a.arch)
        if arch_of(net) != a.arch:
            raise SystemExit(f"--init is a {arch_of(net)} model, --arch says {a.arch}")
        train(net, pages, a.its, a.lr, a.clean_weight, log=lambda s: print(s, flush=True))
        torch.save(net.state_dict(), a.out)
    elif a.cmd == "eval":
        net = None if str(a.model) == "features" else load_net(a.model)
        names = list(a.pages) if a.pages is not None else []
        names += [n for n in page_names(a.data) if album_of(n) in set(a.album)]
        if not names and a.pages is None:
            names = DEFAULT_VAL
        pages = score_pages(net, load_pages(a.data, names))
        for th in a.th:
            s, w, m = th.split(",")
            rec, fa, per, by_kind = rates(pages, float(s), float(w), int(m))
            print(f"seed {s} weak {w} min {m}: recall {rec:.3f} false alarms {fa:.4f}")
            for kind, (r, f, n) in by_kind.items():
                if n:
                    print(f"  {kind:8s} ({n} pages): recall {r:.3f} false alarms {f:.4f}")
            for n, r, f, share, kind in per:
                print(f"    {n:24s} {kind:8s} recall {r:.3f} false alarms {f:.4f} glare share {share:.3f}")
    elif a.cmd == "export":
        net = load_net(a.model)
        export(net, a.onnx)
        print("wrote", a.onnx, a.onnx.stat().st_size, "bytes", arch_of(net))
    else:
        synth_pages(a.out, range(a.seeds[0], a.seeds[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
