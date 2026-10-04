"""Stand-ins for the album step that child processes can import (the worker starts them with spawn)."""

import time


def slow(work: str, out: str) -> None:
    time.sleep(60)


def fail(work: str, out: str) -> None:
    raise RuntimeError("cannot read page/x.jpg")


def ok(work: str, out: str) -> None:
    pass
