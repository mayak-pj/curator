"""Выполнение плана папки в две фазы (ARCHITECTURE.md, 10.2 и 10.3).

Фаза A ничего не разрушает: оригиналы только читаются, всё созданное имеет
суффикс .part. Фаза B состоит из переименований и удалений — она короткая,
и только в ней папка меняет состояние.

Ни одно изменение на диске не выполняется без предварительной записи
намерения в журнал (ARCHITECTURE.md, 10.4).
"""

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from tifjpg.domain import states
from tifjpg.domain.errors import ConversionCancelled, IntegrityError
from tifjpg.fs import safe_copy
from tifjpg.fs.hashing import CHUNK_SIZE, copy_with_hash, file_hash, file_signature, same_signature
from tifjpg.fs.retry import DEFAULT_DELAYS, with_retry
from tifjpg.imaging import ConversionOptions, converter_for, validate_jpeg
from tifjpg.transaction.journal import NOTE

# JPEG Q100 из 16-битного снимка примерно вдвое меньше оригинала (замеры этапов 1 и 2).
JPEG_SIZE_FACTOR = 0.5
TEMP_PREFIX = "tifjpg-"
RENAME_SUFFIX = ".rename.part"


@dataclass(frozen=True)
class ExecutionOptions:
    conversion: ConversionOptions = ConversionOptions()
    temp_dir: Optional[str] = None
    chunk_size: int = CHUNK_SIZE
    delays: Tuple[int, ...] = DEFAULT_DELAYS
    sleep: Callable = time.sleep
    space_margin: float = 0.05
    check_space: bool = True


@dataclass
class FileResult:
    index: int
    source: str
    jpeg: str
    archive: str
    state: Optional[str] = None  # None — к файлу ещё не приступали
    source_hash: Optional[str] = None
    jpeg_hash: Optional[str] = None
    jpeg_bytes: int = 0
    seconds: float = 0.0
    error: Optional[str] = None
    signature: Optional[dict] = None


@dataclass
class FolderResult:
    folder: str
    state: str = states.FOLDER_RUNNING
    files: List[FileResult] = field(default_factory=list)
    archive_created: bool = False
    error: Optional[str] = None
    seconds: float = 0.0

    @property
    def done(self):
        return self.state == states.FOLDER_DONE


