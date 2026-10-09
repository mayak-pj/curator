import pytest

from tests.tiff_writer import ASCII, RATIONAL, SHORT, write_tiff
from curator.imaging.tiff_tags import TiffHeaderError, read_tiff_header


@pytest.mark.parametrize("byte_order", ["<", ">"])
@pytest.mark.parametrize("bigtiff", [False, True])
def test_reads_basic_tags(tmp_path, byte_order, bigtiff):
    path = write_tiff(
        tmp_path / "a.tif", 5, 3, list(range(15)), bits=16, photometric=0,
        byte_order=byte_order, bigtiff=bigtiff, rows_per_strip=1,
        extra_tags=[(305, ASCII, "XRay Soft"), (282, RATIONAL, [(300, 1)]),
                    (283, RATIONAL, [(300, 1)]), (296, SHORT, [2])],
    )
    header = read_tiff_header(path)
    assert header.bigtiff is bigtiff
    assert header.byte_order == ("little" if byte_order == "<" else "big")
    assert (header.width, header.height) == (5, 3)
    assert header.bits_per_sample == 16
    assert header.samples_per_pixel == 1
    assert header.photometric == "MinIsWhite"
    assert header.compression == "none"
    assert header.chunk_count == 3
    assert header.page_count == 1
    assert header.tag(305) == "XRay Soft"
    assert header.resolution == (300.0, 300.0, "inch")


def test_counts_pages(tmp_path):
    path = write_tiff(tmp_path / "multi.tif", 2, 2, [1, 2, 3, 4], pages=3)
    assert read_tiff_header(path).page_count == 3


def test_not_a_tiff(tmp_path):
    path = tmp_path / "fake.tif"
    path.write_bytes(b"this is not a tiff at all")
    with pytest.raises(TiffHeaderError):
        read_tiff_header(str(path))


def test_truncated_ifd(tmp_path):
    data = (tmp_path / "ok.tif")
    write_tiff(data, 4, 4, list(range(16)))
    truncated = tmp_path / "cut.tif"
    truncated.write_bytes(data.read_bytes()[:-20])
    with pytest.raises(TiffHeaderError):
        read_tiff_header(str(truncated))
