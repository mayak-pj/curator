"""Сервис обработки (ARCHITECTURE.md, раздел 5).

Единственное, что видит GUI: команды scan / start / cancel / rollback /
accept / recover и поток событий. Ни файловых операций, ни libvips выше
этого слоя нет.
"""

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from curator import __version__, appdirs, config, logging_setup
from curator.app import events as ev
from curator.app.scan import scan_root
from curator.domain import states
from curator.domain.errors import PreconditionError, CuratorError
from curator.fs.locks import FolderLock, FolderLocked, normalize
from curator.transaction import recovery as recovery_module
from curator.transaction.executor import ExecutionOptions, FolderExecutor
from curator.transaction.journal import Journal, session_filename, session_header
from curator.transaction.records import folder_from_result, folders_from_journal
from curator.transaction.rollback import ROLLED_BACK, rollback_folder

CONTINUE = "continue"
ROLLBACK = "rollback"
IGNORE = "ignore"


@dataclass
class FolderOutcome:
    folder: str
    state: str
    files: int = 0
    error: Optional[str] = None
    rolled_back: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self):
        return self.state == states.FOLDER_DONE


@dataclass
class SessionSummary:
    root: str
    outcomes: List[FolderOutcome] = field(default_factory=list)
    skipped_locked: int = 0
    done_folders: int = 0
    conflicts: int = 0
    cancelled: bool = False
    seconds: float = 0.0
    accepted: bool = False

    @property
    def succeeded(self):
        return [outcome for outcome in self.outcomes if outcome.ok]

    @property
    def failed(self):
        return [outcome for outcome in self.outcomes if not outcome.ok]

    @property
    def files(self):
        return sum(outcome.files for outcome in self.outcomes if outcome.ok)


def locks_dir(settings, paths):
    """Папка блокировок: общая с версией 1, если она указана в настройках (26.4)."""
    if settings.locks_dir:
        return paths.resolve(settings.locks_dir)
    return paths.locks


def legacy_journal_dirs(settings, paths):
    return [paths.resolve(path) for path in settings.legacy_journal_dirs or ()]


def shared_with_v1_problems(settings, paths):
    """Что мешает работать рядом с версией 1; пустой список — всё в порядке. Ничего не создаёт."""
    if not settings.shared_with_v1_configured:
        return ["в {} нет настроек locks_dir и legacy_journal_dirs (папки locks и journal версии 1; "
                "если версии 1 на этом диске нет — \"locks_dir\": \"locks\", \"legacy_journal_dirs\": [])"
                .format(paths.config)]
    problems = []
    shared = locks_dir(settings, paths)
    # Чужую папку не создаём: опечатка в пути дала бы отдельные блокировки, невидимые версии 1.
    if os.path.normcase(shared) != os.path.normcase(paths.locks):
        if not os.path.isdir(shared):
            problems.append("locks_dir: папка блокировок версии 1 не найдена: {}".format(shared))
        else:
            try:
                appdirs.probe_writable(shared)
            except OSError as exc:
                problems.append("locks_dir: нет права записи в {}: {}".format(shared, exc))
    for directory in legacy_journal_dirs(settings, paths):
        try:
            os.listdir(directory)
        except OSError as exc:
            problems.append("legacy_journal_dirs: журналы версии 1 недоступны: {}: {}".format(directory, exc))
    return problems


