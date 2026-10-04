"""Contract tests: the requests of the app spec's "Server contract", plus the server's own rules."""

from __future__ import annotations

import io
import threading

import pytest
from PIL import Image

from conftest import Api, make_jpeg, new_id, sha

# -- ping and errors ----------------------------------------------------------


def test_ping(api):
    r = api.get("/ping")
    assert r.status_code == 200


def test_bad_ids_are_400_with_an_error_body(api):
    r = api.get("/albums/not-a-uuid")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_id"
    assert "not-a-uuid" in r.json()["message"]
    a = new_id()
    assert api.get(f"/albums/{a}/pages/xyz").status_code == 400
    assert api.delete(f"/albums/{a}/pages/{new_id()}/shots/12").status_code == 400


def test_unknown_route_is_404_json(api):
    r = api.get("/nothing-here")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


# -- album metadata -----------------------------------------------------------


def test_put_album_creates_and_get_returns_it(api):
    a, p1, p2 = new_id(), new_id(), new_id()
    r = api.put_album(a, [p1, p2])
    assert r.status_code == 200
    got = api.get(f"/albums/{a}").json()
    assert got == {
        "id": a,
        "name": "Rawlins family 1962-1968",
        "page_size": "8.5x11in",
        "created": "2026-10-04T01:09:19Z",
        "pages": [{"id": p1, "shots": 0}, {"id": p2, "shots": 0}],
    }


def test_put_album_replaces_metadata_and_reorders(api):
    a, p1, p2, p3 = new_id(), new_id(), new_id(), new_id()
    api.put_album(a, [p1, p2, p3])
    api.put_shot(a, p1, new_id(), make_jpeg(1))
    r = api.put_album(a, [p3, p1, p2], name="Renamed", page_size="10x12in")
    assert r.status_code == 200
    got = api.get(f"/albums/{a}").json()
    assert got["name"] == "Renamed"
    assert got["page_size"] == "10x12in"
    assert [p["id"] for p in got["pages"]] == [p3, p1, p2]
    assert [p["shots"] for p in got["pages"]] == [0, 1, 0]


def test_omitted_pages_with_shots_are_kept_in_first_shot_order(api, clock):
    a, p1, p2, p3, p4 = (new_id() for _ in range(5))
    api.put_album(a, [p1, p2, p3, p4])
    # shots arrive for p3 first, then p2
    api.put_shot(a, p3, new_id(), make_jpeg(3))
    clock.advance(1)
    api.put_shot(a, p2, new_id(), make_jpeg(2))
    # stale metadata lists only p4 and p1; p1 and p4 have no shots, p2 and p3 do
    api.put_album(a, [p4, p1])
    assert [p["id"] for p in api.get(f"/albums/{a}").json()["pages"]] == [p4, p1, p3, p2]


def test_omitted_pages_without_shots_are_dropped(api):
    a, p1, p2 = new_id(), new_id(), new_id()
    api.put_album(a, [p1, p2])
    api.put_album(a, [p2])
    assert [p["id"] for p in api.get(f"/albums/{a}").json()["pages"]] == [p2]


@pytest.mark.parametrize(
    "body, field",
    [
        ({"name": "  "}, "name"),
        ({"page_size": "huge"}, "page_size"),
        ({"page_size": None}, "page_size"),
        ({"pages": ["not-a-uuid"]}, "pages"),
        ({"pages": ["11111111-1111-4111-8111-111111111111"] * 2}, "listed twice"),
        ({"created": "yesterday"}, "created"),
        ({"created": "2026-02-30T01:00:00Z"}, "created"),
        ({"created": "2026-10-04T01:09:19"}, "created"),  # no zone
        ({"colour": "red"}, "colour"),
        ({"name": 7}, "name"),
    ],
)
def test_put_album_rejects_bad_metadata_and_changes_nothing(api, body, field):
    a = new_id()
    good = {"name": "Album", "page_size": "letter", "pages": [], "created": "2026-10-04T01:09:19Z"}
    api.put_album(a, [new_id()], name="Before")
    r = api.c.put(f"/api/v1/albums/{a}", json={**good, **body})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"
    assert field in r.json()["message"]
    assert api.get(f"/albums/{a}").json()["name"] == "Before"


@pytest.mark.parametrize("created", ["2026-10-04T01:09:19Z", "2026-10-04T01:09:19.123456789Z", "2026-10-04T03:09:19+02:00"])
def test_put_album_accepts_instant_formats(api, created):
    a = new_id()
    assert api.put_album(a, [], created=created).status_code == 200
    assert api.get(f"/albums/{a}").json()["created"] == created


