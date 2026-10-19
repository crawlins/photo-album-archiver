#!/usr/bin/env python3
"""Stands in for scanimage in tests. Behaviour comes from FAKE_SCANIMAGE_* environment variables.

FAKE_SCANIMAGE_MODE: ok (default) | fail | slow | open_fail
FAKE_SCANIMAGE_LOG: file to append each command line to
"""

import os
import signal
import sys
import time

LIST = (
    "<html>stray text with no newline</html>"
    "\nalbumscan\tv4l:/dev/video0\tNoname\tHP HD Camera\tvirtual device\n"
    "\nalbumscan\tescl:https://192.168.1.5:443\tEpson\tET-2650 Series\tplaten scanner\n"
    "\nalbumscan\tgenesys:libusb:001:007\tCanon\tLiDE 220\tflatbed scanner\n"
)

OPTIONS = """
All options specific to device `genesys:libusb:001:007':
  Scan Mode:
    --mode Color|Gray|Lineart [Gray]
        Selects the scan mode.
    --resolution 4800|2400|1200|600|300|150|100|75dpi [75]
        Sets the resolution of the scanned image.
  Geometry:
    -l 0..218mm (in steps of 0.0999908) [0]
    -t 0..299mm (in steps of 0.0999908) [0]
    -x 0..218mm (in steps of 0.0999908) [218]
    -y 0..299mm (in steps of 0.0999908) [299]
"""


def main(argv):
    mode = os.environ.get("FAKE_SCANIMAGE_MODE", "ok")
    log = os.environ.get("FAKE_SCANIMAGE_LOG")
    if log:
        with open(log, "a") as f:
            f.write(" ".join(argv) + "\n")
    if "-L" in argv:
        sys.stdout.write(LIST)
        return 0
    if "-A" in argv:
        if mode == "no_mode" and "--mode" in argv:
            sys.stderr.write("scanimage: attempted to set inactive option mode\n")
            return 1
        if mode == "open_fail":
            sys.stderr.write("scanimage: open of device genesys:libusb:001:007 failed: Invalid argument\n")
            return 1
        # In colour this backend offers up to 2400 dpi, in its default grey mode up to 4800.
        sys.stdout.write(OPTIONS.replace("4800|", "") if "--mode" in argv else OPTIONS)
        return 0
    out = argv[argv.index("-o") + 1]
    res = int(argv[argv.index("--resolution") + 1])
    w, h = max(1, 218 * res // 254 // 10), max(1, 299 * res // 254 // 10)  # a tenth of the real size, for speed
    if mode == "fail":
        sys.stderr.write("Progress: 10.0%\rscanimage: sane_start: Error during device I/O\n")
        return 1
    stop = []
    signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
    steps = 50 if mode == "slow" else 4
    for i in range(steps):
        if stop:
            sys.stderr.write("\nscanimage: received signal 15\n")
            return 1
        sys.stderr.write(f"Progress: {100 * i / steps:.1f}%\r")
        sys.stderr.flush()
        time.sleep(0.1 if mode == "slow" else 0.01)
    sys.stderr.write("Progress: 100.0%\r")
    with open(out, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode())
        row = bytes((x * 255 // max(1, w - 1)) % 256 for x in range(w) for _ in range(3))
        f.write(row * h)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
