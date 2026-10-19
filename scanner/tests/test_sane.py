import shutil
import subprocess
import threading

import pytest
from PIL import Image

from albumscan import sane
from albumscan.sane import Device, Options, ScanCancelled, ScanError, Scanner, choose_device, parse_devices, parse_options

CANON = Device("genesys:libusb:001:007", "Canon", "LiDE 220", "flatbed scanner")
EPSON = Device("escl:https://192.168.1.5:443", "Epson", "ET-2650 Series", "platen scanner")


def test_devices_skip_stray_text_and_webcams(fake_scanimage):
    devices = sane.list_devices()
    assert devices == [EPSON, CANON]
    assert CANON.usb and not EPSON.usb
    assert "-f" in fake_scanimage.read_text()


def test_parse_devices_ignores_lines_without_the_marker():
    assert parse_devices("device `test:0' is a Noname frontend-tester virtual device\nalbumscan\ta\tb\n") == []


@pytest.mark.parametrize(
    "last, expected",
    [("escl:https://192.168.1.5:443", EPSON), ("gone:0", CANON), (None, CANON)],
)
def test_choose_device_prefers_last_then_usb(last, expected):
    assert choose_device([EPSON, CANON], last) == expected


def test_choose_device_falls_back_to_the_first_and_none():
    assert choose_device([EPSON], None) == EPSON
    assert choose_device([], "x") is None


def test_options_are_read_in_colour_and_default_to_the_highest(fake_scanimage):
    opt = sane.read_options(CANON.name)
    assert opt.resolutions == [300, 600, 1200, 2400]
    assert opt.default_resolution() == 2400
    assert "--mode Color -A" in fake_scanimage.read_text()
    assert (opt.width_mm, opt.height_mm) == (218, 299)
    assert opt.colour


def test_options_without_a_mode_option(fake_scanimage, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "no_mode")
    opt = sane.read_options(CANON.name)
    assert opt.default_resolution() == 4800
    # A whole 218 x 299 mm glass at 4800 dpi is 2.3 gigapixels: too big to process.
    assert opt.usable(1e9) == [300, 600, 1200, 2400] and opt.default_resolution(1e9) == 2400
    assert fake_scanimage.read_text().splitlines()[-1].endswith(f"-d {CANON.name} -A")


def test_options_from_a_range_and_without_colour():
    opt = parse_options("    --mode Gray|Lineart [Gray]\n    --resolution 1..1000dpi (in steps of 1) [50]\n    -x 0..2550 [2550]\n")
    assert opt.resolutions == [300, 400, 600, 800, 1000]
    assert opt.default_resolution() == 1000  # the top of the range
    assert opt.pixels(300) is None and opt.usable(1) == opt.resolutions  # glass size unknown: nothing ruled out
    assert opt.width_mm is None  # not in mm: the backend's own area is used
    assert not opt.colour


def test_options_failure_carries_the_scanners_message(fake_scanimage, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "open_fail")
    with pytest.raises(ScanError, match="open of device"):
        sane.read_options(CANON.name)


def test_scan_writes_the_image_and_reports_progress(fake_scanimage, tmp_path):
    seen = []
    out = Scanner(CANON, sane.read_options(CANON.name)).scan(600, tmp_path / "s.pnm", seen.append)
    with Image.open(out) as im:
        assert im.mode == "RGB" and im.size[0] > 0
    assert seen and seen[-1] == 100.0 and seen == sorted(seen)
    cmd = fake_scanimage.read_text().splitlines()[-1]
    for part in ("-d genesys:libusb:001:007", "--mode Color", "--resolution 600", "--format=pnm", "-x 218", "-y 299", "-l 0", "-t 0"):
        assert part in cmd
    assert not list(tmp_path.glob("*.part"))


def test_scan_failure_raises_the_scanners_message(fake_scanimage, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "fail")
    with pytest.raises(ScanError, match="Error during device I/O"):
        Scanner(CANON, Options(resolutions=[300], modes=["Color"])).scan(300, tmp_path / "s.pnm")
    assert not list(tmp_path.glob("s.pnm*"))


def test_scan_can_be_cancelled(fake_scanimage, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_SCANIMAGE_MODE", "slow")
    cancel = threading.Event()
    seen = []

    def progress(p):
        seen.append(p)
        cancel.set()

    with pytest.raises(ScanCancelled):
        Scanner(CANON, Options(resolutions=[300], modes=["Color"])).scan(300, tmp_path / "s.pnm", progress, cancel)
    assert not list(tmp_path.glob("s.pnm*"))


def test_a_scanner_without_colour_is_refused(tmp_path):
    with pytest.raises(ScanError, match="colour"):
        Scanner(CANON, Options(resolutions=[300], modes=["Gray"])).scan(300, tmp_path / "s.pnm")


def test_missing_scanimage(monkeypatch):
    monkeypatch.setattr(sane, "SCANIMAGE", "/nonexistent/scanimage")
    with pytest.raises(ScanError, match="sane-utils"):
        sane.list_devices()


def _sane_test_backend() -> bool:
    if not shutil.which("scanimage"):
        return False
    try:
        return subprocess.run(["scanimage", "-d", "test", "-A"], capture_output=True, timeout=30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


@pytest.mark.skipif(not _sane_test_backend(), reason="SANE's test backend is not available")
def test_real_scanimage_with_the_test_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(sane, "SCANIMAGE", "scanimage")
    opt = sane.read_options("test")
    assert opt.colour and opt.resolutions and opt.width_mm
    dev = Device("test", "Noname", "frontend-tester", "virtual device")
    seen = []
    out = Scanner(dev, Options(resolutions=[300], modes=opt.modes, width_mm=20, height_mm=20)).scan(300, tmp_path / "t.pnm", seen.append)
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert abs(im.size[0] - round(20 / 25.4 * 300)) <= 2
    assert seen
