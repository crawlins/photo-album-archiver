import json

import pytest

from albumscan.client import Client, HttpError, Unreachable, detail_of


def test_requests_carry_the_token_and_parse_json(server):
    server.json["/api/v1/albums"] = {"albums": [{"id": "a", "name": "A"}]}
    c = Client(server.url + "/", "secret")
    assert c.albums() == [{"id": "a", "name": "A"}]
    method, path, headers, _ = server.requests[-1]
    assert (method, path, headers["Authorization"]) == ("GET", "/api/v1/albums", "Bearer secret")


def test_errors_carry_the_servers_detail(server):
    server.answer("GET", "/api/v1/ping", 401, {"detail": "invalid token"})
    with pytest.raises(HttpError) as e:
        Client(server.url, "bad").ping()
    assert e.value.status == 401 and e.value.detail == "invalid token"


def test_upload_headers(server):
    r = Client(server.url, "t").put_shot("a", "p", "s", b"\xff\xd8jpeg", "abc")
    assert r.status == 201
    method, path, headers, body = server.requests[-1]
    assert (method, path, body) == ("PUT", "/api/v1/albums/a/pages/p/shots/s", b"\xff\xd8jpeg")
    assert headers["X-Content-SHA256"] == "abc" and headers["Content-Type"] == "image/jpeg"


def test_album_metadata_is_json(server):
    Client(server.url, "t").put_album("a", {"name": "N", "pages": []})
    _, _, headers, body = server.requests[-1]
    assert json.loads(body) == {"name": "N", "pages": []} and headers["Content-Type"] == "application/json"


def test_unreachable():
    with pytest.raises(Unreachable):
        Client("http://127.0.0.1:9", "t", timeout=2).ping()
    with pytest.raises(Unreachable):
        Client("not a url", "t").ping()


@pytest.mark.parametrize(
    "body, text",
    [(b'{"error": "limit", "message": "too many shots"}', "too many shots"), (b'{"detail": "too many shots"}', "too many shots"), (b'{"detail": [{"loc": 1}]}', '[{"loc": 1}]'), (b"plain\nmore", "plain"), (b"", "")],
)
def test_detail_of(body, text):
    assert detail_of(body) == text
