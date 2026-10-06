"""End to end: real child processes, the real album step, and ``albumserver serve`` itself."""

from __future__ import annotations

import io
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pikepdf
import pytest

from albumproc.synth import make_page, make_shots, make_table
from albumserver.worker import ProcessRunner, Worker
from conftest import new_id, sha

import server_targets


def _wait(cond, timeout=120.0, every=0.1):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(every)
    return False


def _page_shots(seed: int) -> list[bytes]:
    """Two small JPEG shots of a synthetic 1000 x 760 page, as in the album tests."""
    rng = np.random.default_rng(seed)
    pw, ph, m = 1000, 760, 600
    scene = make_table(rng, pw + 2 * m, ph + 2 * m)
    scene[m : m + ph, m : m + pw] = make_page(rng, pw, ph)
    shots = make_shots(rng, scene, (m, m, pw, ph), [(0.5, 0.5), (0.52, 0.48)], fill=0.72, img_size=(900, 680))
    return [cv2.imencode(".jpg", s.image, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes() for s in shots]


# -- child processes ------------------------------------------------------------------


def test_process_run_reports_success_failure_and_can_be_killed(tmp_path):
    for name in ("w", "o"):
        (tmp_path / name).mkdir()
    ok = ProcessRunner(server_targets.ok).start(tmp_path / "w", tmp_path / "o")
    assert _wait(lambda: ok.poll() is not None, 60)
    assert ok.poll() == (True, None)

    bad = ProcessRunner(server_targets.fail).start(tmp_path / "w", tmp_path / "o")
    assert _wait(lambda: bad.poll() is not None, 60)
    assert bad.poll() == (False, "cannot read page/x.jpg")

    slow = ProcessRunner(server_targets.slow).start(tmp_path / "w", tmp_path / "o")
    time.sleep(0.5)
    assert slow.poll() is None
    slow.kill()
    assert not slow.proc.is_alive()


def test_upload_to_pdf_with_the_real_album_step(api, client, settings):
    """Shots in through the API, the album step in a child process, the PDF out through the API."""
    a, p = new_id(), new_id()
    assert api.put_album(a, [p], name="Synthetic", page_size="5x3.8in").status_code == 200
    for shot in _page_shots(101):
        assert api.put_shot(a, p, new_id(), shot).status_code == 201
    api.put_album(a, [p, new_id()], name="Synthetic", page_size="5x3.8in")  # "Next page": run page 1 now

    worker = Worker(settings, client.store, client.layout)  # real clock, real child processes
    try:
        def settled():
            worker.step()
            return api.get(f"/albums/{a}/status").json()["pdf"] == "current"

        assert _wait(settled, timeout=300, every=0.2), api.get(f"/albums/{a}/status").json()
    finally:
        worker.stop()
    st = api.get(f"/albums/{a}/status").json()
    assert st["pages"][0]["state"] == "done", st
    r = api.get(f"/albums/{a}/pdf")
    assert r.status_code == 200
    pdf = pikepdf.open(io.BytesIO(r.content))
    assert len(pdf.pages) == 1
    w_pt, h_pt = (float(v) for v in pdf.pages[0].MediaBox[2:])
    assert sorted([round(w_pt / 72, 2), round(h_pt / 72, 2)]) == [3.8, 5.0]
    pr = api.get(f"/albums/{a}/pages/{p}/print")
    assert pr.status_code == 200 and pr.headers["content-type"] == "image/jpeg"
    assert not client.layout.work(a, p).exists()


# -- albumserver serve ------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _children(pid: int) -> list[int]:
    out = []
    for task in Path(f"/proc/{pid}/task").iterdir():
        out += [int(x) for x in (task / "children").read_text().split()]
    return out


def _gone(pid: int) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] in "ZX"
    except FileNotFoundError:
        return True


@pytest.mark.skipif(not Path("/proc/self/task").exists(), reason="needs Linux /proc")
def test_serve_warns_cleans_up_serves_and_stops_on_sigterm(tmp_path):
    data = tmp_path / "data"
    port = _free_port()
    env = {**os.environ, "ALBUMSERVER_DATA_DIR": str(data), "ALBUMSERVER_HOST": "0.0.0.0", "ALBUMSERVER_PORT": str(port)}
    exe = [sys.executable, "-m", "albumserver.cli"]
    token = subprocess.run([*exe, "token", "create", "e2e"], env=env, capture_output=True, text=True, check=True).stdout.strip()
    stale = data / "albums" / new_id() / "photos" / "x.jpg.0000.tmp"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"left by a crash")

    log = tmp_path / "server.log"
    with open(log, "wb") as err:  # a file, not a pipe: children must not hold our pipe open
        proc = subprocess.Popen([*exe, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=err)
    try:
        url = f"http://127.0.0.1:{port}/api/v1"
        headers = {"Authorization": f"Bearer {token}"}

        def up():
            try:
                return httpx.get(f"{url}/ping", headers=headers, timeout=2).status_code == 200
            except httpx.TransportError:
                return False

        assert _wait(up, timeout=60), log.read_text()
        assert httpx.get(f"{url}/ping", timeout=2).status_code == 401
        from conftest import make_jpeg

        data_bytes = make_jpeg(1)
        r = httpx.put(
            f"{url}/albums/{new_id()}/pages/{new_id()}/shots/{new_id()}",
            content=data_bytes,
            headers={**headers, "X-Content-SHA256": sha(data_bytes)},
            timeout=10,
        )
        assert r.status_code == 201
        workers = _children(proc.pid)
        assert workers, "the processing worker should be running"

        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=60) == 0
        assert _wait(lambda: all(_gone(w) for w in workers), timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    text = log.read_text()
    assert "over plain HTTP" in text and "unencrypted" in text
    assert "startup cleanup removed" in text
    assert not stale.parent.parent.exists()
    assert "processing worker started" in text and "processing worker stopped" in text
    assert "server stopped" in text
    assert token not in text
    assert "PUT /api/v1/albums/" in text and " 201 " in text


def test_serve_refuses_an_unknown_setting(tmp_path):
    env = {**os.environ, "ALBUMSERVER_DATA_DIR": str(tmp_path), "ALBUMSERVER_POTR": "1"}
    r = subprocess.run([sys.executable, "-m", "albumserver.cli", "serve"], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "ALBUMSERVER_POTR: unknown setting" in r.stderr


def test_no_warning_on_loopback(tmp_path, caplog):
    import logging

    from albumserver.cli import prepare
    from albumserver.config import Settings

    caplog.set_level(logging.INFO, logger="albumserver")
    _, store = prepare(Settings(data_dir=tmp_path))
    store.close()
    assert "plain HTTP" not in caplog.text
    _, store = prepare(Settings(data_dir=tmp_path, host="192.168.1.5"))
    store.close()
    assert "plain HTTP" in caplog.text
    caplog.clear()
    _, store = prepare(Settings(data_dir=tmp_path, host="0.0.0.0", tls_cert="c.pem", tls_key="k.pem"))
    store.close()
    assert "plain HTTP" not in caplog.text
