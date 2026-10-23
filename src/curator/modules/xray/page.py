"""Страница модуля «Рентгенограммы TIFF → JPEG».

Этап 12: окно обработки на Qt ещё не перенесено (это этап 13, чек-лист 26.5), поэтому страница
показывает, готова ли программа к работе рядом с версией 1 (26.4) — то, что проверяется на
выезде в первую очередь. Обработка до этапа 13 — в версии 1 (ярлык «Curator»).
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from curator import config
from curator.app import service as service_module
from curator.domain.errors import CuratorError


def readiness(paths):
    """(готово ли, строки для показа) — без изменений на диске."""
    try:
        settings = config.load(paths.config)
    except CuratorError as exc:
        return False, ["Настройки: {}".format(exc)]
    problems = service_module.shared_with_v1_problems(settings, paths)
    if problems:
        return False, problems
    lines = ["Блокировки (общие с версией 1): {}".format(service_module.locks_dir(settings, paths))]
    journals = service_module.legacy_journal_dirs(settings, paths)
    lines.append("Журналы версии 1: {}".format(", ".join(journals) if journals else "нет — версии 1 на этом диске нет"))
    return True, lines


class XrayPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 28)
        layout.setSpacing(16)

        intro = QLabel("Окно обработки переносится на новую библиотеку окна — следующий этап. "
                       "До этого папки обрабатываются в версии 1 (ярлык «Curator»): функционал тот же.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.card = QFrame()
        self.card.setObjectName("card")
        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)
        self.status_icon = QLabel()
        card_layout.addWidget(self.status_icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        self.status_title = QLabel()
        self.status_title.setProperty("role", "title")
        self.status_text = QLabel()
        self.status_text.setWordWrap(True)
        self.status_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(self.status_title)
        texts.addWidget(self.status_text)
        card_layout.addLayout(texts, 1)
        layout.addWidget(self.card)
        layout.addStretch(1)

        self.ready, self.lines = readiness(context.paths)
        self.status_title.setText("Готово к работе рядом с версией 1" if self.ready
                                  else "Обработка будет недоступна")
        self.status_text.setText("\n".join(self.lines))

    def refresh_theme(self, theme):
        name, color = ("check", "success") if self.ready else ("triangle-alert", "danger")
        self.status_icon.setPixmap(theme.pixmap(name, color, 24))
