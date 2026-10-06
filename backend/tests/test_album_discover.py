import json

import pytest

from albumproc.album import AlbumError, discover_album, natural_key
from albumproc.printsize import PageSize

SIZE = PageSize(10, 12)


def _album(tmp_path, pages, manifest=None):
    for name, files in pages.items():
        d = tmp_path / name
        d.mkdir()
        for f in files:
            (d / f).write_bytes(b"x")
    if manifest is not None:
        (tmp_path / "album.json").write_text(json.dumps(manifest))
    return tmp_path


def test_natural_order_and_supported_files(tmp_path):
    a = _album(tmp_path, {"page-10": ["a.jpg"], "page-2": ["b.JPEG", "a.png", "notes.txt", ".hidden.jpg"], "page-1": ["x.tif"], "empty": ["readme.md"]})
    spec = discover_album(a, SIZE)
    assert [p.name for p in spec.pages] == ["page-1", "page-2", "page-10"]
    assert [p.index for p in spec.pages] == [1, 2, 3]
    assert [f.name for f in spec.pages[1].photos] == ["a.png", "b.JPEG"]
    assert spec.title == tmp_path.name
    assert all(p.size == SIZE for p in spec.pages)


def test_natural_key():
    assert sorted(["p10", "p2", "P1", "p1a"], key=natural_key) == ["P1", "p1a", "p2", "p10"]


def test_manifest_order_and_overrides(tmp_path):
    manifest = {
        "title": "Family 1962",
        "page_size": "8x10in",
        "fit": "fill",
        "pages": [
            {"folder": "cover", "name": "Front cover", "page_size": "9x11in", "rotate": 90, "fit": "fit"},
            "page 1",
            {"folder": "gone"},
        ],
    }
    a = _album(tmp_path, {"page 1": ["a.jpg"], "cover": ["c.jpg"], "unlisted": ["u.jpg"]}, manifest)
    spec = discover_album(a, SIZE)
    assert spec.title == "Family 1962"
    cover, p1, gone = spec.pages
    assert (cover.name, cover.slug, cover.rotate, cover.fit, cover.size) == ("Front cover", "cover", 90, "fit", PageSize(9, 11))
    assert (p1.name, p1.slug, p1.rotate, p1.fit, p1.size) == ("page 1", "page-1", 0, "fill", PageSize(8, 10))
    assert p1.stem == "002-page-1"
    assert gone.problem == "folder 'gone' not found" and cover.problem is None


def test_manifest_page_without_photos_is_a_problem(tmp_path):
    a = _album(tmp_path, {"p1": ["notes.txt"]}, {"pages": ["p1"]})
    assert discover_album(a, SIZE).pages[0].problem == "no photos in 'p1'"


def test_empty_album(tmp_path):
    with pytest.raises(AlbumError, match="no pages found"):
        discover_album(_album(tmp_path, {"p1": ["notes.txt"]}), SIZE)


def test_size_defaults_to_letter(tmp_path):
    a = _album(tmp_path, {"p1": ["a.jpg"], "p2": ["a.jpg"]}, {"pages": [{"folder": "p1", "page_size": "4x6in"}, "p2"]})
    assert [p.size for p in discover_album(a, None).pages] == [PageSize(4, 6), PageSize(8.5, 11)]


def test_manifest_size_wins_over_command_line(tmp_path):
    a = _album(tmp_path, {"p1": ["a.jpg"]}, {"page_size": "4x6in"})
    assert discover_album(a, SIZE).pages[0].size == PageSize(4, 6)


@pytest.mark.parametrize(
    "manifest, match",
    [
        ({"page_size": "big"}, "page_size.*'big'"),
        ({"pagesize": "4x6in"}, "unknown key.*pagesize"),
        ({"pages": [{"folder": "p1", "rotat": 90}]}, r"pages\[0\].*unknown key.*rotat"),
        ({"pages": [{"folder": "p1", "rotate": 45}]}, r"pages\[0\].*rotate"),
        ({"pages": [{"folder": "p1", "fit": "squash"}]}, r"pages\[0\].*fit"),
        ({"pages": ["p1", "p1"]}, "listed twice"),
        ({"pages": [3]}, "folder name"),
        ([], "JSON object"),
    ],
)
def test_bad_manifest(tmp_path, manifest, match):
    a = _album(tmp_path, {"p1": ["a.jpg"]}, manifest)
    with pytest.raises(AlbumError, match=match):
        discover_album(a, SIZE)


def test_invalid_json(tmp_path):
    a = _album(tmp_path, {"p1": ["a.jpg"]})
    (a / "album.json").write_text("{")
    with pytest.raises(AlbumError, match="invalid JSON"):
        discover_album(a, SIZE)
