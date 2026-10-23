"""Как состояние папки и прогресс выглядят в окне (ARCHITECTURE.md, разделы 18 и 26.5).

Чистые функции без библиотеки окна: цвет, подпись и доля прогресса определяются здесь,
проверяются тестами, а окно только рисует. Перенесено из gui/status.py версии 1 как есть
(этап 13); там осталась ссылка сюда, окно версии 1 пользуется тем же кодом.
"""

import os
import time

from curator.domain import states
from curator.domain.models import CONFLICT, DONE, EMPTY, PROCESS_FROM_ARCHIVE, PROCESS_ROOT
from curator.transaction.rollback import NOTHING, PARTIAL, ROLLED_BACK

# Ключи цветов; конкретные значения — в theme.py
SUCCESS = "success"
ERROR = "error"
WARNING = "warning"
NEUTRAL = "neutral"
RUNNING = "running"

WAITING = ("ожидает", NEUTRAL)

# Подписи шагов конвертации и сетевых шагов (executor.py шлёт их байтовым
# прогрессом) — без них статус часами не меняется на "encode 100 %", пока
# файл на самом деле ещё копируется по сети.
STAGE_LABELS = {
    "histogram": "анализ снимка",
    "encode": "сохранение JPEG",
    states.STAGED: "чтение оригинала",
    states.JPEG_UPLOADED: "запись JPEG",
    # На этом шаге оригинал действительно копируется в архив — но под конец
    # обработки файла он удаляется из исходной папки (executor.py,
    # _delete_original), поэтому для пользователя это перенос, а не копия,
    # которая остаётся лежать в двух местах (my_reports, 07.10.2026).
    states.SOURCE_COPIED: "перенос оригинала в архив",
}

# Доля "Общего прогресса", которую занимает текущий файл по шагам: сумма
# равна 1.0. "convert" — общий бакет для "histogram"+"encode" (обе эти
# стадии и так уже сообщают долю в одной сквозной шкале 0..1 — см.
# imaging/vips_converter.py, _ProgressTracker). Подобраны по замерам на
# целевой машине (my_reports, этапы 9-10): дольше всего — сеть.
STAGE_WEIGHTS = {
    states.STAGED: 0.35,
    "convert": 0.10,
    states.JPEG_UPLOADED: 0.20,
    states.SOURCE_COPIED: 0.35,
}


def initial_status(plan):
    """Строка списка сразу после сканирования."""
    kind = plan.decision.kind
    if plan.problems:
        return ("не может быть обработана: " + plan.problems[0], ERROR)
    if kind in (PROCESS_ROOT, PROCESS_FROM_ARCHIVE):
        return ("к обработке: {} файлов".format(len(plan.files)), NEUTRAL)
    if kind == DONE:
        return ("не обрабатывалась — уже готова", SUCCESS)
    if kind == CONFLICT:
        return (plan.decision.reason, WARNING)
    if kind == EMPTY:
        return ("снимков нет", NEUTRAL)
    return WAITING


def display_name(folder, root):
    """Путь папки в списке — относительно корневой папки."""
    try:
        relative = os.path.relpath(folder, root)
    except ValueError:
        return folder
    if relative in (".", ""):
        # Выбранная корневая папка сама является обрабатываемой папкой:
        # относительный путь до самой себя — "." (не название для человека;
        # my_reports, 28.09.2026: имя папки не отображалось).
        return os.path.basename(os.path.normpath(folder)) or folder
    return folder if relative.startswith("..") else relative


def folder_progress(index, total):
    """Строка статуса папки, пока она обрабатывается: «файл N из M» (N — текущий)."""
    return ("обрабатывается: файл {} из {}".format(index, total), RUNNING)


def folder_status(state, error=None, rolled_back=False):
    """Строка списка после обработки папки."""
    if state == states.FOLDER_DONE:
        return ("готово", SUCCESS)
    if state == states.FOLDER_RUNNING:
        return ("обрабатывается…", RUNNING)
    if state == states.FOLDER_CANCELLED:
        return ("остановлено, папка не изменена", WARNING)
    if state == states.FOLDER_ROLLBACK_REQUIRED:
        if rolled_back:
            return ("ошибка, откат выполнен: {}".format(error or ""), WARNING)
        return ("ошибка, нужен откат: {}".format(error or ""), ERROR)
    if state == states.FOLDER_FAILED:
        return ("ошибка, папка не изменена: {}".format(error or ""), ERROR)
    return (state, NEUTRAL)


