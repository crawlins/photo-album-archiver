"""``albumscan``: the scanning window, or ``--list-scanners`` to see what SANE finds."""

from __future__ import annotations

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="albumscan", description="Scan photo-album pages and upload them to the album server.")
    p.add_argument("--version", action="version", version=f"albumscan {__version__}")
    p.add_argument("--list-scanners", action="store_true", help="list the scanners SANE finds and exit")
    a = p.parse_args(argv)
    if a.list_scanners:
        from .sane import ScanError, list_devices

        try:
            devices = list_devices()
        except ScanError as e:
            print(e, file=sys.stderr)
            return 2
        if not devices:
            print("no scanners found; check the cable and run scanimage -L", file=sys.stderr)
            return 1
        for d in devices:
            print(f"{d.name}\t{d.vendor} {d.model}\t{'usb' if d.usb else 'network'}")
        return 0
    try:
        from .window import run
    except (ImportError, ValueError) as e:
        print(f"GTK 4 is not available ({e}): install python3-gi and gir1.2-gtk-4.0, and run with the system Python", file=sys.stderr)
        return 2
    return run()
