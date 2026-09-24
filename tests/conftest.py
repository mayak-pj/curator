from array import array

import pytest


@pytest.fixture(scope="session")
def vips():
    from tifjpg.imaging.vips_runtime import load_pyvips

    return load_pyvips()


def gradient(width, height, low, high):
    """Горизонтальный градиент low..high, одинаковый во всех строках."""
    row = [low + (high - low) * x // (width - 1) for x in range(width)]
    return row * height


def vips_image(vips, width, height, values, bands=1, pixel_format="ushort"):
    typecode = {"ushort": "H", "uchar": "B", "float": "f"}[pixel_format]
    data = array(typecode, values).tobytes()
    return vips.Image.new_from_memory(data, width, height, bands, pixel_format)


def read_row(vips, path, y, band=0):
    image = vips.Image.new_from_file(str(path))
    row = image.extract_band(band).crop(0, y, image.width, 1)
    return list(bytearray(row.write_to_memory()))
