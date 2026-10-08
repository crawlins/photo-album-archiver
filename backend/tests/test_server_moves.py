"""Moving shots between pages: ``POST /albums/{albumId}/pages/{pageId}/move`` (shot-actions spec)."""

from __future__ import annotations

import pytest

from albumserver.files import cleanup
from conftest import make_jpeg, new_id


def move(api, a, p, shots):
    return api.c.post(f"/api/v1/albums/{a}/pages/{p}/move", json={"shots": shots})


def shots_of(api, a, p):
    return [s["id"] for s in api.get(f"/albums/{a}/pages/{p}").json()["shots"]]


def page_states(api, a):
    return {p["id"]: p["state"] for p in api.get(f"/albums/{a}/status").json()["pages"]}


def _album(api, clock, n_shots=4, pages=1):
    """An album whose first page has ``n_shots`` shots taken a second apart."""
    a = new_id()
    ps = [new_id() for _ in range(pages)]
    api.put_album(a, ps)
    ss = []
    for i in range(n_shots):
        s = new_id()
        assert api.put_shot(a, ps[0], s, make_jpeg(i)).status_code == 201
        clock.advance(1)
        ss.append(s)
    return a, ps, ss


def test_move_to_an_existing_page(api, client, clock):
    a, (p1, p2), ss = _album(api, clock, pages=2)
    other = new_id()
    api.put_shot(a, p2, other, make_jpeg(9))
    assert move(api, a, p2, ss[2:]).status_code == 204
    assert shots_of(api, a, p1) == ss[:2]
    # moved shots keep their time taken, so they come before the later shot
    assert shots_of(api, a, p2) == [*ss[2:], other]
    for s in ss[2:]:
        assert client.layout.photo(a, p2, s).is_file()
        assert not client.layout.photo(a, p1, s).exists()
        r = api.get(f"/albums/{a}/pages/{p2}/shots/{s}")
        assert r.status_code == 200
        assert api.get(f"/albums/{a}/pages/{p1}/shots/{s}").status_code == 404


def test_moved_shot_is_byte_identical_and_keeps_its_preview(api, client, clock):
    a, (p1,), ss = _album(api, clock, n_shots=2)
    original = api.get(f"/albums/{a}/pages/{p1}/shots/{ss[1]}").content
    api.get(f"/albums/{a}/pages/{p1}/shots/{ss[1]}", params={"size": "thumb"})
    p2 = new_id()
    assert move(api, a, p2, [ss[1]]).status_code == 204
    r = api.get(f"/albums/{a}/pages/{p2}/shots/{ss[1]}")
    assert r.content == original
    assert client.layout.thumb(a, ss[1]).exists()
    assert api.get(f"/albums/{a}/pages/{p2}/shots/{ss[1]}", params={"size": "thumb"}).status_code == 200


def test_move_to_an_unknown_page_creates_it_at_the_end_then_metadata_places_it(api, clock):
    a, (p1, p3), ss = _album(api, clock, pages=2)
    p2 = new_id()
    assert move(api, a, p2, ss[2:]).status_code == 204
    assert [p["id"] for p in api.get(f"/albums/{a}").json()["pages"]] == [p1, p3, p2]
    api.put_album(a, [p1, p2, p3])
    pages = api.get(f"/albums/{a}").json()["pages"]
    assert pages == [{"id": p1, "shots": 2}, {"id": p2, "shots": 2}, {"id": p3, "shots": 0}]


def test_repeated_move_changes_nothing_more(api, clock):
    a, (p1,), ss = _album(api, clock)
    p2 = new_id()
    assert move(api, a, p2, ss[1:]).status_code == 204
    before = api.get(f"/albums/{a}").json()
    assert move(api, a, p2, ss[1:]).status_code == 204
    assert api.get(f"/albums/{a}").json() == before


def test_unknown_and_foreign_shots_are_skipped(api, clock):
    a, (p1,), ss = _album(api, clock, n_shots=2)
    b, (q1,), (foreign, *_) = _album(api, clock, n_shots=1)
    p2 = new_id()
    assert move(api, a, p2, [ss[1], new_id(), foreign]).status_code == 204
    assert shots_of(api, a, p2) == [ss[1]]
    assert shots_of(api, b, q1) == [foreign]


def test_move_that_would_exceed_25_shots_is_422_and_changes_nothing(api, client, clock):
    a, (p1, p2), ss = _album(api, clock, n_shots=3, pages=2)
    for i in range(23):
        assert api.put_shot(a, p2, new_id(), make_jpeg(100 + i)).status_code == 201
    r = move(api, a, p2, ss)
    assert r.status_code == 422
    assert r.json()["error"] == "max_shots"
    assert shots_of(api, a, p1) == ss
    assert len(shots_of(api, a, p2)) == 23
    assert not any(client.layout.photo(a, p2, s).exists() for s in ss)
    assert move(api, a, p2, ss[1:]).status_code == 204
    assert len(shots_of(api, a, p2)) == 25


