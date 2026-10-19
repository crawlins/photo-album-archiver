"""The album server's HTTP API, as the phone app uses it (album server spec)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass


class Unreachable(Exception):
    """No HTTP answer: the server is down, the address is wrong, or the network is."""


class HttpError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}" if detail else str(status))
        self.status = status
        self.detail = detail


def detail_of(body: bytes) -> str:
    """The server's message from an error body (``message``, or FastAPI's ``detail``), or the body's first line."""
    try:
        raw = json.loads(body)
        d = raw.get("message", raw.get("detail"))
        if isinstance(d, str):
            return d
        if d is not None:
            return json.dumps(d)
    except (ValueError, AttributeError):
        pass
    return body.decode(errors="replace").strip().splitlines()[0][:200] if body.strip() else ""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict


class Client:
    def __init__(self, base_url: str, token: str, timeout: float = 120):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> Response:
        """One request; any HTTP status is returned, only a missing answer raises (Unreachable)."""
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        try:
            req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return Response(r.status, r.read(), dict(r.headers))
        except urllib.error.HTTPError as e:
            with e:
                return Response(e.code, e.read(), dict(e.headers or {}))
        except (urllib.error.URLError, OSError, ValueError) as e:  # ValueError: a malformed URL
            raise Unreachable(str(getattr(e, "reason", e))) from e

    def _ok(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None) -> Response:
        r = self.request(method, path, body, headers)
        if not 200 <= r.status < 300:
            raise HttpError(r.status, detail_of(r.body))
        return r

    def _json(self, path: str):
        return json.loads(self._ok("GET", path).body)

    # -- reading -----------------------------------------------------------------

    def ping(self) -> None:
        self._ok("GET", "/api/v1/ping")

    def albums(self) -> list[dict]:
        return self._json("/api/v1/albums")["albums"]

    def album(self, album_id: str) -> dict:
        return self._json(f"/api/v1/albums/{album_id}")

    def page(self, album_id: str, page_id: str) -> dict:
        return self._json(f"/api/v1/albums/{album_id}/pages/{page_id}")

    def thumb(self, album_id: str, page_id: str, shot_id: str) -> bytes:
        return self._ok("GET", f"/api/v1/albums/{album_id}/pages/{page_id}/shots/{shot_id}?size=thumb").body

    def status(self, album_id: str) -> dict:
        return self._json(f"/api/v1/albums/{album_id}/status")

    def print_image(self, album_id: str, page_id: str) -> tuple[bytes, str]:
        r = self._ok("GET", f"/api/v1/albums/{album_id}/pages/{page_id}/print")
        return r.body, r.headers.get("content-type", r.headers.get("Content-Type", ""))

    # -- writing (the outbox sends these and reads the status itself) --------------

    def put_album(self, album_id: str, meta: dict) -> Response:
        return self.request("PUT", f"/api/v1/albums/{album_id}", json.dumps(meta).encode(), {"Content-Type": "application/json"})

    def put_shot(self, album_id: str, page_id: str, shot_id: str, data: bytes, sha256: str) -> Response:
        return self.request(
            "PUT",
            f"/api/v1/albums/{album_id}/pages/{page_id}/shots/{shot_id}",
            data,
            {"Content-Type": "image/jpeg", "X-Content-SHA256": sha256},
        )

    def delete_shot(self, album_id: str, page_id: str, shot_id: str) -> Response:
        return self.request("DELETE", f"/api/v1/albums/{album_id}/pages/{page_id}/shots/{shot_id}")
