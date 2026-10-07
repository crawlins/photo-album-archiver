"""Curvature options on the command line and in album.json, and the straightness command."""

import json

import cv2
import numpy as np
import pytest

import albumproc.album as album_mod
import albumproc.pipeline as pipeline_mod
from albumproc.album import AlbumError, discover_album
from albumproc.cli import main
from albumproc.synth import make_case


class _Stop(Exception):
    pass


def _photo(path):
    cv2.imwrite(str(path), np.full((120, 160, 3), 128, np.uint8))
    return str(path)


def test_page_options_reach_the_pipeline(tmp_path, monkeypatch):
    seen = []

    def fake(images, opt, debug_dir=None):
        seen.append(opt.curvature)
        raise _Stop

    monkeypatch.setattr(pipeline_mod, "process_page", fake)
    shot = _photo(tmp_path / "a.jpg")
    with pytest.raises(_Stop):
        main(["page", shot, "-o", str(tmp_path / "p.png"), "--curvature", "force", "--binding", "right"])
    with pytest.raises(_Stop):
        main(["page", shot, "-o", str(tmp_path / "p.png")])
    assert (seen[0].mode, seen[0].binding) == ("force", "right")
    assert (seen[1].mode, seen[1].binding) == ("auto", "auto")
    with pytest.raises(SystemExit):
        main(["page", shot, "-o", str(tmp_path / "p.png"), "--curvature", "sometimes"])


def _album(tmp_path, pages):
    album = tmp_path / "album"
    for name in ("page-1", "page-2"):
        (album / name).mkdir(parents=True)
        _photo(album / name / "shot.jpg")
    (album / "album.json").write_text(json.dumps({"pages": pages}))
    return album


def test_album_json_overrides_curvature_per_page(tmp_path, monkeypatch):
    seen = {}

    def fake(images, opt, debug_dir=None):
        seen[len(seen)] = (opt.curvature.mode, opt.curvature.binding)
        raise RuntimeError("stop here")

    monkeypatch.setattr(album_mod, "process_page", fake)
    album = _album(tmp_path, [{"folder": "page-1", "curvature": "off"}, {"folder": "page-2", "binding": "left"}])
    assert main(["album", str(album), "-o", str(tmp_path / "out"), "--curvature", "force"]) == 3
    assert seen == {0: ("off", "auto"), 1: ("force", "left")}


def test_curvature_settings_are_part_of_the_page_cache_key(tmp_path):
    album = _album(tmp_path, ["page-1", {"folder": "page-2", "curvature": "off"}])
    spec = discover_album(album)
    base = album_mod.AlbumOptions()
    keys = [album_mod._key(album_mod.asdict(album_mod._page_options(base.page, p))) for p in spec.pages]
    assert keys[0] != keys[1]
    assert album_mod._page_options(base.page, spec.pages[0]) is base.page  # no override: the album's options


def test_bad_curvature_in_album_json_is_refused(tmp_path):
    album = _album(tmp_path, [{"folder": "page-1", "binding": "spine"}])
    with pytest.raises(AlbumError, match="binding must be one of"):
        discover_album(album)


@pytest.fixture(scope="module")
def bent_page(tmp_path_factory):
    """A bent page through the command line, flattened and not."""
    d = tmp_path_factory.mktemp("bent")
    case = make_case(3, "curved", 1, lift=0.05, binding="left")
    shot = d / "shot.jpg"
    cv2.imwrite(str(shot), case.images[0], [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert main(["page", str(shot), "-o", str(d / "on.png"), "--debug", str(d / "debug")]) == 0
    assert main(["page", str(shot), "-o", str(d / "off.png"), "--curvature", "off"]) == 0
    return d


def test_debug_images_of_the_fit(bent_page):
    names = {p.name for p in (bent_page / "debug").iterdir()}
    assert {"curvature_outline.jpg", "curvature_profile.png", "curvature_grid.jpg"} <= names
    meta = json.loads((bent_page / "on.json").read_text())
    assert meta["curvature"]["applied"] and meta["curvature"]["binding"] == "left"
    off = json.loads((bent_page / "off.json").read_text())["curvature"]
    assert off == {**off, "mode": "off", "applied": False, "reason": "off", "profile": [], "alignment": {}}


def test_straightness_shows_the_bend_gone(bent_page, capsys):
    out = {}
    for name in ("on", "off"):
        assert main(["straightness", str(bent_page / f"{name}.png"), "--overlay", str(bent_page / f"{name}.lines.jpg")]) == 0
        out[name] = json.loads(capsys.readouterr().out)
        assert (bent_page / f"{name}.lines.jpg").exists()
    assert out["on"]["lines"] and {"length", "deviation", "deviation_frac", "pieces", "start", "end"} <= set(out["on"]["lines"][0])
    assert out["on"]["worst"] < 0.5 * out["off"]["worst"]


def test_straightness_of_an_unreadable_file(tmp_path):
    (tmp_path / "x.png").write_bytes(b"nope")
    assert main(["straightness", str(tmp_path / "x.png")]) == 2
