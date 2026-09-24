"""Чтение заголовка TIFF без декодирования пикселей.

libvips не отдаёт часть тегов (сжатие, раскладка strips/tiles,
SMin/SMaxSampleValue, число страниц), а они нужны для отчёта и проверок.
Поддерживаются classic TIFF и BigTIFF, оба порядка байт.
"""

import struct
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from tifjpg.domain.errors import FileProcessingError

TAG_NAMES = {
    254: "NewSubfileType", 256: "ImageWidth", 257: "ImageLength", 258: "BitsPerSample",
    259: "Compression", 262: "PhotometricInterpretation", 270: "ImageDescription",
    271: "Make", 272: "Model", 273: "StripOffsets", 274: "Orientation",
    277: "SamplesPerPixel", 278: "RowsPerStrip", 279: "StripByteCounts",
    280: "MinSampleValue", 281: "MaxSampleValue", 282: "XResolution", 283: "YResolution",
    284: "PlanarConfiguration", 296: "ResolutionUnit", 305: "Software", 306: "DateTime",
    317: "Predictor", 322: "TileWidth", 323: "TileLength", 324: "TileOffsets",
    325: "TileByteCounts", 339: "SampleFormat", 340: "SMinSampleValue", 341: "SMaxSampleValue",
}

COMPRESSION_NAMES = {
    1: "none", 2: "ccitt-rle", 3: "ccitt-g3", 4: "ccitt-g4", 5: "lzw", 6: "ojpeg",
    7: "jpeg", 8: "deflate", 32773: "packbits", 32946: "deflate", 34887: "lerc",
    34925: "lzma", 50000: "zstd", 50001: "webp",
}

PHOTOMETRIC_NAMES = {
    0: "MinIsWhite", 1: "MinIsBlack", 2: "RGB", 3: "Palette", 4: "Mask",
    5: "Separated", 6: "YCbCr", 8: "CIELab",
}

RESOLUTION_UNIT_NAMES = {1: "none", 2: "inch", 3: "centimeter"}

# тип TIFF -> (формат struct для одного значения, размер в байтах)
_TYPES = {
    1: ("B", 1), 2: ("B", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1),
    7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8), 11: ("f", 4), 12: ("d", 8),
    13: ("I", 4), 16: ("Q", 8), 17: ("q", 8), 18: ("Q", 8),
}
_ASCII = 2
_RATIONALS = (5, 10)
_MAX_PAGES = 10000


class TiffHeaderError(FileProcessingError):
    pass


@dataclass(frozen=True)
class TiffHeader:
    path: str
    byte_order: str
    bigtiff: bool
    page_count: int
    tags: Dict[int, Tuple] = field(repr=False)

    def tag(self, code, default=None):
        values = self.tags.get(code)
        if values is None:
            return default
        return values[0] if len(values) == 1 else values

    @property
    def width(self):
        return self.tag(256)

    @property
    def height(self):
        return self.tag(257)

    @property
    def bits_per_sample(self):
        bits = self.tags.get(258, (1,))
        return bits[0]

    @property
    def samples_per_pixel(self):
        return self.tag(277, 1)

    @property
    def compression(self):
        code = self.tag(259, 1)
        return COMPRESSION_NAMES.get(code, "unknown({})".format(code))

    @property
    def photometric(self):
        code = self.tag(262)
        if code is None:
            return None
        return PHOTOMETRIC_NAMES.get(code, "unknown({})".format(code))

    @property
    def sample_format(self):
        return {1: "uint", 2: "int", 3: "float"}.get(self.tag(339, 1), "unknown")

    @property
    def is_tiled(self):
        return 322 in self.tags

    @property
    def chunk_count(self):
        """Число strips или tiles."""
        offsets = self.tags.get(324 if self.is_tiled else 273, ())
        return len(offsets)

    @property
    def rows_per_strip(self):
        return self.tag(278)

    @property
    def resolution(self):
        # type: () -> Optional[Tuple[float, float, str]]
        if 282 not in self.tags or 283 not in self.tags:
            return None
        unit = RESOLUTION_UNIT_NAMES.get(self.tag(296, 2), "unknown")
        return self.tag(282), self.tag(283), unit

    def summary(self):
        tile = None
        if self.is_tiled:
            tile = [self.tag(322), self.tag(323)]
        return {
            "width": self.width,
            "height": self.height,
            "bits_per_sample": self.bits_per_sample,
            "samples_per_pixel": self.samples_per_pixel,
            "sample_format": self.sample_format,
            "photometric": self.photometric,
            "compression": self.compression,
            "predictor": self.tag(317),
            "planar": self.tag(284, 1),
            "layout": "tiles" if self.is_tiled else "strips",
            "tile_size": tile,
            "rows_per_strip": None if self.is_tiled else self.rows_per_strip,
            "chunk_count": self.chunk_count,
            "page_count": self.page_count,
            "smin_sample_value": self.tag(340),
            "smax_sample_value": self.tag(341),
            "resolution": self.resolution,
            "orientation": self.tag(274),
            "software": self.tag(305),
            "make": self.tag(271),
            "model": self.tag(272),
            "datetime": self.tag(306),
            "description": self.tag(270),
            "byte_order": self.byte_order,
            "bigtiff": self.bigtiff,
        }


