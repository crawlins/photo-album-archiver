"""End to end: a small synthetic album through the page pipeline, print images and PDF."""

import json
import os
import shutil

import cv2
import numpy as np
import pikepdf
import pytest

from albumproc.album import AlbumOptions, PageFailed, process_album
from albumproc.cli import main
from albumproc.printimage import PrintOptions
from albumproc.printsize import PageSize
from albumproc.synth import make_page, make_shots, make_table

EPOCH = "1700000000"
SIZE = PageSize(5, 3.8)  # the synthetic pages are 1000 x 760


def _write_page(folder, seed):
    """Two small shots of a synthetic page, so each page takes a few seconds."""
    rng = np.random.default_rng(seed)
    pw, ph, m = 1000, 760, 600
    scene = make_table(rng, pw + 2 * m, ph + 2 * m)
    scene[m : m + ph, m : m + pw] = make_page(rng, pw, ph)
    shots = make_shots(rng, scene, (m, m, pw, ph), [(0.5, 0.5), (0.52, 0.48)], fill=0.72, img_size=(900, 680))
    folder.mkdir(parents=True)
    for i, s in enumerate(shots):
        cv2.imwrite(str(folder / f"shot_{i}.jpg"), s.image, [cv2.IMWRITE_JPEG_QUALITY, 92])


def _opt(**kw):
    return AlbumOptions(print=PrintOptions(dpi=150), page_size=SIZE, **kw)


@pytest.fixture(scope="module")
def first_run(tmp_path_factory):
    album = tmp_path_factory.mktemp("album")
    for k in range(1, 4):
        _write_page(album / f"page-{k}", seed=100 + k)
    out = tmp_path_factory.mktemp("out")
    os.environ["SOURCE_DATE_EPOCH"] = EPOCH
    try:
        lines = []
        report = process_album(album, out, _opt(), progress=lines.append)
    finally:
        del os.environ["SOURCE_DATE_EPOCH"]
    return album, out, report, lines


@pytest.fixture
def rerun(first_run, tmp_path, monkeypatch):
    """A copy of the album and of the first run's outputs, to run again."""
    album, out, _, _ = first_run
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    shutil.copytree(album, tmp_path / "album")
    shutil.copytree(out, tmp_path / "out")
    return tmp_path / "album", tmp_path / "out"


def _statuses(report):
    return [p["status"] for p in report["pages"]]


def test_album_outputs(first_run):
    album, out, report, lines = first_run
    assert _statuses(report) == ["ok", "ok", "ok"]
    assert (report["pages_found"], report["pages_ok"], report["pages_failed"]) == (3, 3, 0)
    assert report["pdf"] == "album.pdf" and report["pdf_pages"] == 3
    assert report["title"] == album.name and report["dpi"] == 150
    assert json.loads((out / "report.json").read_text()) == report
    for k, p in enumerate(report["pages"], 1):
        assert p["name"] == f"page-{k}"
        assert p["photos"] == [f"page-{k}/shot_0.jpg", f"page-{k}/shot_1.jpg"]
        assert p["print"] == f"print/{k:03d}-page-{k}.jpg" and (out / p["print"]).exists()
        assert p["processed"] == f"pages/{k:03d}-page-{k}.png" and (out / p["processed"]).exists()
        assert p["size_px"] == [750, 570] and p["size_in"] == [5, 3.8] and p["fit"] == "stretch"
        assert p["page_report"]["coverage"] > 0.99
        assert 100 < p["capture_dpi"] < 150
        assert {w["code"] for w in p["warnings"]} == {"upsampled", "low_resolution"}
        assert p["process_key"].startswith("sha256:") and p["print_key"] != p["process_key"]
    with pikepdf.open(out / "album.pdf") as pdf:
        assert len(pdf.pages) == 3
        assert [round(float(v), 2) for v in pdf.pages[0].MediaBox] == [0, 0, 360, 273.6]
    assert len(lines) == 3 and lines[0].startswith("[1/3] page-1 ok")
    assert not list(out.rglob("*.tmp"))


