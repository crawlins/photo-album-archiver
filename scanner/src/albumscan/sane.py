"""The scanner, through SANE's ``scanimage``.

A subprocess rather than a SANE binding: ``scanimage`` is already installed
with SANE, gives a parseable device list, the options of each device,
progress and cancellation, and keeps a crashing backend driver out of the
app's process.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

SCANIMAGE = os.environ.get("ALBUMSCAN_SCANIMAGE", "scanimage")

# The leading newline and marker matter: some backends print stray text with
# no newline before the list (epsonscan2 prints an HTML page).
LIST_FORMAT = "%nalbumscan\t%d\t%v\t%m\t%t%n"
_NETWORK = ("net:", "http:", "https:", "airscan:", "escl:", "network")
MIN_DPI = 300
_STANDARD_DPI = (300, 400, 600, 800, 1200, 2400, 4800)


class ScanError(Exception):
    """A scan or a query failed; the message is the scanner's own where it gave one."""


class ScanCancelled(ScanError):
    pass


@dataclass(frozen=True)
class Device:
    name: str  # the SANE device name, e.g. genesys:libusb:001:007
    vendor: str
    model: str
    kind: str  # SANE's type, e.g. "flatbed scanner"

    @property
    def usb(self) -> bool:
        return not any(n in self.name.lower() for n in _NETWORK)

    @property
    def label(self) -> str:
        return f"{self.vendor} {self.model} ({self.name})"


def parse_devices(text: str) -> list[Device]:
    """Devices from ``scanimage -f LIST_FORMAT -L`` output, webcams left out."""
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 5 or parts[0] != "albumscan":
            continue
        d = Device(*(p.strip() for p in parts[1:]))
        if d.name.startswith("v4l:"):
            continue
        out.append(d)
    return out


def list_devices(timeout: float = 60) -> list[Device]:
    try:
        p = subprocess.run([SCANIMAGE, "-f", LIST_FORMAT, "-L"], capture_output=True, text=True, errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise ScanError("scanimage not found: install sane-utils") from e
    except subprocess.TimeoutExpired as e:
        raise ScanError("looking for scanners took too long") from e
    return parse_devices(p.stdout)


def choose_device(devices: list[Device], last: str | None) -> Device | None:
    """The one used last if present, else the first USB scanner, else the first."""
    for d in devices:
        if d.name == last:
            return d
    return next((d for d in devices if d.usb), devices[0] if devices else None)


@dataclass
class Options:
    resolutions: list[int] = field(default_factory=list)  # offered, from MIN_DPI up
    width_mm: float | None = None  # the glass, when the backend gives it in mm
    height_mm: float | None = None
    modes: list[str] = field(default_factory=list)  # empty: the device has no mode option

    @property
    def colour(self) -> bool:
        return not self.modes or "Color" in self.modes

    def pixels(self, dpi: int) -> float | None:
        """Pixels in a whole-glass scan at ``dpi``, when the glass size is known."""
        if not (self.width_mm and self.height_mm):
            return None
        return (self.width_mm / 25.4 * dpi) * (self.height_mm / 25.4 * dpi)

    def usable(self, max_pixels: float) -> list[int]:
        """The offered resolutions whose whole-glass scan stays within ``max_pixels``."""
        return [r for r in self.resolutions if (self.pixels(r) or 0) <= max_pixels]

    def default_resolution(self, max_pixels: float = float("inf")) -> int | None:
        """The highest resolution offered in colour whose scan stays within ``max_pixels``."""
        ok = self.usable(max_pixels)
        return max(ok) if ok else None


_OPT_RE = re.compile(r"^\s+(--resolution|--mode|-x|-y)\s+(\S+)")
_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)(dpi|mm)?(?:\s|$)")


