"""Scan mode: pages whose photos are all flatbed scans marked by ``albumscan``."""

import json

import cv2
import numpy as np
import pytest
from PIL import Image

from albumproc.album import AlbumOptions, process_album
from albumproc.cli import main
from albumproc.pipeline import PageOptions, process_page
from albumproc.printimage import PrintOptions
from albumproc.printsize import PageSize
from albumproc.scanmark import all_scans, is_scan, page_source, scan_dpi
from albumproc.synth import make_page, make_shots, make_table

PW, PH = 1000, 760  # the synthetic page, in scan pixels


def _glass(rng, w, h):
    """What a scanner sees around a page with its lid open: dark, a little noisy."""
    img = np.full((h, w, 3), 38, np.float32) + rng.normal(0, 4, (h, w, 1))
    return img.clip(0, 255).astype(np.uint8)


def _save(path, bgr, dpi=300, software="albumscan 0.1.0"):
    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    exif = Image.Exif()
    if software is not None:
        exif[0x0131] = software
    exif[0x011A] = float(dpi)
    exif[0x011B] = float(dpi)
    exif[0x0128] = 2
    im.save(path, "JPEG", quality=95, subsampling=0, exif=exif.tobytes())
    return path


def _scan_scene(seed, margin=150):
    rng = np.random.default_rng(seed)
    scene = _glass(rng, PW + 2 * margin, PH + 2 * margin)
    scene[margin : margin + PH, margin : margin + PW] = make_page(rng, PW, PH)
    return scene


def test_is_scan_reads_the_marker(tmp_path):
    img = np.full((40, 60, 3), 128, np.uint8)
    marked = _save(tmp_path / "a.jpg", img)
    other = _save(tmp_path / "b.jpg", img, software="GIMP 2.10")
    bare = _save(tmp_path / "c.jpg", img, software=None)
    cv2.imwrite(str(tmp_path / "d.jpg"), img)  # no EXIF at all
    (tmp_path / "e.jpg").write_bytes(b"not a jpeg")
    assert is_scan(marked)
    assert not any(is_scan(tmp_path / n) for n in ("b.jpg", "c.jpg", "d.jpg", "e.jpg", "missing.jpg"))
    assert all_scans([marked, marked]) and not all_scans([marked, other]) and not all_scans([])
    assert page_source([marked]) == ("scan", 300.0)
    assert page_source([marked, bare]) == ("photo", None)


def test_scan_dpi_is_common_or_none(tmp_path):
    img = np.full((40, 60, 3), 128, np.uint8)
    a = _save(tmp_path / "a.jpg", img, dpi=600)
    b = _save(tmp_path / "b.jpg", img, dpi=600)
    c = _save(tmp_path / "c.jpg", img, dpi=300)
    assert scan_dpi([a, b]) == 600.0
    assert scan_dpi([a, c]) is None


def test_a_scanned_page_keeps_its_proportions_and_skips_photo_checks():
    # A page much wider than tall on a square-on view: the side lengths are the answer.
    scene = _scan_scene(1)
    res = process_page([scene], PageOptions(source="scan", scan_dpi=300.0))
    r = res.report
    assert r.source == "scan" and r.scan_dpi == 300.0
    assert r.aspect == pytest.approx(PW / PH, rel=0.01)
    w, h = r.size
    assert w / h == pytest.approx(PW / PH, rel=0.01)
    assert r.curvature["mode"] == "off"
    assert r.glare["regions"] == [] and r.shadow["regions"] == []
    assert r.single_coverage == {"fraction": 0.0, "regions": []}
    assert not res.single_coverage.any()
    codes = {w["code"] for w in r.warnings}
    assert not codes & {"glare", "single_coverage", "shadow", "curvature_uncorrected"}
    assert r.detector["glare"]["enabled"] is False and r.detector["shadow"]["enabled"] is False


def test_a_photo_page_still_looks_for_glare():
    scene = _scan_scene(1)
    r = process_page([scene], PageOptions()).report
    assert r.source == "photo" and r.scan_dpi is None
    assert r.detector["glare"]["enabled"] is True
    assert r.single_coverage["fraction"] > 0  # one shot covers it all


def test_two_overlapping_scans_are_stitched():
    # An oversized page scanned in two parts, the second shifted and turned a little on the glass.
    scene = _scan_scene(2)
    h, w = scene.shape[:2]
    left = scene[:, : int(w * 0.62)].copy()
    M = cv2.getRotationMatrix2D((w / 2, h / 2), 1.5, 1.0)
    turned = cv2.warpAffine(scene, M, (w, h), borderValue=(38, 38, 38))
    right = turned[:, int(w * 0.38) :].copy()
    res = process_page([left, right], PageOptions(source="scan"))
    r = res.report
    assert sorted(r.used) == [0, 1] and r.dropped == []
    assert r.aspect == pytest.approx(PW / PH, rel=0.02)
    assert r.uncovered["fraction"] < 0.01


