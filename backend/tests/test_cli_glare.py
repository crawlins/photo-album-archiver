import json

import cv2
import numpy as np
import pytest

from albumproc.cli import main
from albumproc.evaluate import residual_truth
from albumproc.synth import make_case


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """A stitched page with sleeve glare, processed once through the command line."""
    d = tmp_path_factory.mktemp("page")
    case = make_case(40, "stitch", 2, glare_style="sleeve")
    shots = []
    for i, img in enumerate(case.images):
        shots.append(d / f"shot_{i}.jpg")
        cv2.imwrite(str(shots[-1]), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    out = d / "out" / "page.png"
    assert main(["page", *map(str, shots), "-o", str(out), "--masks", "--debug", str(d / "debug")]) == 0
    return case, shots, out, d


def test_page_writes_metadata_next_to_the_image(page, capsys):
    _, _, out, d = page
    meta = json.loads(out.with_suffix(".json").read_text())
    assert meta["schema_version"] == 2
    assert {"glare", "uncovered", "warnings", "detector", "coverage", "glare_fraction", "inputs"} <= set(meta)
    assert all(isinstance(k, str) for k in meta["glare"]["per_shot"])
    assert not list(out.parent.glob("*.tmp"))
    assert sorted(p.name for p in (d / "debug").glob("glare_*.png")) == ["glare_00.png", "glare_01.png"]
    assert (d / "debug" / "quality_overlay.jpg").exists()


def test_masks_are_page_sized(page):
    _, _, out, _ = page
    img = cv2.imread(str(out))
    glare = cv2.imread(str(out.with_suffix(".glare.png")), cv2.IMREAD_GRAYSCALE)
    unc = cv2.imread(str(out.with_suffix(".uncovered.png")), cv2.IMREAD_GRAYSCALE)
    assert glare.shape == unc.shape == img.shape[:2]
    assert glare.max() > 127  # the glare the metadata reports
    assert set(np.unique(unc)) <= {0, 255}


def test_report_path_and_stdout(page, tmp_path, capsys):
    _, shots, _, _ = page
    out, meta = tmp_path / "p.png", tmp_path / "meta" / "page-report.json"
    assert main(["page", *map(str, shots), "-o", str(out), "--report", str(meta), "--glare-threshold", "0.6", "--min-region", "0.001"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(meta.read_text()) == printed
    assert printed["detector"]["quality"] == {"residual_threshold": 0.6, "min_region_area": 0.001}
    assert not (tmp_path / "p.json").exists()
    assert not (tmp_path / "p.glare.png").exists()  # masks only when asked


def test_glare_command(page, tmp_path, capsys):
    _, shots, _, _ = page
    assert main(["glare", str(shots[0]), "--overlay", str(tmp_path)]) == 0
    (r,) = json.loads(capsys.readouterr().out)
    assert r["photo"] == str(shots[0])
    assert 0 <= r["glare_fraction"] < 1
    for g in r["regions"]:
        x0, y0, x1, y1 = g["bbox_px"]
        assert 0 <= x0 < x1 <= 2000 and 0 <= y0 < y1 <= 1500  # photo pixels
    assert (tmp_path / f"{shots[0].stem}.glare.jpg").exists()


def test_glare_command_unreadable_photo(tmp_path, capsys):
    bad = tmp_path / "nope.jpg"
    bad.write_text("not a photo")
    assert main(["glare", str(bad)]) == 2
    assert "nope.jpg" in capsys.readouterr().err


def test_glare_eval_scores_against_a_drawn_mask(page, tmp_path, capsys):
    case, _, out, _ = page
    truth = residual_truth(cv2.imread(str(out)), case.page)
    mask = tmp_path / "truth.png"
    small = cv2.resize(truth.astype(np.uint8) * 255, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(mask), small)  # drawn at a different size
    assert main(["glare-eval", str(out.with_suffix(".json")), "--truth", str(mask)]) == 0
    captured = capsys.readouterr()
    scores = json.loads(captured.out)
    assert set(scores) == {"recall", "false_positive_share", "iou"}
    assert scores["false_positive_share"] <= 0.002 and scores["recall"] > 0
    assert "resizing" in captured.err


def test_glare_eval_needs_the_mask_image(tmp_path, capsys):
    meta = tmp_path / "x.json"
    meta.write_text("{}")
    cv2.imwrite(str(tmp_path / "t.png"), np.zeros((4, 4), np.uint8))
    assert main(["glare-eval", str(meta), "--truth", str(tmp_path / "t.png")]) == 2
    assert "--masks" in capsys.readouterr().err
