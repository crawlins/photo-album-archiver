"""The data folder: where each file lives, safe writes, startup cleanup and ``check``.

Files and index are kept consistent by ordering alone. A file is written
under a temporary name, flushed and renamed into place before its row is
committed; a row is committed as deleted before its files go. So a crash
leaves at most files the index does not know about, and startup cleanup
removes exactly those.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("albumserver")

TMP = ".tmp"


class Layout:
    """Paths under the data folder::

        index.sqlite
        albums/<albumId>/photos/<pageId>/<shotId>.jpg
        albums/<albumId>/thumbs/<shotId>.jpg
        albums/<albumId>/work/<pageId>/            one-page album folder for a run
        albums/<albumId>/out/<pageId>/<run>/       one run's album step outputs
        albums/<albumId>/album.pdf, report.json
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.index = self.root / "index.sqlite"
        self.albums = self.root / "albums"

    def create(self) -> None:
        self.albums.mkdir(parents=True, exist_ok=True)

    def album(self, album_id: str) -> Path:
        return self.albums / album_id

    def photos(self, album_id: str, page_id: str) -> Path:
        return self.album(album_id) / "photos" / page_id

    def photo(self, album_id: str, page_id: str, shot_id: str) -> Path:
        return self.photos(album_id, page_id) / f"{shot_id}.jpg"

    def thumb(self, album_id: str, shot_id: str) -> Path:
        return self.album(album_id) / "thumbs" / f"{shot_id}.jpg"

    def work(self, album_id: str, page_id: str) -> Path:
        return self.album(album_id) / "work" / page_id

    def outputs(self, album_id: str, page_id: str) -> Path:
        return self.album(album_id) / "out" / page_id

    def new_run(self, album_id: str, page_id: str) -> str:
        """A fresh output folder name for a run, relative to the album folder."""
        return f"out/{page_id}/run-{uuid.uuid4().hex[:12]}"

    def pdf(self, album_id: str) -> Path:
        return self.album(album_id) / "album.pdf"

    def report(self, album_id: str) -> Path:
        return self.album(album_id) / "report.json"


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` through a temporary file, flushed before the rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}{TMP}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class TooLarge(Exception):
    pass


class Upload:
    """A body streamed to a temporary file next to ``final`` while being hashed."""

    def __init__(self, final: Path, limit: int):
        self.final = final
        self.limit = limit
        final.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = final.with_name(f"{final.name}.{uuid.uuid4().hex[:8]}{TMP}")
        self._f = open(self.tmp, "wb")
        self._h = hashlib.sha256()
        self.size = 0

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            raise TooLarge()
        self._h.update(chunk)
        self._f.write(chunk)

    def finish(self) -> str:
        """Flush to disk; returns the SHA-256 in hex."""
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        return self._h.hexdigest()

    def place(self) -> None:
        os.replace(self.tmp, self.final)
        fsync_dir(self.final.parent)

    def discard(self) -> None:
        if not self._f.closed:
            self._f.close()
        self.tmp.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Findings:
    missing: list[str] = field(default_factory=list)  # index entries without their file
    extra: list[str] = field(default_factory=list)  # files the index does not know
    changed: list[str] = field(default_factory=list)  # photos whose hash no longer matches

    @property
    def ok(self) -> bool:
        return not (self.missing or self.extra or self.changed)


def _scan(layout: Layout, snap: dict) -> tuple[list[Path], list[Path]]:
    """(paths to remove, indexed photos that are missing) for ``snap``, without changing anything.

    Work folders are always removable: they only hold hard links made for a run.
    """
    albums = set(snap["albums"])
    pages = {(p["album_id"], p["id"]): p for p in snap["pages"]}
    shots = {(s["album_id"], s["page_id"], s["id"]) for s in snap["shots"]}
    shot_ids = {(s["album_id"], s["id"]) for s in snap["shots"]}
    remove: list[Path] = []
    if not layout.albums.is_dir():
        return remove, [layout.photo(*k) for k in sorted(shots)]
    for adir in sorted(layout.albums.iterdir()):
        aid = adir.name
        if aid not in albums or not adir.is_dir():
            remove.append(adir)
            continue
        for f in adir.iterdir():
            if f.is_file() and f.name.endswith(TMP):
                remove.append(f)
        if (adir / "work").exists():
            remove.append(adir / "work")
        photos = adir / "photos"
        if photos.is_dir():
            for pdir in sorted(photos.iterdir()):
                if (aid, pdir.name) not in pages or not pdir.is_dir():
                    remove.append(pdir)
                    continue
                for f in sorted(pdir.iterdir()):
                    if not (f.suffix == ".jpg" and (aid, pdir.name, f.stem) in shots):
                        remove.append(f)
        thumbs = adir / "thumbs"
        if thumbs.is_dir():
            for f in sorted(thumbs.iterdir()):
                if not (f.suffix == ".jpg" and (aid, f.stem) in shot_ids):
                    remove.append(f)
        out = adir / "out"
        if out.is_dir():
            for pdir in sorted(out.iterdir()):
                page = pages.get((aid, pdir.name))
                if page is None or not pdir.is_dir():
                    remove.append(pdir)
                    continue
                keep = page["output"].split("/")[-1] if page["output"] else None
                for run in sorted(pdir.iterdir()):
                    if run.name != keep:
                        remove.append(run)
    missing = [layout.photo(a, p, s) for a, p, s in sorted(shots) if not layout.photo(a, p, s).is_file()]
    return remove, missing


def cleanup(layout: Layout, snap: dict) -> Findings:
    """Startup: delete temporary and unindexed files, and log indexed photos that are missing."""
    remove, missing = _scan(layout, snap)
    found = Findings()
    for path in remove:
        found.extra.append(str(path.relative_to(layout.root)))
        if path.is_dir() and not path.is_symlink():
            remove_tree(path)
        else:
            path.unlink(missing_ok=True)
    for path in missing:
        found.missing.append(str(path.relative_to(layout.root)))
        log.warning("photo missing from the data folder: %s", path.relative_to(layout.root))
    quiet = [p for p in found.extra if "/work" not in f"/{p}"]
    if quiet:
        log.info("startup cleanup removed %d unindexed or temporary item(s)", len(quiet))
    return found


def check(layout: Layout, snap: dict) -> Findings:
    """Report missing photos, unindexed files and changed photos; changes nothing."""
    remove, missing = _scan(layout, snap)
    found = Findings()
    found.extra = [str(p.relative_to(layout.root)) for p in remove if p.name != "work"]
    found.missing = [str(p.relative_to(layout.root)) for p in missing]
    for s in snap["shots"]:
        path = layout.photo(s["album_id"], s["page_id"], s["id"])
        if path.is_file() and sha256_file(path) != s["sha256"]:
            found.changed.append(str(path.relative_to(layout.root)))
    return found
