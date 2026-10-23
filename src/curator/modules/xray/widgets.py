"""Части окна обработки на Qt: список папок, панель ошибок, диалог восстановления (26.5).

Те же элементы и то же поведение, что у окна версии 1 (gui/folder_list.py, gui/error_panel.py,
gui/recovery_dialog.py), вид — тема digger. Цвета — только из токенов темы.
"""

import datetime
import html

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget,
)

from curator.app.service import CONTINUE, IGNORE, ROLLBACK
from curator.modules.xray import status as status_module

# Ключ цвета из status.py → токен темы
TONE_TOKENS = {
    status_module.SUCCESS: "success",
    status_module.ERROR: "danger",
    status_module.WARNING: "warning",
    status_module.NEUTRAL: "text_muted",
    status_module.RUNNING: "accent",
}


def set_property(widget, name, value):
    """Свойство, от которого зависит QSS: без повторной полировки стиль не обновится."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class FolderRow(QFrame):
    def __init__(self, name, on_rollback, parent=None):
        super().__init__(parent)
        self.setObjectName("folderRow")
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 6, 12, 6)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(2)
        # Полоска слева: цветом отмечает папку, которая обрабатывается прямо
        # сейчас — так её видно, даже не читая текст статуса.
        self.indicator = QFrame()
        self.indicator.setObjectName("rowIndicator")
        self.indicator.setFixedWidth(4)
        layout.addWidget(self.indicator, 0, 0, 2, 1)
        self.name = QLabel(name)
        self.name.setProperty("role", "rowTitle")
        self.name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.name, 0, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status, 1, 1)
        layout.setColumnStretch(1, 1)
        self.button = QPushButton("Откатить")
        self.button.setEnabled(False)
        self.button.clicked.connect(on_rollback)
        layout.addWidget(self.button, 0, 2, 2, 1, Qt.AlignVCenter)
        self.running = False

    def set_status(self, text, colour):
        self.status.setText(text)
        set_property(self.status, "tone", colour)
        running = colour == status_module.RUNNING
        if running != self.running:
            self.running = running
            set_property(self, "running", running)
            set_property(self.indicator, "running", running)


class FolderList(QScrollArea):
    """Список папок: путь, статус цветом и кнопка «Откатить» у каждой строки."""

    def __init__(self, on_rollback=None, parent=None):
        super().__init__(parent)
        self.setObjectName("folderList")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._on_rollback = on_rollback
        self._rows = {}
        self._root = ""
        inner = QWidget()
        inner.setObjectName("folderListInner")
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._layout.addStretch(1)
        self._placeholder = QLabel("Папки появятся здесь после «Проверить»")
        self._placeholder.setProperty("muted", True)
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._layout.insertWidget(0, self._placeholder)
        self.setWidget(inner)

    # ------------------------------------------------------------------ API

    @property
    def rows(self):
        return self._rows

    def clear(self):
        for row in self._rows.values():
            self._layout.removeWidget(row)
            row.deleteLater()
        self._rows = {}
        self._placeholder.show()

    def set_folders(self, plans, root, status_of):
        self.clear()
        self._root = root
        for plan in plans:
            self._add_row(plan.folder, *status_of(plan))
        self.verticalScrollBar().setValue(0)

    def set_status(self, folder, text, colour):
        row = self._rows.get(folder)
        if row is None:
            row = self._add_row(folder, text, colour)
        row.set_status(text, colour)

    def set_rollback_enabled(self, folder, enabled):
        row = self._rows.get(folder)
        if row is not None:
            row.button.setEnabled(enabled)

    def disable_all_rollback(self):
        for row in self._rows.values():
            row.button.setEnabled(False)

    def show_folder(self, folder):
        """Прокрутить список к папке, которая сейчас обрабатывается."""
        row = self._rows.get(folder)
        if row is not None:
            # Строка могла только что появиться: координаты будут после раскладки.
            QTimer.singleShot(0, lambda: self._ensure_visible(folder))

    def _ensure_visible(self, folder):
        row = self._rows.get(folder)
        if row is not None:
            self.ensureWidgetVisible(row, 0, 40)

    # -------------------------------------------------------------- частное

    def _add_row(self, folder, text, colour):
        self._placeholder.hide()
        row = FolderRow(status_module.display_name(folder, self._root),
                        lambda _checked=False, path=folder: self._rollback(path))
        row.setToolTip(folder)
        row.set_status(text, colour)
        self._layout.insertWidget(self._layout.count() - 1, row)
        self._rows[folder] = row
        return row

    def _rollback(self, folder):
        if self._on_rollback is not None:
            self._on_rollback(folder)


class ErrorPanel(QGroupBox):
    """Ошибки и предупреждения с временем и папкой; заголовок показывает их число."""

    TITLE = "Ошибки и предупреждения"

    def __init__(self, parent=None):
        super().__init__(self.TITLE, parent)
        self._entries = []
        self._theme = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._text = QPlainTextEdit()
        self._text.setObjectName("errorLog")
        self._text.setReadOnly(True)
        self._text.setMinimumHeight(60)
        layout.addWidget(self._text)

    @property
    def count(self):
        return len(self._entries)

    def text(self):
        return self._text.toPlainText()

    def refresh_theme(self, theme):
        """Цвет строк зависит от темы: перерисовываем накопленное заново."""
        self._theme = theme
        self._text.clear()
        for entry in self._entries:
            self._append(*entry)

    def add(self, message, level="warning", folder=None):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = "{}  {}{}".format(stamp, "{}: ".format(folder) if folder else "", message)
        entry = (line, "error" if level == "error" else "warning")
        self._entries.append(entry)
        self._append(*entry)
        self.setTitle("{} ({})".format(self.TITLE, self.count))

    def clear(self):
        self._entries = []
        self._text.clear()
        self.setTitle(self.TITLE)

    def _append(self, line, level):
        colour = self._theme.color("danger" if level == "error" else "warning") if self._theme else ""
        escaped = html.escape(line).replace("\n", "<br>")
        self._text.appendHtml('<span style="color:{}">{}</span>'.format(colour, escaped) if colour else escaped)
        self._text.verticalScrollBar().setValue(self._text.verticalScrollBar().maximum())


SITUATION_TEXT = {
    "phase_b_unfinished": "фиксация не завершена: часть файлов уже переименована",
    "phase_a_leftovers": "остались временные файлы предыдущего запуска",
    "consistent": "состояние согласовано",
}


class RecoveryDialog(QDialog):
    """Диалог восстановления после сбоя (раздел 14): выбор по каждой папке, сам ничего не делает.

    «Игнорировать» ничего не удаляет; выполняет выбранное вызывающая сторона (decisions).
    """

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Обнаружена незавершённая операция")
        self.decisions = []
        self._rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)
        title = QLabel("Предыдущий запуск завершился не полностью.")
        title.setProperty("role", "title")
        layout.addWidget(title)
        hint = QLabel("Выберите, что сделать с каждой папкой. «Игнорировать» ничего не удаляет.")
        hint.setProperty("muted", True)
        layout.addWidget(hint)

        boxes = QWidget()
        boxes_layout = QVBoxLayout(boxes)
        boxes_layout.setContentsMargins(0, 0, 0, 0)
        for _journal_path, item in items:
            boxes_layout.addWidget(self._row(item))
        boxes_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("plainScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(boxes)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        nothing = QPushButton("Ничего не делать")
        nothing.clicked.connect(self.reject)
        buttons.addWidget(nothing)
        apply_button = QPushButton("Применить")
        apply_button.setProperty("accent", True)
        apply_button.clicked.connect(self._apply)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)
        self.resize(620, min(220 + 150 * len(items), 640))

    def _row(self, item):
        box = QGroupBox(item.folder)
        column = QVBoxLayout(box)
        situation = QLabel(SITUATION_TEXT.get(item.situation, item.situation))
        situation.setProperty("muted", True)
        column.addWidget(situation)
        for detail in item.details[:5]:
            label = QLabel("• " + detail)
            label.setProperty("muted", True)
            label.setWordWrap(True)
            column.addWidget(label)
        choices = QHBoxLayout()
        group = QButtonGroup(box)
        buttons = {}
        for text, value in (("Продолжить", CONTINUE), ("Откатить", ROLLBACK), ("Игнорировать", IGNORE)):
            button = QRadioButton(text)
            group.addButton(button)
            choices.addWidget(button)
            buttons[value] = button
        buttons[IGNORE].setChecked(True)
        choices.addStretch(1)
        column.addLayout(choices)
        self._rows.append((item, buttons))
        return box

    def choose(self, item, action):
        """Выбрать действие для папки (для проверок без человека)."""
        for candidate, buttons in self._rows:
            if candidate is item:
                buttons[action].setChecked(True)

    def selected(self):
        return [(item, value) for item, buttons in self._rows
                for value, button in buttons.items() if button.isChecked() and value != IGNORE]

    def _apply(self):
        self.decisions = self.selected()
        self.accept()
