"""Tokens: created once, stored as a hash, listed without the token, revocable; 401 otherwise."""

import logging


from albumserver.auth import Authenticator, hash_token
from albumserver.cli import main
from conftest import TOKEN_NAME, make_jpeg, new_id


def test_token_cli_create_list_revoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALBUMSERVER_DATA_DIR", str(tmp_path / "data"))
    assert main(["token", "create", "pixel-8"]) == 0
    out = capsys.readouterr()
    token = out.out.strip()
    assert len(token) >= 43  # 32 bytes, base64
    assert "shown only now" in out.err

    from albumserver.store import Store

    store = Store(tmp_path / "data" / "index.sqlite")
    rows = store.token_hashes()
    assert rows == [("pixel-8", hash_token(token))]
    raw = (tmp_path / "data" / "index.sqlite").read_bytes()
    assert token.encode() not in raw  # only the hash is stored

    assert main(["token", "create", "pixel-8"]) == 1  # names are unique
    capsys.readouterr()
    assert main(["token", "list"]) == 0
    listing = capsys.readouterr().out
    assert "pixel-8" in listing and "never" in listing
    assert token not in listing and hash_token(token) not in listing

    assert main(["token", "revoke", "pixel-8"]) == 0
    assert store.token_hashes() == []
    assert main(["token", "revoke", "pixel-8"]) == 1
    store.close()


def test_every_route_needs_a_token(client, token):
    a, p, s = new_id(), new_id(), new_id()
    paths = [
        ("GET", "/api/v1/ping"),
        ("GET", "/api/v1/albums"),
        ("GET", f"/api/v1/albums/{a}"),
        ("PUT", f"/api/v1/albums/{a}"),
        ("DELETE", f"/api/v1/albums/{a}"),
        ("GET", f"/api/v1/albums/{a}/pages/{p}"),
        ("DELETE", f"/api/v1/albums/{a}/pages/{p}"),
        ("PUT", f"/api/v1/albums/{a}/pages/{p}/shots/{s}"),
        ("GET", f"/api/v1/albums/{a}/pages/{p}/shots/{s}"),
        ("DELETE", f"/api/v1/albums/{a}/pages/{p}/shots/{s}"),
        ("GET", f"/api/v1/albums/{a}/status"),
        ("GET", f"/api/v1/albums/{a}/pdf"),
        ("GET", f"/api/v1/albums/{a}/pages/{p}/print"),
        ("GET", "/api/v1/no-such-route"),
    ]
    for auth in (None, "Bearer wrong", f"Basic {token}", "Bearer ", token):
        for method, path in paths:
            headers = {"Authorization": auth} if auth is not None else {"Authorization": ""}
            r = client.request(method, path, headers=headers, content=make_jpeg() if method == "PUT" else None)
            assert r.status_code == 401, (auth, method, path)
            assert r.json()["error"] == "unauthorized"
            assert r.headers["www-authenticate"] == "Bearer"
    assert client.store.list_albums() == []


def test_revoked_token_gets_401(client, store):
    assert client.get("/api/v1/ping").status_code == 200
    store.revoke_token(TOKEN_NAME)
    assert client.get("/api/v1/ping").status_code == 401


def test_each_token_works_on_its_own(client, store):
    from albumserver.auth import create_token

    second = create_token(store, "tablet")
    assert client.get("/api/v1/ping", headers={"Authorization": f"Bearer {second}"}).status_code == 200
    store.revoke_token(TOKEN_NAME)
    assert client.get("/api/v1/ping", headers={"Authorization": f"Bearer {second}"}).status_code == 200
    assert client.get("/api/v1/ping").status_code == 401


def test_rejections_are_logged_without_the_token(client, token, caplog):
    caplog.set_level(logging.INFO, logger="albumserver")
    client.get("/api/v1/ping", headers={"Authorization": "Bearer guessed-token-value"})
    client.get("/api/v1/albums")
    client.put(f"/api/v1/albums/{new_id()}/pages/{new_id()}/shots/{new_id()}", content=make_jpeg(), headers={"X-Content-SHA256": "0" * 64})
    text = caplog.text
    assert "rejected request from testclient" in text
    assert "invalid token" in text
    assert "guessed-token-value" not in text
    assert token not in text
    assert "Authorization" not in text and "Bearer" not in text
    # one line per request: method, path, status, size, duration
    assert "GET /api/v1/ping 401" in text
    assert "GET /api/v1/albums 200" in text


def test_last_use_is_recorded(client, store, clock):
    assert store.list_tokens()[0]["last_used"] is None
    client.get("/api/v1/ping")
    assert store.list_tokens()[0]["last_used"] == "2026-10-04T12:00:00.000Z"


def test_authenticator_compares_every_hash(store, monkeypatch):
    import albumserver.auth as auth

    from albumserver.auth import create_token

    tokens = [create_token(store, f"phone-{i}") for i in range(3)]
    calls = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    a = Authenticator(store)
    assert a.check(f"Bearer {tokens[0]}") == "phone-0"
    assert len(calls) == 3  # no early exit on the first match
    calls.clear()
    assert a.check("Bearer nope") is None
    assert len(calls) == 3
