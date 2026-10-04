"""Settings, from a TOML file and ``ALBUMSERVER_`` environment variables.

The environment wins over the file, so a systemd unit or a container can
override one setting without editing the file. An unknown or invalid setting
in either place stops the server with its name, rather than being ignored.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

ENV_PREFIX = "ALBUMSERVER_"


class SettingsError(ValueError):
    """A setting is unknown or invalid; the message names it."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path = Path("~/.local/share/albumserver")
    host: str = "127.0.0.1"
    port: int = Field(8080, ge=0, le=65535)
    tls_cert: Path | None = None
    tls_key: Path | None = None
    max_body_mb: float = Field(50, gt=0)
    idle_period_s: float = Field(30, ge=0)
    workers: int = Field(1, ge=1)
    cancel_runs: bool = False
    max_pages: int = Field(500, ge=1)
    max_shots: int = Field(25, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if (self.tls_cert is None) != (self.tls_key is None):
            raise ValueError("tls_cert and tls_key must be set together")
        object.__setattr__(self, "data_dir", self.data_dir.expanduser())
        return self

    @property
    def max_body_bytes(self) -> int:
        return int(self.max_body_mb * 1024 * 1024)

    @property
    def tls(self) -> bool:
        return self.tls_cert is not None


def load_settings(config: str | Path | None = None, environ: Mapping[str, str] | None = None) -> Settings:
    """Settings from ``config`` (a TOML file, optional), overridden by the environment."""
    environ = os.environ if environ is None else environ
    values: dict = {}
    if config is not None:
        try:
            with open(config, "rb") as f:
                values = tomllib.load(f)
        except OSError as e:
            raise SettingsError(f"{config}: {e.strerror or e}") from None
        except tomllib.TOMLDecodeError as e:
            raise SettingsError(f"{config}: invalid TOML: {e}") from None
    fields = Settings.model_fields
    for key, value in environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        name = key[len(ENV_PREFIX) :].lower()
        if name not in fields:
            raise SettingsError(f"{key}: unknown setting; known: {', '.join(sorted(fields))}")
        values[name] = value
    try:
        return Settings(**values)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            where = ".".join(str(x) for x in err["loc"]) or "settings"
            msg = err["msg"]
            if err["type"] == "extra_forbidden":
                msg = f"unknown setting; known: {', '.join(sorted(fields))}"
            problems.append(f"{where}: {msg}")
        raise SettingsError("; ".join(problems)) from None
