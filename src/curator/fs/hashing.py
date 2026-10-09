"""Хеши и отпечатки файлов (ARCHITECTURE.md, 9.1).

Оригинал удаляется только тогда, когда два независимых чтения — самого
оригинала и его копии — дали одинаковый sha256. Хеш оригинала считается
по ходу копирования, хеш копии — отдельным чтением уже с диска.
"""

import hashlib
import os

CHUNK_SIZE = 1024 * 1024
ALGORITHM = "sha256"


def file_hash(path, chunk_size=CHUNK_SIZE, on_bytes=None, is_cancelled=None):
    digest = hashlib.new(ALGORITHM)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            if is_cancelled is not None and is_cancelled():
                raise _cancelled(path)
            digest.update(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
    return format_digest(digest)


def copy_with_hash(source, destination, chunk_size=CHUNK_SIZE, on_bytes=None, is_cancelled=None):
    """Скопировать файл, посчитав по дороге хеш прочитанных байтов.

    Возвращает (размер, хеш прочитанного). Данные и метаданные каталога
    сбрасываются на диск, чтобы после сбоя питания копия не осталась
    наполовину записанной.
    """
    digest = hashlib.new(ALGORITHM)
    written = 0
    with open(source, "rb") as reader, open(destination, "wb") as writer:
        for chunk in iter(lambda: reader.read(chunk_size), b""):
            if is_cancelled is not None and is_cancelled():
                raise _cancelled(source)
            writer.write(chunk)
            digest.update(chunk)
            written += len(chunk)
            if on_bytes is not None:
                on_bytes(len(chunk))
        writer.flush()
        os.fsync(writer.fileno())
    return written, format_digest(digest)


def file_signature(path):
    """Размер и время изменения — быстрая проверка, что файл не менялся."""
    info = os.stat(path)
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns}


def same_signature(path, signature):
    try:
        return file_signature(path) == signature
    except OSError:
        return False


def format_digest(digest):
    return "{}:{}".format(ALGORITHM, digest.hexdigest())


def _cancelled(path):
    from curator.domain.errors import ConversionCancelled

    return ConversionCancelled(path)
