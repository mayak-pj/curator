"""Правила «папка готова / папку обрабатывать» (ARCHITECTURE.md, раздел 6).

Отдельный заменяемый модуль: чтобы поменять политику, достаточно написать
другую функцию с той же сигнатурой и выбрать её в config.json.

Правила default_v1 (решение D15, уточнено пользователем 2026-09-24):
  T — число TIFF в папке и в её архиве суммарно;
  J — число JPEG в самой папке, похожих на результат обработки.
"""

from tifjpg.domain.models import (
    CONFLICT,
    DONE,
    EMPTY,
    PROCESS_FROM_ARCHIVE,
    PROCESS_ROOT,
    FolderDecision,
)

NAME = "default_v1"


def classify(snapshot, naming):
    """FolderSnapshot -> FolderDecision."""
    tiffs = snapshot.tiffs()
    archive_tiffs = snapshot.archive_tiffs()
    total_tiffs = len(tiffs) + len(archive_tiffs)
    jpegs = snapshot.processed_jpegs(naming)

    if not total_tiffs:
        # Конвертировать нечего: либо папка уже доведена до конца и оригиналы
        # унесены, либо в ней просто нет снимков.
        if jpegs:
            return FolderDecision(DONE, "R1", "снимков нет, JPEG уже есть ({} шт.)".format(len(jpegs)))
        return FolderDecision(EMPTY, "R6", "ни TIFF, ни JPEG")

    if jpegs:
        if len(jpegs) == total_tiffs:
            return FolderDecision(
                DONE, "R1",
                "папка уже обработана: JPEG {} шт. = TIFF {} шт.".format(len(jpegs), total_tiffs))
        return FolderDecision(
            CONFLICT, "R2",
            "JPEG {} шт., а TIFF {} шт. — количество не совпадает".format(len(jpegs), total_tiffs))

    if tiffs and archive_tiffs:
        return FolderDecision(
            CONFLICT, "R5",
            "TIFF есть и в папке ({} шт.), и в {} ({} шт.)".format(
                len(tiffs), snapshot.archive_dir, len(archive_tiffs)))

    if tiffs:
        return FolderDecision(PROCESS_ROOT, "R3", "TIFF в папке: {} шт.".format(len(tiffs)))

    return FolderDecision(
        PROCESS_FROM_ARCHIVE, "R4",
        "TIFF только в {}: {} шт., JPEG создаются в самой папке".format(
            snapshot.archive_dir, len(archive_tiffs)))
