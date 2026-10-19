"""Scans and requests kept until the server has them, sent in order by one thread.

One SQLite table of operations, ordered by ``seq``, and a folder of shot
files. A shot's file is written under a temporary name, flushed and renamed
before its row is committed, so a row never points at a missing or partial
file; files with no row are removed at startup.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .client import Client, Response, Unreachable, detail_of

PUT_SHOT, DELETE_SHOT, ALBUM_META = "put_shot", "delete_shot", "album_meta"
DONE, RETRY, AUTH, BLOCKED = "done", "retry", "auth", "blocked"
MAX_DELAY = 60.0


def classify(status: int) -> str:
    if status in (200, 201, 204):
        return DONE
    if status >= 500 or status == 429:
        return RETRY
    if status == 401:
        return AUTH
    return BLOCKED


@dataclass(frozen=True)
class Op:
    seq: int
    kind: str
    album_id: str
    page_id: str | None
    shot_id: str | None
    sha256: str | None
    path: str | None
    body: str | None

    def describe(self) -> str:
        return {
            PUT_SHOT: f"upload of a scan to page {self.page_id}",
            DELETE_SHOT: f"deletion of a scan from page {self.page_id}",
            ALBUM_META: "album details",
        }[self.kind]


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Outbox:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.shots = self.folder / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.folder / "outbox.db", check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS ops (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, album_id TEXT NOT NULL,"
            " page_id TEXT, shot_id TEXT, sha256 TEXT, path TEXT, body TEXT, created REAL NOT NULL)"
        )
        self.in_flight: int | None = None  # seq the sender is sending now
        self._cleanup()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _cleanup(self) -> None:
        known = {r[0] for r in self._db.execute("SELECT path FROM ops WHERE path IS NOT NULL")}
        for f in self.shots.iterdir():
            if f.name not in known:
                f.unlink(missing_ok=True)

    def _rows(self, sql: str, args=()) -> list[Op]:
        cols = "seq, kind, album_id, page_id, shot_id, sha256, path, body"
        return [Op(*r) for r in self._db.execute(f"SELECT {cols} FROM ops {sql}", args)]

    def _insert(self, kind, album_id, page_id=None, shot_id=None, sha256=None, path=None, body=None) -> None:
        self._db.execute(
            "INSERT INTO ops (kind, album_id, page_id, shot_id, sha256, path, body, created) VALUES (?,?,?,?,?,?,?,?)",
            (kind, album_id, page_id, shot_id, sha256, path, body, time.time()),
        )

    # -- adding ----------------------------------------------------------------------

    def add_shot(self, album_id: str, page_id: str, shot_id: str, data: bytes, sha256: str) -> Path:
        """Keep the shot on disk and queue its upload; returns its file."""
        name = f"{shot_id}.jpg"
        final = self.shots / name
        tmp = self.shots / f".{uuid.uuid4().hex}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
        _fsync_dir(self.shots)
        with self._lock:
            self._insert(PUT_SHOT, album_id, page_id, shot_id, sha256, name)
        return final

    def put_album(self, album_id: str, meta: dict) -> None:
        """Queue the album's metadata, replacing any not yet sent."""
        with self._lock:
            self._db.execute("BEGIN")
            self._db.execute("DELETE FROM ops WHERE kind = ? AND album_id = ? AND seq IS NOT ?", (ALBUM_META, album_id, self.in_flight))
            self._insert(ALBUM_META, album_id, body=json.dumps(meta))
            self._db.execute("COMMIT")

    def delete_shot(self, album_id: str, page_id: str, shot_id: str) -> None:
        """Drop the shot's upload if it has not gone, else queue its deletion on the server."""
        with self._lock:
            pending = self._rows("WHERE kind = ? AND shot_id = ?", (PUT_SHOT, shot_id))
            if pending and pending[0].seq != self.in_flight:
                self._db.execute("DELETE FROM ops WHERE seq = ?", (pending[0].seq,))
                (self.shots / pending[0].path).unlink(missing_ok=True)
                return
            self._insert(DELETE_SHOT, album_id, page_id, shot_id)

    # -- reading ---------------------------------------------------------------------

    def head(self) -> Op | None:
        with self._lock:
            rows = self._rows("ORDER BY seq LIMIT 1")
            return rows[0] if rows else None

    def all(self) -> list[Op]:
        with self._lock:
            return self._rows("ORDER BY seq")

    def pending_shots(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM ops WHERE kind = ?", (PUT_SHOT,)).fetchone()[0]

    def unsent_shots(self, album_id: str, page_id: str) -> list[Op]:
        with self._lock:
            return self._rows("WHERE kind = ? AND album_id = ? AND page_id = ? ORDER BY seq", (PUT_SHOT, album_id, page_id))

    def unsent_meta(self, album_id: str) -> dict | None:
        with self._lock:
            rows = self._rows("WHERE kind = ? AND album_id = ? ORDER BY seq DESC LIMIT 1", (ALBUM_META, album_id))
            return json.loads(rows[0].body) if rows else None

    def take(self) -> Op | None:
        """The head, marked as being sent so that deleting its shot queues a deletion instead."""
        with self._lock:
            rows = self._rows("ORDER BY seq LIMIT 1")
            self.in_flight = rows[0].seq if rows else None
            return rows[0] if rows else None

    def release(self) -> None:
        with self._lock:
            self.in_flight = None

    def file(self, op: Op) -> Path:
        return self.shots / op.path

    # -- sending -----------------------------------------------------------------------

    def remove(self, seq: int) -> None:
        """The server has it, or the user gave it up: forget it and its file."""
        with self._lock:
            rows = self._rows("WHERE seq = ?", (seq,))
            self._db.execute("DELETE FROM ops WHERE seq = ?", (seq,))
            if rows and rows[0].path:
                (self.shots / rows[0].path).unlink(missing_ok=True)


def send(client: Client, outbox: Outbox, op: Op) -> Response:
    """Send one operation (Unreachable propagates)."""
    if op.kind == PUT_SHOT:
        return client.put_shot(op.album_id, op.page_id, op.shot_id, outbox.file(op).read_bytes(), op.sha256)
    if op.kind == DELETE_SHOT:
        return client.delete_shot(op.album_id, op.page_id, op.shot_id)
    return client.put_album(op.album_id, json.loads(op.body))


class Sender:
    """Sends the outbox's head until it is empty; one thread, never reordering.

    ``state`` is ``idle``, ``sending``, ``waiting`` (to retry), ``auth``
    (the token was refused) or ``blocked`` (the server refused the head;
    ``message`` says why). The last two last until ``unblock`` or ``discard``.
    """

    def __init__(self, outbox: Outbox, client: Client | None, on_change: Callable[["Sender"], None] | None = None, sleep=None):
        self.outbox = outbox
        self.client = client
        self.on_change = on_change
        self.state = "idle"
        self.message = ""
        self.blocked_op: Op | None = None
        self._delay = 1.0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._wake.wait(s))
        self._thread: threading.Thread | None = None

    def _set(self, state: str, message: str = "") -> None:
        self.state, self.message = state, message
        if self.on_change:
            self.on_change(self)

    def step(self) -> str | None:
        """One attempt at the head; returns its outcome, or None when there is nothing to do."""
        if self.state in (AUTH, BLOCKED) or self.client is None:
            return None
        op = self.outbox.take()
        if op is None:
            if self.state != "idle":
                self._set("idle")
            return None
        self._set("sending")
        status, detail = None, ""
        try:
            r = send(self.client, self.outbox, op)
            status, outcome, detail = r.status, classify(r.status), detail_of(r.body)
        except Unreachable as e:
            outcome, detail = RETRY, f"server unreachable: {e}"
        except FileNotFoundError:
            outcome, detail = BLOCKED, "the scan's file is missing from the outbox"
        finally:
            self.outbox.release()
        if outcome == DONE:
            self.outbox.remove(op.seq)
            self._delay = 1.0
            self._set("sending")
        elif outcome == RETRY:
            self._set("waiting", detail if status is None else f"the server answered {status}; retrying")
        else:
            self.blocked_op = op
            if outcome == AUTH:
                self._set(AUTH, "the server refused the token")
            else:
                why = f"{status}: {detail}" if status and detail else str(status or detail)
                self._set(BLOCKED, f"the server refused the {op.describe()} ({why})")
        return outcome

    def run(self) -> None:
        while not self._stop.is_set():
            outcome = self.step()
            if outcome == RETRY:
                self._sleep(self._delay)
                self._delay = min(MAX_DELAY, self._delay * 2)
                self._wake.clear()
            elif outcome is None:
                self._wake.wait()
                self._wake.clear()

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="albumscan-sender", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=10)

    def wake(self) -> None:
        """New work, or retry now."""
        self._delay = 1.0
        self._wake.set()

    def unblock(self, client: Client | None = None) -> None:
        """Try the head again, with a new client (token) if given."""
        if client is not None:
            self.client = client
        self.blocked_op = None
        self._set("idle")
        self.wake()

    def discard(self) -> None:
        """Give up the request the server refused, and carry on."""
        if self.blocked_op is not None:
            self.outbox.remove(self.blocked_op.seq)
        self.unblock()
