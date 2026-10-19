import json
import threading
import time

import pytest

from albumscan.client import Client
from albumscan.outbox import ALBUM_META, AUTH, BLOCKED, DELETE_SHOT, DONE, PUT_SHOT, RETRY, Outbox, Sender, classify


@pytest.fixture
def box(tmp_path):
    b = Outbox(tmp_path / "out")
    yield b
    b.close()


@pytest.mark.parametrize(
    "status, outcome",
    [(200, DONE), (201, DONE), (204, DONE), (500, RETRY), (503, RETRY), (429, RETRY), (401, AUTH), (409, BLOCKED), (422, BLOCKED), (400, BLOCKED), (404, BLOCKED)],
)
def test_classify(status, outcome):
    assert classify(status) == outcome


def test_order_survives_a_restart(tmp_path):
    b = Outbox(tmp_path / "o")
    b.put_album("A", {"name": "x"})
    f = b.add_shot("A", "P", "S1", b"one", "h1")
    b.add_shot("A", "P", "S2", b"two", "h2")
    b.delete_shot("A", "P", "OLD")
    b.close()
    b = Outbox(tmp_path / "o")
    assert [(o.kind, o.shot_id) for o in b.all()] == [(ALBUM_META, None), (PUT_SHOT, "S1"), (PUT_SHOT, "S2"), (DELETE_SHOT, "OLD")]
    assert f.read_bytes() == b"one" and b.pending_shots() == 2
    b.close()


def test_startup_removes_orphans_and_temp_files(tmp_path):
    b = Outbox(tmp_path / "o")
    keep = b.add_shot("A", "P", "S1", b"one", "h1")
    b.close()
    (tmp_path / "o" / "shots" / ".abc.tmp").write_bytes(b"partial")
    (tmp_path / "o" / "shots" / "stray.jpg").write_bytes(b"x")
    Outbox(tmp_path / "o").close()
    assert sorted(p.name for p in (tmp_path / "o" / "shots").iterdir()) == [keep.name]


def test_newer_metadata_replaces_unsent(box):
    box.put_album("A", {"name": "1"})
    box.add_shot("A", "P", "S", b"x", "h")
    box.put_album("B", {"name": "b"})
    box.put_album("A", {"name": "2"})
    assert [(o.kind, o.album_id) for o in box.all()] == [(PUT_SHOT, "A"), (ALBUM_META, "B"), (ALBUM_META, "A")]
    assert box.unsent_meta("A") == {"name": "2"}


def test_deleting_an_unsent_shot_drops_it(box):
    f = box.add_shot("A", "P", "S", b"x", "h")
    box.delete_shot("A", "P", "S")
    assert box.all() == [] and not f.exists()


def test_deleting_a_shot_being_sent_queues_a_deletion(box):
    box.add_shot("A", "P", "S", b"x", "h")
    op = box.take()
    box.delete_shot("A", "P", "S")
    box.release()
    assert [(o.kind, o.shot_id) for o in box.all()] == [(PUT_SHOT, "S"), (DELETE_SHOT, "S")]
    assert box.file(op).exists()


def test_sender_sends_in_order_and_cleans_up(box, server):
    box.put_album("A", {"name": "x", "pages": ["P"]})
    f = box.add_shot("A", "P", "S", b"\xff\xd8x", "h")
    box.delete_shot("A", "P", "GONE")
    s = Sender(box, Client(server.url, "t"))
    assert [s.step(), s.step(), s.step(), s.step()] == [DONE, DONE, DONE, None]
    assert [(m, p) for m, p, _, _ in server.requests] == [
        ("PUT", "/api/v1/albums/A"),
        ("PUT", "/api/v1/albums/A/pages/P/shots/S"),
        ("DELETE", "/api/v1/albums/A/pages/P/shots/GONE"),
    ]
    assert server.requests[1][2]["X-Content-SHA256"] == "h"
    assert box.all() == [] and not f.exists() and s.state == "idle"


def test_a_repeated_upload_counts_as_done(box, server):
    box.add_shot("A", "P", "S", b"x", "h")
    server.answer("PUT", "/api/v1/albums/A/pages/P/shots/S", 200)
    assert Sender(box, Client(server.url, "t")).step() == DONE and box.all() == []


def test_server_errors_retry_the_same_request(box, server):
    box.add_shot("A", "P", "S1", b"x", "h")
    box.add_shot("A", "P", "S2", b"y", "h")
    server.answer("PUT", "/api/v1/albums/A/pages/P/shots/S1", 503)
    s = Sender(box, Client(server.url, "t"))
    assert s.step() == RETRY and s.state == "waiting"
    assert [o.shot_id for o in box.all()] == ["S1", "S2"]
    assert s.step() == DONE
    assert [p for _, p, _, _ in server.requests] == ["/api/v1/albums/A/pages/P/shots/S1"] * 2


def test_unreachable_retries(box):
    box.add_shot("A", "P", "S", b"x", "h")
    s = Sender(box, Client("http://127.0.0.1:9", "t", timeout=2))
    assert s.step() == RETRY and "unreachable" in s.message and len(box.all()) == 1


@pytest.mark.parametrize("status", [409, 422, 400])
def test_refusals_block_until_the_user_acts(box, server, status):
    box.add_shot("A", "P", "S1", b"x", "h")
    box.add_shot("A", "P", "S2", b"y", "h")
    server.answer("PUT", "/api/v1/albums/A/pages/P/shots/S1", status, {"detail": "page has 25 shots"})
    s = Sender(box, Client(server.url, "t"))
    assert s.step() == BLOCKED and s.state == BLOCKED
    assert "page has 25 shots" in s.message and str(status) in s.message
    assert s.step() is None and len(server.requests) == 1  # nothing more is sent
    s.discard()
    assert s.step() == DONE and [o.shot_id for o in box.all()] == []


def test_a_refused_token_waits_for_a_new_one(box, server):
    box.add_shot("A", "P", "S", b"x", "h")
    server.answer("PUT", "/", 401, {"detail": "invalid token"})
    s = Sender(box, Client(server.url, "bad"))
    assert s.step() == AUTH and s.state == AUTH and len(box.all()) == 1
    s.unblock(Client(server.url, "good"))
    assert s.step() == DONE
    assert server.requests[-1][2]["Authorization"] == "Bearer good"


def test_the_thread_backs_off_and_drains(box, server):
    delays = []
    done = threading.Event()
    box.add_shot("A", "P", "S", b"x", "h")
    for _ in range(3):
        server.answer("PUT", "/", 500)
    s = Sender(box, Client(server.url, "t"), on_change=lambda s: s.state == "idle" and done.set(), sleep=delays.append)
    s.start()
    try:
        assert done.wait(10)
        assert delays == [1.0, 2.0, 4.0] and box.all() == []
        box.add_shot("A", "P", "S2", b"y", "h")
        done.clear()
        s.wake()
        assert done.wait(10) and box.all() == []
    finally:
        s.stop()
