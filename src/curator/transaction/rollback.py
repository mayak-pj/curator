"""Откат папки (ARCHITECTURE.md, раздел 13).

Главное правило: **ничего не удаляем без проверки**. Файл удаляется, только
если его sha256 совпадает с тем, что программа записала в журнал. Если
пользователь успел изменить, переименовать или заменить файл, он остаётся
на месте, а в отчёт попадает предупреждение.
"""

import os
from dataclasses import dataclass, field
from typing import List

from curator.domain import states
from curator.fs import safe_copy
from curator.fs.hashing import CHUNK_SIZE, file_hash
from curator.transaction.journal import NOTE

ROLLED_BACK = "rolled_back"
PARTIAL = "rollback_partial"
NOTHING = "nothing_to_roll_back"

# Состояния, в которых оригинал уже не на своём исходном месте.
_MOVED = (states.SOURCE_FINAL, states.COMPLETED)


@dataclass
class FileRollback:
    index: int
    actions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RollbackResult:
    folder: str
    state: str
    files: List[FileRollback] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def complete(self):
        return self.state == ROLLED_BACK


def rollback_folder(record, journal, chunk_size=CHUNK_SIZE):
    """Вернуть папку в состояние до обработки, насколько это безопасно."""
    result = RollbackResult(folder=record.folder, state=ROLLED_BACK)
    journal.folder_state(record.folder, "rollback_started")

    touched = False
    for file_record in reversed(record.ordered()):
        file_result = FileRollback(index=file_record.index)
        result.files.append(file_result)
        restored = _restore_original(file_record, file_result, journal, chunk_size)
        if restored:
            _remove_jpeg(file_record, file_result, journal, chunk_size)
        elif os.path.exists(file_record.jpeg):
            # Оригинал вернуть не удалось — JPEG остаётся единственной копией снимка.
            file_result.warnings.append("{}: оригинал не восстановлен, JPEG оставлен".format(
                os.path.basename(file_record.jpeg)))
        touched = touched or bool(file_result.actions)
        result.warnings.extend(file_result.warnings)

    if record.archive_created and record.archive_dir:
        if safe_copy.remove_dir_if_empty(record.archive_dir):
            journal.record(NOTE, folder=record.folder, action="rollback_remove_archive_dir",
                           path=record.archive_dir)
        elif os.path.isdir(record.archive_dir):
            result.warnings.append("папка {} не пуста — оставлена".format(record.archive_dir))

    if result.warnings:
        result.state = PARTIAL
    elif not touched:
        result.state = NOTHING
    journal.folder_state(record.folder, result.state, warnings=result.warnings)
    return result


def _restore_original(file_record, file_result, journal, chunk_size):
    """Вернуть оригинал на место. True — оригинал доступен, False — восстановить не удалось."""
    source, archive = file_record.source, file_record.archive
    if not source or not archive:
        return True

    # Следы фазы A: временные файлы всегда наши, их можно убирать без проверки.
    for temporary in (safe_copy.part_path(file_record.jpeg), safe_copy.part_path(archive)):
        if safe_copy.remove_quietly(temporary):
            _note(journal, file_record, file_result, "removed_part", temporary,
                  "удалён временный файл {}".format(os.path.basename(temporary)))

    if file_record.state not in _MOVED:
        return True

    if file_record.source_in_archive:
        # Оригинал никуда не копировался, его только переименовали внутри архива.
        if os.path.lexists(source):
            return True
        if not os.path.exists(archive):
            file_result.warnings.append("{}: файл в архиве не найден, имя не восстановлено".format(
                os.path.basename(source)))
            return False
        if not _hash_matches(archive, file_record.source_hash, chunk_size):
            file_result.warnings.append("{}: файл в архиве изменён — имя не восстановлено".format(
                os.path.basename(archive)))
            return False
        safe_copy.safe_rename(archive, source)
        _note(journal, file_record, file_result, "renamed_back", source,
              "{} -> {}".format(os.path.basename(archive), os.path.basename(source)))
        return True

    if not os.path.exists(archive):
        if os.path.exists(source):
            return True
        file_result.warnings.append("{}: ни оригинала, ни копии в архиве — восстановить нечем".format(
            os.path.basename(source)))
        return False

    if not _hash_matches(archive, file_record.source_hash, chunk_size):
        file_result.warnings.append("{}: копия в архиве изменена — не трогаем".format(
            os.path.basename(archive)))
        return os.path.exists(source)

    if os.path.exists(source):
        # Оригинал на месте: копия в архиве — наша, её можно убрать.
        safe_copy.remove_quietly(archive)
        _note(journal, file_record, file_result, "removed_archive_copy", archive,
              "удалена копия {}".format(os.path.basename(archive)))
        return True

    temporary, digest = safe_copy.copy_verified(archive, source, chunk_size=chunk_size)
    if file_record.source_hash and digest != file_record.source_hash:
        safe_copy.remove_quietly(temporary)
        file_result.warnings.append("{}: восстановленная копия не совпала с журналом".format(
            os.path.basename(source)))
        return False
    safe_copy.safe_rename(temporary, source)
    safe_copy.remove_quietly(archive)
    _note(journal, file_record, file_result, "restored_original", source,
          "оригинал восстановлен из архива: {}".format(os.path.basename(source)))
    return True


def _remove_jpeg(file_record, file_result, journal, chunk_size):
    jpeg = file_record.jpeg
    if not jpeg or not os.path.exists(jpeg):
        return
    if file_record.state not in (states.JPEG_FINAL, states.SOURCE_FINAL, states.COMPLETED):
        return
    if not _hash_matches(jpeg, file_record.jpeg_hash, chunk_size):
        file_result.warnings.append("{}: JPEG изменён после обработки — оставлен".format(
            os.path.basename(jpeg)))
        return
    safe_copy.remove_quietly(jpeg)
    _note(journal, file_record, file_result, "removed_jpeg", jpeg,
          "удалён {}".format(os.path.basename(jpeg)))


def _hash_matches(path, expected, chunk_size):
    if not expected:
        return False
    try:
        return file_hash(path, chunk_size=chunk_size) == expected
    except OSError:
        return False


def _note(journal, file_record, file_result, action, path, message):
    journal.record(NOTE, folder=os.path.dirname(file_record.jpeg), n=file_record.index,
                   action="rollback_" + action, path=path)
    file_result.actions.append(message)
