"""The index: albums, pages, shots, tokens and processing state, in one SQLite file.

The database runs in write-ahead-log mode, so a crash never corrupts it, and
every write is one ``BEGIN IMMEDIATE`` transaction. That serialises writers,
which is what makes the page and shot limits race-free: the count and the
insert it guards happen under the same lock.

The API process and the processing worker each open their own connections
and share nothing else. The worker learns about changes from the page rows:
``change_gen`` goes up on every change to a page's shots or size, and a run
records the generation it started from, so a change during a run is noticed
however close together the two happen.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE album (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    page_size TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    pdf_stale INTEGER NOT NULL DEFAULT 0,
    pdf_built TEXT
);
CREATE TABLE page (
    id TEXT PRIMARY KEY,
    album_id TEXT NOT NULL REFERENCES album (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    first_shot TEXT,
    changed_at TEXT,
    change_gen INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'changed',
    ready_at TEXT,
    run_gen INTEGER,
    last_run TEXT,
    last_error TEXT,
    output TEXT,
    print_path TEXT,
    warnings TEXT,
    UNIQUE (album_id, position)
);
CREATE INDEX page_state ON page (state);
CREATE TABLE shot (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES page (id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    taken TEXT NOT NULL,
    received TEXT NOT NULL
);
CREATE INDEX shot_page ON shot (page_id);
CREATE TABLE token (
    name TEXT PRIMARY KEY,
    hash TEXT NOT NULL UNIQUE,
    created TEXT NOT NULL,
    last_used TEXT
);
"""

# A page is pending while it has shots and is in one of these states; the
# album's PDF is only built once none of its pages is.
PENDING = ("changed", "ready", "processing")
_HAS_SHOTS = "EXISTS (SELECT 1 FROM shot s WHERE s.page_id = page.id)"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC with milliseconds and a ``Z``: fixed width, so these strings sort by time."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class StoreError(Exception):
    """A request the index refuses; the API maps each subclass to a status code."""


class Invalid(StoreError):
    pass


class Conflict(StoreError):
    pass


class LimitExceeded(StoreError):
    def __init__(self, limit: str, message: str):
        super().__init__(message)
        self.limit = limit


@dataclass
class Claim:
    """A page the worker has taken to run."""

    page_id: str
    album_id: str
    gen: int
    number: int
    title: str
    page_size: str
    output: str | None  # the page's current output folder, relative to the album folder
    shots: list[str]


@dataclass
class Removed:
    """What a delete took out of the index, so the caller can remove the files."""

    album_id: str
    page_ids: list[str]
    shot_ids: list[str]
    outputs_dropped: bool = False


