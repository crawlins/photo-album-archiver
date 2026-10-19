"""Settings in ~/.config/albumscan/, the token alone in a file only the user can read."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "albumscan"


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "albumscan"


@dataclass
class Settings:
    server_url: str = ""
    device: str | None = None
    resolution: int | None = None
    placements: dict[str, int] = field(default_factory=dict)  # album id -> degrees clockwise
    last_album: str | None = None

    @classmethod
    def load(cls, folder: Path | None = None) -> "Settings":
        path = (folder or config_dir()) / "settings.json"
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls()
        known = {k: raw[k] for k in asdict(cls()) if k in raw}
        return cls(**known)

    def save(self, folder: Path | None = None) -> None:
        folder = folder or config_dir()
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "settings.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n")
        os.replace(tmp, folder / "settings.json")

    def placement(self, album_id: str | None) -> int:
        return self.placements.get(album_id or "", 0)


def load_token(folder: Path | None = None) -> str:
    try:
        return ((folder or config_dir()) / "token").read_text().strip()
    except OSError:
        return ""


def save_token(token: str, folder: Path | None = None) -> None:
    """Write the token readable by the user only, created that way so it is never readable by others."""
    folder = folder or config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / "token.tmp"
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token.strip() + "\n")
    os.replace(tmp, folder / "token")
