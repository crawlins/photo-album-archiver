import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from fake_server import FakeServer  # noqa: E402


@pytest.fixture
def fake_scanimage(monkeypatch, tmp_path):
    """scanimage replaced by tests/fake_scanimage.py; returns the file each call is logged to."""
    import albumscan.sane as sane

    monkeypatch.setattr(sane, "SCANIMAGE", str(HERE / "fake_scanimage.py"))
    log = tmp_path / "scanimage.log"
    monkeypatch.setenv("FAKE_SCANIMAGE_LOG", str(log))
    return log


@pytest.fixture
def server():
    s = FakeServer()
    yield s
    s.close()
