"""Состояния обработки файла и папки (ARCHITECTURE.md, раздел 11).

Состояние всегда пишется в журнал до действия на диске, поэтому по
журналу видно, на каком шаге прервалась работа.
"""

# Фаза A — подготовка, оригиналы не трогаются
PLANNED = "PLANNED"
STAGED = "STAGED"              # оригинал скопирован в локальную папку, хеш посчитан
CONVERTED = "CONVERTED"        # JPEG создан локально
VALIDATED = "VALIDATED"        # JPEG проверен
JPEG_UPLOADED = "JPEG_UPLOADED"  # JPEG лежит рядом со снимками как .part
SOURCE_COPIED = "SOURCE_COPIED"  # копия оригинала лежит в архиве как .part

# Фаза B — фиксация
JPEG_FINAL = "JPEG_FINAL"      # .part переименован в .jpeg
SOURCE_FINAL = "SOURCE_FINAL"  # оригинал занял своё место в архиве
COMPLETED = "COMPLETED"        # исходный файл удалён (или не требовал удаления)

FAILED = "FAILED"
CLEANED = "CLEANED"                      # следы фазы A удалены, папка как была
ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"  # фаза B прервана, нужен откат (этап 5)

FILE_STATES = (
    PLANNED, STAGED, CONVERTED, VALIDATED, JPEG_UPLOADED, SOURCE_COPIED,
    JPEG_FINAL, SOURCE_FINAL, COMPLETED, FAILED, CLEANED, ROLLBACK_REQUIRED,
)

# Порядок нормального хода работы
SEQUENCE = (
    PLANNED, STAGED, CONVERTED, VALIDATED, JPEG_UPLOADED, SOURCE_COPIED,
    JPEG_FINAL, SOURCE_FINAL, COMPLETED,
)

TERMINAL = (COMPLETED, CLEANED, ROLLBACK_REQUIRED)

# Состояния, при которых оригинал ещё на месте: фазу A можно просто убрать
PHASE_A_STATES = (PLANNED, STAGED, CONVERTED, VALIDATED, JPEG_UPLOADED, SOURCE_COPIED)


def next_state(current):
    index = SEQUENCE.index(current)
    return SEQUENCE[index + 1] if index + 1 < len(SEQUENCE) else None


def check_transition(current, new):
    """Недопустимый переход — ошибка в программе, а не в данных."""
    if new in (FAILED, CLEANED, ROLLBACK_REQUIRED):
        return
    if current is None:
        if new != PLANNED:
            raise RuntimeError("обработка начинается с {}, а не с {}".format(PLANNED, new))
        return
    if new != next_state(current):
        raise RuntimeError("недопустимый переход {} -> {}".format(current, new))


# Состояния папки
FOLDER_RUNNING = "running"
FOLDER_DONE = "done"
FOLDER_FAILED = "failed"
FOLDER_CANCELLED = "cancelled"
FOLDER_ROLLBACK_REQUIRED = "rollback_required"
