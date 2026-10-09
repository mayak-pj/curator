"""Восстановление после сбоя (ARCHITECTURE.md, раздел 14).

Журналу верят только как записи намерений: фактическое состояние всегда
сверяется с диском. Отсюда три возможных положения дел у папки и три
действия, которые предлагаются пользователю: продолжить, откатить,
игнорировать.
"""

import os
from dataclasses import dataclass, field
from typing import List

from curator.domain import states
from curator.fs import safe_copy
from curator.fs.hashing import CHUNK_SIZE, file_hash, same_signature
from curator.transaction.journal import NOTE, read_journal
from curator.transaction.records import folders_from_journal, session_finished

CONSISTENT = "consistent"                  # на диске всё согласовано, делать нечего
PHASE_A_LEFTOVERS = "phase_a_leftovers"    # остались только временные файлы
PHASE_B_UNFINISHED = "phase_b_unfinished"  # часть файлов зафиксирована, часть нет

JOURNAL_SUFFIX = ".jsonl"


@dataclass
class FolderRecovery:
    record: object
    situation: str
    details: List[str] = field(default_factory=list)

    @property
    def folder(self):
        return self.record.folder

    @property
    def needs_attention(self):
        return self.situation != CONSISTENT


def find_unfinished(journal_dir):
    """Журналы, которые могли остаться после сбоя: без отметки о завершении."""
    if not os.path.isdir(journal_dir):
        return []
    unfinished = []
    for name in sorted(os.listdir(journal_dir)):
        if not name.endswith(JOURNAL_SUFFIX):
            continue
        path = os.path.join(journal_dir, name)
        try:
            entries = read_journal(path)
        except OSError:
            continue
        if not entries:
            continue
        if not session_finished(entries):
            unfinished.append(path)
            continue
        if any(record.state == states.FOLDER_ROLLBACK_REQUIRED
               for record in folders_from_journal(entries).values()):
            unfinished.append(path)
    return unfinished


def inspect(journal_path):
    """Разбор одного журнала: по папке на запись, с состоянием по факту на диске."""
    entries = read_journal(journal_path)
    return [analyse(record) for record in folders_from_journal(entries).values()]


def analyse(record):
    details = []
    situation = CONSISTENT
    for file_record in record.ordered():
        state = _file_situation(file_record)
        if state == PHASE_B_UNFINISHED:
            situation = PHASE_B_UNFINISHED
            details.append("#{}: {} — фиксация не завершена".format(
                file_record.index, os.path.basename(file_record.source or "?")))
        elif state == PHASE_A_LEFTOVERS:
            if situation == CONSISTENT:
                situation = PHASE_A_LEFTOVERS
            details.append("#{}: остались временные файлы".format(file_record.index))
    return FolderRecovery(record=record, situation=situation, details=details)


def _file_situation(file_record):
    jpeg, archive, source = file_record.jpeg, file_record.archive, file_record.source
    if not jpeg or not archive or not source:
        return CONSISTENT

    parts_left = os.path.lexists(safe_copy.part_path(jpeg)) or os.path.lexists(safe_copy.part_path(archive))
    jpeg_ready = os.path.exists(jpeg)
    archive_ready = os.path.exists(archive)
    source_left = os.path.lexists(source)

    if file_record.source_in_archive:
        # Оригинал остаётся в архиве: работа закончена, когда он носит новое имя.
        if jpeg_ready and archive_ready and not source_left:
            return PHASE_A_LEFTOVERS if parts_left else CONSISTENT
        if jpeg_ready or archive_ready:
            return PHASE_B_UNFINISHED
        return PHASE_A_LEFTOVERS if parts_left else CONSISTENT

    if jpeg_ready and archive_ready and not source_left:
        return PHASE_A_LEFTOVERS if parts_left else CONSISTENT
    if jpeg_ready or archive_ready:
        # Что-то уже зафиксировано, а оригинал ещё на месте — фаза B прервалась.
        return PHASE_B_UNFINISHED
    return PHASE_A_LEFTOVERS if parts_left else CONSISTENT


@dataclass
class FinishResult:
    folder: str
    actions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def complete(self):
        return not self.warnings


def finish(record, journal, chunk_size=CHUNK_SIZE):
    """Довести прерванную фазу B до конца. Операция идемпотентна."""
    result = FinishResult(folder=record.folder)
    journal.folder_state(record.folder, "recovery_finish_started")

    for file_record in record.ordered():
        jpeg, archive, source = file_record.jpeg, file_record.archive, file_record.source
        if not jpeg or not archive or not source:
            continue

        jpeg_part = safe_copy.part_path(jpeg)
        if not os.path.exists(jpeg) and os.path.exists(jpeg_part):
            safe_copy.safe_rename(jpeg_part, jpeg)
            _note(journal, record, file_record, "finish_jpeg", jpeg, result,
                  "{} доведён до готового имени".format(os.path.basename(jpeg)))

        if file_record.source_in_archive:
            if os.path.lexists(source) and not os.path.exists(archive):
                safe_copy.safe_rename(source, archive)
                _note(journal, record, file_record, "finish_rename", archive, result,
                      "{} -> {}".format(os.path.basename(source), os.path.basename(archive)))
            continue

        archive_part = safe_copy.part_path(archive)
        if not os.path.exists(archive) and os.path.exists(archive_part):
            safe_copy.safe_rename(archive_part, archive)
            _note(journal, record, file_record, "finish_archive", archive, result,
                  "копия {} доведена до готового имени".format(os.path.basename(archive)))

        if os.path.lexists(source) and os.path.exists(archive):
            if not _matches(archive, file_record.source_hash, chunk_size):
                result.warnings.append("{}: копия в архиве не совпадает с журналом — оригинал оставлен".format(
                    os.path.basename(source)))
                continue
            if file_record.signature and not same_signature(source, file_record.signature):
                result.warnings.append("{}: оригинал изменился — оставлен на месте".format(
                    os.path.basename(source)))
                continue
            os.remove(source)
            _note(journal, record, file_record, "finish_delete_source", source, result,
                  "удалён оригинал {}".format(os.path.basename(source)))

    journal.folder_state(record.folder, "recovery_finished", warnings=result.warnings)
    return result


def _matches(path, expected, chunk_size):
    if not expected:
        return False
    try:
        return file_hash(path, chunk_size=chunk_size) == expected
    except OSError:
        return False


def _note(journal, record, file_record, action, path, result, message):
    journal.record(NOTE, folder=record.folder, n=file_record.index, action=action, path=path)
    result.actions.append(message)
