"""Состояние папки, собранное из журнала (ARCHITECTURE.md, разделы 12–14).

Одна и та же структура используется и для отката в текущей сессии, и для
разбора журнала прошлого запуска после сбоя.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from curator.transaction.journal import FILE_STATE, FOLDER_STATE, NOTE, SESSION_END, SESSION_START

_FILE_FIELDS = ("source", "jpeg", "archive", "signature", "source_in_archive",
                "source_hash", "jpeg_hash", "archive_hash")


@dataclass
class FileRecord:
    index: int
    source: str = ""
    jpeg: str = ""
    archive: str = ""
    state: Optional[str] = None       # последнее подтверждённое состояние
    intent: Optional[str] = None      # последнее записанное намерение
    source_hash: Optional[str] = None
    jpeg_hash: Optional[str] = None
    archive_hash: Optional[str] = None
    signature: Optional[dict] = None
    source_in_archive: bool = False


@dataclass
class FolderRecord:
    folder: str
    files: Dict[int, FileRecord] = field(default_factory=dict)
    archive_dir: str = ""
    archive_created: bool = False
    state: Optional[str] = None
    session: Optional[dict] = None

    def file(self, index):
        if index not in self.files:
            self.files[index] = FileRecord(index=index)
        return self.files[index]

    def ordered(self):
        # type: () -> List[FileRecord]
        return [self.files[index] for index in sorted(self.files)]


def folders_from_journal(entries):
    """Все папки, встречающиеся в журнале, с их последним состоянием."""
    session = None
    folders = {}  # type: Dict[str, FolderRecord]
    for entry in entries:
        if entry.get("type") == SESSION_START:
            session = entry
            continue
        folder_path = entry.get("folder")
        if not folder_path:
            continue
        record = folders.setdefault(folder_path, FolderRecord(folder=folder_path, session=session))
        kind = entry.get("type")
        if kind == FOLDER_STATE:
            record.state = entry.get("state", record.state)
            record.archive_dir = entry.get("archive") or record.archive_dir
        elif kind == NOTE and entry.get("action") == "archive_created":
            record.archive_created = True
            record.archive_dir = entry.get("path") or record.archive_dir
        elif kind == FILE_STATE:
            _apply_file_entry(record, entry)
    return folders


def session_finished(entries):
    return any(entry.get("type") == SESSION_END for entry in entries)


def folder_from_result(plan, result):
    """Запись о папке по результату работы исполнителя — для отката без чтения журнала."""
    record = FolderRecord(folder=plan.folder, archive_dir=plan.archive_dir,
                          archive_created=result.archive_created, state=result.state)
    for planned, file_result in zip(plan.files, result.files):
        record.files[planned.index] = FileRecord(
            index=planned.index,
            source=planned.source,
            jpeg=planned.jpeg,
            archive=planned.archive,
            state=file_result.state,
            intent=file_result.state,
            source_hash=file_result.source_hash,
            jpeg_hash=file_result.jpeg_hash,
            archive_hash=file_result.source_hash,
            signature=file_result.signature,
            source_in_archive=planned.source_in_archive,
        )
    return record


def _apply_file_entry(record, entry):
    index = entry.get("n")
    if index is None:
        return
    file_record = record.file(index)
    for name in _FILE_FIELDS:
        if entry.get(name) is not None:
            setattr(file_record, name, entry[name])
    state = entry.get("state")
    if entry.get("phase") == "done":
        file_record.state = state
    else:
        file_record.intent = state
    if not record.archive_dir and file_record.archive:
        import os

        record.archive_dir = os.path.dirname(file_record.archive)
