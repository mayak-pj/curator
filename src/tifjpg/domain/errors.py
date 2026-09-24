"""Иерархия ошибок приложения (ARCHITECTURE.md, раздел 17).

Этап 1 содержит только классы, нужные модулю imaging; остальные
добавляются на своих этапах.
"""


class TifJpgError(Exception):
    """Базовый класс всех ожидаемых ошибок приложения."""


class FatalError(TifJpgError):
    """Обработка невозможна в принципе (например, не загрузился libvips)."""


class VipsUnavailableError(FatalError):
    pass


class FileProcessingError(TifJpgError):
    """Ошибка обработки конкретного файла. Ведёт к откату папки."""

    def __init__(self, path, reason):
        super().__init__("{}: {}".format(path, reason))
        self.path = path
        self.reason = reason


class UnsupportedImageError(FileProcessingError):
    """Файл читается, но его параметры не поддерживаются конвертером."""


class ConversionCancelled(TifJpgError):
    """Конвертация остановлена пользователем."""

    def __init__(self, path):
        super().__init__("{}: конвертация отменена".format(path))
        self.path = path
