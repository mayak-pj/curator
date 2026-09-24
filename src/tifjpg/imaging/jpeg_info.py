"""Чтение заголовка JPEG без декодирования (чистый Python).

Даёт размеры, режим (baseline/progressive), субдискретизацию и оценку
качества по таблицам квантования (формула IJG/libjpeg). Используется в
валидации и для сравнения с JPEG прежней программы.
"""

import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Стандартная таблица яркости JPEG (Annex K), построчный порядок.
STD_LUMINANCE = (
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
)

# i-й коэффициент в zigzag-порядке -> индекс в построчном порядке.
ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)

_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
_PROGRESSIVE_MARKERS = {0xC2, 0xC6, 0xCA, 0xCE}
_STANDALONE_MARKERS = set(range(0xD0, 0xD8)) | {0x01}


class JpegHeaderError(ValueError):
    pass


@dataclass(frozen=True)
class JpegHeader:
    width: int
    height: int
    components: int
    precision: int
    progressive: bool
    sampling: Tuple[Tuple[int, int], ...]
    quant_tables: Dict[int, List[int]]
    estimated_quality: Optional[int]
    quality_exact: bool
    ends_with_eoi: bool
    file_size: int

    def summary(self):
        return {
            "width": self.width,
            "height": self.height,
            "components": self.components,
            "precision": self.precision,
            "progressive": self.progressive,
            "sampling": ["{}x{}".format(h, v) for h, v in self.sampling],
            "estimated_quality": self.estimated_quality,
            "quality_exact": self.quality_exact,
            "ends_with_eoi": self.ends_with_eoi,
            "file_size": self.file_size,
        }


def read_jpeg_header(path):
    # type: (str) -> JpegHeader
    file_size = os.path.getsize(path)
    with open(path, "rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            raise JpegHeaderError("{}: нет сигнатуры JPEG".format(path))
        frame = None
        progressive = False
        tables = {}  # type: Dict[int, List[int]]
        while True:
            marker = _next_marker(stream)
            if marker is None:
                break
            if marker in _STANDALONE_MARKERS:
                continue
            if marker == 0xD9:
                break
            length_bytes = stream.read(2)
            if len(length_bytes) != 2:
                break
            length = struct.unpack(">H", length_bytes)[0]
            payload = stream.read(length - 2)
            if len(payload) != length - 2:
                raise JpegHeaderError("{}: сегмент JPEG обрывается".format(path))
            if marker == 0xDB:
                _parse_dqt(payload, tables)
            elif marker in _SOF_MARKERS:
                frame = _parse_sof(payload)
                progressive = marker in _PROGRESSIVE_MARKERS
            elif marker == 0xDA:
                break
        if frame is None:
            raise JpegHeaderError("{}: не найден заголовок кадра (SOF)".format(path))

        ends_with_eoi = False
        if file_size >= 2:
            stream.seek(file_size - 2)
            ends_with_eoi = stream.read(2) == b"\xff\xd9"

    precision, height, width, sampling = frame
    quality, exact = estimate_quality(tables.get(0))
    return JpegHeader(
        width=width,
        height=height,
        components=len(sampling),
        precision=precision,
        progressive=progressive,
        sampling=tuple(sampling),
        quant_tables=tables,
        estimated_quality=quality,
        quality_exact=exact,
        ends_with_eoi=ends_with_eoi,
        file_size=file_size,
    )


def estimate_quality(luminance):
    # type: (Optional[List[int]]) -> Tuple[Optional[int], bool]
    """Качество IJG (1..100), чья таблица ближе всего к данной; exact — точное совпадение."""
    if not luminance or len(luminance) != 64:
        return None, False
    best_quality = None
    best_error = None
    for quality in range(1, 101):
        error = 0
        for actual, expected in zip(luminance, ijg_table(quality)):
            error += abs(actual - expected)
        if best_error is None or error < best_error:
            best_quality, best_error = quality, error
    return best_quality, best_error == 0


def ijg_table(quality):
    scale = 5000 // quality if quality < 50 else 200 - quality * 2
    return [min(max((value * scale + 50) // 100, 1), 255) for value in STD_LUMINANCE]


def _next_marker(stream):
    byte = stream.read(1)
    while byte and byte != b"\xff":
        byte = stream.read(1)
    if not byte:
        return None
    while byte == b"\xff":
        byte = stream.read(1)
    if not byte:
        return None
    return byte[0]


def _parse_dqt(payload, tables):
    position = 0
    while position < len(payload):
        info = payload[position]
        position += 1
        wide = info >> 4
        table_id = info & 0x0F
        if wide:
            values = struct.unpack(">64H", payload[position:position + 128])
            position += 128
        else:
            values = tuple(payload[position:position + 64])
            position += 64
        natural = [0] * 64
        for zigzag_index, value in enumerate(values):
            natural[ZIGZAG[zigzag_index]] = value
        tables[table_id] = natural


def _parse_sof(payload):
    precision, height, width, components = struct.unpack(">BHHB", payload[:6])
    sampling = []
    for index in range(components):
        factors = payload[6 + index * 3 + 1]
        sampling.append((factors >> 4, factors & 0x0F))
    return precision, height, width, sampling
