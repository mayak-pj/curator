"""Обработка изображений. pyvips импортируется лениво — при первой конвертации."""

from tifjpg.imaging.base import (
    ConversionOptions,
    ConversionResult,
    ImageConverter,
    converter_for,
    register_converter,
    supported_extensions,
)
from tifjpg.imaging.tone import DEFAULT_TONE, ToneSpec
from tifjpg.imaging.tone import METHODS as TONE_METHODS
from tifjpg.imaging.validate import JpegCheck, validate_jpeg
from tifjpg.imaging.vips_converter import VipsTiffJpegConverter

register_converter(VipsTiffJpegConverter.extensions, VipsTiffJpegConverter)

__all__ = [
    "ConversionOptions",
    "ConversionResult",
    "DEFAULT_TONE",
    "ImageConverter",
    "JpegCheck",
    "TONE_METHODS",
    "ToneSpec",
    "VipsTiffJpegConverter",
    "converter_for",
    "register_converter",
    "supported_extensions",
    "validate_jpeg",
]
