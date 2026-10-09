"""Где приложение хранит свои файлы (ARCHITECTURE.md, раздел 4).

Журнал, логи, блокировки и конфиг лежат **рядом с exe**: программа стоит в
одной общей папке, и коллеги запускают её через ярлыки. Без права записи
туда обработка запрещена — без журнала откат не гарантирован.
"""

import os
import sys
import tempfile

APP_NAME = "curator"
JOURNAL_DIR = "journal"
LOGS_DIR = "logs"
LOCKS_DIR = "locks"
CONFIG_NAME = "config.json"
GUI_STATE_NAME = "gui_state.json"


def app_dir():
    """Папка программы: каталог exe, а при запуске из исходников — <проект>/var."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    project = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project, "var")


class AppPaths:
    def __init__(self, base=None):
        self.base = os.path.abspath(base or app_dir())

    @property
    def journal(self):
        return os.path.join(self.base, JOURNAL_DIR)

    @property
    def logs(self):
        return os.path.join(self.base, LOGS_DIR)

    @property
    def locks(self):
        return os.path.join(self.base, LOCKS_DIR)

    @property
    def config(self):
        return os.path.join(self.base, CONFIG_NAME)

    @property
    def gui_state(self):
        """Последняя папка и тема окна — удобство, а не журнал: сохраняется best-effort."""
        return os.path.join(self.base, GUI_STATE_NAME)

    @property
    def temp(self):
        """Локальные временные файлы: %TEMP%, а не сетевой диск."""
        return os.path.join(tempfile.gettempdir(), APP_NAME)

    def prepare(self):
        """Создать рабочие каталоги. Возвращает список недоступных для записи."""
        problems = []
        for path in (self.journal, self.logs, self.locks, self.temp):
            try:
                os.makedirs(path, exist_ok=True)
                _probe(path)
            except OSError as exc:
                problems.append("{}: {}".format(path, exc))
        return problems


def _probe(directory):
    probe = os.path.join(directory, ".write_probe")
    with open(probe, "w", encoding="utf-8") as stream:
        stream.write("ok")
    os.remove(probe)
