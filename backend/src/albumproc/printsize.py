"""Physical page sizes: parsing ``10x12in``-style strings and named paper sizes."""

from __future__ import annotations

import re
from dataclasses import dataclass

MM_PER_IN = 25.4
UNITS = {"in": 1.0, "mm": 1 / MM_PER_IN, "cm": 10 / MM_PER_IN}
NAMED = {
    "letter": (8.5, 11.0),
    "a4": (210 / MM_PER_IN, 297 / MM_PER_IN),
    "a3": (297 / MM_PER_IN, 420 / MM_PER_IN),
}

_NUM = r"(\d+(?:\.\d*)?|\.\d+)"
_SIZE_RE = re.compile(rf"^{_NUM}\s*x\s*{_NUM}\s*(in|mm|cm)$")
_LEN_RE = re.compile(rf"^{_NUM}\s*(in|mm|cm)$")


@dataclass(frozen=True)
class PageSize:
    """The two side lengths of a page in inches, in no particular order."""

    a_in: float
    b_in: float

    @property
    def long_in(self) -> float:
        return max(self.a_in, self.b_in)

    @property
    def short_in(self) -> float:
        return min(self.a_in, self.b_in)

    def oriented(self, landscape: bool) -> tuple[float, float]:
        """(width, height) in inches, with the long side across when ``landscape``."""
        return (self.long_in, self.short_in) if landscape else (self.short_in, self.long_in)

    def __str__(self) -> str:
        return f"{self.a_in:g}x{self.b_in:g}in"


def parse_page_size(text: str) -> PageSize:
    """``"10x12in"``, ``"254x305mm"``, ``"25.4x30.5cm"``, ``"letter"``, ``"a4"`` or ``"a3"``."""
    t = str(text).strip().lower()
    if t in NAMED:
        return PageSize(*NAMED[t])
    m = _SIZE_RE.match(t)
    if not m:
        raise ValueError(f"cannot parse page size {text!r}; expected e.g. 10x12in, 254x305mm, letter or a4")
    a, b, unit = float(m[1]), float(m[2]), UNITS[m[3]]
    if a <= 0 or b <= 0:
        raise ValueError(f"page size {text!r} must have positive sides")
    return PageSize(a * unit, b * unit)


def parse_length(text: str) -> float:
    """A length such as ``"0.125in"`` or ``"3mm"``, in inches. Zero is allowed."""
    t = str(text).strip().lower()
    m = _LEN_RE.match(t)
    if not m:
        raise ValueError(f"cannot parse length {text!r}; expected e.g. 0.125in or 3mm")
    return float(m[1]) * UNITS[m[2]]
