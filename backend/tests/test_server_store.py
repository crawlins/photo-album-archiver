"""The index and the data folder: limits under concurrent writers, ordering, cascades, cleanup, check."""

import logging
import threading

import pytest

from albumserver.files import Layout, check, cleanup
from albumserver.store import LimitExceeded, Store
from conftest import make_jpeg, new_id


def _add(store, a, p, s=None, digest=None):
    return store.add_shot(a, p, s or new_id(), digest or "0" * 64, 10, "2026-10-04T12:00:00.000Z", lambda: None)


def test_wal_mode(store):
    assert store._conn().execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_shot_limit_under_concurrent_writers(settings, store):
    a, p = new_id(), new_id()
    for _ in range(20):
        _add(store, a, p)
    results = []
    barrier = threading.Barrier(12)

    def writer():
        s = Store(store.path, max_shots=25)  # its own connection, as the worker and other threads have
        barrier.wait()
        try:
            _add(s, a, p)
            results.append("ok")
        except LimitExceeded:
            results.append("limit")
        s.close()

    threads = [threading.Thread(target=writer) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 5
    assert results.count("limit") == 7
    assert len(store.get_page(a, p)["shots"]) == 25


def test_page_limit_under_concurrent_writers(store):
    store.max_pages = 10
    a = new_id()
    store.put_album(a, "x", "letter", [new_id() for _ in range(8)], "2026-10-04T00:00:00Z")
    results = []
    barrier = threading.Barrier(6)

    def writer():
        s = Store(store.path, max_pages=10)
        barrier.wait()
        try:
            _add(s, a, new_id())
            results.append("ok")
        except LimitExceeded:
            results.append("limit")
        s.close()

    threads = [threading.Thread(target=writer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 2
    assert len(store.get_album(a)["pages"]) == 10


def test_refused_shot_is_never_placed(store):
    store.max_shots = 1
    a, p = new_id(), new_id()
    _add(store, a, p)
    placed = []
    with pytest.raises(LimitExceeded):
        store.add_shot(a, p, new_id(), "1" * 64, 1, "t", lambda: placed.append(1))
    assert placed == []


def test_renumbering_keeps_positions_unique(store):
    a = new_id()
    pages = [new_id() for _ in range(6)]
    store.put_album(a, "x", "letter", pages, "c")
    for order in (pages[::-1], pages[2:] + pages[:2], [pages[3], pages[0]]):
        store.put_album(a, "x", "letter", order, "c")
        assert [p["id"] for p in store.get_album(a)["pages"]] == order
        rows = store._conn().execute("SELECT position FROM page WHERE album_id = ? ORDER BY position", (a,)).fetchall()
        assert [r[0] for r in rows] == list(range(1, len(order) + 1))


def test_cascading_deletes(store):
    a, p = new_id(), new_id()
    _add(store, a, p)
    _add(store, a, p)
    store.delete_album(a)
    c = store._conn()
    assert c.execute("SELECT COUNT(*) FROM page").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM shot").fetchone()[0] == 0
    _add(store, a, p)
    removed = store.delete_page(a, p)
    assert len(removed.shot_ids) == 1
    assert c.execute("SELECT COUNT(*) FROM shot").fetchone()[0] == 0


def test_store_survives_reopen(store, settings):
    a, p = new_id(), new_id()
    _add(store, a, p)
    store.close()
    again = Store(store.path)
    assert again.get_album(a)["pages"] == [{"id": p, "shots": 1}]
    again.close()


# -- crash recovery -------------------------------------------------------------


def _upload(api, a, p, s, seed):
    data = make_jpeg(seed)
    assert api.put_shot(a, p, s, data).status_code == 201
    return data


def test_cleanup_after_crashes_at_each_step(api, client, caplog):
    layout, store = client.layout, client.store
    a, p, keep, b = new_id(), new_id(), new_id(), new_id()
    _upload(api, a, p, keep, 1)
    api.get(f"/albums/{a}/pages/{p}/shots/{keep}", params={"size": "thumb"})

    # 1. crash while the body was being written: a temporary file
    tmp = layout.photos(a, p) / f"{new_id()}.jpg.1234abcd.tmp"
    tmp.write_bytes(b"partial")
    # 2. crash after the rename, before the row: an unindexed photo
    orphan = layout.photo(a, p, new_id())
    orphan.write_bytes(make_jpeg(2))
    # 3. crash after a delete's commit, before its files went
    gone = new_id()
    _upload(api, a, p, gone, 3)
    api.get(f"/albums/{a}/pages/{p}/shots/{gone}", params={"size": "thumb"})
    store.delete_shot(a, p, gone)
    # 4. an album and a page deleted from the index only
    _upload(api, b, new_id(), new_id(), 4)
    store.delete_album(b)
    lost_page = new_id()
    _upload(api, a, lost_page, new_id(), 5)
    store.delete_page(a, lost_page)
    # 5. leftovers of runs: a work folder, an old output folder, a temp PDF
    (layout.work(a, p) / "page").mkdir(parents=True)
    (layout.outputs(a, p) / "run-old").mkdir(parents=True)
    (layout.album(a) / "album.pdf.tmp").write_bytes(b"x")
    # 6. an indexed photo whose file is gone
    missing = new_id()
    _upload(api, a, p, missing, 6)
    layout.photo(a, p, missing).unlink()

    caplog.set_level(logging.INFO, logger="albumserver")
    found = cleanup(layout, store.snapshot())
    assert not tmp.exists()
    assert not orphan.exists()
    assert not layout.photo(a, p, gone).exists()
    assert not layout.thumb(a, gone).exists()
    assert not layout.album(b).exists()
    assert not layout.photos(a, lost_page).exists()
    assert not (layout.album(a) / "work").exists()
    assert not layout.outputs(a, p).joinpath("run-old").exists()
    assert not (layout.album(a) / "album.pdf.tmp").exists()
    # what is indexed stays
    assert layout.photo(a, p, keep).exists()
    assert layout.thumb(a, keep).exists()
    assert found.missing == [str(layout.photo(a, p, missing).relative_to(layout.root))]
    assert "photo missing" in caplog.text and missing in caplog.text
    # and a second pass finds nothing more to do
    assert cleanup(layout, store.snapshot()).extra == []


def test_cleanup_keeps_the_current_output(client):
    layout, store = client.layout, client.store
    a, p = new_id(), new_id()
    _add(store, a, p)
    c = store._conn()
    c.execute("UPDATE page SET output = ? WHERE id = ?", (f"out/{p}/run-cur", p))
    (layout.outputs(a, p) / "run-cur").mkdir(parents=True)
    (layout.outputs(a, p) / "run-old").mkdir(parents=True)
    cleanup(layout, store.snapshot())
    assert (layout.outputs(a, p) / "run-cur").exists()
    assert not (layout.outputs(a, p) / "run-old").exists()


def test_check_reports_without_changing_anything(api, client, capsys, monkeypatch):
    layout, store = client.layout, client.store
    a, p = new_id(), new_id()
    s_missing, s_changed, s_ok = new_id(), new_id(), new_id()
    _upload(api, a, p, s_missing, 1)
    _upload(api, a, p, s_changed, 2)
    _upload(api, a, p, s_ok, 3)
    layout.photo(a, p, s_missing).unlink()
    layout.photo(a, p, s_changed).write_bytes(make_jpeg(99))
    extra = layout.photo(a, p, new_id())
    extra.write_bytes(b"x")

    found = check(layout, store.snapshot())
    rel = lambda path: str(path.relative_to(layout.root))  # noqa: E731
    assert found.missing == [rel(layout.photo(a, p, s_missing))]
    assert found.changed == [rel(layout.photo(a, p, s_changed))]
    assert found.extra == [rel(extra)]
    assert extra.exists()  # nothing removed

    from albumserver.cli import main

    monkeypatch.setenv("ALBUMSERVER_DATA_DIR", str(layout.root))
    assert main(["check"]) == 1
    out = capsys.readouterr().out
    assert s_missing in out and s_changed in out and extra.name in out
    extra.unlink()
    layout.photo(a, p, s_changed).unlink()
    store.delete_shot(a, p, s_changed)
    store.delete_shot(a, p, s_missing)
    assert main(["check"]) == 0


def test_check_on_empty_data_folder(tmp_path):
    layout = Layout(tmp_path)
    s = Store(layout.index)
    assert check(layout, s.snapshot()).ok
    s.close()
