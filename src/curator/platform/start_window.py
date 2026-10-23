"""Главное окно версии 2: шапка + стартовая страница с плитками модулей (ARCHITECTURE.md 26.1, D21).

Нажатие на плитку открывает страницу модуля в том же окне; «Все задачи» в шапке возвращает
к плиткам. Окно о конкретных модулях не знает — только реестр platform/modules.py.
"""

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)

from curator import __version__
from curator.platform import modules as registry
from curator.platform import theme as theme_module

TITLE = "Curator v2"           # D24: «v2» — там, где нужно отличить от версии 1
COLUMNS = 3


def _label(text, role=None, wrap=False):
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    label.setWordWrap(wrap)
    label.setAttribute(Qt.WA_TransparentForMouseEvents)  # нажатие идёт плитке, а не подписи
    return label


class Tile(QPushButton):
    """Плитка модуля: крупная иконка, название, описание; у заглушки — отметка «скоро»."""

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self.module = module
        self.setObjectName("tile")
        self.setCursor(Qt.PointingHandCursor if module.available else Qt.ArrowCursor)
        self.setEnabled(module.available)
        self.setMinimumSize(260, 150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAccessibleName(module.title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.icon_label = _label("")
        top.addWidget(self.icon_label)
        top.addStretch(1)
        if not module.available:
            top.addWidget(_label("скоро", "badge"), 0, Qt.AlignTop)
        layout.addLayout(top)
        layout.addWidget(_label(module.title, "tileTitle", wrap=True))
        layout.addWidget(_label(module.description, "tileText", wrap=True))
        layout.addStretch(1)

    def refresh_theme(self, theme):
        color = "accent" if self.module.available else "text_disabled"
        self.icon_label.setPixmap(theme.pixmap(self.module.icon, color, 36))


class StartPage(QWidget):
    def __init__(self, on_open, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)
        layout.addWidget(_label("Выберите задачу", "heading"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.tiles = []
        for index, module in enumerate(registry.MODULES):
            tile = Tile(module)
            tile.clicked.connect(lambda _checked=False, m=module: on_open(m))
            grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
            self.tiles.append(tile)
        layout.addLayout(grid, 1)

    def refresh_theme(self, theme):
        for tile in self.tiles:
            tile.refresh_theme(theme)


class StartWindow(QMainWindow):
    def __init__(self, context):
        super().__init__()
        self.context = context
        self.module_page = None
        # Страница модуля создаётся один раз и живёт до закрытия окна: «Все задачи» во время
        # обработки не должны обрывать работу и терять её состояние.
        self.module_pages = {}
        self.setWindowTitle("{} {}".format(TITLE, __version__))
        self.resize(1000, 720)            # как окно версии 1: окну обработки нужен список папок
        self.setMinimumSize(QSize(860, 560))

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._header())
        self.stack = QStackedWidget()
        self.start_page = StartPage(self.open_module)
        self.stack.addWidget(self.start_page)
        column.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Папка программы: {}".format(context.paths.base))
        self.refresh_theme()

    def _header(self):
        header = QFrame()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(20, 12, 16, 12)
        row.setSpacing(12)
        self.back_button = QPushButton("Все задачи")
        self.back_button.clicked.connect(self.show_start)
        self.back_button.hide()
        row.addWidget(self.back_button)
        self.logo = QLabel()
        row.addWidget(self.logo)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title_label = QLabel("Curator")
        self.title_label.setProperty("role", "title")
        self.subtitle_label = QLabel("Автоматизация работы со снимками · версия {}".format(__version__))
        self.subtitle_label.setProperty("muted", True)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        row.addLayout(titles, 1)
        self.theme_button = QPushButton()
        self.theme_button.setProperty("iconOnly", True)
        self.theme_button.clicked.connect(self.toggle_theme)
        row.addWidget(self.theme_button)
        return header

    # ------------------------------------------------------------- навигация

    def open_module(self, module):
        if not module.available:
            return
        page = self.module_pages.get(module.id)
        if page is None:
            page = self.module_pages[module.id] = module.open(self.context)
            self.stack.addWidget(page)
        self.module_page = page
        self.stack.setCurrentWidget(page)
        self.title_label.setText(module.title)
        self.back_button.show()
        self.refresh_theme()

    def show_start(self):
        self.stack.setCurrentWidget(self.start_page)
        self.title_label.setText("Curator")
        self.back_button.hide()

    # -------------------------------------------------------------- закрытие

    def closeEvent(self, event):
        # Модуль может не отпустить окно: идёт обработка или результат не принят.
        for page in self.module_pages.values():
            if hasattr(page, "confirm_close") and not page.confirm_close():
                event.ignore()
                return
        self.shutdown_modules()
        event.accept()

    def shutdown_modules(self):
        for page in self.module_pages.values():
            if hasattr(page, "shutdown"):
                page.shutdown()

    # ------------------------------------------------------------------ тема

    def toggle_theme(self):
        self.context.set_theme("light" if self.context.theme.name == "dark" else "dark")
        self.refresh_theme()

    def refresh_theme(self):
        theme = self.context.theme
        dark = theme.name == "dark"
        self.logo.setPixmap(theme.pixmap("images", "accent", 28))
        self.back_button.setIcon(theme.icon("arrow-left"))
        # Как лампочка версии 1: горит — светлая тема.
        self.theme_button.setIcon(theme.icon("lightbulb-off" if dark else "lightbulb"))
        self.theme_button.setToolTip("Светлая тема" if dark else "Тёмная тема")
        self.start_page.refresh_theme(theme)
        for page in self.module_pages.values():
            if hasattr(page, "refresh_theme"):
                page.refresh_theme(theme)
