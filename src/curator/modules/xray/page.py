"""Страница модуля «Рентгенограммы TIFF → JPEG»: окно обработки на Qt (этап 13, чек-лист 26.5).

Те же действия и те же события сервиса, что у окна версии 1 (gui/main_window.py): страница только
отображает события ProcessingService и отправляет ему команды. Работа идёт в фоновом потоке
(platform/worker.py), события разбирает таймер страницы в главном потоке — Qt трогаем только оттуда.
Подписи и прогресс — общие с версией 1 (status.py). Сверху — карточка готовности к работе рядом
с версией 1 (26.4), её проверка ничего не пишет.
"""

import os

from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from curator import config
from curator.app import events as ev
from curator.app import service as service_module
from curator.domain.errors import CuratorError
from curator.modules.xray import status as status_module
from curator.modules.xray.widgets import ErrorPanel, FolderList, RecoveryDialog, set_property
from curator.platform import state
from curator.platform.worker import TASK_DONE, TASK_FAILED, Worker
from curator.transaction.rollback import NOTHING, ROLLED_BACK

PROGRESS_STEPS = 1000          # QProgressBar целочисленный: доля 0..1 → 0..1000
POLL_MS = 100
ERRORS_HEIGHT = 130


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


def _button(text, handler, accent=False, enabled=True):
    button = QPushButton(text)
    if accent:
        button.setProperty("accent", True)
    button.setEnabled(enabled)
    button.clicked.connect(handler)
    return button


def _muted(text=""):
    label = QLabel(text)
    label.setProperty("muted", True)
    label.setWordWrap(True)
    return label