def read_tiff_header(path):
    # type: (str) -> TiffHeader
    try:
        with open(path, "rb") as stream:
            return _parse(path, stream)
    except OSError as exc:
        raise TiffHeaderError(path, "не удалось прочитать файл: {}".format(exc)) from exc
    except struct.error as exc:
        raise TiffHeaderError(path, "повреждённый заголовок TIFF") from exc


def _parse(path, stream):
    head = stream.read(16)
    if len(head) < 8:
        raise TiffHeaderError(path, "файл слишком короткий для TIFF")
    if head[:2] == b"II":
        order = "<"
    elif head[:2] == b"MM":
        order = ">"
    else:
        raise TiffHeaderError(path, "нет сигнатуры TIFF")

    magic = struct.unpack(order + "H", head[2:4])[0]
    if magic == 42:
        bigtiff = False
        first_ifd = struct.unpack(order + "I", head[4:8])[0]
    elif magic == 43:
        bigtiff = True
        if len(head) < 16:
            raise TiffHeaderError(path, "обрезанный заголовок BigTIFF")
        first_ifd = struct.unpack(order + "Q", head[8:16])[0]
    else:
        raise TiffHeaderError(path, "неизвестная версия TIFF: {}".format(magic))

    tags = _read_ifd_tags(path, stream, order, bigtiff, first_ifd)
    page_count = _count_pages(path, stream, order, bigtiff, first_ifd)
    return TiffHeader(
        path=path,
        byte_order="little" if order == "<" else "big",
        bigtiff=bigtiff,
        page_count=page_count,
        tags=tags,
    )


def _ifd_layout(bigtiff):
    # (формат счётчика записей, размер записи, формат смещения, размер поля значения)
    if bigtiff:
        return "Q", 20, "Q", 8
    return "H", 12, "I", 4


def _read_exact(path, stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise TiffHeaderError(path, "заголовок TIFF обрывается")
    return data


def _read_ifd_tags(path, stream, order, bigtiff, offset):
    count_fmt, entry_size, offset_fmt, value_size = _ifd_layout(bigtiff)
    count_size = struct.calcsize(count_fmt)
    stream.seek(offset)
    entry_count = struct.unpack(order + count_fmt, _read_exact(path, stream, count_size))[0]
    raw = _read_exact(path, stream, entry_count * entry_size)

    tags = {}
    for index in range(entry_count):
        entry = raw[index * entry_size:(index + 1) * entry_size]
        code, type_id = struct.unpack(order + "HH", entry[:4])
        if type_id not in _TYPES:
            continue
        if bigtiff:
            count = struct.unpack(order + "Q", entry[4:12])[0]
            value_field = entry[12:20]
        else:
            count = struct.unpack(order + "I", entry[4:8])[0]
            value_field = entry[8:12]
        item_fmt, item_size = _TYPES[type_id]
        byte_length = count * item_size
        if byte_length <= value_size:
            data = value_field[:byte_length]
        else:
            value_offset = struct.unpack(order + offset_fmt, value_field)[0]
            position = stream.tell()
            stream.seek(value_offset)
            data = _read_exact(path, stream, byte_length)
            stream.seek(position)
        tags[code] = _decode_values(order, type_id, item_fmt, count, data)
    return tags


def _decode_values(order, type_id, item_fmt, count, data):
    if type_id == _ASCII:
        text = data.split(b"\x00", 1)[0]
        return (text.decode("utf-8", errors="replace").strip(),)
    values = struct.unpack(order + item_fmt * count, data)
    if type_id in _RATIONALS:
        pairs = zip(values[0::2], values[1::2])
        return tuple((num / den) if den else 0.0 for num, den in pairs)
    return values


def _count_pages(path, stream, order, bigtiff, offset):
    count_fmt, entry_size, offset_fmt, _ = _ifd_layout(bigtiff)
    count_size = struct.calcsize(count_fmt)
    next_size = struct.calcsize(offset_fmt)
    seen = set()
    pages = 0
    while offset and offset not in seen and pages < _MAX_PAGES:
        seen.add(offset)
        pages += 1
        stream.seek(offset)
        entry_count = struct.unpack(order + count_fmt, _read_exact(path, stream, count_size))[0]
        stream.seek(offset + count_size + entry_count * entry_size)
        offset = struct.unpack(order + offset_fmt, _read_exact(path, stream, next_size))[0]
    return pages