def rollback_status(state, warnings=()):
    if state == ROLLED_BACK:
        return ("откат выполнен", NEUTRAL)
    if state == NOTHING:
        return ("откатывать нечего", NEUTRAL)
    if state == PARTIAL:
        return ("откат частичный: {}".format(warnings[0] if warnings else ""), WARNING)
    return (state, WARNING)


def can_rollback(state):
    """Откат предлагается только там, где на диске что-то менялось."""
    return state in (states.FOLDER_DONE, states.FOLDER_ROLLBACK_REQUIRED)


def summary_line(summary):
    parts = [
        "успешно {}".format(len(summary.succeeded)),
        "с ошибками {}".format(len(summary.failed)),
        "уже готовых {}".format(summary.done_folders),
        "конфликтов {}".format(summary.conflicts),
    ]
    if summary.skipped_locked:
        parts.append("занято другими {}".format(summary.skipped_locked))
    if summary.cancelled:
        parts.append("остановлено пользователем")
    return "Обработано файлов {} · {} · за {:.0f} с".format(
        summary.files, ", ".join(parts), summary.seconds)


def progress_line(done_files, total_files, fraction, elapsed_seconds):
    """Строка над списком папок во время обработки: доля и оценка остатка.

    fraction — точная доля 0..1 с учётом продвижения внутри текущего файла
    (та же величина, что заполняет полосу «Общий прогресс»), поэтому
    процент в тексте и сама полоса никогда не расходятся (my_reports, 01.10.2026).
    """
    percent = int(round(fraction * 100))
    line = "Готово файлов: {} из {} · {} %".format(done_files, total_files, percent)
    remaining = _estimate_remaining(fraction, elapsed_seconds)
    if remaining is not None:
        line += " · осталось ~{}".format(format_duration(remaining))
    return line


def _estimate_remaining(fraction, elapsed_seconds):
    """Оценка по среднему темпу — доступна после первого заметного продвижения."""
    if fraction <= 0 or fraction >= 1:
        return None
    return elapsed_seconds / fraction - elapsed_seconds


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "{} с".format(seconds)
    minutes = seconds // 60
    if minutes < 60:
        return "{} мин".format(minutes)
    hours, minutes = divmod(minutes, 60)
    return "{} ч {} мин".format(hours, minutes)


def progress_bucket(stage):
    return "convert" if stage in ("histogram", "encode") else stage


def stage_line(index, stage, fraction):
    """Процент текущего файла — строка сразу под общим прогрессом (my_reports, 07.10.2026)."""
    return "Файл #{}: {} {:.0f} %".format(index, STAGE_LABELS[stage], fraction * 100)


class SessionProgress:
    """Общий прогресс сессии по событиям сервиса: готовые файлы + доля текущего по STAGE_WEIGHTS.

    Без доли текущего файла полоса стояла бы на месте весь файл и прыгала целой единицей
    только в его конце. Логика окна версии 1 (gui/main_window.py, _update_progress) как есть.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.total_files = 1
        self.done_files = 0
        self._stage_fraction = {}
        self._started_at = clock()
        self._max_value = 0.0

    def start(self, files):
        self.total_files = max(files, 1)
        self.done_files = 0
        self._stage_fraction = {}
        self._started_at = self._clock()
        self._max_value = 0.0

    def file_started(self):
        self._stage_fraction = {}

    def file_prepared(self):
        self.done_files += 1
        self._stage_fraction = {}

    def stage(self, stage, fraction):
        self._stage_fraction[progress_bucket(stage)] = fraction

    @property
    def value(self):
        """Сколько файлов «готово» с учётом текущего: 0..total_files, назад не отступает."""
        file_fraction = sum(weight * self._stage_fraction.get(key, 0.0)
                            for key, weight in STAGE_WEIGHTS.items())
        value = min(self.done_files + file_fraction, self.total_files)
        # Повтор сетевого шага после сбоя обнуляет долю ТЕКУЩЕГО шага (видно
        # по комментарию в executor._byte_progress), поэтому value здесь может
        # на мгновение просесть. Раньше это шло прямо в оценку «осталось» —
        # доля и время расходились, и «осталось ~X» прыгало вверх ровно в
        # момент повтора. Полоса и оценка времени не отступают назад
        # (my_reports, 07.10.2026).
        self._max_value = max(value, self._max_value)
        return self._max_value

    @property
    def fraction(self):
        return self.value / self.total_files

    def line(self):
        value = self.value
        return progress_line(self.done_files, self.total_files, value / self.total_files,
                             self._clock() - self._started_at)
