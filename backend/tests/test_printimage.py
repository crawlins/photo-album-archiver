import io

import numpy as np
import pytest
from PIL import Image, ImageCms

from albumproc.printimage import PrintOptions, make_print_image, placeholder_image, write_print_image
from albumproc.printsize import PageSize


def _page(w, h):
    """A BGR test page with a distinct colour in each corner."""
    img = np.full((h, w, 3), 200, np.uint8)
    img[: h // 4, : w // 4] = (255, 0, 0)  # blue top-left
    img[-h // 4 :, -w // 4 :] = (0, 0, 255)  # red bottom-right
    return img


def _codes(info):
    return {w.code for w in info.warnings}


@pytest.mark.parametrize("dpi", [150, 300, 600])
@pytest.mark.parametrize("fit", ["stretch", "fit", "fill"])
def test_exact_pixel_size(dpi, fit):
    out, info = make_print_image(_page(1203, 1001), PageSize(12, 10), PrintOptions(dpi=dpi, fit=fit))
    assert out.shape == (10 * dpi, 12 * dpi, 3)
    assert info.size_px == (12 * dpi, 10 * dpi)
    assert info.size_in == (12, 10)
    assert info.fit_used == fit


def test_rounds_fractional_sizes():
    out, _ = make_print_image(_page(850, 1100), PageSize(8.5, 11), PrintOptions(dpi=301))
    assert out.shape[:2] == (round(11 * 301), round(8.5 * 301))


def test_size_is_matched_to_page_orientation():
    out, info = make_print_image(_page(1000, 1200), PageSize(12, 10), PrintOptions(dpi=100))
    assert out.shape[:2] == (1200, 1000)  # portrait page, portrait output
    assert info.size_in == (10, 12)


def test_output_is_rgb():
    out, _ = make_print_image(_page(400, 300), PageSize(4, 3), PrintOptions(dpi=100))
    assert tuple(out[5, 5]) == (0, 0, 255)  # the blue corner, now in RGB order


def test_auto_stretches_within_tolerance_and_fits_beyond():
    _, info = make_print_image(_page(1210, 1000), PageSize(12, 10), PrintOptions(dpi=100))
    assert info.fit_used == "stretch" and "aspect_mismatch" not in _codes(info)
    out, info = make_print_image(_page(1300, 1000), PageSize(12, 10), PrintOptions(dpi=100))
    assert info.fit_used == "fit" and "aspect_mismatch" in _codes(info)
    # The page is wider than the size, so the padding is above and below.
    assert (out[0] == 255).all() and (out[-1] == 255).all()
    assert not (out[out.shape[0] // 2] == 255).all()


def test_fit_pads_with_fill_colour():
    out, _ = make_print_image(_page(1000, 1000), PageSize(6, 5), PrintOptions(dpi=100, fit="fit", fill=(10, 20, 30)))
    assert tuple(out[250, 0]) == (10, 20, 30)
    assert tuple(out[250, -1]) == (10, 20, 30)


def test_fill_crops_instead_of_padding():
    out, _ = make_print_image(_page(1000, 1000), PageSize(6, 5), PrintOptions(dpi=100, fit="fill"))
    assert out.shape[:2] == (500, 600)
    assert not (out == 255).all(axis=2).any()


def test_rotation_is_applied_first():
    page = _page(1200, 1000)  # landscape; blue top-left
    out, info = make_print_image(page, PageSize(5, 6), PrintOptions(dpi=100, rotate=90))
    assert out.shape[:2] == (600, 500)
    assert tuple(out[5, -5]) == (0, 0, 255)  # blue top-left moved to top-right
    out, _ = make_print_image(page, PageSize(5, 6), PrintOptions(dpi=100, rotate=180))
    assert tuple(out[-5, -5]) == (0, 0, 255)


def test_bleed_is_mirrored_page_content():
    opt = PrintOptions(dpi=100, bleed_in=0.1)
    out, info = make_print_image(_page(400, 300), PageSize(4, 3), opt)
    assert info.bleed_px == 10
    assert out.shape[:2] == (300 + 20, 400 + 20)
    np.testing.assert_array_equal(out[:10, 10:-10], out[20:10:-1, 10:-10])  # reflect-101 about row 10


def test_capture_dpi_and_resolution_warnings():
    _, info = make_print_image(_page(3600, 3000), PageSize(12, 10), PrintOptions())
    assert info.capture_dpi == 300 and not _codes(info)
    _, info = make_print_image(_page(2400, 2000), PageSize(12, 10), PrintOptions())
    assert info.capture_dpi == 200 and _codes(info) == {"upsampled"}
    _, info = make_print_image(_page(1200, 1000), PageSize(12, 10), PrintOptions())
    assert _codes(info) == {"upsampled", "low_resolution"}


def test_invalid_options_rejected():
    for opt in [PrintOptions(dpi=50), PrintOptions(fit="squash"), PrintOptions(rotate=45), PrintOptions(format="gif")]:
        with pytest.raises(ValueError):
            make_print_image(_page(100, 100), PageSize(1, 1), opt)


@pytest.mark.parametrize("fmt, ext", [("jpeg", ".jpg"), ("png", ".png"), ("tiff", ".tif")])
def test_written_files_carry_dpi_and_srgb(tmp_path, fmt, ext):
    opt = PrintOptions(dpi=300, format=fmt)
    out, _ = make_print_image(_page(600, 450), PageSize(2, 1.5), opt)
    path = tmp_path / f"p{ext}"
    write_print_image(path, out, opt)
    assert [p.name for p in tmp_path.iterdir()] == [path.name]  # no temp file left behind
    with Image.open(path) as im:
        assert im.mode == "RGB"
        assert im.size == (600, 450)
        assert tuple(round(float(d)) for d in im.info["dpi"]) == (300, 300)
        prof = ImageCms.ImageCmsProfile(io.BytesIO(im.info["icc_profile"]))
        assert "sRGB" in ImageCms.getProfileDescription(prof)


def test_written_files_are_reproducible(tmp_path):
    out, _ = make_print_image(_page(600, 450), PageSize(2, 1.5), PrintOptions())
    write_print_image(tmp_path / "a.jpg", out)
    write_print_image(tmp_path / "b.jpg", out)
    assert (tmp_path / "a.jpg").read_bytes() == (tmp_path / "b.jpg").read_bytes()


def test_placeholder_has_page_size_plus_bleed():
    img = placeholder_image(PageSize(4, 3), True, "page 7", PrintOptions(dpi=100, bleed_in=0.1))
    assert img.shape == (320, 420, 3)
    assert (img[0, 0] == 255).all() and not (img == 255).all()
