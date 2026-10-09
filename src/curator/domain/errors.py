"""Иерархия ошибок приложения (ARCHITECTURE.md, раздел 17).

Этап 1 содержит только классы, нужные модулю imaging; остальные
добавляются на своих этапах.
"""


class CuratorError(Exception):
    """Базовый класс всех ожидаемых ошибок приложения."""


class FatalError(CuratorError):
    """Обработка невозможна в принципе (например, не загрузился libvips)."""


class VipsUnavailableError(FatalError):
    pass


class FileProcessingError(CuratorError):
    """Ошибка обработки конкретного файла. Ведёт к откату папки."""

    def __init__(self, path, reason):
        super().__init__("{}: {}".format(path, reason))
        self.path = path
        self.reason = reason


class UnsupportedImageError(FileProcessingError):
    """Файл читается, но его параметры не поддерживаются конвертером."""


class TransientNetworkError(CuratorError):
    """Временный сбой сети или диска: операцию можно повторить."""

    def __init__(self, operation, cause):
        super().__init__("{}: {}".format(operation, cause))
        self.operation = operation
        self.cause = cause


class PreconditionError(CuratorError):
    """Условия для обработки не выполнены: нет места, нет прав, длинный путь."""


class IntegrityError(FileProcessingError):
    """Копия не совпала с оригиналом — оригинал не трогаем."""


class JournalError(CuratorError):
    """Журнал недоступен: без него обработка запрещена (ARCHITECTURE.md, 10.4)."""


class ConversionCancelled(CuratorError):
    """Конвертация остановлена пользователем."""

    def __init__(self, path):
        super().__init__("{}: конвертация отменена".format(path))
        self.path = path
