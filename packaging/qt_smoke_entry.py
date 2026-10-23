"""Этап 11: проверка Qt (PyQt5) + libvips в одной сборке PyInstaller на Windows 7 (ARCHITECTURE.md, 26.3).

    curator_qt_smoke.exe           окно (тема Fusion + QSS, SVG-иконки) с результатами и кнопкой «Конвертировать свой TIFF…»
    curator_qt_smoke.exe --no-gui  те же проверки, окно открывается на секунду для снимков; код возврата 1 при ошибке

Отчёт — qt_smoke_report.txt, снимки окна в обеих темах — qt_smoke_light.png и qt_smoke_dark.png
рядом с exe (если туда нельзя — в %LOCALAPPDATA%\\curator-smoke). Выбранный пользователем TIFF только читается.
Проверки libvips, CRT и конвертации — общие с этапом 2 (smoke_entry.py).
"""

import ctypes
import datetime
import os
import sys
import threading
import time
import traceback

from curator import __version__
from curator.imaging import ConversionOptions, VipsTiffJpegConverter, validate_jpeg

import smoke_entry
from smoke_entry import app_dir, check, peak_memory_mb, work_dir, write_text

REPORT_NAME = "qt_smoke_report.txt"
SNAPSHOT_NAMES = {"light": "qt_smoke_light.png", "dark": "qt_smoke_dark.png"}
STARTED = time.monotonic()

# Цвета — подмножество токенов digger (digger/ui/theme/tokens.py); полная тема переносится на этапе 12.
THEMES = {
    "light": {
        "bg": "#f4f6f9", "surface": "#ffffff", "border": "#d5dae1", "text": "#1c2430", "text_muted": "#647082",
        "text_disabled": "#a3acb8", "accent": "#2563eb", "accent_hover": "#1d4fd8", "on_accent": "#ffffff",
        "icon": "#465264",
    },
    "dark": {
        "bg": "#171a1f", "surface": "#20242b", "border": "#363c47", "text": "#e6e9ee", "text_muted": "#9aa4b2",
        "text_disabled": "#5f6875", "accent": "#5b8cff", "accent_hover": "#7aa2ff", "on_accent": "#0e1116",
        "icon": "#b4bcc8",
    },
}

QSS = """
QWidget {{ background: {bg}; color: {text}; font-size: 13px; }}
QLabel#title {{ font-size: 16px; font-weight: bold; }}
QLabel#muted {{ color: {text_muted}; }}
QPlainTextEdit {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 6px;
                  font-family: Consolas, Menlo, monospace; font-size: 12px; }}
QPushButton {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 6px 14px; min-height: 20px; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:disabled {{ color: {text_disabled}; }}
QPushButton#primary {{ background: {accent}; color: {on_accent}; border-color: {accent}; }}
QPushButton#primary:hover {{ background: {accent_hover}; }}
QProgressBar {{ background: {surface}; border: 1px solid {border}; border-radius: 6px; height: 12px; text-align: center; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 5px; }}
"""


