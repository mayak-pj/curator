"""Модели предметной области (ARCHITECTURE.md, разделы 6 и 10).

Чистые данные без ввода-вывода: снимок содержимого папки, решение по ней
и план обработки. Всё, что читает диск, живёт в fs/.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, Tuple

TIFF_EXTENSIONS = (".tif", ".tiff")

# Виды решений по папке
PROCESS_ROOT = "process_root"
PROCESS_FROM_ARCHIVE = "process_from_archive"
DONE = "done"
CONFLICT = "conflict"
EMPTY = "empty"

PROCESSABLE = (PROCESS_ROOT, PROCESS_FROM_ARCHIVE)


@dataclass(frozen=True)
class FolderSnapshot:
    """Что лежит в папке и в её папке архива на момент сканирования."""

    path: str
    files: Tuple[str, ...] = ()
    archive_dir: Optional[str] = None  # фактическое имя: ИСХ, Исх, …
    archive_files: Tuple[str, ...] = ()

    def tiffs(self):
        return tuple(name for name in self.files if _is_tiff(name))

    def archive_tiffs(self):
        return tuple(name for name in self.archive_files if _is_tiff(name))

    def tiff_count(self):
        return len(self.tiffs()) + len(self.archive_tiffs())

    def processed_jpegs(self, naming):
        """JPEG в самой папке, похожие на результат обработки (см. D15)."""
        return tuple(name for name in self.files if naming.is_processed_jpeg(name))

    def archive_path(self, naming):
        """Путь к папке архива: существующей или той, которую предстоит создать."""
        return os.path.join(self.path, self.archive_dir or naming.archive_dir_name())


@dataclass(frozen=True)
class FolderDecision:
    kind: str
    rule: str
    reason: str

    @property
    def processable(self):
        return self.kind in PROCESSABLE


@dataclass(frozen=True)
class PlannedFile:
    """Один исходник и его будущие имена."""

    index: int
    source: str
    jpeg: str
    archive: str
    source_in_archive: bool

    @property
    def archive_unchanged(self):
        """Оригинал уже лежит в архиве под нужным именем — переименование не нужно."""
        return self.source_in_archive and os.path.normcase(self.source) == os.path.normcase(self.archive)


@dataclass(frozen=True)
class FolderPlan:
    folder: str
    decision: FolderDecision
    files: Tuple[PlannedFile, ...] = ()
    archive_dir: str = ""
    creates_archive_dir: bool = False
    problems: Tuple[str, ...] = field(default=())
    # Переименование внутри архива, где целевое имя занято другим исходником,
    # выполняется через временные имена (этап 4).
    two_step_renames: bool = False

    @property
    def ok(self):
        return self.decision.processable and not self.problems and bool(self.files)


def _is_tiff(name):
    return os.path.splitext(name)[1].lower() in TIFF_EXTENSIONS
