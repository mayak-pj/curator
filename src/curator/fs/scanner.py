"""Рекурсивный обход дерева папок (ARCHITECTURE.md, раздел 10.1).

Только чтение: собирает снимок содержимого каждой папки. Папка архива не
считается самостоятельной — она принадлежит своему родителю, и сканер в
неё не спускается.
"""

import os

from curator.domain.models import FolderSnapshot
from curator.domain.ordering import natural_key


def scan_tree(root, naming, on_error=None):
    """Снимки всех папок дерева, включая корневую.

    on_error(path, exception) вызывается для папок, которые не удалось
    прочитать (нет прав, оборвалась сеть); обход продолжается.
    """
    snapshots = []
    for directory, subdirectories, files in os.walk(root, onerror=lambda exc: _report(on_error, exc)):
        archive_dir = None
        for name in list(subdirectories):
            if naming.is_archive_dir(name):
                archive_dir = name
                subdirectories.remove(name)  # не спускаемся в архив
        subdirectories.sort(key=natural_key)

        archive_files = ()
        if archive_dir:
            try:
                archive_files = tuple(_files_in(os.path.join(directory, archive_dir)))
            except OSError as exc:
                _report(on_error, exc)
                archive_files = ()

        snapshots.append(FolderSnapshot(
            path=directory,
            files=tuple(sorted(files, key=natural_key)),
            archive_dir=archive_dir,
            archive_files=archive_files,
        ))
    return snapshots


def read_folder(path, naming):
    """Снимок одной папки — для повторной проверки состояния перед работой."""
    entries = list(os.scandir(path))
    files = tuple(sorted((entry.name for entry in entries if entry.is_file()), key=natural_key))
    archive_dir = next(
        (entry.name for entry in entries if entry.is_dir() and naming.is_archive_dir(entry.name)), None)
    archive_files = ()
    if archive_dir:
        archive_files = tuple(_files_in(os.path.join(path, archive_dir)))
    return FolderSnapshot(path=path, files=files, archive_dir=archive_dir, archive_files=archive_files)


def _files_in(path):
    return sorted((entry.name for entry in os.scandir(path) if entry.is_file()), key=natural_key)


def _report(on_error, exc):
    if on_error is not None:
        on_error(getattr(exc, "filename", None), exc)
