"""The overlap-label tool's album mode and shares (glare-model-round-2 spec, Requirement 1)."""

import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from real_glare_data import LABEL_CLEAN, LABEL_GLARE, LABEL_UNKNOWN, album_pages, label_shares  # noqa: E402

TOOL = Path(__file__).resolve().parents[1] / "tools" / "real_glare_data.py"


def test_album_pages_are_named_by_the_report_s_page_number(tmp_path):
    album = tmp_path / "album"
    for pid in ("b-second", "a-first", "c-unlisted"):
        (album / "photos" / pid).mkdir(parents=True)
    (album / "report.json").write_text(json.dumps({"pages": [{"id": "a-first", "number": 1}, {"id": "b-second", "number": 2}, {"id": "gone", "number": 3}]}))
    assert album_pages(album) == [("page-01", album / "photos" / "a-first"), ("page-02", album / "photos" / "b-second"), ("c-unlisted", album / "photos" / "c-unlisted")]
    (album / "report.json").unlink()
    assert [n for n, _ in album_pages(album)] == ["a-first", "b-second", "c-unlisted"]


def test_label_shares_sum_to_one():
    lab = np.full((2, 10, 10), LABEL_UNKNOWN, np.uint8)
    lab[0, :5] = LABEL_GLARE
    lab[1, :, :3] = LABEL_CLEAN
    s = label_shares(lab)
    assert s == {"shots": 2, "glare": 0.25, "clean": 0.15, "unlabelled": 0.6}
    assert s["glare"] + s["clean"] + s["unlabelled"] == pytest.approx(1.0)


@pytest.mark.slow
def test_album_mode_labels_each_page_and_writes_the_shares(tmp_path):
    from albumproc.synth import make_case

    album = tmp_path / "album"
    case = make_case(3, "glare", 2)
    (album / "photos" / "p1").mkdir(parents=True)
    for k, img in enumerate(case.images):
        cv2.imwrite(str(album / "photos" / "p1" / f"shot_{k}.jpg"), img)
    (album / "report.json").write_text(json.dumps({"pages": [{"id": "p1", "number": 7}]}))
    out = tmp_path / "labels"
    r = subprocess.run([sys.executable, str(TOOL), "--album", str(album), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "page-07.npz").is_file()
    shares = json.loads((out / "labels.json").read_text())
    assert set(shares) == {"page-07"} and shares["page-07"]["shots"] == 2
    assert shares["page-07"]["clean"] > 0.1
    # A second run reuses the cached page and keeps its shares.
    r = subprocess.run([sys.executable, str(TOOL), "--album", str(album), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and "cached" in r.stdout
    assert json.loads((out / "labels.json").read_text()) == shares
