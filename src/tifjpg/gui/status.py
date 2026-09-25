"""Как состояние папки выглядит в списке (ARCHITECTURE.md, раздел 18).

Чистые функции без tkinter: цвет и подпись определяются здесь, проверяются
тестами, а окно только рисует.
"""

from tifjpg.domain import states
from tifjpg.domain.models import CONFLICT, DONE, EMPTY, PROCESS_FROM_ARCHIVE, PROCESS_ROOT
from tifjpg.transaction.rollback import NOTHING, PARTIAL, ROLLED_BACK

# Ключи цветов; конкретные значения — в theme.py
SUCCESS = "success"
ERROR = "error"
WARNING = "warning"
NEUTRAL = "neutral"
RUNNING = "running"

WAITING = ("ожидает", NEUTRAL)


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
