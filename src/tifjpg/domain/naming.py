"""Схема именования (ARCHITECTURE.md, разделы 7 и 6).

Программа создаёт имена вида `Рентгенограмма 1.jpeg`, но обработанной
считает и папку, где человек назвал файлы иначе — `Рентгенограмма 1.jpg`,
`рентгенограмма-1.jpeg` и т. п. Поэтому признак «наш результат» —
вхождение слова, а не точный шаблон (решение D15).

Другая схема именования = другая реализация NamingScheme, выбирается в
config.json.
"""

import os
from abc import ABC, abstractmethod

JPEG_EXTENSIONS = (".jpg", ".jpeg")


class NamingScheme(ABC):
    @abstractmethod
    def archive_dir_name(self):
        """Имя создаваемой папки архива."""

    @abstractmethod
    def jpeg_name(self, index):
        """Имя JPEG для порядкового номера index (с 1)."""

    @abstractmethod
    def source_name(self, index):
        """Имя оригинала в папке архива."""

    @abstractmethod
    def is_processed_jpeg(self, filename):
        """Похож ли файл на результат обработки — нашей или ручной."""


class XrayNaming(NamingScheme):
    prefix = "Рентгенограмма"
    marker = "рентгенограмма"
    archive_dir = "ИСХ"
    jpeg_extension = ".jpeg"
    source_extension = ".tif"

    def archive_dir_name(self):
        return self.archive_dir

    def jpeg_name(self, index):
        return "{} {}{}".format(self.prefix, index, self.jpeg_extension)

    def source_name(self, index):
        return "{} {}{}".format(self.prefix, index, self.source_extension)

    def is_processed_jpeg(self, filename):
        stem, extension = os.path.splitext(filename)
        if extension.lower() not in JPEG_EXTENSIONS:
            return False
        return self.marker in normalize(stem)

    def is_archive_dir(self, name):
        return normalize(name) == normalize(self.archive_dir)


def normalize(text):
    """Регистр и «ё» не должны влиять на сравнение имён."""
    return text.lower().replace("ё", "е")
