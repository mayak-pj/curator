"""Логирование (ARCHITECTURE.md, 12.2).

Человекочитаемый лог сессии лежит рядом с exe, в папке logs. Отдельный
обработчик копит сообщения от WARNING и выше — из них панель ошибок в GUI
показывает список проблем. Телеметрии нет: наружу ничего не уходит.
"""

import logging
import os

LOGGER_NAME = "curator"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class ProblemCollector(logging.Handler):
    """Хранит предупреждения и ошибки сессии для панели ошибок."""

    def __init__(self, level=logging.WARNING):
        super().__init__(level=level)
        self.records = []

    def emit(self, record):
        self.records.append({
            "level": record.levelname,
            "message": record.getMessage(),
            "time": record.created,
            "folder": getattr(record, "folder", None),
            "file": getattr(record, "file", None),
        })

    def clear(self):
        self.records = []


def configure(log_dir, session_name, level="INFO"):
    """Настроить логгер приложения. Возвращает (логгер, путь к файлу, сборщик проблем)."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    path = None
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, session_name)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(getattr(logging, level, logging.INFO))
        file_handler.setFormatter(logging.Formatter(FORMAT))
        logger.addHandler(file_handler)

    collector = ProblemCollector()
    logger.addHandler(collector)
    return logger, path, collector


def get_logger():
    return logging.getLogger(LOGGER_NAME)
