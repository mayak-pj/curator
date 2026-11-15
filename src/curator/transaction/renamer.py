"""Переименование JPEG в готовой папке: «….jpg» -> «….jpeg» (ARCHITECTURE.md, D26).

Файлы не копируются и не меняются — только имя, по одному переименованию на файл.
Каждое пишется в журнал (намерение -> действие -> отметка), поэтому откат и разбор
после сбоя работают так же, как для обычной обработки. Переименование атомарно:
после сбоя файл либо под старым, либо под новым именем — третьего нет.
"""

import os
from dataclasses import dataclass
from typing import Optional

from curator.domain import states
from curator.fs import safe_copy
from curator.transaction.journal import RENAME
from curator.transaction.records import FolderRecord, RenameRecord


@dataclass
class RenameResult:
    record: FolderRecord
    state: str
    error: Optional[str] = None

    @property
    def renamed(self):
        return sum(1 for item in self.record.renames.values() if item.done)


def rename_jpegs(plan, journal):
    record = FolderRecord(folder=plan.folder, state=states.FOLDER_RUNNING)
    journal.folder_state(plan.folder, "started", rule=plan.decision.rule, action="jpeg_extension",
                         files=len(plan.jpeg_renames))
    error = None
    try:
        for index, (source, target) in enumerate(plan.jpeg_renames, start=1):
            # Перед действием — сверка с диском: со времени «Проверить» могло что-то измениться.
            if not os.path.isfile(source):
                raise FileNotFoundError(2, "файл пропал после проверки", source)
            item = RenameRecord(index=index, source=source, target=target)
            record.renames[index] = item
            journal.record(RENAME, folder=plan.folder, n=index, src=source, dst=target, phase="intent")
            safe_copy.safe_rename(source, target)  # занятое имя — ошибка, без перезаписи
            item.done = True
            journal.record(RENAME, folder=plan.folder, n=index, src=source, dst=target, phase="done")
        state = states.FOLDER_DONE
    except Exception as exc:
        error = "{}: {}".format(exc.__class__.__name__, exc)
        done = any(item.done for item in record.renames.values())
        state = states.FOLDER_ROLLBACK_REQUIRED if done else states.FOLDER_FAILED
    record.state = state
    journal.folder_state(plan.folder, state, error=error)
    return RenameResult(record=record, state=state, error=error)