def test_unknown_source_is_refused():
    with pytest.raises(ValueError):
        process_page([_scan_scene(1)], PageOptions(source="fax"))


def _write_mixed_album(root, seed=3):
    """Page 1: two marked scans of one page. Page 2: one scan and one phone photo of another."""
    (root / "p1").mkdir(parents=True)
    (root / "p2").mkdir()
    scene = _scan_scene(seed)
    _save(root / "p1" / "a.jpg", scene, dpi=600)
    _save(root / "p1" / "b.jpg", cv2.flip(cv2.flip(scene, 0), 0), dpi=600)
    rng = np.random.default_rng(seed)
    m = 600
    table = make_table(rng, PW + 2 * m, PH + 2 * m)
    table[m : m + PH, m : m + PW] = make_page(rng, PW, PH)
    shot = make_shots(rng, table, (m, m, PW, PH), [(0.5, 0.5)], fill=0.72, img_size=(900, 680))[0]
    cv2.imwrite(str(root / "p2" / "photo.jpg"), shot.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    _save(root / "p2" / "scan.jpg", scene)
    (root / "album.json").write_text(json.dumps({"title": "Scans", "page_size": "5x3.8in", "pages": ["p1", "p2"]}))


def test_album_step_picks_scan_mode_per_page(tmp_path):
    _write_mixed_album(tmp_path / "album")
    report = process_album(tmp_path / "album", tmp_path / "out", AlbumOptions(print=PrintOptions(dpi=150), page_size=PageSize(5, 3.8)))
    p1, p2 = report["pages"]
    assert p1["page_report"]["source"] == "scan" and p1["page_report"]["scan_dpi"] == 600.0
    assert p2["page_report"]["source"] == "photo"
    assert p1["page_report"]["schema_version"] == 6


def test_process_key_follows_the_source(tmp_path):
    album = tmp_path / "album"
    _write_mixed_album(album)
    opt = AlbumOptions(print=PrintOptions(dpi=150), page_size=PageSize(5, 3.8))
    first = process_album(album, tmp_path / "out", opt)
    # Re-saving page 1's scans without the marker makes them photos: it is processed again.
    for p in (album / "p1").glob("*.jpg"):
        _save(p, cv2.imread(str(p)), software="Camera")
    second = process_album(album, tmp_path / "out", opt)
    assert first["pages"][0]["process_key"] != second["pages"][0]["process_key"]
    assert second["pages"][0]["page_report"]["source"] == "photo"
    assert second["pages"][1]["status"] == "reused"


def test_cli_page_detects_scans_and_can_be_overridden(tmp_path, capsys):
    scan = _save(tmp_path / "scan.jpg", _scan_scene(4))
    assert main(["page", str(scan), "-o", str(tmp_path / "auto.png")]) == 0
    assert json.loads((tmp_path / "auto.json").read_text())["source"] == "scan"
    assert main(["page", str(scan), "-o", str(tmp_path / "forced.png"), "--source", "photo"]) == 0
    assert json.loads((tmp_path / "forced.json").read_text())["source"] == "photo"


def test_low_resolution_advice_suits_a_scan(tmp_path):
    (tmp_path / "album" / "p1").mkdir(parents=True)
    _save(tmp_path / "album" / "p1" / "a.jpg", _scan_scene(5), dpi=75)
    (tmp_path / "album" / "album.json").write_text(json.dumps({"title": "S", "page_size": "10x7.6in", "pages": ["p1"]}))
    report = process_album(tmp_path / "album", tmp_path / "out", AlbumOptions(print=PrintOptions(dpi=300)))
    low = [w for w in report["pages"][0]["warnings"] if w["code"] == "low_resolution"]
    assert len(low) == 1
    assert "scan at a higher resolution" in low[0]["message"] and "scanned at 75 dpi" in low[0]["message"]
    assert "shoot closer" not in low[0]["message"]


def test_large_scans_are_not_refused_as_decompression_bombs(tmp_path):
    # 200 million pixels, over Pillow's default refusal at about 179 million.
    from albumserver.thumbs import exif_taken, is_jpeg

    p = tmp_path / "big.jpg"
    exif = Image.Exif()
    exif[0x0131] = "albumscan 0.1.0"
    Image.new("L", (16000, 12500), 200).save(p, "JPEG", quality=50, exif=exif.tobytes())
    assert is_jpeg(p) and is_scan(p)
    assert exif_taken(p) is None
