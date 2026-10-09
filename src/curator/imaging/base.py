"""Интерфейс конвертера и реестр по расширениям (ARCHITECTURE.md, раздел 19).

Новый входной формат добавляется регистрацией нового ImageConverter —
сканер и остальные слои берут список расширений из реестра.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Optional, Tuple

from curator.domain.errors import UnsupportedImageError
from curator.imaging.tone import DEFAULT_TONE, ToneSpec

# progress(stage, fraction 0..1); вызывается из потока обработки.
ProgressCallback = Callable[[str, float], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True)
class ConversionOptions:
    tone: ToneSpec = DEFAULT_TONE
    quality: int = 100

    def __post_init__(self):
        if not 1 <= self.quality <= 100:
            raise ValueError("качество JPEG должно быть в диапазоне 1..100, получено {}".format(self.quality))


@dataclass(frozen=True)
class ConversionResult:
    source: str
    destination: str
    converter: str
    width: int
    height: int
    bands: int
    source_format: str
    photometric: Optional[str]
    compression: str
    tone: str
    window: Tuple[int, int]
    quality: int
    output_bytes: int
    seconds: float
    warnings: Tuple[str, ...] = ()

    def as_dict(self):
        return asdict(self)


class ImageConverter(ABC):
    name = ""  # type: str
    extensions = ()  # type: Tuple[str, ...]

    @abstractmethod
    def convert(self, source, destination, options, progress=None, is_cancelled=None):
        # type: (str, str, ConversionOptions, Optional[ProgressCallback], Optional[CancelCheck]) -> ConversionResult
        """Создать destination из source.

        destination не должен существовать (перезапись запрещена). При
        ошибке или отмене частично записанный destination удаляется.
        """


_registry = {}  # type: Dict[str, Callable[[], ImageConverter]]


def register_converter(extensions, factory):
    for extension in extensions:
        _registry[extension.lower()] = factory


def supported_extensions():
    return tuple(sorted(_registry))


def converter_for(path):
    # type: (str) -> ImageConverter
    extension = os.path.splitext(path)[1].lower()
    factory = _registry.get(extension)
    if factory is None:
        raise UnsupportedImageError(path, "нет конвертера для расширения {!r}".format(extension))
    return factory()
