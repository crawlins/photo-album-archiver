import json
from datetime import date, datetime, timezone

import pytest

from albumscan import session as sess
from albumscan.client import Client
from albumscan.outbox import ALBUM_META, PUT_SHOT, Outbox
from albumscan.session import LimitError, Session, check_page_size, default_name


@pytest.fixture
def box(tmp_path):
    b = Outbox(tmp_path / "out")
    yield b
    b.close()


def _metas(box):
    return [json.loads(o.body) for o in box.all() if o.kind == ALBUM_META]


def test_new_album_queues_its_metadata(box):
    s = Session.new(box, " Family ", "A4", now=datetime(2026, 10, 19, 1, 2, 3, tzinfo=timezone.utc))
    assert (s.name, s.page_size, s.page_number) == ("Family", "a4", 1)
    assert _metas(box) == [{"name": "Family", "page_size": "a4", "pages": [s.page_id], "created": "2026-10-19T01:02:03Z"}]


@pytest.mark.parametrize("bad", ["", "  "])
def test_new_album_needs_a_name(box, bad):
    with pytest.raises(ValueError):
        Session.new(box, bad, "letter")


@pytest.mark.parametrize("text, ok", [("letter", "letter"), ("10x12in", "10x12in"), ("254 x 305mm", "254 x 305mm"), ("25.4x30.5CM", "25.4x30.5cm")])
def test_page_sizes(text, ok):
    assert check_page_size(text) == ok


@pytest.mark.parametrize("bad", ["", "huge", "10x12", "0x5in", "10x12ft"])
def test_bad_page_sizes(bad):
    with pytest.raises(ValueError):
        check_page_size(bad)


def test_default_name():
    assert default_name(date(2026, 10, 19)) == "Album 2026-10-19"


def test_scans_and_pages(box):
    s = Session.new(box, "A", "letter")
    first = s.page_id
    assert not s.next_page()  # nothing scanned: stay
    a = s.add_scan(b"\xff\xd8one")
    assert a.path.read_bytes() == b"\xff\xd8one"
    assert s.next_page() and s.page_number == 2 and s.shots == [] and s.previous_page_id == first
    assert _metas(box)[-1]["pages"] == [first, s.page_id]
    puts = [o for o in box.all() if o.kind == PUT_SHOT]
    assert [(o.page_id, o.shot_id) for o in puts] == [(first, a.id)]


def test_shot_limit_is_checked_before_scanning(box, monkeypatch):
    monkeypatch.setattr(sess, "MAX_SHOTS", 2)
    s = Session.new(box, "A", "letter")
    s.add_scan(b"1")
    s.check_can_scan()
    s.add_scan(b"2")
    with pytest.raises(LimitError, match="2 scans"):
        s.check_can_scan()
    with pytest.raises(LimitError):
        s.add_scan(b"3")
    assert box.pending_shots() == 2


def test_page_limit(box, monkeypatch):
    monkeypatch.setattr(sess, "MAX_PAGES", 2)
    s = Session.new(box, "A", "letter")
    s.add_scan(b"1")
    assert s.next_page()
    s.add_scan(b"2")
    with pytest.raises(LimitError, match="2 pages"):
        s.next_page()
    assert s.page_number == 2


def test_delete_shot(box):
    s = Session.new(box, "A", "letter")
    a, b = s.add_scan(b"1"), s.add_scan(b"2")
    s.delete_shot(a.id)
    assert [x.id for x in s.shots] == [b.id]
    assert [o.shot_id for o in box.all() if o.kind == PUT_SHOT] == [b.id] and not a.path.exists()


def test_rename_and_page_size(box):
    s = Session.new(box, "A", "letter")
    s.rename("B")
    s.set_page_size("10x12in")
    assert _metas(box) == [{"name": "B", "page_size": "10x12in", "pages": [s.page_id], "created": s.created}]
    with pytest.raises(ValueError):
        s.set_page_size("big")


def test_open_makes_the_last_page_current(box, server):
    server.json["/api/v1/albums/A"] = {
        "id": "A", "name": "Old", "page_size": "letter", "created": "2026-01-01T00:00:00Z",
        "pages": [{"id": "P1", "shots": 3}, {"id": "P2", "shots": 2}],
    }
    server.json["/api/v1/albums/A/pages/P2"] = {"id": "P2", "shots": [{"id": "S1"}, {"id": "S2"}]}
    s = Session.open(box, Client(server.url, "t"), "A")
    assert (s.name, s.pages, s.page_number) == ("Old", ["P1", "P2"], 2)
    assert [(x.id, x.path) for x in s.shots] == [("S1", None), ("S2", None)]
    assert box.all() == []  # opening sends nothing
    s.delete_shot("S1")
    assert [(o.kind, o.shot_id) for o in box.all()] == [("delete_shot", "S1")]


def test_open_keeps_what_the_outbox_still_holds(box, server):
    s = Session.new(box, "New", "a4")
    s.add_scan(b"1")
    s.next_page()
    local = s.add_scan(b"2")
    # The server has seen only the first metadata and nothing else.
    server.json[f"/api/v1/albums/{s.album_id}"] = {"id": s.album_id, "name": "Untitled", "page_size": None, "created": s.created, "pages": []}
    again = Session.open(box, Client(server.url, "t"), s.album_id)
    assert again.pages == s.pages and again.name == "New" and again.page_size == "a4"
    assert [(x.id, x.path) for x in again.shots] == [(local.id, local.path)]


def test_open_an_empty_album_gives_it_a_page(box, server):
    server.json["/api/v1/albums/A"] = {"id": "A", "name": "E", "page_size": "letter", "created": "2026-01-01T00:00:00Z", "pages": []}
    s = Session.open(box, Client(server.url, "t"), "A")
    assert len(s.pages) == 1 and _metas(box)[-1]["pages"] == s.pages
