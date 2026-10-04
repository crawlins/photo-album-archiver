import pytest

from albumproc.printsize import PageSize, parse_length, parse_page_size


@pytest.mark.parametrize(
    "text, a, b",
    [
        ("10x12in", 10, 12),
        ("10 x 12 in", 10, 12),
        ("8.5X11IN", 8.5, 11),
        ("254x304.8mm", 10, 12),
        ("25.4x30.48cm", 10, 12),
        (".5x1in", 0.5, 1),
        ("letter", 8.5, 11),
        ("A4", 210 / 25.4, 297 / 25.4),
        ("a3", 297 / 25.4, 420 / 25.4),
    ],
)
def test_parse_page_size(text, a, b):
    s = parse_page_size(text)
    assert s.a_in == pytest.approx(a)
    assert s.b_in == pytest.approx(b)


@pytest.mark.parametrize("text", ["", "10x12", "10in", "10x12ft", "-10x12in", "axbin", "10x12x3in"])
def test_parse_page_size_rejects(text):
    with pytest.raises(ValueError, match="cannot parse"):
        parse_page_size(text)


def test_parse_page_size_rejects_zero():
    with pytest.raises(ValueError, match="'0x12in'.*positive"):
        parse_page_size("0x12in")


def test_oriented_puts_long_side_across_for_landscape():
    s = PageSize(12, 10)
    assert s.oriented(landscape=True) == (12, 10)
    assert s.oriented(landscape=False) == (10, 12)
    assert PageSize(10, 12).oriented(landscape=True) == (12, 10)


def test_parse_length():
    assert parse_length("0.125in") == pytest.approx(0.125)
    assert parse_length("3.175mm") == pytest.approx(0.125)
    assert parse_length("0in") == 0
    with pytest.raises(ValueError, match="'3'"):
        parse_length("3")