class XrayPage(QWidget):
    def __init__(self, context, parent=None, service=None):
        super().__init__(parent)
        self.context = context
        self.worker = Worker(None, self._handle_event)
        self.scan_result = None
        self.summary = None
        self.processed_folders = []
        self._finished_status = {}
        self._rolled_back = set()
        self.progress_model = status_module.SessionProgress()
        self._current_folder = None
        self._current_folder_files = 0
        self.notices = []          # сообщения, которые без человека не показываются окном (selftest)

        self.service, service_error = service, None
        if self.service is None:
            try:
                self.service = service_module.ProcessingService(on_event=self.worker.post, paths=context.paths)
            except (CuratorError, OSError) as exc:
                service_error = str(exc)
        self.ready, self.lines = readiness(context.paths)
        if service_error:
            self.ready, self.lines = False, ["Сервис обработки не запущен: {}".format(service_error)] + self.lines

        self._build()
        self._show_readiness()
        last_root = state.load(context.paths.gui_state).get("last_root")
        if last_root:
            self.root_edit.setText(last_root)
            self.current_label.setText("Загружена последняя папка. «Проверить» покажет план, ничего не меняя.")
        if self.service is None:
            for button in (self.scan_button, self.start_button):
                button.setEnabled(False)

        # Таймер принадлежит странице: удалили страницу — разбор событий остановился вместе с ней.
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.worker.pump)
        self._timer.start(POLL_MS)
        if self.service is not None and context.interactive:
            QTimer.singleShot(400, self._check_recovery)

    # ------------------------------------------------------------- интерфейс

    def _build(self):
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 16, 24, 12)
        column.setSpacing(12)

        # Готовность к работе рядом с версией 1 (26.4): одна строка, подробности — в подсказке.
        self.card = QFrame()
        self.card.setObjectName("card")
        card_row = QHBoxLayout(self.card)
        card_row.setContentsMargins(12, 8, 12, 8)
        card_row.setSpacing(10)
        self.status_icon = QLabel()
        card_row.addWidget(self.status_icon, 0, Qt.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.status_title = QLabel()
        self.status_title.setProperty("role", "rowTitle")
        self.status_text = _muted()
        self.status_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addWidget(self.status_title)
        texts.addWidget(self.status_text)
        card_row.addLayout(texts, 1)
        column.addWidget(self.card)

        chooser = QHBoxLayout()
        chooser.setSpacing(8)
        chooser.addWidget(QLabel("Корневая папка:"))
        self.root_edit = QLineEdit()
        self.root_edit.setPlaceholderText("Папка объекта или диска со снимками")
        chooser.addWidget(self.root_edit, 1)
        self.browse_button = _button("Обзор…", self._choose)
        chooser.addWidget(self.browse_button)
        column.addLayout(chooser)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.scan_button = _button("Проверить (без изменений)", self._scan)
        self.start_button = _button("Начать обработку", self._start, accent=True)
        self.stop_button = _button("Остановить", self._stop, enabled=False)
        for button in (self.scan_button, self.start_button, self.stop_button):
            actions.addWidget(button)
        actions.addStretch(1)
        column.addLayout(actions)

        progress = QVBoxLayout()
        progress.setSpacing(4)
        progress.addWidget(QLabel("Общий прогресс:"))
        self.progress = QProgressBar()
        self.progress.setRange(0, PROGRESS_STEPS)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        progress.addWidget(self.progress)
        self.current_label = _muted("Выберите папку и нажмите «Проверить»")
        progress.addWidget(self.current_label)
        # Процент текущего файла — сразу под общим прогрессом (my_reports, 07.10.2026).
        self.stage_label = _muted()
        progress.addWidget(self.stage_label)
        column.addLayout(progress)

        splitter = self.splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        self.folders = FolderList(on_rollback=self._rollback_folder)
        self.errors = ErrorPanel()
        splitter.addWidget(self.folders)
        splitter.addWidget(self.errors)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        column.addWidget(splitter, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.accept_button = _button("Принять", self._accept, accent=True, enabled=False)
        self.rollback_all_button = _button("Отменить всё", self._rollback_all, enabled=False)
        bottom.addWidget(self.accept_button)
        bottom.addWidget(self.rollback_all_button)
        bottom.addStretch(1)
        self.app_dir_button = _button("Папка с журналом и логами", self._open_app_dir)
        bottom.addWidget(self.app_dir_button)
        column.addLayout(bottom)

        self.status_label = _muted("Готово")
        column.addWidget(self.status_label)

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_sized", False):
            # Список папок — главное; панель ошибок невысокая, её можно растянуть мышью.
            # До первого показа высоты ещё нет, поэтому размеры задаются здесь.
            self._sized = True
            total = sum(self.splitter.sizes())
            self.splitter.setSizes([max(total - ERRORS_HEIGHT, 1), ERRORS_HEIGHT])

    def _show_readiness(self):
        # Всё в порядке — карточку не показываем; при проблеме она объясняет, почему обработка недоступна.
        self.card.setVisible(not self.ready)
        self.status_title.setText("Готово к работе рядом с версией 1" if self.ready
                                  else "Обработка будет недоступна")
        self.status_text.setText(" · ".join(self.lines))
        set_property(self.status_title, "tone", status_module.NEUTRAL if self.ready else status_module.ERROR)
        if self.context.theme is not None:
            self.refresh_theme(self.context.theme)

    def refresh_theme(self, theme):
        name, color = ("check", "success") if self.ready else ("triangle-alert", "danger")
        self.status_icon.setPixmap(theme.pixmap(name, color, 20))
        self.browse_button.setIcon(theme.icon("folder-open"))
        self.errors.refresh_theme(theme)

    # -------------------------------------------------------------- команды

    def _choose(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите корневую папку со снимками", self.root_edit.text())
        if folder:
            self.root_edit.setText(os.path.normpath(folder))
            self.current_label.setText("Папка выбрана. «Проверить» покажет план, ничего не меняя.")

    def _scan(self):
        root = self._selected_root()
        if not root:
            return
        self.folders.clear()
        self.errors.clear()
        self._busy("Проверка папок…")
        self.worker.submit("scan", self.service.scan, root)

    def _start(self):
        root = self._selected_root()
        if not root:
            return
        self.errors.clear()
        self.processed_folders = []
        self._finished_status = {}
        self._rolled_back = set()
        self._busy("Обработка…")
        self.stop_button.setEnabled(True)
        self.worker.submit("start", self.service.start, root)

    def _stop(self):
        self.service.cancel()
        self.stop_button.setEnabled(False)
        self.status_label.setText("Остановка после текущего файла…")

    def _rollback_folder(self, folder):
        if not self.confirm("Откатить папку",
                            "Вернуть папку в состояние до обработки?\n\n{}\n\n"
                            "Изменённые вами файлы удалены не будут.".format(folder)):
            return
        self._busy("Откат папки…")
        self.worker.submit("rollback", self.service.rollback_folder, folder)

    def _rollback_all(self):
        if not self.confirm("Отменить всё", "Вернуть все обработанные папки в исходное состояние?"):
            return
        self._busy("Откат всех папок…")
        self.worker.submit("rollback_all", self.service.rollback_all)

    def _accept(self):
        self.service.accept()
        self.processed_folders = []      # сессия закрыта: откатывать больше нечего
        self.folders.disable_all_rollback()
        self.accept_button.setEnabled(False)
        self.rollback_all_button.setEnabled(False)
        self.status_label.setText("Результат принят. Журнал сохранён в папке программы.")

    def _open_app_dir(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.context.paths.base))

    # --------------------------------------------------------------- события

    def _handle_event(self, event):
        kind = event.get("kind")
        if kind == ev.SCAN_FINISHED:
            self._on_scan_finished(event["result"])
        elif kind == ev.SESSION_STARTED:
            self._on_session_started(event)
        elif kind == ev.FOLDER_STARTED:
            self._current_folder = event["folder"]
            self._current_folder_files = event["files"]
            self.folders.set_status(event["folder"], *status_module.folder_status("running"))
            self.folders.show_folder(event["folder"])
        elif kind == ev.FOLDER_FINISHED:
            self._on_folder_finished(event)
        elif kind == ev.FOLDER_SKIPPED:
            self.folders.set_status(event["folder"], "пропущена: занята другим пользователем", status_module.WARNING)
            self.errors.add(event["reason"], "warning", event["folder"])
        elif kind == ev.FILE_STARTED:
            # Строка папки и строка под полосой берут номер файла из одного и того
            # же события — иначе они на время расходятся (my_reports, 01.10.2026).
            self.progress_model.file_started()
            self.stage_label.setText("")
            if event.get("folder") == self._current_folder and self._current_folder_files:
                self.folders.set_status(self._current_folder, *status_module.folder_progress(
                    event.get("index", 0), self._current_folder_files))
        elif kind == ev.FILE_PREPARED:
            self.progress_model.file_prepared()
            self.stage_label.setText("")
            self._update_progress()
        elif kind == ev.PROGRESS and event.get("stage") in status_module.STAGE_LABELS:
            fraction = event.get("fraction", 0)
            self.progress_model.stage(event["stage"], fraction)
            self.stage_label.setText(status_module.stage_line(event.get("index", "?"), event["stage"], fraction))
            self._update_progress()
        elif kind == ev.PROGRESS and event.get("stage") == "scan":
            self.status_label.setText("Просмотр: {}".format(event["folder"]))
        elif kind == ev.RETRY:
            self.errors.add("повтор {}: попытка {}, пауза {} с — {}".format(
                event["operation"], event["attempt"], event["delay"], event["error"]), "warning")
        elif kind == ev.PROBLEM:
            self.errors.add(event.get("message", ""), "warning", event.get("folder"))
        elif kind == ev.ROLLBACK_FINISHED:
            self._on_rollback_finished(event)
        elif kind == ev.SESSION_FINISHED:
            self.summary = event["summary"]
        elif kind == TASK_DONE:
            self._on_task_done(event)
        elif kind == TASK_FAILED:
            self._on_task_failed(event)

    def _on_scan_finished(self, result):
        self.scan_result = result
        plans = [plan for plan in result.plans if plan.decision.kind != "empty"]
        self.folders.set_folders(plans, result.root, status_module.initial_status)
        self.current_label.setText(
            "К обработке {} файлов в {} папках · уже готовых {} · конфликтов {}".format(
                result.total_files, len(result.to_process),
                len(result.by_kind("done")), len(result.by_kind("conflict"))))

    def _on_session_started(self, event):
        self.progress_model.start(event.get("files", 0))
        self.stage_label.setText("")
        self._update_progress()

    def _on_folder_finished(self, event):
        # Событие приходит дважды: от исполнителя папки (до автоотката) и от сервиса (итог,
        # с rolled_back). Повтор того же итога не дублирует строку в панели ошибок и в списке
        # папок для отката — окно версии 1 их дублировало.
        folder = event["folder"]
        text, colour = status_module.folder_status(event["state"], event.get("error"),
                                                   event.get("rolled_back", False))
        repeated = self._finished_status.get(folder) == (text, colour)
        self._finished_status[folder] = (text, colour)
        self.folders.set_status(folder, text, colour)
        if status_module.can_rollback(event["state"]) and not event.get("rolled_back") \
                and folder not in self.processed_folders:
            self.folders.set_rollback_enabled(folder, True)
            self.processed_folders.append(folder)
        if colour in (status_module.ERROR, status_module.WARNING) and not repeated:
            self.errors.add(text, "error" if colour == status_module.ERROR else "warning", folder)

    def _on_rollback_finished(self, event):
        folder = event["folder"]
        text, colour = status_module.rollback_status(event["state"], event.get("warnings", ()))
        # «Отменить всё» проходит и по уже откатанным папкам: «откатывать нечего» не должно
        # затирать у них «откат выполнен».
        if not (event["state"] == NOTHING and folder in self._rolled_back):
            self.folders.set_status(folder, text, colour)
        if event["state"] == ROLLED_BACK:
            self._rolled_back.add(folder)
        self.folders.set_rollback_enabled(folder, False)
        # Откатанную (в том числе автоматически, D11) папку больше не предлагаем откатывать:
        # окно версии 1 снова включало ей «Откатить» после окончания работы (_idle).
        if folder in self.processed_folders:
            self.processed_folders.remove(folder)
        for warning in event.get("warnings", ()):
            self.errors.add(warning, "warning", folder)

    def _on_task_done(self, event):
        tag = event["tag"]
        self._idle()
        if tag == "start":
            self.summary = event["result"]
            self.current_label.setText(status_module.summary_line(self.summary))
            self.stage_label.setText("")
            self.progress.setValue(PROGRESS_STEPS)
            self.status_label.setText("Обработка завершена. Проверьте результат и нажмите «Принять» или «Отменить всё».")
        elif tag == "scan":
            self.status_label.setText("Проверка завершена, на диске ничего не изменилось")
        elif tag in ("rollback", "rollback_all"):
            self.status_label.setText("Откат завершён")
            if tag == "rollback_all":
                self.processed_folders = []
                self.folders.disable_all_rollback()
                self.rollback_all_button.setEnabled(False)
                self.accept_button.setEnabled(False)
        elif tag == "recovery":
            self._show_recovery(event["result"])
        elif tag == "recover_apply":
            self.status_label.setText("Восстановление выполнено")

    def _on_task_failed(self, event):
        self._idle()
        error = event["error"]
        message = str(error) if isinstance(error, CuratorError) else "{}: {}".format(
            error.__class__.__name__, error)
        self.errors.add(message, "error")
        self.status_label.setText("Ошибка")
        self.notify(QMessageBox.Critical, "Ошибка", message)

    # --------------------------------------------------------- восстановление

    def _check_recovery(self):
        self.worker.submit("recovery", self.service.find_recovery)

    def _show_recovery(self, found):
        if not found:
            return
        dialog = RecoveryDialog(found, self)
        dialog.exec_()
        if not dialog.decisions:
            return
        self._busy("Восстановление…")
        self.worker.submit("recover_apply", self._apply_recovery, dialog.decisions)

    def _apply_recovery(self, decisions):
        return [self.service.recover(item, action) for item, action in decisions]

    # ------------------------------------------------------ окно и закрытие

    @property
    def busy(self):
        return self.worker.busy

    def confirm(self, title, text):
        if not self.context.interactive:
            return True
        return QMessageBox.question(self, title, text) == QMessageBox.Yes

    def notify(self, icon, title, text):
        self.notices.append("{}: {}".format(title, text))
        if self.context.interactive:
            QMessageBox(icon, title, text, QMessageBox.Ok, self).exec_()

    def confirm_close(self):
        """Можно ли закрыть окно программы — как у окна версии 1."""
        if self.worker.busy:
            if self.confirm("Работа не завершена", "Обработка ещё идёт. Остановить её и закрыть программу?"):
                self.service.cancel()
                self.status_label.setText("Остановка после текущего файла… Окно можно будет закрыть после остановки.")
            return False
        if self.processed_folders and self.accept_button.isEnabled():
            return self.confirm("Результат не принят",
                                "Есть обработанные папки, результат не принят.\n"
                                "Закрыть программу? Откат останется доступен через журнал.")
        return True

    def shutdown(self):
        self._timer.stop()
        if self.service is not None:
            self.service.close()

    # --------------------------------------------------------------- частное

    def _selected_root(self):
        root = self.root_edit.text().strip()
        if not root or not os.path.isdir(root):
            self.notify(QMessageBox.Warning, "Папка не выбрана", "Сначала выберите существующую корневую папку.")
            return None
        state.save(self.context.paths.gui_state, last_root=root)
        return root

    def _busy(self, message):
        self.status_label.setText(message)
        for button in (self.scan_button, self.start_button, self.accept_button, self.rollback_all_button):
            button.setEnabled(False)
        self.folders.disable_all_rollback()

    def _idle(self):
        self.scan_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        for folder in self.processed_folders:
            self.folders.set_rollback_enabled(folder, True)
        # «Принять» и «Отменить всё» доступны, пока в сессии есть что откатывать. В окне версии 1
        # их включало только окончание обработки, и после отката одной папки (или «Проверить»)
        # они оставались выключенными до следующей обработки.
        has_changes = bool(self.processed_folders)
        self.accept_button.setEnabled(has_changes)
        self.rollback_all_button.setEnabled(has_changes)

    def _update_progress(self):
        self.progress.setValue(int(round(self.progress_model.fraction * PROGRESS_STEPS)))
        self.current_label.setText(self.progress_model.line())