class ProcessingService:
    def __init__(self, settings=None, paths=None, on_event=None, session_name=None):
        self.paths = paths if paths is not None else appdirs.AppPaths()
        self.settings = settings if settings is not None else config.load(self.paths.config)
        self._on_event = on_event
        self._cancel = threading.Event()
        self._journal = None
        self._records = {}  # type: Dict[str, object]
        self.session = None  # type: Optional[SessionSummary]
        self._session_name = session_name or session_filename()
        self._legacy = {}  # type: Dict[str, str]
        self.logger, self.log_path, self.problems = logging_setup.configure(
            self.paths.logs, os.path.splitext(self._session_name)[0] + ".log", self.settings.log_level)

    # ------------------------------------------------------------- окружение

    def check_environment(self):
        """Без права записи рядом с exe обработка запрещена: журнал обязателен. Без доступа
        к блокировкам и журналам версии 1 — тоже (26.4, D23): иначе версии возьмутся за одну папку."""
        problems = self.paths.prepare()
        if problems:
            raise PreconditionError(
                "нет доступа на запись в папку программы — обработка невозможна:\n  " + "\n  ".join(problems))
        problems = self._shared_with_v1_problems()
        if problems:
            raise PreconditionError(
                "не настроена совместная работа с версией 1 — обработка невозможна:\n  " + "\n  ".join(problems))
        return True

    @property
    def locks_dir(self):
        return locks_dir(self.settings, self.paths)

    def legacy_journal_dirs(self):
        return legacy_journal_dirs(self.settings, self.paths)

    def _shared_with_v1_problems(self):
        return shared_with_v1_problems(self.settings, self.paths)

    def legacy_unfinished(self):
        """Папки, оставленные незавершёнными в журналах версии 1: {нормализованный путь: журнал}.
        Их версия 2 не берёт — довести или откатить их может только версия 1 (26.4)."""
        found = {}
        for directory in self.legacy_journal_dirs():
            for path in recovery_module.find_unfinished(directory):
                try:
                    items = recovery_module.inspect(path)
                except (OSError, ValueError) as exc:
                    self.logger.warning("Журнал версии 1 %s не прочитан: %s", path, exc)
                    continue
                for item in items:
                    if item.needs_attention:
                        found[normalize(item.folder)] = path
        return found

    def _legacy_reason(self, folder):
        journal = self._legacy.get(normalize(folder))
        if journal is None:
            return None
        return "папка не завершена в версии 1 (журнал {}) — завершите или откатите её в версии 1".format(
            os.path.basename(journal))

    # ---------------------------------------------------------------- чтение

    def scan(self, root):
        self._emit(ev.SCAN_STARTED, root=root)
        result = scan_root(root, rules=self.settings.folder_rules,
                           on_folder=lambda plan: self._emit(ev.PROGRESS, stage="scan", folder=plan.folder))
        self.logger.info("Просмотрено папок: %s, к обработке %s файлов в %s папках",
                         result.folders_seen, result.total_files, len(result.to_process))
        try:
            self._legacy = self.legacy_unfinished()
        except OSError as exc:
            # «Проверить» ничего не меняет — не мешаем ему; обработку остановит check_environment.
            self._legacy = {}
            self._problem("Журналы версии 1 недоступны: %s", exc)
        for plan in result.plans:
            for problem in plan.problems:
                self._problem("%s: %s", plan.folder, problem, folder=plan.folder)
        for plan in result.plans:
            reason = self._legacy_reason(plan.folder)
            if reason:
                self._problem("%s: %s", plan.folder, reason, folder=plan.folder)
        self._emit(ev.SCAN_FINISHED, result=result)
        return result

    # -------------------------------------------------------------- обработка

    def start(self, root_or_scan):
        result = root_or_scan if hasattr(root_or_scan, "plans") else self.scan(root_or_scan)
        self.check_environment()
        self._legacy = self.legacy_unfinished()
        self._cancel.clear()
        started = time.monotonic()
        summary = SessionSummary(root=result.root)
        summary.conflicts = len(result.by_kind("conflict"))
        summary.done_folders = len(result.by_kind("done"))
        self.session = summary

        journal = self._open_journal(result.root)
        self._emit(ev.SESSION_STARTED, root=result.root, folders=len(result.to_process),
                   files=result.total_files, journal=journal.path, log=self.log_path)
        self.logger.info("Старт обработки: %s (%s папок, %s файлов)",
                         result.root, len(result.to_process), result.total_files)

        for plan in result.to_process:
            if self._cancel.is_set():
                summary.cancelled = True
                break
            outcome = self._process_folder(plan, journal)
            if outcome is not None:
                summary.outcomes.append(outcome)
            else:
                summary.skipped_locked += 1

        summary.seconds = round(time.monotonic() - started, 2)
        self.logger.info("Обработка завершена: успешно %s, с ошибками %s, пропущено занятых %s, за %s с",
                         len(summary.succeeded), len(summary.failed), summary.skipped_locked, summary.seconds)
        self._emit(ev.SESSION_FINISHED, summary=summary)
        return summary

    def cancel(self):
        self._cancel.set()
        self.logger.info("Пользователь остановил обработку")

    def _process_folder(self, plan, journal):
        reason = self._legacy_reason(plan.folder)
        if reason:
            self._problem("Папка %s пропущена: %s", plan.folder, reason, folder=plan.folder)
            self._emit(ev.FOLDER_SKIPPED, folder=plan.folder, reason=reason)
            return None
        lock = FolderLock(plan.folder, self.locks_dir, self.settings.lock_stale_minutes)
        try:
            lock.acquire()
        except FolderLocked as exc:
            self._problem("Папка занята: %s", exc, folder=plan.folder)
            self._emit(ev.FOLDER_SKIPPED, folder=plan.folder, reason=str(exc))
            return None

        self._emit(ev.FOLDER_STARTED, folder=plan.folder, files=len(plan.files))
        self.logger.info("Папка %s: файлов %s", plan.folder, len(plan.files))
        try:
            executor = FolderExecutor(
                journal, self._execution_options(), on_event=self._forward,
                is_cancelled=self._cancel.is_set)
            result = executor.run(plan)
        finally:
            lock.release()

        record = folder_from_result(plan, result)
        self._records[plan.folder] = record
        outcome = FolderOutcome(folder=plan.folder, state=result.state,
                                files=len(result.files), error=result.error)

        if result.state == states.FOLDER_ROLLBACK_REQUIRED:
            # Решение D11: ошибка в папке -> папка автоматически возвращается в исходный вид.
            self.logger.error("Папка %s: %s — выполняется автоматический откат", plan.folder, result.error)
            rolled = self.rollback_folder(plan.folder)
            outcome.rolled_back = rolled.state == ROLLED_BACK
            outcome.warnings = list(rolled.warnings)
        elif result.state != states.FOLDER_DONE:
            self._problem("Папка %s: %s", plan.folder, result.error or result.state, folder=plan.folder)
        else:
            self.logger.info("Папка %s обработана за %s с", plan.folder, result.seconds)

        self._emit(ev.FOLDER_FINISHED, folder=plan.folder, state=outcome.state,
                   error=outcome.error, rolled_back=outcome.rolled_back)
        return outcome

    # ------------------------------------------------------------------ откат

    def rollback_folder(self, folder):
        record = self._records.get(folder)
        if record is None:
            raise CuratorError("папка {} не обрабатывалась в этой сессии".format(folder))
        journal = self._open_journal(folder)
        self._emit(ev.ROLLBACK_STARTED, folder=folder)
        lock = FolderLock(folder, self.locks_dir, self.settings.lock_stale_minutes)
        try:
            lock.acquire(take_over_stale=True)
        except FolderLocked as exc:
            self._problem("Откат невозможен, папка занята: %s", exc, folder=folder)
            raise
        try:
            result = rollback_folder(record, journal)
        finally:
            lock.release()
        for warning in result.warnings:
            self._problem("Откат %s: %s", folder, warning, folder=folder)
        self.logger.info("Откат папки %s: %s", folder, result.state)
        self._emit(ev.ROLLBACK_FINISHED, folder=folder, state=result.state, warnings=result.warnings)
        return result

    def rollback_all(self):
        """«Отменить всё»: откат в обратном порядке обработки."""
        return [self.rollback_folder(folder) for folder in reversed(list(self._records))]

    def accept(self):
        """«Принять»: сессия закрыта, кнопки отката больше не действуют."""
        if self.session is not None:
            self.session.accepted = True
        self._records.clear()
        self._close_journal(result="accepted")
        self.logger.info("Результат принят пользователем")
        self._emit(ev.SESSION_ACCEPTED)

    # ----------------------------------------------------- восстановление

    def find_recovery(self):
        """Незавершённые сессии прошлых запусков (ARCHITECTURE.md, раздел 14)."""
        found = []
        for path in recovery_module.find_unfinished(self.paths.journal):
            if os.path.basename(path) == os.path.basename(self._journal_path()):
                continue
            for item in recovery_module.inspect(path):
                if item.needs_attention and not self._locked_by_someone(item.folder):
                    found.append((path, item))
                    self._emit(ev.RECOVERY_FOUND, folder=item.folder, situation=item.situation,
                               journal=path, details=item.details)
        return found

    def recover(self, item, action):
        if action == IGNORE:
            self.logger.info("Незавершённая операция в %s оставлена без изменений", item.folder)
            return None
        journal = self._open_journal(item.folder)
        lock = FolderLock(item.folder, self.locks_dir, self.settings.lock_stale_minutes)
        lock.acquire(take_over_stale=True)
        try:
            if action == CONTINUE:
                result = recovery_module.finish(item.record, journal)
                self.logger.info("Продолжение %s: %s действий, предупреждений %s",
                                 item.folder, len(result.actions), len(result.warnings))
            elif action == ROLLBACK:
                result = rollback_folder(item.record, journal)
                self.logger.info("Откат %s после сбоя: %s", item.folder, result.state)
            else:
                raise ValueError("неизвестное действие восстановления: {!r}".format(action))
        finally:
            lock.release()
        for warning in getattr(result, "warnings", []):
            self._problem("Восстановление %s: %s", item.folder, warning, folder=item.folder)
        return result

    # --------------------------------------------------------------- сервис

    def close(self):
        self._close_journal(result="closed")

    def _execution_options(self):
        return ExecutionOptions(
            conversion=self.settings.conversion_options(),
            temp_dir=self.paths.temp,
            delays=tuple(self.settings.network_retry_delays_s),
            check_space=self.settings.check_free_space,
        )

    def _journal_path(self):
        return os.path.join(self.paths.journal, self._session_name)

    def _open_journal(self, root):
        if self._journal is None:
            self.check_environment()
            self._journal = Journal(self._journal_path(), header=session_header(
                root, __version__, self.settings.folder_rules,
                extra={"settings": self.settings.to_dict(), "log": self.log_path}))
        return self._journal

    def _close_journal(self, result):
        if self._journal is not None:
            self._journal.close(result=result)
            self._journal = None

    def _locked_by_someone(self, folder):
        lock = FolderLock(folder, self.locks_dir, self.settings.lock_stale_minutes)
        try:
            lock.acquire()
        except FolderLocked:
            return True
        lock.release()
        return False

    def _forward(self, event):
        kind = event.pop("kind", ev.PROGRESS)
        self._emit(kind, **event)

    def _problem(self, message, *args, **extra):
        self.logger.warning(message, *args, extra=extra)
        self._emit(ev.PROBLEM, message=message % args if args else message, **extra)

    def _emit(self, kind, **data):
        if self._on_event is not None:
            self._on_event(ev.event(kind, **data))