class FolderExecutor:
    def __init__(self, journal, options=None, on_event=None, is_cancelled=None):
        self.journal = journal
        self.options = options or ExecutionOptions()
        self._on_event = on_event
        self._is_cancelled = is_cancelled

    # ------------------------------------------------------------------ API

    def run(self, plan):
        if not plan.ok:
            raise ValueError("план папки {} не пригоден к выполнению".format(plan.folder))
        started = time.monotonic()
        result = FolderResult(folder=plan.folder)
        self.journal.folder_state(plan.folder, "started", rule=plan.decision.rule,
                                  files=len(plan.files), archive=plan.archive_dir)
        temp_dir = tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=self.options.temp_dir)
        try:
            self._check_space(plan, temp_dir)
            result.archive_created = safe_copy.ensure_dir(plan.archive_dir)
            if result.archive_created:
                self.journal.record(NOTE, folder=plan.folder, action="archive_created", path=plan.archive_dir)
            self._phase_a(plan, result, temp_dir)
            self._phase_b(plan, result)
            result.state = states.FOLDER_DONE
        except ConversionCancelled as exc:
            result.error = str(exc)
            result.state = self._after_failure(plan, result, states.FOLDER_CANCELLED)
        except Exception as exc:
            result.error = "{}: {}".format(exc.__class__.__name__, exc)
            result.state = self._after_failure(plan, result, states.FOLDER_FAILED)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            result.seconds = round(time.monotonic() - started, 2)
            self.journal.folder_state(plan.folder, result.state, error=result.error, seconds=result.seconds)
            self._emit("folder_finished", folder=plan.folder, state=result.state, error=result.error)
        return result

    # -------------------------------------------------------------- фаза A

    def _phase_a(self, plan, result, temp_dir):
        for planned in plan.files:
            self._raise_if_cancelled(planned.source)
            file_result = FileResult(index=planned.index, source=planned.source,
                                     jpeg=planned.jpeg, archive=planned.archive)
            result.files.append(file_result)
            started = time.monotonic()

            file_result.signature = file_signature(planned.source)
            self._step(file_result, states.PLANNED, None,
                       source=planned.source, jpeg=planned.jpeg, archive=planned.archive,
                       signature=file_result.signature)

            stage = os.path.join(temp_dir, "{:04d}_{}".format(planned.index, os.path.basename(planned.source)))
            local_jpeg = os.path.join(temp_dir, "{:04d}.jpeg".format(planned.index))

            def download():
                safe_copy.remove_quietly(stage)
                return copy_with_hash(planned.source, stage, chunk_size=self.options.chunk_size,
                                      is_cancelled=self._is_cancelled)

            _, source_hash = self._step(file_result, states.STAGED,
                                        lambda: self._retry(download, "чтение оригинала"))
            file_result.source_hash = source_hash

            conversion = self._step(file_result, states.CONVERTED, lambda: self._convert(stage, local_jpeg, planned))
            file_result.jpeg_bytes = conversion.output_bytes

            def validate():
                validate_jpeg(local_jpeg, conversion.width, conversion.height, conversion.bands)
                return file_hash(local_jpeg, chunk_size=self.options.chunk_size)

            file_result.jpeg_hash = self._step(file_result, states.VALIDATED, validate)

            def upload_jpeg():
                _, digest = safe_copy.copy_verified(local_jpeg, planned.jpeg, chunk_size=self.options.chunk_size,
                                                    is_cancelled=self._is_cancelled)
                if digest != file_result.jpeg_hash:
                    raise IntegrityError(planned.jpeg, "выгруженный JPEG отличается от локального")
                return digest

            self._step(file_result, states.JPEG_UPLOADED,
                       lambda: self._retry(upload_jpeg, "запись JPEG"),
                       bytes=conversion.output_bytes)

            if planned.source_in_archive:
                # Оригинал уже лежит в архиве: копировать нечего, в фазе B его переименуем.
                self._step(file_result, states.SOURCE_COPIED, None, note="оригинал уже в архиве")
            else:
                def copy_original():
                    _, digest = safe_copy.copy_verified(planned.source, planned.archive,
                                                        chunk_size=self.options.chunk_size,
                                                        is_cancelled=self._is_cancelled)
                    if digest != source_hash:
                        raise IntegrityError(planned.source, "копия в архиве отличается от оригинала")
                    return digest

                self._step(file_result, states.SOURCE_COPIED,
                           lambda: self._retry(copy_original, "копирование оригинала в архив"))

            safe_copy.remove_quietly(stage)
            safe_copy.remove_quietly(local_jpeg)
            file_result.seconds = round(time.monotonic() - started, 2)
            self._emit("file_prepared", folder=plan.folder, index=planned.index, seconds=file_result.seconds)

    def _convert(self, stage, local_jpeg, planned):
        converter = converter_for(planned.source)
        return converter.convert(
            stage, local_jpeg, self.options.conversion,
            progress=lambda stage_name, fraction: self._emit(
                "progress", index=planned.index, stage=stage_name, fraction=fraction),
            is_cancelled=self._is_cancelled,
        )

    # -------------------------------------------------------------- фаза B

    def _phase_b(self, plan, result):
        """Только переименования и удаления. Отмену здесь не проверяем: фаза короткая."""
        sources = self._free_occupied_names(plan, result)
        for planned, file_result in zip(plan.files, result.files):
            self._step(file_result, states.JPEG_FINAL,
                       lambda p=planned: safe_copy.safe_rename(safe_copy.part_path(p.jpeg), p.jpeg))

            if planned.source_in_archive:
                current = sources[planned.index]
                if os.path.normcase(current) == os.path.normcase(planned.archive):
                    self._step(file_result, states.SOURCE_FINAL, None, note="имя уже правильное")
                else:
                    self._step(file_result, states.SOURCE_FINAL,
                               lambda c=current, p=planned: safe_copy.safe_rename(c, p.archive))
                self._step(file_result, states.COMPLETED, None, note="оригинал остаётся в архиве")
            else:
                self._step(file_result, states.SOURCE_FINAL,
                           lambda p=planned: safe_copy.safe_rename(safe_copy.part_path(p.archive), p.archive))
                self._step(file_result, states.COMPLETED,
                           lambda p=planned, f=file_result: self._delete_original(p, f))
            self._emit("file_completed", folder=plan.folder, index=planned.index)

    def _free_occupied_names(self, plan, result):
        """Переименование внутри архива: сначала уводим оригиналы на временные имена.

        Иначе «Рентгенограмма 2.tif» -> «Рентгенограмма_1.tif» затёрло бы соседний снимок.
        """
        current = {planned.index: planned.source for planned in plan.files}
        if not plan.two_step_renames:
            return current
        for planned, file_result in zip(plan.files, result.files):
            temporary = os.path.join(os.path.dirname(planned.archive),
                                     "{:04d}{}".format(planned.index, RENAME_SUFFIX))
            self.journal.record(NOTE, folder=plan.folder, n=planned.index,
                                action="rename_temp", source=planned.source, target=temporary)
            safe_copy.remove_quietly(temporary)
            os.rename(planned.source, temporary)
            current[planned.index] = temporary
        return current

    def _delete_original(self, planned, file_result):
        if not same_signature(planned.source, file_result.signature):
            raise IntegrityError(planned.source, "оригинал изменился во время обработки — не удаляем")
        os.remove(planned.source)

    # ------------------------------------------------------------- служебное

    def _step(self, file_result, state, action, **fields):
        """Намерение в журнал -> действие -> отметка о выполнении."""
        states.check_transition(file_result.state, state)
        self.journal.file_state(self._folder_of(file_result), file_result.index, state, phase="intent", **fields)
        value = action() if action is not None else None
        file_result.state = state
        self.journal.file_state(self._folder_of(file_result), file_result.index, state, phase="done")
        return value

    @staticmethod
    def _folder_of(file_result):
        return os.path.dirname(file_result.jpeg)

    def _retry(self, operation, description):
        return with_retry(
            operation, description=description, delays=self.options.delays, sleep=self.options.sleep,
            on_retry=lambda what, attempt, delay, exc: self._on_retry(what, attempt, delay, exc),
            is_cancelled=self._is_cancelled,
        )

    def _on_retry(self, what, attempt, delay, exc):
        self.journal.record(NOTE, action="retry", operation=what, attempt=attempt,
                            delay=delay, error=str(exc))
        self._emit("retry", operation=what, attempt=attempt, delay=delay, error=str(exc))

    def _check_space(self, plan, temp_dir):
        if not self.options.check_space:
            return
        sizes = [os.path.getsize(planned.source) for planned in plan.files]
        total = sum(sizes)
        from_archive = all(planned.source_in_archive for planned in plan.files)
        # На сетевом диске: JPEG всегда, копии оригиналов — только если они ещё не в архиве.
        needed_remote = total * JPEG_SIZE_FACTOR + (0 if from_archive else total)
        safe_copy.require_free_space(plan.folder, needed_remote, self.options.space_margin,
                                     what="диске со снимками")
        needed_local = max(sizes) * (1 + JPEG_SIZE_FACTOR) if sizes else 0
        safe_copy.require_free_space(temp_dir, needed_local, self.options.space_margin,
                                     what="локальном диске")

    def _after_failure(self, plan, result, default_state):
        """Фаза A обратима: убираем свои .part. Фаза B — задача отката (этап 5)."""
        if any(file_result.state in (states.JPEG_FINAL, states.SOURCE_FINAL, states.COMPLETED)
               for file_result in result.files):
            self.journal.folder_state(plan.folder, states.FOLDER_ROLLBACK_REQUIRED, error=result.error)
            return states.FOLDER_ROLLBACK_REQUIRED

        for planned in plan.files:
            safe_copy.remove_quietly(safe_copy.part_path(planned.jpeg))
            if not planned.source_in_archive:
                safe_copy.remove_quietly(safe_copy.part_path(planned.archive))
        if result.archive_created:
            safe_copy.remove_dir_if_empty(plan.archive_dir)
        for file_result in result.files:
            file_result.state = states.CLEANED
        self.journal.folder_state(plan.folder, states.CLEANED, error=result.error)
        return default_state

    def _raise_if_cancelled(self, path):
        if self._is_cancelled is not None and self._is_cancelled():
            raise ConversionCancelled(path)

    def _emit(self, kind, **data):
        if self._on_event is not None:
            data["kind"] = kind
            self._on_event(data)
