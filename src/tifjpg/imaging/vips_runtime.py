"""Загрузка pyvips/libvips и настройка памяти (ARCHITECTURE.md, 8.5).

pyvips импортируется только через load_pyvips(): так остальной код не
падает при импорте, если libvips недоступен, а ошибка превращается в
понятную VipsUnavailableError.
"""

import os
import sys
import threading

from tifjpg.domain.errors import VipsUnavailableError

DEFAULT_CONCURRENCY = 2

_lock = threading.Lock()
_pyvips = None


def bundled_vips_dir():
    """Каталог с DLL libvips.

    Собранное приложение использует только DLL из своей папки _internal;
    переменная TIFJPG_VIPS_DIR учитывается лишь при запуске из исходников
    (тесты на Windows).
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return base
    override = os.environ.get("TIFJPG_VIPS_DIR")
    if override:
        return override if os.path.isdir(override) else None
    return None


def load_pyvips(concurrency=DEFAULT_CONCURRENCY):
    """Импортировать pyvips один раз за процесс.

    concurrency учитывается только при первом вызове: libvips читает его
    при инициализации.
    """
    global _pyvips
    with _lock:
        if _pyvips is None:
            _pyvips = _import_pyvips(concurrency)
        return _pyvips


def _import_pyvips(concurrency):
    os.environ["VIPS_CONCURRENCY"] = str(concurrency)
    dll_dir = bundled_vips_dir()
    if dll_dir and sys.platform == "win32":
        os.add_dll_directory(dll_dir)
        os.environ["PATH"] = dll_dir + os.pathsep + os.environ.get("PATH", "")
    try:
        import pyvips
    except Exception as exc:  # ImportError или OSError из cffi.dlopen
        raise VipsUnavailableError("не удалось загрузить libvips: {}".format(exc)) from exc

    # Кэш операций не нужен при однократной потоковой обработке и только тратит память.
    pyvips.cache_set_max(0)
    pyvips.cache_set_max_mem(0)
    pyvips.cache_set_max_files(0)
    return pyvips


def at_least(vips, major, minor):
    return (vips.version(0), vips.version(1)) >= (major, minor)


def vips_version():
    vips = load_pyvips()
    return "{}.{}.{}".format(vips.version(0), vips.version(1), vips.version(2))