def parse_options(text: str) -> Options:
    """The options the app uses, from ``scanimage -d DEV -A``."""
    opt = Options()
    for line in text.splitlines():
        m = _OPT_RE.match(line)
        if not m:
            continue
        name, value = m[1], m[2]
        if name == "--mode":
            opt.modes = [v for v in value.split("|") if v]
        elif name == "--resolution":
            r = _RANGE_RE.match(value)
            if r:
                lo, hi = float(r[1]), float(r[2])
                top = [int(hi)] if hi >= MIN_DPI else []
                opt.resolutions = sorted({d for d in _STANDARD_DPI if lo <= d <= hi} | set(top))
            else:
                vals = [int(float(v)) for v in re.findall(r"\d+(?:\.\d+)?", value)]
                opt.resolutions = sorted({v for v in vals if v >= MIN_DPI})
        else:
            r = _RANGE_RE.match(value)
            if r and r[3] == "mm":
                setattr(opt, "width_mm" if name == "-x" else "height_mm", float(r[2]))
    return opt


def read_options(device: str, timeout: float = 60) -> Options:
    """The device's options in colour mode, since a backend may offer other resolutions in each mode."""

    def query(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run([SCANIMAGE, "-d", device, *extra, "-A"], capture_output=True, text=True, errors="replace", timeout=timeout)

    try:
        p = query("--mode", "Color")
        if p.returncode != 0:  # no colour mode, or no mode option at all
            p = query()
    except FileNotFoundError as e:
        raise ScanError("scanimage not found: install sane-utils") from e
    except subprocess.TimeoutExpired as e:
        raise ScanError("the scanner did not answer") from e
    if p.returncode != 0:
        raise ScanError(_last_line(p.stderr) or f"cannot open {device}")
    return parse_options(p.stdout)


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text or "") if ln.strip() and not ln.strip().startswith("Progress:")]
    return lines[-1].removeprefix("scanimage: ") if lines else ""


_PROGRESS_RE = re.compile(r"Progress:\s*([\d.]+)%")


class Scanner:
    def __init__(self, device: Device, options: Options):
        self.device = device
        self.options = options

    def command(self, resolution: int, out: Path) -> list[str]:
        cmd = [SCANIMAGE, "-d", self.device.name, "--format=pnm", "--resolution", str(resolution), "--progress", "-o", str(out)]
        if self.options.modes:
            cmd[3:3] = ["--mode", "Color"]
        if self.options.width_mm and self.options.height_mm:
            cmd += ["-l", "0", "-t", "0", "-x", f"{self.options.width_mm:g}", "-y", f"{self.options.height_mm:g}"]
        return cmd

    def scan(
        self,
        resolution: int,
        out: Path,
        on_progress: Callable[[float], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        """Scan the whole glass in colour to ``out`` (PNM); raises ScanError, or ScanCancelled when ``cancel`` is set."""
        if not self.options.colour:
            raise ScanError(f"{self.device.label} cannot scan in colour")
        out = Path(out)
        tmp = out.with_name(out.name + ".part")
        try:
            proc = subprocess.Popen(self.command(resolution, tmp), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except FileNotFoundError as e:
            raise ScanError("scanimage not found: install sane-utils") from e
        err: list[str] = []

        def read_stderr():
            buf = b""
            while chunk := proc.stderr.read1(4096):
                buf += chunk
                *parts, buf = re.split(rb"[\r\n]", buf)
                for part in parts:
                    text = part.decode(errors="replace")
                    m = _PROGRESS_RE.search(text)
                    if m and on_progress:
                        on_progress(min(100.0, float(m[1])))
                    elif text.strip():
                        err.append(text)
            if buf.strip():
                err.append(buf.decode(errors="replace"))

        reader = threading.Thread(target=read_stderr, daemon=True)
        reader.start()
        cancelled = False
        try:
            while True:
                try:
                    proc.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set() and not cancelled:
                        cancelled = True
                        proc.send_signal(signal.SIGTERM)  # scanimage cancels the scan and exits
                        try:
                            proc.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                        break
        finally:
            reader.join(timeout=5)
        if cancelled:
            tmp.unlink(missing_ok=True)
            raise ScanCancelled("scan cancelled")
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise ScanError(_last_line("\n".join(err)) or f"scanimage failed with status {proc.returncode}")
        os.replace(tmp, out)
        return out