def test_rerun_reuses_everything(rerun, first_run):
    album, out = rerun
    before = {p: p.stat().st_mtime_ns for p in (out / "print").iterdir()}
    report = process_album(album, out, _opt())
    assert _statuses(report) == ["reused"] * 3
    assert {p: p.stat().st_mtime_ns for p in (out / "print").iterdir()} == before
    assert report["pdf_pages"] == 3
    assert [p["warnings"] for p in report["pages"]] == [p["warnings"] for p in first_run[2]["pages"]]


def test_changed_photo_reprocesses_only_that_page(rerun):
    album, out = rerun
    shot = album / "page-2" / "shot_1.jpg"
    cv2.imwrite(str(shot), cv2.imread(str(shot)), [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert _statuses(process_album(album, out, _opt())) == ["reused", "ok", "reused"]


def test_force_reprocesses(rerun):
    album, out = rerun
    shutil.rmtree(album / "page-2")
    shutil.rmtree(album / "page-3")
    assert _statuses(process_album(album, out, _opt(force=True))) == ["ok"]


def test_print_option_change_reuses_processed_pages(rerun):
    album, out = rerun
    report = process_album(album, out, AlbumOptions(print=PrintOptions(dpi=100, bleed_in=0.1), page_size=SIZE))
    assert _statuses(report) == ["reused"] * 3
    assert [p["size_px"] for p in report["pages"]] == [[520, 400]] * 3
    with pikepdf.open(out / "album.pdf") as pdf:
        assert [round(float(v), 2) for v in pdf.pages[0].TrimBox] == [7.2, 7.2, 367.2, 280.8]


def test_manifest_overrides(rerun):
    album, out = rerun
    (album / "album.json").write_text(json.dumps({"title": "Test", "pages": [{"folder": "page-3", "rotate": 90, "name": "Back"}, "page-1"]}))
    report = process_album(album, out, _opt())
    assert [p["name"] for p in report["pages"]] == ["Back", "page-1"]
    assert report["pages"][0]["size_px"] == [570, 750]
    assert report["pages"][0]["print"] == "print/001-page-3.jpg"
    with pikepdf.open(out / "album.pdf") as pdf:
        assert str(pdf.docinfo["/Title"]) == "Test"
        assert [str(pdf.Root.PageLabels.Nums[k].P) for k in (1, 3)] == ["Back", "page-1"]


def _add_broken_page(album):
    (album / "page-4").mkdir()
    (album / "page-4" / "broken.jpg").write_bytes(b"not a jpeg")


def test_failed_page_is_skipped(rerun):
    album, out = rerun
    _add_broken_page(album)
    report = process_album(album, out, _opt())
    assert _statuses(report) == ["reused", "reused", "reused", "failed"]
    assert report["pages"][3]["error"] == "cannot read page-4/broken.jpg"
    assert report["pdf_pages"] == 3 and report["pages_failed"] == 1


def test_page_whose_processing_raises_is_failed_and_the_rest_carry_on(rerun, monkeypatch):
    import albumproc.album as album_mod

    album, out = rerun
    real, calls = album_mod.process_page, []

    def flaky(images, *a, **kw):
        calls.append(len(images))
        if len(calls) == 2:
            raise ValueError("no page outline found")
        return real(images, *a, **kw)

    monkeypatch.setattr(album_mod, "process_page", flaky)
    report = process_album(album, out, _opt(force=True))
    assert _statuses(report) == ["ok", "failed", "ok"]
    assert report["pages"][1]["error"] == "ValueError: no page outline found"
    assert report["pages_failed"] == 1 and report["pdf_pages"] == 2


def test_placeholder_keeps_page_count(rerun):
    album, out = rerun
    _add_broken_page(album)
    report = process_album(album, out, _opt(placeholder=True))
    assert report["pdf_pages"] == 4
    assert report["pages"][3]["placeholder"] == "print/004-page-4.jpg"
    with pikepdf.open(out / "album.pdf") as pdf:
        assert str(pdf.Root.PageLabels.Nums[7].P) == "page-4"


def test_strict_stops_and_writes_no_pdf(rerun):
    album, out = rerun
    (album / "page-1" / "shot_0.jpg").write_bytes(b"broken")
    with pytest.raises(PageFailed, match="page-1"):
        process_album(album, out, _opt(strict=True))
    assert not (out / "album.pdf").exists()
    assert _statuses(json.loads((out / "report.json").read_text())) == ["failed"]


def test_strict_in_parallel_reports_every_page_it_wrote(first_run, tmp_path, monkeypatch):
    album, _, _, _ = first_run
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    shutil.copytree(album, tmp_path / "album")
    (tmp_path / "album" / "page-1" / "shot_0.jpg").write_bytes(b"broken")
    out = tmp_path / "out"
    with pytest.raises(PageFailed, match="page-1"):
        process_album(tmp_path / "album", out, _opt(strict=True, jobs=2))
    assert not (out / "album.pdf").exists()
    report = json.loads((out / "report.json").read_text())
    assert report["pages"][0]["status"] == "failed"
    written = sorted(p.relative_to(out).as_posix() for p in (out / "print").glob("*")) if (out / "print").exists() else []
    assert written == sorted(e["print"] for e in report["pages"] if e["status"] != "failed")


def test_parallel_matches_serial(first_run, tmp_path, monkeypatch):
    album, out1, _, _ = first_run
    monkeypatch.setenv("SOURCE_DATE_EPOCH", EPOCH)
    out2 = tmp_path / "out"
    report = process_album(album, out2, _opt(jobs=2))
    assert _statuses(report) == ["ok"] * 3
    for name in ["album.pdf", "print/001-page-1.jpg", "print/003-page-3.jpg"]:
        assert (out2 / name).read_bytes() == (out1 / name).read_bytes(), name


def test_cli_album_exit_codes(rerun, tmp_path, capsys):
    album, out = rerun
    assert main(["album", str(album), "-o", str(out), "--page-size", "5x3.8in", "--dpi", "150"]) == 0
    assert "3 of 3 pages done" in capsys.readouterr().out
    _add_broken_page(album)
    assert main(["album", str(album), "-o", str(out), "--page-size", "5x3.8in", "--dpi", "150"]) == 3
    (tmp_path / "empty").mkdir()
    assert main(["album", str(tmp_path / "empty"), "-o", str(out), "--page-size", "a4"]) == 2
    with pytest.raises(SystemExit):
        main(["album", str(album), "-o", str(out), "--page-size", "huge"])


def test_cli_page_output_is_the_page_pipelines(first_run, tmp_path, capsys):
    from albumproc.pipeline import PageOptions, process_page

    album, _, _, _ = first_run
    photos = sorted((album / "page-1").glob("*.jpg"))
    dst, rep = tmp_path / "page.png", tmp_path / "page.json"
    assert main(["page", *map(str, photos), "-o", str(dst), "--report", str(rep)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert json.loads(rep.read_text()) == printed
    assert printed["inputs"] == [str(p) for p in photos]
    expected = process_page([cv2.imread(str(p), cv2.IMREAD_COLOR) for p in photos], PageOptions(max_side=8000, focal_35mm=26.0))
    assert np.array_equal(cv2.imread(str(dst), cv2.IMREAD_COLOR), expected.image)
    assert {k: v for k, v in printed.items() if k != "inputs"} == json.loads(json.dumps(expected.report.to_dict()))


def test_cli_print(first_run, tmp_path, capsys):
    _, out, _, _ = first_run
    dst = tmp_path / "p.png"
    assert main(["print", str(out / "pages/001-page-1.png"), "-o", str(dst), "--page-size", "3.8x5in", "--dpi", "200", "--bleed", "0.05in"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["size_px"] == [1000 + 20, 760 + 20] and info["bleed_px"] == 10
    from PIL import Image

    with Image.open(dst) as im:
        assert im.size == (1020, 780) and round(float(im.info["dpi"][0])) == 200
    assert main(["print", str(out / "pages/001-page-1.png"), "-o", str(tmp_path / "p.gif"), "--page-size", "a4"]) == 2
