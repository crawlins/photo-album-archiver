"""Sleeve options on the command line and in album.json, and the sleeve's debug image."""

import json

import cv2
import numpy as np
import pytest

import albumproc.album as album_mod
import albumproc.pipeline as pipeline_mod
from albumproc.album import AlbumError, discover_album
from albumproc.cli import main
from albumproc.synth import SleeveSpec, make_case


class _Stop(Exception):
    pass


def _photo(path):
    cv2.imwrite(str(path), np.full((120, 160, 3), 128, np.uint8))
    return str(path)


def test_page_options_reach_the_pipeline(tmp_path, monkeypatch):
    seen = []

    def fake(images, opt, debug_dir=None):
        seen.append(opt.sleeve)
        raise _Stop

    monkeypatch.setattr(pipeline_mod, "process_page", fake)
    shot = _photo(tmp_path / "a.jpg")
    with pytest.raises(_Stop):
        main(["page", shot, "-o", str(tmp_path / "p.png"), "--sleeve", "off", "--crop", "sleeve"])
    with pytest.raises(_Stop):
        main(["page", shot, "-o", str(tmp_path / "p.png")])
    assert (seen[0].mode, seen[0].crop) == ("off", "sleeve")
    assert (seen[1].mode, seen[1].crop) == ("auto", "paper")
    with pytest.raises(SystemExit):
        main(["page", shot, "-o", str(tmp_path / "p.png"), "--crop", "page"])


def _album(tmp_path, pages):
    album = tmp_path / "album"
    for name in ("page-1", "page-2"):
        (album / name).mkdir(parents=True)
        _photo(album / name / "shot.jpg")
    (album / "album.json").write_text(json.dumps({"pages": pages}))
    return album


def test_album_json_overrides_sleeve_per_page(tmp_path, monkeypatch):
    seen = {}

    def fake(images, opt, debug_dir=None):
        seen[len(seen)] = (opt.sleeve.mode, opt.sleeve.crop, opt.curvature.mode)
        raise RuntimeError("stop here")

    monkeypatch.setattr(album_mod, "process_page", fake)
    album = _album(tmp_path, [{"folder": "page-1", "sleeve": "off"}, {"folder": "page-2", "crop": "paper"}])
    assert main(["album", str(album), "-o", str(tmp_path / "out"), "--crop", "sleeve", "--curvature", "off"]) == 3
    assert seen == {0: ("off", "sleeve", "off"), 1: ("auto", "paper", "off")}


def test_sleeve_settings_are_part_of_the_page_cache_key(tmp_path):
    album = _album(tmp_path, ["page-1", {"folder": "page-2", "crop": "sleeve"}])
    spec = discover_album(album)
    base = album_mod.AlbumOptions()
    keys = [album_mod._key(album_mod.asdict(album_mod._page_options(base.page, p))) for p in spec.pages]
    assert keys[0] != keys[1]
    assert album_mod._page_options(base.page, spec.pages[0]) is base.page


@pytest.mark.parametrize("entry,match", [({"folder": "page-1", "sleeve": "on"}, "sleeve must be one of"), ({"folder": "page-1", "crop": "page"}, "crop must be one of")])
def test_bad_sleeve_in_album_json_is_refused(tmp_path, entry, match):
    with pytest.raises(AlbumError, match=match):
        discover_album(_album(tmp_path, [entry]))


def test_debug_image_and_metadata_of_a_sleeved_page(tmp_path):
    case = make_case(1, "glare", 1, sleeve=SleeveSpec(binding="left"))
    shot = tmp_path / "shot.jpg"
    cv2.imwrite(str(shot), case.images[0], [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert main(["page", str(shot), "-o", str(tmp_path / "p.png"), "--debug", str(tmp_path / "debug"), "--curvature", "off"]) == 0
    assert (tmp_path / "debug" / "sleeve.jpg").exists()
    meta = json.loads((tmp_path / "p.json").read_text())
    assert meta["schema_version"] == 6
    assert meta["sleeve"]["mode"] == "auto" and meta["sleeve"]["sleeved"] and meta["sleeve"]["strip_side"] == "left"
