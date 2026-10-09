import os

import pytest

from tests.conftest import gradient, read_row, vips_image
from tests.tiff_writer import build_tiff, write_tiff
from curator.domain.errors import ConversionCancelled, FileProcessingError, UnsupportedImageError
from curator.imaging import ConversionOptions, ToneSpec, VipsTiffJpegConverter, validate_jpeg
from curator.imaging.jpeg_info import read_jpeg_header
from curator.imaging.tone import build_lut

pytestmark = pytest.mark.vips

W, H = 256, 32
JPEG_TOLERANCE = 3


def options(method, **kwargs):
    return ConversionOptions(tone=ToneSpec(method, **kwargs))


def max_error(actual, expected):
    return max(abs(a - b) for a, b in zip(actual, expected))


@pytest.fixture
def converter(vips):
    return VipsTiffJpegConverter()


def test_linear_mapping_keeps_size_and_values(vips, converter, tmp_path):
    source = write_tiff(tmp_path / "g.tif", W, H, gradient(W, H, 0, 65535))
    destination = str(tmp_path / "g.jpeg")

    result = converter.convert(source, destination, options("linear"))

    assert (result.width, result.height, result.bands) == (W, H, 1)
    assert result.window == (0, 65535)
    assert result.output_bytes == os.path.getsize(destination)
    lut = build_lut(0, 65535)
    expected = [lut[value] for value in gradient(W, 1, 0, 65535)]
    assert max_error(read_row(vips, destination, H // 2), expected) <= JPEG_TOLERANCE


def test_minmax_stretches_narrow_range(vips, converter, tmp_path):
    source = write_tiff(tmp_path / "narrow.tif", W, H, gradient(W, H, 10000, 20000))
    destination = str(tmp_path / "narrow.jpeg")

    result = converter.convert(source, destination, options("minmax"))

    assert result.window == (10000, 20000)
    row = read_row(vips, destination, H // 2)
    assert row[0] <= JPEG_TOLERANCE
    assert row[-1] >= 255 - JPEG_TOLERANCE


def test_percentile_ignores_outliers_minmax_does_not(converter, tmp_path):
    values = gradient(W, H, 1000, 2000)
    values[0] = 0
    values[1] = 60000
    source = write_tiff(tmp_path / "outliers.tif", W, H, values)

    minmax = converter.convert(source, str(tmp_path / "minmax.jpeg"), options("minmax"))
    percentile = converter.convert(source, str(tmp_path / "pct.jpeg"), options("percentile", p_low=0.5, p_high=99.5))

    assert minmax.window == (0, 60000)
    lo, hi = percentile.window
    assert 1000 <= lo <= 1010
    assert 1990 <= hi <= 2000


@pytest.mark.parametrize("compression", ["none", "lzw", "deflate"])
@pytest.mark.parametrize("tiled", [False, True])
def test_libvips_written_variants(vips, converter, tmp_path, compression, tiled):
    source = str(tmp_path / "v.tif")
    image = vips_image(vips, W, H, gradient(W, H, 0, 65535)).copy(interpretation="grey16")
    image.tiffsave(source, compression=compression, tile=tiled, tile_width=64, tile_height=16)
    destination = str(tmp_path / "v.jpeg")

    result = converter.convert(source, destination, options("linear"))

    assert result.compression == compression
    lut = build_lut(0, 65535)
    expected = [lut[value] for value in gradient(W, 1, 0, 65535)]
    assert max_error(read_row(vips, destination, H // 2), expected) <= JPEG_TOLERANCE


def test_min_is_white_keeps_visual_meaning(vips, converter, tmp_path):
    # MinIsWhite: значение 0 — белый. После конвертации левый край должен быть светлым.
    source = write_tiff(tmp_path / "miw.tif", W, H, gradient(W, H, 0, 65535), photometric=0)
    destination = str(tmp_path / "miw.jpeg")

    result = converter.convert(source, destination, options("linear"))

    assert result.photometric == "MinIsWhite"
    row = read_row(vips, destination, H // 2)
    assert row[0] >= 255 - JPEG_TOLERANCE
    assert row[-1] <= JPEG_TOLERANCE


def test_8bit_source_is_not_stretched(vips, converter, tmp_path):
    source = write_tiff(tmp_path / "8bit.tif", W, H, gradient(W, H, 50, 150), bits=8)
    destination = str(tmp_path / "8bit.jpeg")

    result = converter.convert(source, destination, options("minmax"))

    assert result.source_format == "uchar"
    assert result.tone.startswith("none")
    assert max_error(read_row(vips, destination, H // 2), gradient(W, 1, 50, 150)) <= JPEG_TOLERANCE


def test_rgb16_is_scaled_once(vips, converter, tmp_path):
    source = str(tmp_path / "rgb16.tif")
    vips_image(vips, W, H, [32768] * (W * H * 3), bands=3).copy(interpretation="rgb16").tiffsave(source)
    destination = str(tmp_path / "rgb16.jpeg")

    converter.convert(source, destination, options("linear"))

    assert read_jpeg_header(destination).components == 3
    for band in range(3):
        assert abs(read_row(vips, destination, H // 2, band=band)[W // 2] - 128) <= JPEG_TOLERANCE


def test_never_overwrites_existing_destination(converter, tmp_path):
    source = write_tiff(tmp_path / "g.tif", W, H, gradient(W, H, 0, 65535))
    destination = tmp_path / "exists.jpeg"
    destination.write_bytes(b"user data")

    with pytest.raises(FileExistsError):
        converter.convert(source, str(destination), options("linear"))
    assert destination.read_bytes() == b"user data"


def test_unreadable_pixel_data_fails_without_output(converter, tmp_path):
    path = tmp_path / "broken.tif"
    path.write_bytes(build_tiff(W, H, gradient(W, H, 0, 65535), strip_offset_delta=10 ** 7))
    destination = tmp_path / "broken.jpeg"

    with pytest.raises(FileProcessingError):
        converter.convert(str(path), str(destination), options("linear"))
    assert not destination.exists()


def test_not_a_tiff(converter, tmp_path):
    path = tmp_path / "text.tif"
    path.write_text("not an image")
    with pytest.raises(FileProcessingError):
        converter.convert(str(path), str(tmp_path / "text.jpeg"), options("linear"))


def test_float_tiff_is_unsupported(vips, converter, tmp_path):
    source = str(tmp_path / "float.tif")
    vips_image(vips, W, H, [0.5] * (W * H), pixel_format="float").tiffsave(source)
    destination = tmp_path / "float.jpeg"

    with pytest.raises(UnsupportedImageError):
        converter.convert(source, str(destination), options("linear"))
    assert not destination.exists()


def test_alpha_channel_is_unsupported(vips, converter, tmp_path):
    source = str(tmp_path / "alpha.tif")
    vips_image(vips, W, H, [1000] * (W * H * 2), bands=2).copy(interpretation="grey16").tiffsave(source)

    with pytest.raises(UnsupportedImageError):
        converter.convert(source, str(tmp_path / "alpha.jpeg"), options("linear"))


BIG = 2048


@pytest.fixture(scope="module")
def big_tiff(tmp_path_factory):
    return write_tiff(tmp_path_factory.mktemp("big") / "big.tif", BIG, BIG, gradient(BIG, BIG, 0, 65535))


@pytest.mark.parametrize("method", ["linear", "percentile"])
def test_cancel_removes_partial_output(converter, tmp_path, big_tiff, method):
    destination = tmp_path / "cancelled.jpeg"

    with pytest.raises(ConversionCancelled):
        converter.convert(big_tiff, str(destination), options(method), is_cancelled=lambda: True)
    assert not destination.exists()


def test_progress_is_monotonic_and_complete(converter, tmp_path, big_tiff):
    events = []
    converter.convert(big_tiff, str(tmp_path / "p.jpeg"), options("percentile"),
                      progress=lambda stage, fraction: events.append((stage, fraction)))

    fractions = [fraction for _, fraction in events]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    assert {stage for stage, _ in events} == {"histogram", "encode"}


def test_multipage_tiff_warns(converter, tmp_path):
    source = write_tiff(tmp_path / "pages.tif", W, H, gradient(W, H, 0, 65535), pages=2)
    result = converter.convert(source, str(tmp_path / "pages.jpeg"), options("linear"))
    assert any("многостраничный" in warning for warning in result.warnings)


def test_cyrillic_and_spaces_in_paths(converter, tmp_path):
    folder = tmp_path / "Тест" / "Объект №1"
    folder.mkdir(parents=True)
    source = write_tiff(folder / "снимок 1.tif", W, H, gradient(W, H, 0, 65535))
    destination = str(folder / "Рентгенограмма 1.jpeg")

    result = converter.convert(source, destination, options("percentile"))

    check = validate_jpeg(destination, result.width, result.height, 1)
    assert check.estimated_quality == 100


def test_validate_rejects_truncated_jpeg(converter, tmp_path):
    source = write_tiff(tmp_path / "g.tif", W, H, gradient(W, H, 0, 65535))
    destination = tmp_path / "g.jpeg"
    converter.convert(source, str(destination), options("linear"))
    data = destination.read_bytes()
    destination.write_bytes(data[:len(data) // 2])

    with pytest.raises(FileProcessingError):
        validate_jpeg(str(destination), W, H, 1)


def test_validate_rejects_wrong_dimensions(converter, tmp_path):
    source = write_tiff(tmp_path / "g.tif", W, H, gradient(W, H, 0, 65535))
    destination = str(tmp_path / "g.jpeg")
    converter.convert(source, destination, options("linear"))

    with pytest.raises(FileProcessingError):
        validate_jpeg(destination, W + 1, H, 1)


@pytest.mark.parametrize("quality", [75, 95, 100])
def test_quality_estimate_matches_libvips(vips, tmp_path, quality):
    path = str(tmp_path / "q.jpeg")
    vips_image(vips, W, H, gradient(W, H, 0, 255), pixel_format="uchar").jpegsave(path, Q=quality)
    header = read_jpeg_header(path)
    assert (header.estimated_quality, header.quality_exact) == (quality, True)
