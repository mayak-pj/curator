"""Безопасные файловые операции (ARCHITECTURE.md, раздел 9).

Правила, из которых всё следует:
  * ничего не перезаписываем — переименование в занятое имя запрещено;
  * копия проверяется по хешу до того, как оригинал будет удалён;
  * незавершённые файлы всегда имеют суффикс .part и готовыми не считаются.
"""

import os
import shutil

from tifjpg.domain.errors import IntegrityError, PreconditionError
from tifjpg.fs.hashing import CHUNK_SIZE, copy_with_hash, file_hash

PART_SUFFIX = ".part"


def part_path(path):
    return path + PART_SUFFIX


def ensure_dir(path):
    """Создать папку, если её нет. Возвращает True, если создали именно мы."""
    if os.path.isdir(path):
        return False
    os.makedirs(path)
    return True


def safe_rename(source, destination):
    """Переименование без перезаписи: занятое имя — ошибка, а не потеря данных."""
    if os.path.lexists(destination):
        raise FileExistsError(17, "файл уже существует", destination)
    os.rename(source, destination)


def copy_verified(source, destination, chunk_size=CHUNK_SIZE, on_bytes=None, is_cancelled=None):
    """Скопировать source в destination + .part и проверить копию по хешу.

    Возвращает (путь к .part, хеш). Оригинал не трогается. Хеш оригинала
    считается при чтении, хеш копии — отдельным чтением с диска: совпадение
    означает, что на диске лежат два одинаковых файла.
    """
    temporary = part_path(destination)
    remove_quietly(temporary)
    _, source_digest = copy_with_hash(source, temporary, chunk_size=chunk_size,
                                      on_bytes=on_bytes, is_cancelled=is_cancelled)
    copy_digest = file_hash(temporary, chunk_size=chunk_size, is_cancelled=is_cancelled)
    if copy_digest != source_digest:
        remove_quietly(temporary)
        raise IntegrityError(source, "копия не совпала с оригиналом ({} против {})".format(
            copy_digest, source_digest))
    return temporary, source_digest


def verify_copy(path, expected_hash, chunk_size=CHUNK_SIZE):
    actual = file_hash(path, chunk_size=chunk_size)
    if actual != expected_hash:
        raise IntegrityError(path, "хеш не совпал: {} вместо {}".format(actual, expected_hash))
    return actual


def remove_quietly(path):
    try:
        os.remove(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def remove_dir_if_empty(path):
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def free_space(path):
    """Свободное место на томе, где лежит path (учитывая квоты пользователя)."""
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe or ".").free


def require_free_space(path, needed_bytes, margin=0.05, what="диске"):
    available = free_space(path)
    required = int(needed_bytes * (1 + margin))
    if available < required:
        raise PreconditionError(
            "на {} недостаточно места: нужно {:.1f} ГБ, свободно {:.1f} ГБ ({})".format(
                what, required / 1e9, available / 1e9, path))
    return available
