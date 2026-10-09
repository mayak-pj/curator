"""Проверка готового JPEG (ARCHITECTURE.md, 8.4)."""

from dataclasses import dataclass
from typing import Optional

from curator.domain.errors import FileProcessingError
from curator.imaging.jpeg_info import JpegHeaderError, read_jpeg_header
from curator.imaging.vips_runtime import at_least, load_pyvips


@dataclass(frozen=True)
class JpegCheck:
    width: int
    height: int
    components: int
    file_size: int
    estimated_quality: Optional[int]


def validate_jpeg(path, expected_width, expected_height, expected_components=None):
    # type: (str, int, int, Optional[int]) -> JpegCheck
    """Заголовок, маркер конца, размеры и полное декодирование без предупреждений."""
    try:
        header = read_jpeg_header(path)
    except (OSError, JpegHeaderError) as exc:
        raise FileProcessingError(path, "JPEG не читается: {}".format(exc)) from exc

    if header.file_size == 0:
        raise FileProcessingError(path, "JPEG пустой")
    if not header.ends_with_eoi:
        raise FileProcessingError(path, "JPEG обрезан: нет маркера конца файла")
    if (header.width, header.height) != (expected_width, expected_height):
        raise FileProcessingError(
            path,
            "размер JPEG {}x{} не совпадает с исходным {}x{}".format(
                header.width, header.height, expected_width, expected_height
            ),
        )
    if expected_components is not None and header.components != expected_components:
        raise FileProcessingError(
            path, "каналов в JPEG {}, ожидалось {}".format(header.components, expected_components)
        )

    vips = load_pyvips()
    kwargs = {"access": "sequential"}
    if at_least(vips, 8, 12):
        kwargs["fail_on"] = "warning"
    else:
        kwargs["fail"] = True
    try:
        vips.Image.jpegload(path, **kwargs).avg()
    except vips.Error as exc:
        raise FileProcessingError(path, "JPEG не декодируется: {}".format(str(exc).strip())) from exc

    return JpegCheck(
        width=header.width,
        height=header.height,
        components=header.components,
        file_size=header.file_size,
        estimated_quality=header.estimated_quality,
    )