class Report(smoke_entry.Report):
    def text(self):
        header = "Curator Qt smoke {} — {}".format(__version__, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        summary = "ИТОГ: {}".format("ошибок нет" if not self.failures else "ошибок: {}".format(self.failures))
        return "\n".join([header, ""] + self.lines + ["", summary])


def resource_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def icon_path(name):
    return os.path.join(resource_dir(), "qt_smoke_icons", name + ".svg")


def qt_plugins_dir():
    """Папка плагинов Qt внутри пакета PyQt5 (пустая строка, если её нет)."""
    import PyQt5

    path = os.path.join(os.path.dirname(os.path.abspath(PyQt5.__file__)), "Qt5", "plugins")
    return path if os.path.isdir(os.path.join(path, "platforms")) else ""


def process_age():
    """Секунды с момента создания процесса (включая загрузчик PyInstaller); None вне Windows."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *[ctypes.byref(item) for item in times]):
        return None
    created = (times[0].dwHighDateTime << 32 | times[0].dwLowDateTime) / 1e7 - 11644473600
    return time.time() - created


def startup_seconds():
    try:
        age = process_age()
    except Exception:
        age = None
    return age if age is not None else time.monotonic() - STARTED


def describe_location():
    path = app_dir()
    traits = []
    if path.startswith("\\\\"):
        traits.append("сетевой путь UNC")
    if any(ord(char) > 127 for char in path):
        traits.append("есть не-ASCII символы")
    return "{} ({})".format(path, ", ".join(traits) if traits else "локальный путь, только ASCII")


def base_checks():
    """Всё, что не требует Qt: окружение, libvips, CRT, конвертация (как на этапе 2)."""
    report = Report()
    smoke_entry.environment(report)
    report.add("INFO", "Расположение", describe_location())
    check(report, "libvips", smoke_entry.check_vips)
    if sys.platform == "win32":
        check(report, "CRT и Python", smoke_entry.check_runtime_dlls)
    check(report, "Конвертация LZW TIFF", smoke_entry.check_conversion("lzw"))
    try:
        report.add("OK", "Папка программы", smoke_entry.check_app_dir_writable())
    except OSError as exc:
        report.add("WARN", "Папка программы", "запись невозможна: {}".format(exc))
    return report


def prepare_qt(report):
    """До создания QApplication: путь к плагинам задаётся явно (урок digger 01.10.2026 — из папки
    с русскими буквами Qt плагины сам не находит, окно не открывается)."""
    from PyQt5.QtCore import QCoreApplication, Qt

    plugins = qt_plugins_dir()
    if plugins:
        QCoreApplication.addLibraryPath(plugins)
        platform_plugin = os.path.join(plugins, "platforms", "qwindows.dll")
        if sys.platform == "win32" and not os.path.exists(platform_plugin):
            report.add("FAIL", "Плагины Qt", "нет {}".format(platform_plugin))
        else:
            report.add("OK", "Плагины Qt", plugins)
    else:
        report.add("FAIL", "Плагины Qt", "папка PyQt5/Qt5/plugins/platforms не найдена")
    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QCoreApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)


def qt_translations_dir():
    """Переводы Qt внутри пакета PyQt5. QLibraryInfo.TranslationsPath из папки с русскими буквами
    возвращает путь с «?» вместо букв (CI, 23.10.2026) — поэтому, как и плагины, путь задаём сами."""
    import PyQt5

    return os.path.join(os.path.dirname(os.path.abspath(PyQt5.__file__)), "Qt5", "translations")


def install_russian(app):
    from PyQt5.QtCore import QLocale, QTranslator

    translator = QTranslator(app)
    folder = qt_translations_dir()
    if not translator.load(QLocale("ru_RU"), "qtbase", "_", folder):
        raise RuntimeError("qtbase_ru.qm не найден в {}".format(folder))
    app.installTranslator(translator)
    app._curator_translator = translator
    return "qtbase_ru из {}".format(folder)


def svg_bytes(name, color):
    with open(icon_path(name), "r", encoding="utf-8") as stream:
        return stream.read().replace("currentColor", color).encode("utf-8")


def themed_icon(name, color, size=18):
    """Иконка Lucide, перекрашенная под тему (как в digger: currentColor → цвет из токенов)."""
    from PyQt5.QtCore import QByteArray, QRectF, Qt
    from PyQt5.QtGui import QIcon, QPainter, QPixmap
    from PyQt5.QtSvg import QSvgRenderer

    scale = 2
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg_bytes(name, color))).render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)


def check_qt_versions(app):
    from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, qVersion

    return "PyQt {}, Qt {} (собрано с {}), платформа «{}»".format(
        PYQT_VERSION_STR, qVersion(), QT_VERSION_STR, app.platformName())


def check_fusion(app):
    from PyQt5.QtWidgets import QStyleFactory

    keys = QStyleFactory.keys()
    if "Fusion" not in keys:
        raise RuntimeError("стиля Fusion нет, есть: {}".format(", ".join(keys)))
    app.setStyle("Fusion")
    return "стили: {}".format(", ".join(keys))


def check_svg():
    """QtSvg рисует иконку: на картинке есть непрозрачные точки нужного цвета."""
    from PyQt5.QtCore import QByteArray, Qt
    from PyQt5.QtGui import QColor, QImage, QPainter
    from PyQt5.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg_bytes("lightbulb", "#ff0000")))
    if not renderer.isValid():
        raise RuntimeError("QSvgRenderer не разобрал файл")
    image = QImage(48, 48, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    painted = sum(1 for x in range(48) for y in range(48) if QColor(image.pixel(x, y)).alpha() > 0)
    if not painted:
        raise RuntimeError("иконка нарисовалась пустой")
    return "иконка 48×48 нарисована ({} точек)".format(painted)


def check_icon_engine():
    """QIcon из .svg напрямую — через плагин iconengines/qsvgicon."""
    from PyQt5.QtGui import QIcon

    icon = QIcon(icon_path("folder-open"))
    if icon.isNull() or icon.pixmap(32, 32).isNull():
        raise RuntimeError("QIcon из SVG пустая — плагин iconengines не загрузился")
    return "QIcon из SVG работает"


def check_image_formats():
    from PyQt5.QtGui import QImageReader

    formats = sorted(bytes(item).decode("ascii") for item in QImageReader.supportedImageFormats())
    missing = [name for name in ("jpeg", "png", "svg") if name not in formats]
    if missing:
        raise RuntimeError("нет форматов {} (есть: {})".format(", ".join(missing), ", ".join(formats)))
    return ", ".join(formats)


def qt_checks(report, app):
    check(report, "Qt", lambda: check_qt_versions(app))
    check(report, "Стиль Fusion", lambda: check_fusion(app))
    check(report, "SVG (QtSvg)", check_svg)
    check(report, "SVG через QIcon", check_icon_engine)
    check(report, "Форматы изображений Qt", check_image_formats)
    check(report, "Русские подписи Qt", lambda: install_russian(app))


def save_snapshot(widget, name):
    """Снимок окна рядом с exe, а если туда нельзя — в рабочую папку."""
    pixmap = widget.grab()
    for folder in (app_dir(), work_dir()):
        path = os.path.join(folder, name)
        if pixmap.save(path, "PNG"):
            return path
    raise RuntimeError("не удалось сохранить {}".format(name))


def make_window_class():
    from PyQt5.QtCore import QObject, Qt, pyqtSignal
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import (
        QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QProgressBar,
        QPushButton, QVBoxLayout, QWidget,
    )

    class Signals(QObject):
        """Сигналы из фонового потока доставляются в поток окна очередью Qt (как будет на этапе 13)."""
        progress = pyqtSignal(str, float)
        done = pyqtSignal(str, str)

    class SmokeWindow(QMainWindow):
        def __init__(self, report):
            super().__init__()
            self.report = report
            self.theme = "light"
            self.signals = Signals()
            self.signals.progress.connect(self.on_progress)
            self.signals.done.connect(self.on_done)
            self.setWindowTitle("Curator v2 — проверка Qt на Windows 7")
            self.resize(940, 640)

            central = QWidget()
            layout = QVBoxLayout(central)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(10)

            header = QHBoxLayout()
            self.logo = QLabel()
            header.addWidget(self.logo)
            titles = QVBoxLayout()
            title = QLabel("Curator v2 — проверка Qt и libvips")
            title.setObjectName("title")
            subtitle = QLabel("Отчёт и снимки окна сохраняются рядом с программой: {}".format(app_dir()))
            subtitle.setObjectName("muted")
            subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
            titles.addWidget(title)
            titles.addWidget(subtitle)
            header.addLayout(titles, 1)
            layout.addLayout(header)

            self.text = QPlainTextEdit()
            self.text.setReadOnly(True)
            self.text.setFont(QFont("Consolas", 10))
            layout.addWidget(self.text, 1)

            self.progress = QProgressBar()
            self.progress.setRange(0, 1000)
            self.progress.setTextVisible(False)
            layout.addWidget(self.progress)
            self.status = QLabel("")
            self.status.setObjectName("muted")
            layout.addWidget(self.status)

            buttons = QHBoxLayout()
            self.convert_button = QPushButton("Конвертировать свой TIFF…")
            self.convert_button.setObjectName("primary")
            self.convert_button.clicked.connect(self.choose)
            self.theme_button = QPushButton()
            self.theme_button.clicked.connect(self.toggle_theme)
            self.folder_button = QPushButton("Открыть папку с результатами")
            self.folder_button.clicked.connect(self.open_work_dir)
            self.close_button = QPushButton("Закрыть")
            self.close_button.clicked.connect(self.close)
            for button in (self.convert_button, self.theme_button, self.folder_button):
                buttons.addWidget(button)
            buttons.addStretch(1)
            buttons.addWidget(self.close_button)
            layout.addLayout(buttons)

            self.setCentralWidget(central)
            self.apply_theme("light")
            self.refresh()

        def apply_theme(self, name):
            self.theme = name
            colors = THEMES[name]
            QApplication.instance().setStyleSheet(QSS.format(**colors))
            self.logo.setPixmap(themed_icon("lightbulb", colors["accent"], 32).pixmap(32, 32))
            self.convert_button.setIcon(themed_icon("image-plus", colors["on_accent"]))
            self.theme_button.setIcon(themed_icon("lightbulb", colors["icon"]))
            self.theme_button.setText("Светлая тема" if name == "dark" else "Тёмная тема")
            self.folder_button.setIcon(themed_icon("folder-open", colors["icon"]))
            self.close_button.setIcon(themed_icon("x", colors["icon"]))

        def toggle_theme(self):
            self.apply_theme("dark" if self.theme == "light" else "light")

        def snapshots(self):
            """Снимки обеих тем; тема окна возвращается прежней."""
            current = self.theme
            paths = []
            for name in ("light", "dark"):
                self.apply_theme(name)
                QApplication.processEvents()
                paths.append(save_snapshot(self, SNAPSHOT_NAMES[name]))
            self.apply_theme(current)
            return ", ".join(paths)

        def refresh(self):
            self.text.setPlainText(self.report.text())
            path = write_text(REPORT_NAME, self.report.text())
            self.status.setText("Отчёт: {}".format(path or "не удалось сохранить"))

        def set_busy(self, busy):
            self.convert_button.setEnabled(not busy)

        def choose(self):
            source, _ = QFileDialog.getOpenFileName(
                self, "Выберите TIFF (файл только читается)", "",
                "TIFF (*.tif *.tiff *.TIF *.TIFF);;Все файлы (*.*)")
            if not source:
                return
            folder = os.path.join(work_dir(), "manual")
            os.makedirs(folder, exist_ok=True)
            index = 1
            while os.path.exists(os.path.join(folder, "Рентгенограмма {}.jpeg".format(index))):
                index += 1
            destination = os.path.join(folder, "Рентгенограмма {}.jpeg".format(index))
            self.set_busy(True)
            self.progress.setValue(0)
            threading.Thread(target=self.convert, args=(os.path.normpath(source), destination), daemon=True).start()

        def convert(self, source, destination):
            started = time.monotonic()
            try:
                result = VipsTiffJpegConverter().convert(
                    source, destination, ConversionOptions(),
                    progress=lambda stage, fraction: self.signals.progress.emit(stage, fraction),
                )
                check_result = validate_jpeg(destination, result.width, result.height, result.bands)
                memory = peak_memory_mb()
                detail = "{} -> {} | {}x{} {} {} | JPEG Q{} {:.1f} МБ | конвертация {:.1f} с, всего {:.1f} с | пик памяти {} МБ".format(
                    source, destination, result.width, result.height, result.source_format, result.compression,
                    check_result.estimated_quality, result.output_bytes / 1e6, result.seconds,
                    time.monotonic() - started, "{:.0f}".format(memory) if memory else "н/д")
                self.signals.done.emit("OK", detail)
            except Exception as exc:
                self.signals.done.emit("FAIL", "{}: {}: {}".format(source, exc.__class__.__name__, exc))

        def on_progress(self, stage, fraction):
            self.progress.setValue(int(fraction * 1000))
            self.status.setText("{}: {:.0f} %".format(stage, fraction * 100))

        def on_done(self, status, detail):
            self.report.add(status, "Ручная конвертация", detail)
            self.set_busy(False)
            self.refresh()

        def open_work_dir(self):
            if sys.platform == "win32":
                os.startfile(work_dir())

    return SmokeWindow


def show_fatal(message):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "Curator Qt smoke", 0x10)
    elif sys.stderr:
        sys.stderr.write(message + "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    no_gui = "--no-gui" in argv
    report = base_checks()
    try:
        prepare_qt(report)
    except Exception as exc:
        report.add("FAIL", "PyQt5", "{}: {}".format(exc.__class__.__name__, exc))
    # Если Qt не поднимет платформу, он завершит процесс сам — отчёт к этому моменту уже на диске.
    report.add("INFO", "Создание окна Qt", "если строка последняя — Qt аварийно завершил процесс при запуске")
    write_text(REPORT_NAME, report.text())
    report.lines.pop()
    try:
        from PyQt5.QtWidgets import QApplication

        app = QApplication(sys.argv[:1])
        qt_checks(report, app)
        SmokeWindow = make_window_class()
        window = SmokeWindow(report)
        window.show()
        app.processEvents()
        report.add("INFO", "Запуск до окна", "{:.1f} с (от старта процесса)".format(startup_seconds()))
        check(report, "Снимки окна", window.snapshots)
        memory = peak_memory_mb()
        report.add("INFO", "Пиковая память процесса", "{:.0f} МБ".format(memory) if memory else "н/д")
        window.refresh()
    except Exception:
        report.add("FAIL", "Окно Qt", traceback.format_exc().splitlines()[-1])
        report.lines.extend("         " + line for line in traceback.format_exc().splitlines())
        path = write_text(REPORT_NAME, report.text())
        if not no_gui:
            show_fatal("Не удалось открыть окно Qt.\n\nОтчёт: {}".format(path))
        if sys.stdout:
            print(report.text())
        return 1
    if no_gui:
        window.close()
        if sys.stdout:
            print(report.text())
        return 1 if report.failures else 0
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
