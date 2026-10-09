"""Блокировки обрабатываемых папок (ARCHITECTURE.md, 12.3).

Программа лежит в общей папке, и запускать её могут несколько человек
одновременно. Блокировка не даёт двум сессиям взяться за одну папку с
снимками. Файлы блокировок лежат рядом с exe, имя — хеш нормализованного
пути, поэтому `Z:\\Объект` и `\\\\сервер\\общая\\Объект` считаются одной папкой.
"""

import ctypes
import datetime
import getpass
import hashlib
import json
import os
import socket
import sys
import time

from curator.domain.errors import CuratorError

STALE_MINUTES = 10
HEARTBEAT_SECONDS = 60


class FolderLocked(CuratorError):
    def __init__(self, folder, owner):
        super().__init__("папка {} уже обрабатывается: {}".format(folder, describe_owner(owner)))
        self.folder = folder
        self.owner = owner


def normalize(path):
    """Путь в сравнимом виде: сетевой диск разворачивается в UNC."""
    absolute = os.path.abspath(path)
    if sys.platform == "win32":
        absolute = _universal_name(absolute) or absolute
    return os.path.normcase(absolute.rstrip("\\/"))


def lock_filename(folder):
    digest = hashlib.sha1(normalize(folder).encode("utf-8")).hexdigest()
    return digest + ".lock"


def describe_owner(owner):
    if not owner:
        return "владелец неизвестен"
    return "пользователь {} на компьютере {} (pid {}, с {})".format(
        owner.get("user", "?"), owner.get("host", "?"), owner.get("pid", "?"), owner.get("started", "?"))


def read_lock(path):
    try:
        with open(path, encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def is_stale(owner, stale_minutes=STALE_MINUTES, now=None):
    """Блокировка считается брошенной, если давно не обновлялась."""
    if not owner:
        return True
    stamp = owner.get("heartbeat") or owner.get("started")
    if not stamp:
        return True
    try:
        moment = datetime.datetime.fromisoformat(stamp)
    except ValueError:
        return True
    now = now or datetime.datetime.now(moment.tzinfo)
    return (now - moment).total_seconds() > stale_minutes * 60


class FolderLock:
    def __init__(self, folder, directory, stale_minutes=STALE_MINUTES):
        self.folder = folder
        self.path = os.path.join(directory, lock_filename(folder))
        self.directory = directory
        self.stale_minutes = stale_minutes
        self._held = False

    def acquire(self, take_over_stale=False):
        os.makedirs(self.directory, exist_ok=True)
        try:
            self._create()
        except FileExistsError:
            owner = read_lock(self.path)
            if not (take_over_stale and is_stale(owner, self.stale_minutes)):
                raise FolderLocked(self.folder, owner)
            os.remove(self.path)
            self._create()
        self._held = True
        return self

    def heartbeat(self):
        if self._held:
            self._write(self._payload(started=self._started))

    def release(self):
        if not self._held:
            return
        owner = read_lock(self.path)
        # Чужую блокировку не трогаем: свою узнаём по pid и компьютеру.
        if owner and owner.get("pid") == os.getpid() and owner.get("host") == _host():
            try:
                os.remove(self.path)
            except OSError:
                pass
        self._held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback):
        self.release()

    def _create(self):
        self._started = _now()
        handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(self._payload(started=self._started), stream, ensure_ascii=False)

    def _write(self, payload):
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)

    def _payload(self, started):
        return {
            "folder": self.folder,
            "normalized": normalize(self.folder),
            "host": _host(),
            "user": _user(),
            "pid": os.getpid(),
            "started": started,
            "heartbeat": _now(),
        }


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _host():
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _user():
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _universal_name(path):
    """Буква сетевого диска -> UNC (\\\\сервер\\общая\\...). None для локальных путей."""
    try:
        mpr = ctypes.WinDLL("mpr")
    except (AttributeError, OSError):
        return None
    size = ctypes.c_ulong(2048)
    buffer = ctypes.create_string_buffer(size.value)
    # UNIVERSAL_NAME_INFO_LEVEL = 1
    result = mpr.WNetGetUniversalNameW(ctypes.c_wchar_p(path), 1, buffer, ctypes.byref(size))
    if result != 0:
        return None
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_wchar_p))
    return pointer.contents.value
