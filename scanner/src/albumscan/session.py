"""The open album and its current page: every change the window makes goes through here."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .client import Client
from .outbox import Outbox
from .shot import sha256

MAX_SHOTS = 25
MAX_PAGES = 500

_NUM = r"(\d+(?:\.\d*)?|\.\d+)"
_SIZE_RE = re.compile(rf"^{_NUM}\s*x\s*{_NUM}\s*(in|mm|cm)$")
NAMED_SIZES = ("letter", "a4", "a3")


class LimitError(Exception):
    pass


def check_page_size(text: str) -> str:
    """The page size as the server stores it; ValueError when albumproc would not parse it."""
    t = str(text).strip().lower()
    if t in NAMED_SIZES:
        return t
    m = _SIZE_RE.match(t)
    if not m or float(m[1]) <= 0 or float(m[2]) <= 0:
        raise ValueError(f"cannot read page size {text!r}; use letter, a4, a3 or a size such as 10x12in or 254x305mm")
    return t


def default_name(today: date | None = None) -> str:
    return f"Album {(today or date.today()).isoformat()}"


@dataclass
class Shot:
    id: str
    path: Path | None  # in the outbox; None once only the server has it


class Session:
    def __init__(self, outbox: Outbox, meta: dict, shots: list[Shot]):
        self.outbox = outbox
        self.album_id: str = meta["id"]
        self.name: str = meta["name"]
        self.page_size: str | None = meta.get("page_size")
        self.created: str = meta["created"]
        self.pages: list[str] = list(meta["pages"])
        self.shots: list[Shot] = shots

    # -- starting --------------------------------------------------------------------

    @classmethod
    def new(cls, outbox: Outbox, name: str, page_size: str, now: datetime | None = None) -> "Session":
        name = name.strip()
        if not name:
            raise ValueError("the album needs a name")
        meta = {
            "id": str(uuid.uuid4()),
            "name": name,
            "page_size": check_page_size(page_size),
            "created": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pages": [str(uuid.uuid4())],
        }
        s = cls(outbox, meta, [])
        s._queue_meta()
        return s

    @classmethod
    def open(cls, outbox: Outbox, client: Client, album_id: str) -> "Session":
        """The album as the server has it, with anything still in the outbox on top; its last page current."""
        server = client.album(album_id)
        meta = {
            "id": album_id,
            "name": server["name"],
            "page_size": server.get("page_size"),
            "created": server["created"],
            "pages": [p["id"] for p in server["pages"]],
        }
        unsent = outbox.unsent_meta(album_id)
        if unsent:  # newer than the server's
            meta.update({k: unsent[k] for k in ("name", "page_size", "created", "pages")})
            known = set(meta["pages"])
            meta["pages"] += [p["id"] for p in server["pages"] if p["id"] not in known]
        queue = not meta["pages"]
        if queue:
            meta["pages"] = [str(uuid.uuid4())]
        last = meta["pages"][-1]
        shots: list[Shot] = []
        if any(p["id"] == last and p.get("shots") for p in server["pages"]):
            shots = [Shot(s["id"], None) for s in client.page(album_id, last)["shots"]]
        have = {s.id for s in shots}
        shots += [Shot(op.shot_id, outbox.file(op)) for op in outbox.unsent_shots(album_id, last) if op.shot_id not in have]
        s = cls(outbox, meta, shots)
        if queue:
            s._queue_meta()
        return s

    # -- state -----------------------------------------------------------------------

    @property
    def page_id(self) -> str:
        return self.pages[-1]

    @property
    def previous_page_id(self) -> str | None:
        return self.pages[-2] if len(self.pages) > 1 else None

    @property
    def page_number(self) -> int:
        return len(self.pages)

    def meta(self) -> dict:
        m = {"name": self.name, "page_size": self.page_size, "pages": list(self.pages), "created": self.created}
        if m["page_size"] is None:
            del m["page_size"]
        return m

    def _queue_meta(self) -> None:
        self.outbox.put_album(self.album_id, self.meta())

    # -- changes -----------------------------------------------------------------------

    def check_can_scan(self) -> None:
        """Raise LimitError before scanning when the page is full."""
        if len(self.shots) >= MAX_SHOTS:
            raise LimitError(f"page {self.page_number} already has {MAX_SHOTS} scans, the most a page can have; press Next page")

    def add_scan(self, data: bytes) -> Shot:
        self.check_can_scan()
        shot_id = str(uuid.uuid4())
        path = self.outbox.add_shot(self.album_id, self.page_id, shot_id, data, sha256(data))
        shot = Shot(shot_id, path)
        self.shots.append(shot)
        return shot

    def next_page(self) -> bool:
        """Start a new page at the end; False (and nothing done) when the current page has no scans."""
        if not self.shots:
            return False
        if len(self.pages) >= MAX_PAGES:
            raise LimitError(f"the album already has {MAX_PAGES} pages, the most an album can have")
        self.pages.append(str(uuid.uuid4()))
        self.shots = []
        self._queue_meta()
        return True

    def delete_shot(self, shot_id: str) -> None:
        self.shots = [s for s in self.shots if s.id != shot_id]
        self.outbox.delete_shot(self.album_id, self.page_id, shot_id)

    def rename(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("the album needs a name")
        self.name = name
        self._queue_meta()

    def set_page_size(self, page_size: str) -> None:
        self.page_size = check_page_size(page_size)
        self._queue_meta()
