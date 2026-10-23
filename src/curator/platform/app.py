"""Запуск окна версии 2.

    curator.exe                    стартовое окно
    curator.exe --snapshot ПАПКА   проверка без участия человека (CI, выезд): снимки окна в обеих
                                   темах и отчёт platform_check.txt в ПАПКУ; код возврата 1 при ошибке
    curator.exe --selftest accept|rollback ДЕРЕВО ПАПКА
                                   окно обработки само проходит «Проверить» → «Начать обработку» →
                                   «Принять» (или «Откатить» + «Отменить всё») на дереве ДЕРЕВО;
                                   снимки и отчёт xray_selftest.txt в ПАПКУ (modules/xray/selftest.py)
"""

import ctypes
import os
import sys
import tempfile
import time
import traceback

from curator import __version__, appdirs
from curator.platform import modules as registry
from curator.platform import qt_runtime, state

STARTED = time.monotonic()
REPORT_NAME = "platform_check.txt"


class Context:
    """То, что стартовое окно даёт модулям: папки программы, действующая тема и есть ли человек.

    interactive=False — проверка без человека (--snapshot, --selftest): модули не открывают
    диалогов, а подтверждения считаются полученными.
    """

    def __init__(self, app, paths, interactive=True):
        self.app = app
        self.paths = paths
        self.interactive = interactive
        self.theme = None

    def set_theme(self, name, remember=True):
        from curator.platform import theme as theme_module

        cache = os.path.join(tempfile.gettempdir(), appdirs.APP_NAME, "theme")
        self.theme = theme_module.apply_theme(self.app, name, cache)
        if remember:
            state.save(self.paths.gui_state, dark=self.theme.name == "dark")
        return self.theme


def _create_app(paths, interactive=True):
    qt_runtime.prepare()
    from PyQt5.QtWidgets import QApplication

    # Второй QApplication в одном процессе роняет Qt (тесты создают окно несколько раз).
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Curator v2")
    russian = qt_runtime.install_russian(app)
    context = Context(app, paths, interactive)
    context.set_theme("dark" if state.load(paths.gui_state).get("dark") else "light", remember=False)
    return app, context, russian


def run(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--snapshot"]:
        return snapshot(argv[1] if len(argv) > 1 else os.getcwd())
    if argv[:1] == ["--selftest"]:
        return selftest(argv[1:])
    paths = appdirs.AppPaths()
    try:
        app, context, _ = _create_app(paths)
        from curator.platform.start_window import StartWindow

        window = StartWindow(context)
        window.show()
    except Exception:
        _show_fatal("Не удалось открыть окно программы.\n\n" + traceback.format_exc().splitlines()[-1])
        raise
    return app.exec_()


def snapshot(out_dir):
    """Стартовое окно и страница каждого рабочего модуля в обеих темах — PNG и текстовый отчёт."""
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, REPORT_NAME)
    lines = ["Curator {} — проверка окна".format(__version__), "Папка программы: {}".format(appdirs.app_dir())]
    failures = 0

    def write():
        with open(report_path, "w", encoding="utf-8") as stream:
            stream.write("\n".join(lines + ["", "ИТОГ: {}".format(
                "ошибок нет" if not failures else "ошибок: {}".format(failures))]) + "\n")

    # Если Qt не поднимет платформу, он завершит процесс сам — отчёт к этому моменту уже на диске.
    lines.append("Создание окна Qt (если строка последняя — Qt аварийно завершил процесс)")
    write()
    lines.pop()
    try:
        paths = appdirs.AppPaths()
        app, context, russian = _create_app(paths, interactive=False)
        from PyQt5.QtCore import PYQT_VERSION_STR, qVersion

        from curator.platform.start_window import StartWindow

        lines.append("PyQt {}, Qt {}, платформа «{}»".format(PYQT_VERSION_STR, qVersion(), app.platformName()))
        lines.append("Плагины Qt: {}".format(qt_runtime.plugins_dir() or "НЕ НАЙДЕНЫ"))
        if not qt_runtime.plugins_dir():
            failures += 1
        lines.append("Русские подписи Qt: {}".format("да" if russian else "НЕТ ({})".format(qt_runtime.translations_dir())))
        if not russian:
            failures += 1
        window = StartWindow(context)
        window.show()
        app.processEvents()
        lines.append("Запуск до окна: {:.1f} с".format(time.monotonic() - STARTED))
        lines.append("Шрифты: {}".format(context.theme.info.get("fonts")))
        lines.append("Иконок: {}, стрелки QSS: {}".format(context.theme.info.get("icons"),
                                                         context.theme.info.get("qss_images")))
        lines.append("Плиток: {} (рабочих {})".format(
            len(window.start_page.tiles), sum(1 for tile in window.start_page.tiles if tile.isEnabled())))
        original = context.theme.name
        pages = [("start", None)] + [(module.id, module) for module in registry.MODULES if module.available]
        for theme_name in ("light", "dark"):
            context.set_theme(theme_name, remember=False)
            for page_name, module in pages:
                if module is None:
                    window.show_start()
                else:
                    window.open_module(module)
                window.refresh_theme()
                app.processEvents()
                path = os.path.join(out_dir, "{}_{}.png".format(page_name, theme_name))
                if not window.grab().save(path, "PNG"):
                    failures += 1
                    lines.append("Снимок НЕ сохранён: {}".format(path))
                else:
                    lines.append("Снимок: {}".format(path))
        page = window.module_page
        if page is not None and hasattr(page, "lines"):
            lines.append("Модуль xray: {}".format("готов" if page.ready else "обработка недоступна"))
            lines.extend("    " + line for line in page.lines)
        context.set_theme(original, remember=False)
        window.close()
        window.shutdown_modules()
    except Exception:
        failures += 1
        lines.append("ОШИБКА: " + traceback.format_exc().splitlines()[-1])
        lines.extend("    " + line for line in traceback.format_exc().splitlines())
    write()
    return 1 if failures else 0


def selftest(args):
    """Окно обработки без человека на настоящем сервисе: проверка 26.5 в CI и на выезде."""
    if len(args) != 3 or args[0] not in ("accept", "rollback"):
        _show_fatal("Использование: curator --selftest accept|rollback ДЕРЕВО ПАПКА_ОТЧЁТА")
        return 2
    from curator.modules.xray import selftest as xray_selftest

    scenario, root, out_dir = args
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, xray_selftest.REPORT_NAME)
    lines = ["Curator {} — проверка окна обработки ({})".format(__version__, scenario)]
    try:
        paths = appdirs.AppPaths()
        app, context, _ = _create_app(paths, interactive=False)
        from curator.platform.start_window import StartWindow

        window = StartWindow(context)
        window.show()
        problems = xray_selftest.run(window, scenario, root, out_dir, lines)
        window.close()
        window.shutdown_modules()
    except Exception:
        problems = ["ОШИБКА: " + traceback.format_exc().splitlines()[-1]]
        lines.extend("    " + line for line in traceback.format_exc().splitlines())
    lines.extend(["", "ИТОГ: {}".format("ошибок нет" if not problems else "ошибок: {}".format(len(problems)))])
    lines.extend("  - " + problem for problem in problems)
    with open(report_path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return 1 if problems else 0


def _show_fatal(message):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "Curator v2", 0x10)
    elif sys.stderr:
        sys.stderr.write(message + "\n")