def test_move_that_would_create_the_501st_page_is_422(api, clock):
    a = new_id()
    pages = [new_id() for _ in range(500)]
    api.put_album(a, pages)
    s = new_id()
    api.put_shot(a, pages[0], s, make_jpeg(1))
    extra = new_id()
    r = move(api, a, extra, [s])
    assert r.status_code == 422
    assert r.json()["error"] == "max_pages"
    assert len(api.get(f"/albums/{a}").json()["pages"]) == 500
    assert shots_of(api, a, pages[0]) == [s]


@pytest.mark.parametrize(
    "body",
    [
        {"shots": []},
        {"shots": ["not-a-uuid"]},
        {"shots": "x"},
        {},
        {"shots": [], "extra": 1},
    ],
)
def test_bad_bodies_are_400(api, clock, body):
    a, (p1,), ss = _album(api, clock, n_shots=1)
    r = api.c.post(f"/api/v1/albums/{a}/pages/{new_id()}/move", json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"
    assert shots_of(api, a, p1) == ss


def test_too_many_or_repeated_ids_are_400(api, clock):
    a, (p1,), ss = _album(api, clock, n_shots=1)
    assert move(api, a, new_id(), [new_id() for _ in range(26)]).status_code == 400
    assert move(api, a, new_id(), [ss[0], ss[0]]).status_code == 400
    assert shots_of(api, a, p1) == ss


def test_unknown_album_is_404_and_page_of_another_album_is_409(api, clock):
    assert move(api, new_id(), new_id(), [new_id()]).status_code == 404
    a, (p1,), ss = _album(api, clock, n_shots=1)
    b, (q1,), _ = _album(api, clock, n_shots=1)
    assert move(api, a, q1, ss).status_code == 409
    assert shots_of(api, a, p1) == ss


def test_source_page_is_ready_at_once_and_target_waits(api, clock):
    a, (p1,), ss = _album(api, clock)
    p2 = new_id()
    move(api, a, p2, ss[2:])
    states = page_states(api, a)
    assert states[p1] == "ready"
    assert states[p2] == "changed"


def test_page_left_without_shots_is_empty_and_loses_its_outputs(api, client, clock):
    a, (p1, p2), ss = _album(api, clock, n_shots=2, pages=2)
    out = client.layout.outputs(a, p1) / "run-x"
    out.mkdir(parents=True)
    client.store._conn().execute("UPDATE page SET output = 'out/x/run-x', print_path = 'out/x/run-x/p.png' WHERE id = ?", (p1,))
    assert move(api, a, p2, ss).status_code == 204
    assert page_states(api, a)[p1] == "empty"
    assert not client.layout.outputs(a, p1).exists()
    assert api.get(f"/albums/{a}/pages/{p1}/print").status_code == 404
    assert api.get(f"/albums/{a}/status").json()["pdf"] == "none"
    # the app deletes the emptied page next
    assert api.delete(f"/albums/{a}/pages/{p1}").status_code == 204
    assert shots_of(api, a, p2) == ss


def test_apps_order_of_requests(api, clock):
    """What the app sends for "Create a new page" on its last page: the move, then the metadata."""
    a, (p1,), ss = _album(api, clock)
    p2 = new_id()
    assert move(api, a, p2, ss[3:]).status_code == 204
    assert api.put_album(a, [p1, p2]).status_code == 200
    assert api.get(f"/albums/{a}").json()["pages"] == [{"id": p1, "shots": 3}, {"id": p2, "shots": 1}]
    assert page_states(api, a) == {p1: "ready", p2: "changed"}


def test_cleanup_after_a_crash_on_either_side_of_the_commit(api, client, clock):
    layout, store = client.layout, client.store
    a, (p1, p2), ss = _album(api, clock, n_shots=2, pages=2)

    # Before the commit: the new name exists but the index still has the old page.
    layout.photos(a, p2).mkdir(parents=True, exist_ok=True)
    layout.photo(a, p2, ss[0]).hardlink_to(layout.photo(a, p1, ss[0]))
    # After the commit: the index has the new page but the old name is still there.
    store.move_shots(a, p2, [ss[1]], lambda moves: [layout.photo(a, p2, s).hardlink_to(layout.photo(a, src, s)) for s, src in moves])

    cleanup(layout, store.snapshot())
    assert layout.photo(a, p1, ss[0]).is_file() and not layout.photo(a, p2, ss[0]).exists()
    assert layout.photo(a, p2, ss[1]).is_file() and not layout.photo(a, p1, ss[1]).exists()
    assert cleanup(layout, store.snapshot()).ok
