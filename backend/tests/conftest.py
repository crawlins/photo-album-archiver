"""Shared fixtures for the album server tests."""

from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

TOKEN_NAME = "test-phone"


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.t = start or datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def new_id() -> str:
    return str(uuid.uuid4())


def make_jpeg(seed: int = 0, size=(64, 48), exif: dict | None = None, dpi=None) -> bytes:
    """A small real JPEG; ``exif`` maps EXIF tag numbers (base or Exif IFD) to values."""
    im = Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256))
    for x in range(0, size[0], 8):  # something to compress, and a marker of which way is up
        im.putpixel((x, 0), (255, 255, 255))
    kw = {"quality": 90}
    if exif:
        ex = Image.Exif()
        ifd = ex.get_ifd(0x8769)
        for tag, value in exif.items():
            if tag >= 0x9000:
                ifd[tag] = value
            else:
                ex[tag] = value
        kw["exif"] = ex.tobytes()
    if dpi:
        kw["dpi"] = (dpi, dpi)
    buf = io.BytesIO()
    im.save(buf, "JPEG", **kw)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings(tmp_path):
    from albumserver.config import Settings

    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def store(settings, clock):
    from albumserver.files import Layout
    from albumserver.store import Store

    layout = Layout(settings.data_dir)
    layout.create()
    s = Store(layout.index, clock, settings.max_pages, settings.max_shots)
    yield s
    s.close()


@pytest.fixture
def token(store):
    from albumserver.auth import create_token

    return create_token(store, TOKEN_NAME)


@pytest.fixture
def client(settings, store, clock, token):
    from fastapi.testclient import TestClient

    from albumserver.api import create_app

    app = create_app(settings, store, clock)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as c:
        c.layout = app.state.layout
        c.store = store
        yield c


class Api:
    """Short calls for the app's requests."""

    def __init__(self, client):
        self.c = client

    def put_album(self, album_id, pages, name="Rawlins family 1962-1968", page_size="8.5x11in", created="2026-10-04T01:09:19Z", **extra):
        return self.c.put(f"/api/v1/albums/{album_id}", json={"name": name, "page_size": page_size, "pages": pages, "created": created, **extra})

    def put_shot(self, album_id, page_id, shot_id, data, digest=None):
        return self.c.put(
            f"/api/v1/albums/{album_id}/pages/{page_id}/shots/{shot_id}",
            content=data,
            headers={"X-Content-SHA256": digest or sha(data), "Content-Type": "image/jpeg"},
        )

    def get(self, path, **kw):
        return self.c.get(f"/api/v1{path}", **kw)

    def delete(self, path):
        return self.c.delete(f"/api/v1{path}")


@pytest.fixture
def api(client):
    return Api(client)
