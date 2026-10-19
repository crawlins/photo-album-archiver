"""A session scanning into a real ``albumserver serve``, through the outbox, and the page processed in scan mode."""

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from albumscan.client import Client
from albumscan.outbox import Outbox, Sender
from albumscan.session import Session
from albumscan.shot import ScanInfo, encode

REPO = Path(__file__).resolve().parents[2]


def _albumserver() -> str | None:
    # Not ALBUMSERVER_*: the server reads every such variable as a setting and refuses unknown ones.
    for c in (os.environ.get("ALBUMSCAN_TEST_ALBUMSERVER"), REPO / "backend" / ".venv" / "bin" / "albumserver"):
        if c and Path(c).exists():
            return str(c)
    return None


BIN = _albumserver()
pytestmark = pytest.mark.skipif(BIN is None, reason="albumserver is not installed (pip install -e backend into backend/.venv)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def real_server(tmp_path):
    port = _free_port()
    env = {**os.environ, "ALBUMSERVER_DATA_DIR": str(tmp_path / "data"), "ALBUMSERVER_PORT": str(port), "ALBUMSERVER_IDLE_PERIOD_S": "0"}
    token = subprocess.run([BIN, "token", "create", "scanner"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    proc = subprocess.Popen([BIN, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = Client(f"http://127.0.0.1:{port}", token, timeout=30)
    try:
        for _ in range(300):
            try:
                client.ping()
                break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("albumserver did not start")
        yield client
    finally:
        proc.terminate()
        proc.wait(30)


def _scan(seed: int) -> Image.Image:
    """A cream page with a few coloured prints on the dark of an open lid."""
    im = Image.new("RGB", (1300, 1060), (36, 36, 38))
    d = ImageDraw.Draw(im)
    d.rectangle((150, 150, 1149, 909), fill=(236, 228, 210))
    for k, (x, y) in enumerate([(220, 220), (680, 220), (220, 560), (680, 560)]):
        c = ((seed * 50 + 80 * k) % 200 + 30, (90 * k + 40) % 200 + 30, (seed * 30 + 140) % 200 + 30)
        d.rectangle((x, y, x + 400, y + 300), fill=c)
        d.ellipse((x + 60, y + 50, x + 200, y + 190), fill=(250 - c[0], 250 - c[1], 250 - c[2]))
    return im


def test_scans_reach_the_server_and_are_processed_as_scans(real_server, tmp_path):
    box = Outbox(tmp_path / "outbox")
    info = ScanInfo("Canon", "LiDE 220", 300, datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc))
    s = Session.new(box, "Scanned", "5x3.8in")
    first = s.add_scan(encode(_scan(1), 0, info))
    data1 = first.path.read_bytes()
    assert s.next_page()
    second = s.add_scan(encode(_scan(2), 90, info))
    gone = s.add_scan(encode(_scan(3), 0, info))
    s.delete_shot(gone.id)  # never sent

    idle = threading.Event()
    sender = Sender(box, real_server, on_change=lambda x: x.state == "idle" and idle.set())
    sender.start()
    try:
        assert idle.wait(60), sender.message
    finally:
        sender.stop()
    assert box.all() == [] and not first.path.exists()

    album = real_server.album(s.album_id)
    assert album["name"] == "Scanned" and [p["id"] for p in album["pages"]] == s.pages
    assert [p["shots"] for p in album["pages"]] == [1, 1]
    page1 = real_server.page(s.album_id, s.pages[0])["shots"]
    assert page1[0]["id"] == first.id and page1[0]["sha256"] == hashlib.sha256(data1).hexdigest()
    assert page1[0]["taken"].startswith("2026-10-19T12:00:00")
    assert real_server.page(s.album_id, s.pages[1])["shots"][0]["id"] == second.id

    # Reopening carries on at the last page.
    again = Session.open(box, real_server, s.album_id)
    assert again.page_id == s.pages[1] and [x.id for x in again.shots] == [second.id]

    # The server's page run treats the page as a scan: no photo warnings.
    end = time.monotonic() + 180
    while time.monotonic() < end:
        st = real_server.status(s.album_id)
        if st["pages"][0]["state"] in ("done", "failed"):
            break
        time.sleep(0.5)
    page = st["pages"][0]
    assert page["state"] == "done", page
    assert not {w["code"] for w in page.get("warnings", [])} & {"glare", "single_coverage", "shadow"}
    reports = [json.loads(p.read_text()) for p in (tmp_path / "data").rglob("report.json")]
    sources = [pg["page_report"]["source"] for r in reports for pg in r.get("pages", []) if pg.get("page_report")]
    assert sources and set(sources) == {"scan"}
    box.close()