class Store:
    def __init__(self, path: str | Path, clock: Callable[[], datetime] = utcnow, max_pages: int = 500, max_shots: int = 25):
        self.path = Path(path)
        self.clock = clock
        self.max_pages = max_pages
        self.max_shots = max_shots
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = self._conn()
        c.execute("PRAGMA journal_mode=WAL")
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            # executescript commits on its own, so it gets its own transaction.
            c.executescript(f"BEGIN IMMEDIATE;\n{SCHEMA}\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
        elif version > SCHEMA_VERSION:
            raise RuntimeError(f"{self.path}: index version {version} is newer than this server ({SCHEMA_VERSION})")

    # -- connections and transactions -------------------------------------

    def _conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, isolation_level=None, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("PRAGMA busy_timeout=30000")
            self._local.conn = c
            with self._lock:
                self._all.append(c)
        return c

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            yield c
        except BaseException:
            c.execute("ROLLBACK")
            raise
        c.execute("COMMIT")

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        c = self._conn()
        c.execute("BEGIN")
        try:
            yield c
        finally:
            c.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            for c in self._all:
                c.close()
            self._all.clear()
        self._local = threading.local()

    def now(self) -> str:
        return iso(self.clock())

    # -- albums -----------------------------------------------------------

    def list_albums(self) -> list[dict]:
        with self._read() as c:
            rows = c.execute(
                """SELECT a.id, a.name, a.page_size, a.updated,
                          (SELECT COUNT(*) FROM page p WHERE p.album_id = a.id) AS pages,
                          (SELECT COUNT(*) FROM shot s JOIN page p ON s.page_id = p.id WHERE p.album_id = a.id) AS shots
                   FROM album a ORDER BY a.updated DESC, a.id"""
            ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "page_size": r["page_size"], "pages": r["pages"], "shots": r["shots"], "updated": r["updated"]}
            for r in rows
        ]

    def get_album(self, album_id: str) -> dict | None:
        with self._read() as c:
            a = c.execute("SELECT * FROM album WHERE id = ?", (album_id,)).fetchone()
            if a is None:
                return None
            pages = c.execute(
                """SELECT p.id, (SELECT COUNT(*) FROM shot s WHERE s.page_id = p.id) AS shots
                   FROM page p WHERE p.album_id = ? ORDER BY p.position""",
                (album_id,),
            ).fetchall()
        return {
            "id": a["id"],
            "name": a["name"],
            "page_size": a["page_size"],
            "created": a["created"],
            "pages": [{"id": p["id"], "shots": p["shots"]} for p in pages],
        }

    def put_album(self, album_id: str, name: str, page_size: str, pages: list[str], created: str) -> None:
        """Create the album or replace its metadata; ``pages`` is the new page order.

        Pages with shots that ``pages`` leaves out are kept after the listed
        ones, in the order their first shot arrived, so stale metadata never
        loses shots. Left-out pages without shots are dropped.
        """
        if len(set(pages)) != len(pages):
            raise Invalid("pages: a page id is listed twice")
        now = self.now()
        with self._write() as c:
            a = c.execute("SELECT * FROM album WHERE id = ?", (album_id,)).fetchone()
            for pid in pages:
                r = c.execute("SELECT album_id FROM page WHERE id = ?", (pid,)).fetchone()
                if r is not None and r["album_id"] != album_id:
                    raise Invalid(f"pages: page {pid} belongs to another album")
            existing = c.execute(
                f"""SELECT id, position, first_shot, state, {_HAS_SHOTS} AS has_shots
                    FROM page WHERE album_id = ? ORDER BY position""",
                (album_id,),
            ).fetchall()
            known = {r["id"]: r for r in existing}
            listed = set(pages)
            kept = sorted((r for r in existing if r["id"] not in listed and r["has_shots"]), key=lambda r: (r["first_shot"] or "", r["position"]))
            order = list(pages) + [r["id"] for r in kept]
            if len(order) > self.max_pages:
                raise LimitExceeded("max_pages", f"an album can have at most {self.max_pages} pages; this update would give it {len(order)}")

            if a is None:
                c.execute("INSERT INTO album (id, name, page_size, created, updated) VALUES (?, ?, ?, ?, ?)", (album_id, name, page_size, created, now))
            else:
                c.execute("UPDATE album SET name = ?, page_size = ?, created = ?, updated = ? WHERE id = ?", (name, page_size, created, now, album_id))
            for r in existing:
                if r["id"] not in listed and not r["has_shots"]:
                    c.execute("DELETE FROM page WHERE id = ?", (r["id"],))
            # UNIQUE (album_id, position) is checked per statement, so move every
            # page out of the way before numbering them again.
            c.execute("UPDATE page SET position = -position - 1 WHERE album_id = ?", (album_id,))
            for i, pid in enumerate(order, 1):
                if pid in known:
                    c.execute("UPDATE page SET position = ? WHERE id = ?", (i, pid))
                else:
                    c.execute("INSERT INTO page (id, album_id, position) VALUES (?, ?, ?)", (pid, album_id, i))

            if a is not None and a["page_size"] != page_size:
                c.execute(
                    f"""UPDATE page SET changed_at = ?, change_gen = change_gen + 1,
                               state = CASE state WHEN 'processing' THEN 'processing' ELSE 'changed' END
                        WHERE album_id = ? AND {_HAS_SHOTS}""",
                    (now, album_id),
                )
            # "Next page": the app sends the new page's metadata only after every
            # shot of the page being left, so any changed page before a page
            # that has just appeared is complete and can run now.
            new_positions = [i for i, pid in enumerate(order, 1) if pid not in known]
            if new_positions:
                c.execute(
                    f"""UPDATE page SET state = 'ready', ready_at = ?
                        WHERE album_id = ? AND state = 'changed' AND position < ? AND changed_at IS NOT NULL AND {_HAS_SHOTS}""",
                    (now, album_id, max(new_positions)),
                )
                # A page that changed while it was being processed is marked
                # too, so that it goes straight to "ready" when its run ends
                # instead of waiting out the idle period.
                c.execute(
                    f"""UPDATE page SET ready_at = ?
                        WHERE album_id = ? AND state = 'processing' AND change_gen != run_gen AND position < ? AND {_HAS_SHOTS}""",
                    (now, album_id, max(new_positions)),
                )
            if a is not None and (a["name"] != name or [r["id"] for r in existing] != order):
                c.execute("UPDATE album SET pdf_stale = 1 WHERE id = ?", (album_id,))

    def delete_album(self, album_id: str) -> bool:
        with self._write() as c:
            return c.execute("DELETE FROM album WHERE id = ?", (album_id,)).rowcount > 0

    def album_exists(self, album_id: str) -> bool:
        with self._read() as c:
            return c.execute("SELECT 1 FROM album WHERE id = ?", (album_id,)).fetchone() is not None

    # -- pages and shots --------------------------------------------------

    def get_page(self, album_id: str, page_id: str) -> dict | None:
        with self._read() as c:
            p = c.execute("SELECT id FROM page WHERE id = ? AND album_id = ?", (page_id, album_id)).fetchone()
            if p is None:
                return None
            shots = c.execute(
                "SELECT id, sha256, bytes, taken FROM shot WHERE page_id = ? ORDER BY taken, received, rowid", (page_id,)
            ).fetchall()
        return {"id": page_id, "shots": [dict(s) for s in shots]}

    def delete_page(self, album_id: str, page_id: str) -> Removed | None:
        with self._write() as c:
            if c.execute("SELECT 1 FROM page WHERE id = ? AND album_id = ?", (page_id, album_id)).fetchone() is None:
                return None
            shots = [r[0] for r in c.execute("SELECT id FROM shot WHERE page_id = ?", (page_id,))]
            c.execute("DELETE FROM page WHERE id = ?", (page_id,))
            c.execute("UPDATE album SET pdf_stale = 1, updated = ? WHERE id = ?", (self.now(), album_id))
        return Removed(album_id, [page_id], shots, outputs_dropped=True)

    def get_shot(self, shot_id: str) -> dict | None:
        with self._read() as c:
            r = c.execute(
                """SELECT s.id, s.page_id, p.album_id, s.sha256, s.bytes, s.taken
                   FROM shot s JOIN page p ON s.page_id = p.id WHERE s.id = ?""",
                (shot_id,),
            ).fetchone()
        return dict(r) if r else None

    def add_shot(self, album_id: str, page_id: str, shot_id: str, sha256: str, nbytes: int, taken: str, place: Callable[[], None]) -> bool:
        """Record a shot; returns False when it was already stored with this hash.

        ``place`` moves the shot's file into place. It runs inside the
        transaction, after every check has passed and before the row is
        written, so a refused shot leaves no file and a crash leaves at most a
        file without a row, which startup cleanup removes.
        """
        now = self.now()
        with self._write() as c:
            s = c.execute("SELECT sha256 FROM shot WHERE id = ?", (shot_id,)).fetchone()
            if s is not None:
                if s["sha256"] == sha256:
                    return False
                raise Conflict(f"shot {shot_id} is already stored with a different hash")
            if c.execute("SELECT 1 FROM album WHERE id = ?", (album_id,)).fetchone() is None:
                c.execute("INSERT INTO album (id, name, page_size, created, updated) VALUES (?, 'Untitled', NULL, ?, ?)", (album_id, now, now))
            p = c.execute("SELECT album_id FROM page WHERE id = ?", (page_id,)).fetchone()
            if p is not None and p["album_id"] != album_id:
                raise Conflict(f"page {page_id} belongs to another album")
            if p is None:
                n, last = c.execute("SELECT COUNT(*), COALESCE(MAX(position), 0) FROM page WHERE album_id = ?", (album_id,)).fetchone()
                if n >= self.max_pages:
                    raise LimitExceeded("max_pages", f"the album already has {self.max_pages} pages, the most allowed")
                c.execute("INSERT INTO page (id, album_id, position) VALUES (?, ?, ?)", (page_id, album_id, last + 1))
            n = c.execute("SELECT COUNT(*) FROM shot WHERE page_id = ?", (page_id,)).fetchone()[0]
            if n >= self.max_shots:
                raise LimitExceeded("max_shots", f"the page already has {self.max_shots} shots, the most allowed")
            place()
            c.execute(
                "INSERT INTO shot (id, page_id, sha256, bytes, taken, received) VALUES (?, ?, ?, ?, ?, ?)",
                (shot_id, page_id, sha256, nbytes, taken, now),
            )
            self._page_changed(c, page_id, now)
            c.execute("UPDATE page SET first_shot = COALESCE(first_shot, ?) WHERE id = ?", (now, page_id))
            c.execute("UPDATE album SET updated = ? WHERE id = ?", (now, album_id))
        return True

    def delete_shot(self, album_id: str, page_id: str, shot_id: str) -> Removed | None:
        now = self.now()
        with self._write() as c:
            r = c.execute(
                "SELECT 1 FROM shot s JOIN page p ON s.page_id = p.id WHERE s.id = ? AND p.id = ? AND p.album_id = ?",
                (shot_id, page_id, album_id),
            ).fetchone()
            if r is None:
                return None
            c.execute("DELETE FROM shot WHERE id = ?", (shot_id,))
            self._page_changed(c, page_id, now)
            c.execute("UPDATE album SET updated = ? WHERE id = ?", (now, album_id))
            empty = c.execute("SELECT 1 FROM shot WHERE page_id = ?", (page_id,)).fetchone() is None
            if empty:
                # Nothing left to process: the page's outputs go now rather than
                # after a run that could only fail.
                c.execute("UPDATE page SET output = NULL, print_path = NULL, warnings = NULL, last_error = NULL WHERE id = ?", (page_id,))
                c.execute("UPDATE album SET pdf_stale = 1 WHERE id = ?", (album_id,))
        return Removed(album_id, [], [shot_id], outputs_dropped=empty)

    @staticmethod
    def _page_changed(c: sqlite3.Connection, page_id: str, now: str) -> None:
        # A page being processed stays "processing"; the worker sees the new
        # generation when the run ends and sends the page back to "changed".
        c.execute(
            """UPDATE page SET changed_at = ?, change_gen = change_gen + 1,
                      state = CASE state WHEN 'processing' THEN 'processing' ELSE 'changed' END
               WHERE id = ?""",
            (now, page_id),
        )

    # -- everything, for startup cleanup and check ------------------------

    def snapshot(self) -> dict:
        """Every album id, page (with its output folder) and shot (with its hash)."""
        with self._read() as c:
            albums = [r[0] for r in c.execute("SELECT id FROM album")]
            pages = [dict(r) for r in c.execute("SELECT id, album_id, output FROM page")]
            shots = [dict(r) for r in c.execute("SELECT s.id, s.page_id, p.album_id, s.sha256 FROM shot s JOIN page p ON s.page_id = p.id")]
        return {"albums": albums, "pages": pages, "shots": shots}

    # -- tokens -----------------------------------------------------------

    def add_token(self, name: str, token_hash: str) -> None:
        with self._write() as c:
            if c.execute("SELECT 1 FROM token WHERE name = ?", (name,)).fetchone():
                raise Conflict(f"a token named {name!r} already exists")
            c.execute("INSERT INTO token (name, hash, created) VALUES (?, ?, ?)", (name, token_hash, self.now()))

    def list_tokens(self) -> list[dict]:
        with self._read() as c:
            return [dict(r) for r in c.execute("SELECT name, created, last_used FROM token ORDER BY created, name")]

    def token_hashes(self) -> list[tuple[str, str]]:
        with self._read() as c:
            return [(r["name"], r["hash"]) for r in c.execute("SELECT name, hash FROM token")]

    def revoke_token(self, name: str) -> bool:
        with self._write() as c:
            return c.execute("DELETE FROM token WHERE name = ?", (name,)).rowcount > 0

    def touch_token(self, name: str) -> None:
        with self._write() as c:
            c.execute("UPDATE token SET last_used = ? WHERE name = ?", (self.now(), name))

    # -- processing -------------------------------------------------------

    def recover(self) -> int:
        """At startup: a run that was interrupted did not happen."""
        with self._write() as c:
            return c.execute("UPDATE page SET state = 'changed', run_gen = NULL WHERE state = 'processing'").rowcount

    def promote_idle(self, cutoff: datetime) -> int:
        """Changed pages untouched since ``cutoff`` become ready."""
        with self._write() as c:
            return c.execute(
                f"""UPDATE page SET state = 'ready', ready_at = ?
                    WHERE state = 'changed' AND changed_at <= ? AND {_HAS_SHOTS}
                      AND (SELECT page_size FROM album a WHERE a.id = page.album_id) IS NOT NULL""",
                (self.now(), iso(cutoff)),
            ).rowcount

    def claim_ready(self) -> Claim | None:
        """Take the page that has been ready longest and mark it processing."""
        with self._write() as c:
            p = c.execute(
                f"""SELECT page.id, page.album_id, page.change_gen, page.output, a.name, a.page_size
                    FROM page JOIN album a ON a.id = page.album_id
                    WHERE page.state = 'ready' AND a.page_size IS NOT NULL AND {_HAS_SHOTS}
                    ORDER BY page.ready_at, page.rowid LIMIT 1"""
            ).fetchone()
            if p is None:
                return None
            number = c.execute(
                "SELECT COUNT(*) FROM page WHERE album_id = ? AND position <= (SELECT position FROM page WHERE id = ?)", (p["album_id"], p["id"])
            ).fetchone()[0]
            shots = [r[0] for r in c.execute("SELECT id FROM shot WHERE page_id = ? ORDER BY taken, received, rowid", (p["id"],))]
            c.execute("UPDATE page SET state = 'processing', run_gen = change_gen, ready_at = NULL WHERE id = ?", (p["id"],))
        return Claim(p["id"], p["album_id"], p["change_gen"], number, p["name"], p["page_size"], p["output"], shots)

    def run_target(self, page_id: str) -> tuple[bool, int | None]:
        """(page still exists, its current change generation)."""
        with self._read() as c:
            r = c.execute("SELECT change_gen FROM page WHERE id = ?", (page_id,)).fetchone()
        return (r is not None, r["change_gen"] if r else None)

    def finish_run(
        self,
        claim: Claim,
        output: str | None,
        error: str | None,
        print_path: str | None = None,
        warnings: list | None = None,
    ) -> tuple[str, str | None]:
        """Record a finished run; returns (outcome, output folder to delete).

        ``output`` is the run's output folder, or None when the run produced
        nothing usable. The outcome is ``gone`` when the page was deleted
        meanwhile, ``failed`` when a run of the page as it still is produced
        nothing (its previous outputs are dropped, so neither the PDF nor
        ``/print`` keeps showing a result the page no longer has),
        ``discarded`` when the output is otherwise not wanted (no output, or
        the page has no shots left), else ``adopted``. Either way the page
        goes back to ``changed`` if it changed during the run, or to ``ready``
        if a later page also appeared meanwhile (see ``put_album``).
        """
        now = self.now()
        with self._write() as c:
            p = c.execute("SELECT change_gen, output, ready_at FROM page WHERE id = ?", (claim.page_id,)).fetchone()
            if p is None:
                return "gone", output
            moved = p["change_gen"] != claim.gen
            has_shots = c.execute("SELECT 1 FROM shot WHERE page_id = ?", (claim.page_id,)).fetchone() is not None
            if moved:
                state = "ready" if p["ready_at"] else "changed"
            else:
                state = "failed" if error else "done"
            c.execute("UPDATE page SET state = ?, run_gen = NULL, last_run = ?, last_error = ? WHERE id = ?", (state, now, error, claim.page_id))
            if has_shots:
                c.execute("UPDATE album SET pdf_stale = 1 WHERE id = ?", (claim.album_id,))
            if output is None and has_shots and error and not moved:
                c.execute("UPDATE page SET output = NULL, print_path = NULL, warnings = NULL WHERE id = ?", (claim.page_id,))
                return "failed", p["output"]
            if output is None or not has_shots:
                return "discarded", output
            c.execute(
                "UPDATE page SET output = ?, print_path = ?, warnings = ? WHERE id = ?",
                (output, print_path, json.dumps(warnings or []), claim.page_id),
            )
        return "adopted", p["output"] if p["output"] != output else None

    def abandon_run(self, claim: Claim) -> None:
        """A run was stopped: the page waits to be run again."""
        with self._write() as c:
            c.execute(
                """UPDATE page SET state = CASE WHEN ready_at IS NULL THEN 'changed' ELSE 'ready' END, run_gen = NULL
                   WHERE id = ? AND state = 'processing'""",
                (claim.page_id,),
            )

    def albums_to_assemble(self) -> list[str]:
        with self._read() as c:
            return [
                r[0]
                for r in c.execute(
                    f"""SELECT a.id FROM album a
                        WHERE a.pdf_stale = 1 AND a.page_size IS NOT NULL
                          AND NOT EXISTS (SELECT 1 FROM page WHERE page.album_id = a.id
                                          AND page.state IN {PENDING!r} AND {_HAS_SHOTS})
                        ORDER BY a.updated"""
                )
            ]

    def begin_assembly(self, album_id: str) -> dict | None:
        """Clear the album's ``pdf_stale`` and return what to build its PDF from.

        Clearing it first means a change that lands during the build marks the
        PDF out of date again, rather than being lost when the build ends.
        """
        with self._write() as c:
            a = c.execute("SELECT name, pdf_stale FROM album WHERE id = ?", (album_id,)).fetchone()
            if a is None or not a["pdf_stale"]:
                return None
            c.execute("UPDATE album SET pdf_stale = 0 WHERE id = ?", (album_id,))
            pages = c.execute(
                f"""SELECT id, state, output, print_path, last_error, {_HAS_SHOTS} AS has_shots
                    FROM page WHERE album_id = ? ORDER BY position""",
                (album_id,),
            ).fetchall()
        return {"title": a["name"], "pages": [dict(p, number=i) for i, p in enumerate(pages, 1)]}

    def mark_pdf_stale(self, album_id: str) -> None:
        with self._write() as c:
            c.execute("UPDATE album SET pdf_stale = 1 WHERE id = ?", (album_id,))

    def set_pdf_built(self, album_id: str, built: bool) -> None:
        with self._write() as c:
            c.execute("UPDATE album SET pdf_built = ? WHERE id = ?", (self.now() if built else None, album_id))

    def album_status(self, album_id: str) -> dict | None:
        with self._read() as c:
            a = c.execute("SELECT * FROM album WHERE id = ?", (album_id,)).fetchone()
            if a is None:
                return None
            pages = c.execute(
                f"""SELECT id, state, last_run, last_error, warnings, print_path, output, {_HAS_SHOTS} AS has_shots
                    FROM page WHERE album_id = ? ORDER BY position""",
                (album_id,),
            ).fetchall()
        pending = any(p["state"] in PENDING and p["has_shots"] for p in pages)
        if a["pdf_built"] is None:
            pdf = "none"
        elif a["pdf_stale"] or pending:
            pdf = "out_of_date"
        else:
            pdf = "current"
        out = []
        for i, p in enumerate(pages, 1):
            state = p["state"] if p["has_shots"] else "empty"
            e = {"id": p["id"], "number": i, "state": state, "last_run": p["last_run"], "warnings": json.loads(p["warnings"] or "[]")}
            if p["state"] == "failed" and p["last_error"]:
                e["error"] = p["last_error"]
            out.append(e)
        return {"waiting_for_page_size": a["page_size"] is None, "pdf": pdf, "pdf_built": a["pdf_built"], "pages": out}

    def page_print(self, album_id: str, page_id: str) -> str | None:
        with self._read() as c:
            r = c.execute("SELECT print_path FROM page WHERE id = ? AND album_id = ?", (page_id, album_id)).fetchone()
        return r["print_path"] if r else None

    def pdf_built(self, album_id: str) -> str | None:
        with self._read() as c:
            r = c.execute("SELECT pdf_built FROM album WHERE id = ?", (album_id,)).fetchone()
        return r["pdf_built"] if r else None
