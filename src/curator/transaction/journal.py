"""Журнал операций (ARCHITECTURE.md, раздел 12).

Append-only JSONL: одна запись на строку, после каждой — flush и fsync.
Намерение пишется до действия на диске, поэтому по журналу можно
восстановить картину после любого сбоя. Если журнал недоступен, работать
нельзя — откат перестал бы быть гарантированным.
"""

import datetime
import getpass
import json
import os
import socket
import threading
import uuid

from curator.domain.errors import JournalError

SESSION_START = "session_start"
SESSION_END = "session_end"
FOLDER_STATE = "folder_state"
FILE_STATE = "file_state"
NOTE = "note"


class Journal:
    def __init__(self, path, header=None):
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        directory = os.path.dirname(os.path.abspath(path))
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._stream = open(path, "a", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise JournalError("не удалось открыть журнал {}: {}".format(path, exc)) from exc
        if header is not None:
            self.record(SESSION_START, **header)

    def record(self, type, **fields):
        with self._lock:
            self._seq += 1
            entry = {"ts": _timestamp(), "seq": self._seq, "type": type}
            entry.update(fields)
            line = json.dumps(entry, ensure_ascii=False)
            try:
                self._stream.write(line + "\n")
                self._stream.flush()
                os.fsync(self._stream.fileno())
            except OSError as exc:
                raise JournalError("не удалось записать в журнал {}: {}".format(self.path, exc)) from exc
            return self._seq

    def file_state(self, folder, index, state, **fields):
        return self.record(FILE_STATE, folder=folder, n=index, state=state, **fields)

    def folder_state(self, folder, state, **fields):
        return self.record(FOLDER_STATE, folder=folder, state=state, **fields)

    def close(self, **fields):
        if self._stream.closed:
            return
        try:
            self.record(SESSION_END, **fields)
        finally:
            self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close(error=None if exc is None else "{}: {}".format(exc_type.__name__, exc))


def session_header(root, app_version, rules, extra=None):
    header = {
        "app_version": app_version,
        "host": _safe(socket.gethostname),
        "user": _safe(getpass.getuser),
        "pid": os.getpid(),
        "root": os.path.abspath(root),
        "rules": rules,
    }
    if extra:
        header.update(extra)
    return header


def session_filename(now=None):
    """Имя файла сессии: у каждой сессии свой файл, конкурентной записи нет.

    Случайный суффикс нужен потому, что времени с точностью до секунды и pid
    не хватает: два запуска подряд в одном процессе давали одно имя.
    """
    now = now or datetime.datetime.now()
    return "{}_{}_{}_{}_{}.jsonl".format(
        now.strftime("%Y%m%d-%H%M%S"), _safe(socket.gethostname), _safe(getpass.getuser),
        os.getpid(), uuid.uuid4().hex[:6])


def read_journal(path):
    """Прочитать журнал, пропустив оборванную последнюю строку (сбой питания)."""
    entries = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def _timestamp():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _safe(function, default="unknown"):
    try:
        value = function()
    except Exception:
        return default
    return "".join(character for character in value if character.isalnum() or character in "-_.") or default