def test_put_album_missing_key_and_bad_json_are_400(api):
    a = new_id()
    r = api.c.put(f"/api/v1/albums/{a}", json={"name": "x", "page_size": "letter", "pages": []})
    assert r.status_code == 400 and "created" in r.json()["message"]
    r = api.c.put(f"/api/v1/albums/{a}", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert api.get(f"/albums/{a}").status_code == 404


def test_put_album_page_from_another_album_is_400(api):
    a, b, p = new_id(), new_id(), new_id()
    api.put_album(a, [p])
    r = api.put_album(b, [p])
    assert r.status_code == 400
    assert "another album" in r.json()["message"]


def test_500_pages_allowed_501_refused_with_422(api):
    a = new_id()
    pages = [new_id() for _ in range(500)]
    assert api.put_album(a, pages).status_code == 200
    r = api.put_album(a, pages + [new_id()])
    assert r.status_code == 422
    assert r.json()["error"] == "max_pages"
    assert len(api.get(f"/albums/{a}").json()["pages"]) == 500


def test_metadata_limit_counts_kept_pages(api):
    a = new_id()
    pages = [new_id() for _ in range(500)]
    api.put_album(a, pages)
    api.put_shot(a, pages[0], new_id(), make_jpeg())
    # pages[0] is omitted but has a shot, so it is kept: 500 listed + 1 kept
    r = api.put_album(a, pages[1:] + [new_id()])
    assert r.status_code == 422


def test_list_albums_newest_change_first(api, clock):
    a, b = new_id(), new_id()
    api.put_album(a, [], name="A")
    clock.advance(5)
    pb = new_id()
    api.put_album(b, [pb], name="B", page_size="10x12in")
    clock.advance(5)
    api.put_shot(b, pb, new_id(), make_jpeg(1))
    api.put_shot(b, pb, new_id(), make_jpeg(2))
    albums = api.get("/albums").json()["albums"]
    assert [x["id"] for x in albums] == [b, a]
    assert albums[0] == {"id": b, "name": "B", "page_size": "10x12in", "pages": 1, "shots": 2, "updated": "2026-10-04T12:00:10.000Z"}
    clock.advance(5)
    api.put_album(a, [], name="A2")
    assert [x["id"] for x in api.get("/albums").json()["albums"]] == [a, b]


def test_unknown_album_and_page_are_404(api):
    a, p = new_id(), new_id()
    assert api.get(f"/albums/{a}").status_code == 404
    api.put_album(a, [p])
    assert api.get(f"/albums/{a}/pages/{new_id()}").status_code == 404
    assert api.get(f"/albums/{new_id()}/pages/{p}").status_code == 404
    assert api.get(f"/albums/{a}/pages/{p}").json() == {"id": p, "shots": []}


# -- shot upload --------------------------------------------------------------


def test_upload_new_shot_201_stored_byte_for_byte(api, client):
    a, p, s = new_id(), new_id(), new_id()
    api.put_album(a, [p])
    data = make_jpeg(5)
    r = api.put_shot(a, p, s, data)
    assert r.status_code == 201
    assert client.layout.photo(a, p, s).read_bytes() == data
    page = api.get(f"/albums/{a}/pages/{p}").json()
    assert page == {"id": p, "shots": [{"id": s, "sha256": sha(data), "bytes": len(data), "taken": "2026-10-04T12:00:00.000Z"}]}


def test_repeat_upload_is_200_and_does_not_read_the_body(api, client):
    a, p, s = new_id(), new_id(), new_id()
    data = make_jpeg(5)
    api.put_shot(a, p, s, data)
    # Same id and hash, but a body that would fail every check if it were read.
    r = api.put_shot(a, p, s, b"not even a jpeg", digest=sha(data))
    assert r.status_code == 200
    assert client.layout.photo(a, p, s).read_bytes() == data
    assert len(api.get(f"/albums/{a}/pages/{p}").json()["shots"]) == 1


def test_same_shot_id_different_hash_is_409(api, client):
    a, p, s = new_id(), new_id(), new_id()
    data = make_jpeg(5)
    api.put_shot(a, p, s, data)
    r = api.put_shot(a, p, s, make_jpeg(6))
    assert r.status_code == 409
    assert client.layout.photo(a, p, s).read_bytes() == data


def test_hash_mismatch_is_400_and_stores_nothing(api, client):
    a, p, s = new_id(), new_id(), new_id()
    data = make_jpeg(5)
    r = api.put_shot(a, p, s, data, digest=sha(b"something else"))
    assert r.status_code == 400
    assert r.json()["error"] == "hash_mismatch"
    assert api.get(f"/albums/{a}").status_code == 404
    assert not any(client.layout.albums.rglob("*.jpg*"))


@pytest.mark.parametrize("digest", [None, "abc", "Z" * 64])
def test_missing_or_malformed_hash_header_is_400(api, digest):
    a, p, s = new_id(), new_id(), new_id()
    headers = {} if digest is None else {"X-Content-SHA256": digest}
    r = api.c.put(f"/api/v1/albums/{a}/pages/{p}/shots/{s}", content=make_jpeg(), headers=headers)
    assert r.status_code == 400


def test_uppercase_hash_header_is_accepted(api):
    data = make_jpeg(1)
    assert api.put_shot(new_id(), new_id(), new_id(), data, digest=sha(data).upper()).status_code == 201


@pytest.mark.parametrize(
    "data",
    [
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
        b"\xff\xd8\xff\xe0" + b"not really" + b"\xff\xd9",  # starts like a JPEG, but is not one
        b"",
    ],
)
def test_non_jpeg_is_400_and_stores_nothing(api, client, data):
    a, p, s = new_id(), new_id(), new_id()
    r = api.put_shot(a, p, s, data)
    assert r.status_code == 400
    assert r.json()["error"] == "not_jpeg"
    assert api.get(f"/albums/{a}").status_code == 404
    assert not any(f.is_file() for f in client.layout.albums.rglob("*"))


def test_png_is_400(api):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    assert api.put_shot(new_id(), new_id(), new_id(), buf.getvalue()).status_code == 400


def test_over_size_body_is_413(tmp_path, clock):
    from fastapi.testclient import TestClient

    from albumserver.api import create_app
    from albumserver.auth import create_token
    from albumserver.config import Settings

    settings = Settings(data_dir=tmp_path / "d", max_body_mb=0.01)  # about 10 kB
    app = create_app(settings, clock=clock)
    token = create_token(app.state.store, "t")
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        noise = Image.frombytes("RGB", (200, 200), bytes((i * 7919) % 251 for i in range(200 * 200 * 3)))
        buf = io.BytesIO()
        noise.save(buf, "JPEG", quality=95)
        big = buf.getvalue()
        assert len(big) > settings.max_body_bytes
        api = Api(c)
        a, p, s = new_id(), new_id(), new_id()
        r = api.put_shot(a, p, s, big)
        assert r.status_code == 413
        # Without a Content-Length, the limit is enforced while streaming.
        r = c.put(
            f"/api/v1/albums/{a}/pages/{p}/shots/{s}",
            content=iter([big[:5000], big[5000:]]),
            headers={"X-Content-SHA256": sha(big)},
        )
        assert r.status_code == 413
        assert api.get(f"/albums/{a}").status_code == 404
        assert not any(f.is_file() for f in app.state.layout.albums.rglob("*"))
        # Metadata bodies are limited too.
        r = c.put(f"/api/v1/albums/{a}", content=b"x" * 20000, headers={"Content-Type": "application/json"})
        assert r.status_code == 413
        small = make_jpeg(2)
        assert api.put_shot(a, p, s, small).status_code == 201


def test_shot_for_unknown_album_and_page_creates_them(api):
    a, p1, p2, s1, s2 = (new_id() for _ in range(5))
    assert api.put_shot(a, p1, s1, make_jpeg(1)).status_code == 201
    got = api.get(f"/albums/{a}").json()
    assert got["name"] == "Untitled"
    assert got["page_size"] is None
    assert got["pages"] == [{"id": p1, "shots": 1}]
    api.put_shot(a, p2, s2, make_jpeg(2))
    assert [p["id"] for p in api.get(f"/albums/{a}").json()["pages"]] == [p1, p2]
    # Metadata arriving later takes over without losing the pages.
    api.put_album(a, [p1, p2], name="Real name")
    assert api.get(f"/albums/{a}").json()["name"] == "Real name"


def test_shot_for_page_of_another_album_is_409(api):
    a, b, p = new_id(), new_id(), new_id()
    api.put_shot(a, p, new_id(), make_jpeg(1))
    assert api.put_shot(b, p, new_id(), make_jpeg(2)).status_code == 409


def test_exif_time_taken(api):
    a, p = new_id(), new_id()
    s1, s2, s3 = new_id(), new_id(), new_id()
    with_offset = make_jpeg(1, exif={0x9003: "2026:10:03 18:12:40", 0x9011: "-07:00"})
    api.put_shot(a, p, s1, with_offset)
    no_exif = make_jpeg(2)
    api.put_shot(a, p, s2, no_exif)
    early = make_jpeg(3, exif={0x9003: "2020:01:01 00:00:00", 0x9011: "+00:00"})
    api.put_shot(a, p, s3, early)
    shots = api.get(f"/albums/{a}/pages/{p}").json()["shots"]
    # in the order taken
    assert [s["id"] for s in shots] == [s3, s1, s2]
    assert shots[0]["taken"] == "2020-01-01T00:00:00.000Z"
    assert shots[1]["taken"] == "2026-10-04T01:12:40.000Z"
    assert shots[2]["taken"] == "2026-10-04T12:00:00.000Z"  # received


def test_25_shots_allowed_26th_refused_with_422(api, client):
    a, p = new_id(), new_id()
    for i in range(25):
        assert api.put_shot(a, p, new_id(), make_jpeg(i)).status_code == 201
    s = new_id()
    r = api.put_shot(a, p, s, make_jpeg(99))
    assert r.status_code == 422
    assert r.json()["error"] == "max_shots"
    assert len(api.get(f"/albums/{a}/pages/{p}").json()["shots"]) == 25
    assert not client.layout.photo(a, p, s).exists()
    # a repeat of a stored shot is still fine at the limit
    first = api.get(f"/albums/{a}/pages/{p}").json()["shots"][0]
    assert api.put_shot(a, p, first["id"], make_jpeg(0), digest=first["sha256"]).status_code == 200


def test_shot_that_would_be_the_501st_page_is_422(api):
    a = new_id()
    pages = [new_id() for _ in range(500)]
    api.put_album(a, pages)
    assert api.put_shot(a, pages[-1], new_id(), make_jpeg(1)).status_code == 201
    r = api.put_shot(a, new_id(), new_id(), make_jpeg(2))
    assert r.status_code == 422
    assert r.json()["error"] == "max_pages"
    assert len(api.get(f"/albums/{a}").json()["pages"]) == 500


def test_two_concurrent_uploads_at_the_25_shot_boundary(api, client):
    a, p = new_id(), new_id()
    for i in range(24):
        api.put_shot(a, p, new_id(), make_jpeg(i))
    codes = []
    barrier = threading.Barrier(2)

    def upload(seed):
        data = make_jpeg(100 + seed)
        barrier.wait()
        codes.append(api.put_shot(a, p, new_id(), data).status_code)

    threads = [threading.Thread(target=upload, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [201, 422]
    assert len(api.get(f"/albums/{a}/pages/{p}").json()["shots"]) == 25
    assert len(list(client.layout.photos(a, p).glob("*.jpg"))) == 25


# -- retrieval ----------------------------------------------------------------


def test_get_shot_byte_identical_with_headers(api):
    a, p, s = new_id(), new_id(), new_id()
    data = make_jpeg(7, size=(800, 600))
    api.put_shot(a, p, s, data)
    r = api.get(f"/albums/{a}/pages/{p}/shots/{s}")
    assert r.status_code == 200
    assert r.content == data
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-length"] == str(len(data))
    assert r.headers["x-content-sha256"] == sha(data)
    assert r.headers["etag"] == f'"{sha(data)}"'


def test_get_shot_404s(api):
    a, p, s = new_id(), new_id(), new_id()
    api.put_shot(a, p, s, make_jpeg())
    assert api.get(f"/albums/{a}/pages/{p}/shots/{new_id()}").status_code == 404
    assert api.get(f"/albums/{a}/pages/{new_id()}/shots/{s}").status_code == 404
    assert api.get(f"/albums/{new_id()}/pages/{p}/shots/{s}").status_code == 404
    assert api.get(f"/albums/{a}/pages/{p}/shots/{s}?size=huge").status_code == 400


def test_etag_and_304(api):
    a, p, s = new_id(), new_id(), new_id()
    data = make_jpeg(7)
    api.put_shot(a, p, s, data)
    etag = f'"{sha(data)}"'
    r = api.get(f"/albums/{a}/pages/{p}/shots/{s}", headers={"If-None-Match": etag})
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["etag"] == etag
    assert api.get(f"/albums/{a}/pages/{p}/shots/{s}", headers={"If-None-Match": '"other"'}).status_code == 200


def test_thumb_size_orientation_cache_and_etag(api, client):
    a, p, s = new_id(), new_id(), new_id()
    # 1200 x 800 landscape pixels, EXIF orientation 6: shown rotated to portrait.
    im = Image.new("RGB", (1200, 800), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, 1200, 100))  # a red band along the stored top
    ex = Image.Exif()
    ex[0x0112] = 6
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex.tobytes(), quality=90)
    data = buf.getvalue()
    api.put_shot(a, p, s, data)

    r = api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["etag"] == f'"{sha(data)}-thumb"'
    assert "x-content-sha256" not in r.headers
    th = Image.open(io.BytesIO(r.content))
    assert th.size == (213, 320)  # portrait, long side 320
    # orientation 6 turns the stored top to the right-hand side
    right = th.getpixel((th.width - 3, th.height // 2))
    left = th.getpixel((2, th.height // 2))
    assert right[0] > 200 and right[2] < 80
    assert left[2] > 200

    cached = client.layout.thumb(a, s)
    assert cached.is_file()
    mtime = cached.stat().st_mtime_ns
    r2 = api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"})
    assert r2.content == r.content
    assert cached.stat().st_mtime_ns == mtime  # reused, not remade
    r3 = api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"}, headers={"If-None-Match": f'"{sha(data)}-thumb"'})
    assert r3.status_code == 304


def test_small_shot_thumb_is_not_enlarged(api):
    a, p, s = new_id(), new_id(), new_id()
    api.put_shot(a, p, s, make_jpeg(1, size=(100, 50)))
    r = api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"})
    assert Image.open(io.BytesIO(r.content)).size == (100, 50)


# -- deletion -----------------------------------------------------------------


def test_delete_shot_removes_file_and_preview_and_is_idempotent(api, client):
    a, p, s, keep = new_id(), new_id(), new_id(), new_id()
    api.put_shot(a, p, s, make_jpeg(1))
    api.put_shot(a, p, keep, make_jpeg(2))
    api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"})
    assert client.layout.thumb(a, s).exists()
    assert api.delete(f"/albums/{a}/pages/{p}/shots/{s}").status_code == 204
    assert not client.layout.photo(a, p, s).exists()
    assert not client.layout.thumb(a, s).exists()
    assert [x["id"] for x in api.get(f"/albums/{a}/pages/{p}").json()["shots"]] == [keep]
    assert api.get(f"/albums/{a}/pages/{p}/shots/{s}").status_code == 404
    assert api.delete(f"/albums/{a}/pages/{p}/shots/{s}").status_code == 204
    assert api.delete(f"/albums/{new_id()}/pages/{new_id()}/shots/{new_id()}").status_code == 204
    # a shot id deleted from the wrong page is left alone
    assert api.delete(f"/albums/{a}/pages/{new_id()}/shots/{keep}").status_code == 204
    assert client.layout.photo(a, p, keep).exists()


def test_deleted_shot_id_can_be_uploaded_again(api):
    a, p, s = new_id(), new_id(), new_id()
    api.put_shot(a, p, s, make_jpeg(1))
    api.delete(f"/albums/{a}/pages/{p}/shots/{s}")
    assert api.put_shot(a, p, s, make_jpeg(2)).status_code == 201


def test_delete_page_removes_shots_previews_and_outputs(api, client):
    a, p, other, s = new_id(), new_id(), new_id(), new_id()
    api.put_album(a, [p, other])
    api.put_shot(a, p, s, make_jpeg(1))
    api.put_shot(a, other, new_id(), make_jpeg(2))
    api.get(f"/albums/{a}/pages/{p}/shots/{s}", params={"size": "thumb"})
    out = client.layout.outputs(a, p) / "run-x"
    out.mkdir(parents=True)
    (out / "report.json").write_text("{}")
    assert api.delete(f"/albums/{a}/pages/{p}").status_code == 204
    assert api.get(f"/albums/{a}/pages/{p}").status_code == 404
    assert [x["id"] for x in api.get(f"/albums/{a}").json()["pages"]] == [other]
    assert not client.layout.photos(a, p).exists()
    assert not client.layout.thumb(a, s).exists()
    assert not client.layout.outputs(a, p).exists()
    assert api.get(f"/albums/{a}/pages/{p}/shots/{s}").status_code == 404
    assert api.delete(f"/albums/{a}/pages/{p}").status_code == 204


def test_delete_album_removes_everything_and_is_idempotent(api, client):
    a, b, p = new_id(), new_id(), new_id()
    api.put_shot(a, p, new_id(), make_jpeg(1))
    api.put_shot(b, new_id(), new_id(), make_jpeg(2))
    client.layout.pdf(a).write_bytes(b"%PDF")
    assert api.delete(f"/albums/{a}").status_code == 204
    assert api.get(f"/albums/{a}").status_code == 404
    assert not client.layout.album(a).exists()
    assert [x["id"] for x in api.get("/albums").json()["albums"]] == [b]
    assert api.delete(f"/albums/{a}").status_code == 204
