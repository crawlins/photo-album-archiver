"""Real photos of album pages, when the test data is checked out.

The photos are family photos, kept in the private repository
crawlins/photo-album-archiver-data. Clone it beside this one and link its
``real`` folder into the repository root:

    git clone git@github.com:crawlins/photo-album-archiver-data.git ../photo-album-archiver-data
    ln -s ../photo-album-archiver-data/real real

Each page is a sleeved album page photographed twice, with neighbouring
pages (the facing page, the stack of pages beside it) in view. There is no
ground truth, so the expected aspect ratios are those of the single sleeved
page as checked by eye; the tolerance is tight enough to catch a neighbour's
edge, a page stack or the open album being taken in, or a print being taken
for the page.
"""

from pathlib import Path

import cv2
import pytest

from albumproc import process_page

REAL = Path(__file__).resolve().parents[2] / "real"

PAGES = {
    "grandma-wilson": 0.92,
    "page-two": 0.92,
    "page-three": 0.92,
    "page-four": 1.01,
    "page-five": 0.92,
}


@pytest.mark.skipif(not REAL.is_dir(), reason="real test photos not checked out (see module docstring)")
@pytest.mark.parametrize("page,aspect", sorted(PAGES.items()))
def test_real_page_is_the_single_sleeved_page(page, aspect):
    shots = sorted((REAL / page).glob("shot_*.jpg"))
    assert shots, f"no shots in {REAL / page}"
    res = process_page([cv2.imread(str(p), cv2.IMREAD_COLOR) for p in shots])
    assert res.report.dropped == []
    assert res.report.coverage > 0.99
    assert res.report.aspect == pytest.approx(aspect, rel=0.03)
