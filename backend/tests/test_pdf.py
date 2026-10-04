import numpy as np
import pikepdf
import pytest

from albumproc.pdf import PdfPage, write_album_pdf
from albumproc.printimage import PrintOptions, write_print_image


def _image(tmp_path, name, w, h, fmt="jpeg", value=128):
    path = tmp_path / name
    write_print_image(path, np.full((h, w, 3), value, np.uint8), PrintOptions(dpi=100, format=fmt))
    return path


def test_pages_in_order_at_physical_size(tmp_path):
    pages = [
        PdfPage(_image(tmp_path, "1.jpg", 400, 300, value=10), 100, 0, "Cover"),
        PdfPage(_image(tmp_path, "2.jpg", 300, 400, value=20), 100, 0, "page-01"),
    ]
    out = tmp_path / "album.pdf"
    assert write_album_pdf(pages, out, "Test album", epoch=0) == 2
    with pikepdf.open(out) as pdf:
        assert [round(float(v), 3) for v in pdf.pages[0].MediaBox] == [0, 0, 288, 216]  # 4 x 3 in
        assert [round(float(v), 3) for v in pdf.pages[1].MediaBox] == [0, 0, 216, 288]
        assert "/TrimBox" not in pdf.pages[0]
        assert str(pdf.docinfo["/Title"]) == "Test album"
        assert str(pdf.docinfo["/CreationDate"]) == "D:19700101000000Z"
        nums = pdf.Root.PageLabels.Nums
        assert [str(nums[k].P) for k in (1, 3)] == ["Cover", "page-01"]
        for page, spec in zip(pdf.pages, pages):
            (img,) = page.Resources.XObject.values()
            assert img.Filter == "/DCTDecode"
            assert img.read_raw_bytes() == spec.image_path.read_bytes()  # embedded, not re-encoded
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.jpg", "2.jpg", "album.pdf"]


def test_bleed_sets_trim_box(tmp_path):
    pages = [PdfPage(_image(tmp_path, "1.jpg", 420, 320), 100, 10, "p1")]
    out = tmp_path / "album.pdf"
    write_album_pdf(pages, out, "t", epoch=0)
    with pikepdf.open(out) as pdf:
        p = pdf.pages[0]
        assert [round(float(v), 3) for v in p.MediaBox] == [0, 0, 302.4, 230.4]
        assert [round(float(v), 3) for v in p.TrimBox] == [7.2, 7.2, 295.2, 223.2]


def test_same_inputs_same_bytes(tmp_path):
    pages = [PdfPage(_image(tmp_path, "1.jpg", 400, 300), 100, 0, "p1")]
    write_album_pdf(pages, tmp_path / "a.pdf", "t", epoch=1700000000)
    write_album_pdf(pages, tmp_path / "b.pdf", "t", epoch=1700000000)
    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_png_pages(tmp_path):
    pages = [PdfPage(_image(tmp_path, "1.png", 400, 300, fmt="png"), 100, 0, "p1")]
    assert write_album_pdf(pages, tmp_path / "a.pdf", "t") == 1


def test_mixed_dpi_and_empty_rejected(tmp_path):
    a = _image(tmp_path, "1.jpg", 400, 300)
    with pytest.raises(ValueError):
        write_album_pdf([PdfPage(a, 100, 0, "a"), PdfPage(a, 300, 0, "b")], tmp_path / "x.pdf", "t")
    with pytest.raises(ValueError):
        write_album_pdf([], tmp_path / "x.pdf", "t")
