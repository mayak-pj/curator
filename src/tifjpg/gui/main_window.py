"""Главное окно (ARCHITECTURE.md, раздел 18).

Окно только отображает события сервиса и отправляет команды. Вся работа
идёт в фоновом потоке, поэтому интерфейс не замирает на больших снимках.
"""

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tifjpg import __version__
from tifjpg.app import events as ev
from tifjpg.app.service import ProcessingService
from tifjpg.domain.errors import TifJpgError
from tifjpg.gui import recovery_dialog, status as status_module, theme
from tifjpg.gui.bridge import TASK_DONE, TASK_FAILED, Worker
from tifjpg.gui.error_panel import ErrorPanel
from tifjpg.gui.folder_list import FolderList


class MainWindow:
    def __init__(self, service=None):
        self.root = tk.Tk()
        self.root.title("TifJpg {} — конвертер рентгенограмм".format(__version__))
        self.root.geometry("1000x720")
        self.root.minsize(780, 560)
        theme.apply(self.root)

        self.worker = Worker(lambda delay, callback: self.root.after(delay, callback), self._handle_event)
        self.service = service or ProcessingService(on_event=self.worker.post)
        self.scan_result = None
        self.summary = None
        self.total_files = 0
        self.done_files = 0
        self.processed_folders = []

        self._build()
        self.worker.start_polling()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(400, self._check_recovery)

    # ------------------------------------------------------------- интерфейс

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        chooser = ttk.Frame(outer)
        chooser.pack(fill="x")
        ttk.Label(chooser, text="Корневая папка:").pack(side="left")
        self.root_var = tk.StringVar()
        entry = ttk.Entry(chooser, textvariable=self.root_var)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(chooser, text="Обзор…", command=self._choose).pack(side="left")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))
        self.scan_button = ttk.Button(actions, text="Проверить (без изменений)", command=self._scan)
        self.scan_button.pack(side="left")
        self.start_button = ttk.Button(actions, text="Начать обработку", command=self._start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="Остановить", command=self._stop, state="disabled")
        self.stop_button.pack(side="left")

        progress = ttk.Frame(outer)
        progress.pack(fill="x", pady=(12, 0))
        ttk.Label(progress, text="Общий прогресс:").pack(anchor="w")
        self.progress = ttk.Progressbar(progress, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(2, 2))
        self.current_var = tk.StringVar(value="Выберите папку и нажмите «Проверить»")
        ttk.Label(progress, textvariable=self.current_var, style="Muted.TLabel").pack(anchor="w")

        self.folders = FolderList(outer, on_rollback=self._rollback_folder)
        self.folders.pack(fill="both", expand=True, pady=(12, 0))

        self.errors = ErrorPanel(outer)
        self.errors.pack(fill="both", expand=False, pady=(12, 0))

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(12, 0))
        self.accept_button = ttk.Button(bottom, text="Принять", command=self._accept, state="disabled")
        self.accept_button.pack(side="left")
        self.rollback_all_button = ttk.Button(bottom, text="Отменить всё", command=self._rollback_all,
                                              state="disabled")
        self.rollback_all_button.pack(side="left", padx=8)
        ttk.Button(bottom, text="Папка с журналом и логами", command=self._open_app_dir).pack(side="right")

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(outer, textvariable=self.status_var, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

    # -------------------------------------------------------------- команды

    def _choose(self):
        folder = filedialog.askdirectory(title="Выберите корневую папку со снимками")
        if folder:
            self.root_var.set(folder)
            self.current_var.set("Папка выбрана. «Проверить» покажет план, ничего не меняя.")

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
        if not messagebox.askyesno(
                "Начать обработку",
                "Будут созданы JPEG, а оригиналы перенесены в подпапку ИСХ.\n\n"
                "Любую папку можно вернуть кнопкой «Откатить».\n\nПродолжить?"):
            return
        self.errors.clear()
        self.processed_folders = []
        self._busy("Обработка…")
        self.stop_button.configure(state="normal")
        self.worker.submit("start", self.service.start, root)

    def _stop(self):
        self.service.cancel()
        self.stop_button.configure(state="disabled")
        self.status_var.set("Остановка после текущего файла…")

    def _rollback_folder(self, folder):
        if not messagebox.askyesno("Откатить папку",
                                   "Вернуть папку в состояние до обработки?\n\n{}\n\n"
                                   "Изменённые вами файлы удалены не будут.".format(folder)):
            return
        self._busy("Откат папки…")
        self.worker.submit("rollback", self.service.rollback_folder, folder)

    def _rollback_all(self):
        if not messagebox.askyesno("Отменить всё",
                                   "Вернуть все обработанные папки в исходное состояние?"):
            return
        self._busy("Откат всех папок…")
        self.worker.submit("rollback_all", self.service.rollback_all)

    def _accept(self):
        if not messagebox.askyesno("Принять",
                                   "Принять результат? После этого откат из окна будет недоступен."):
            return
        self.service.accept()
        self.folders.disable_all_rollback()
        self.accept_button.configure(state="disabled")
        self.rollback_all_button.configure(state="disabled")
        self.status_var.set("Результат принят. Журнал сохранён в папке программы.")

    def _open_app_dir(self):
        path = self.service.paths.base
        if sys.platform == "win32":
            os.startfile(path)
        else:
            messagebox.showinfo("Папка программы", path)

    # --------------------------------------------------------------- события

    def _handle_event(self, event):
        kind = event.get("kind")
        if kind == ev.SCAN_FINISHED:
            self._on_scan_finished(event["result"])
        elif kind == ev.SESSION_STARTED:
            self._on_session_started(event)
        elif kind == ev.FOLDER_STARTED:
            self.folders.set_status(event["folder"], *status_module.folder_status("running"))
            self.folders.show(event["folder"])
            self.current_var.set("Папка: {} ({} файлов)".format(event["folder"], event["files"]))
        elif kind == ev.FOLDER_FINISHED:
            self._on_folder_finished(event)
        elif kind == ev.FOLDER_SKIPPED:
            self.folders.set_status(event["folder"], "пропущена: занята другим пользователем", "warning")
            self.errors.add(event["reason"], "warning", event["folder"])
        elif kind == ev.FILE_COMPLETED:
            self.done_files += 1
            self._update_progress()
        elif kind == ev.PROGRESS and event.get("stage") in ("histogram", "encode"):
            self.status_var.set("Файл #{}: {} {:.0f} %".format(
                event.get("index", "?"), event["stage"], event.get("fraction", 0) * 100))
        elif kind == ev.PROGRESS and event.get("stage") == "scan":
            self.status_var.set("Просмотр: {}".format(event["folder"]))
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
        self.current_var.set(
            "К обработке {} файлов в {} папках · уже готовых {} · конфликтов {}".format(
                result.total_files, len(result.to_process),
                len(result.by_kind("done")), len(result.by_kind("conflict"))))

    def _on_session_started(self, event):
        self.total_files = max(event.get("files", 0), 1)
        self.done_files = 0
        self.progress.configure(maximum=self.total_files, value=0)

    def _on_folder_finished(self, event):
        folder = event["folder"]
        text, colour = status_module.folder_status(event["state"], event.get("error"),
                                                   event.get("rolled_back", False))
        self.folders.set_status(folder, text, colour)
        if status_module.can_rollback(event["state"]) and not event.get("rolled_back"):
            self.folders.set_rollback_enabled(folder, True)
            self.processed_folders.append(folder)
        if colour in ("error", "warning"):
            self.errors.add(text, "error" if colour == "error" else "warning", folder)

    def _on_rollback_finished(self, event):
        text, colour = status_module.rollback_status(event["state"], event.get("warnings", ()))
        self.folders.set_status(event["folder"], text, colour)
        self.folders.set_rollback_enabled(event["folder"], False)
        for warning in event.get("warnings", ()):
            self.errors.add(warning, "warning", event["folder"])

    def _on_task_done(self, event):
        tag = event["tag"]
        self._idle()
        if tag == "start":
            summary = event["result"]
            self.summary = summary
            self.current_var.set(status_module.summary_line(summary))
            self.progress.configure(value=self.progress["maximum"])
            has_changes = bool(self.processed_folders)
            self.accept_button.configure(state="normal" if has_changes else "disabled")
            self.rollback_all_button.configure(state="normal" if has_changes else "disabled")
            self.status_var.set("Обработка завершена. Проверьте результат и нажмите «Принять» или «Отменить всё».")
        elif tag == "scan":
            self.status_var.set("Проверка завершена, на диске ничего не изменилось")
        elif tag in ("rollback", "rollback_all"):
            self.status_var.set("Откат завершён")
            if tag == "rollback_all":
                self.folders.disable_all_rollback()
                self.rollback_all_button.configure(state="disabled")
                self.accept_button.configure(state="disabled")
        elif tag == "recovery":
            self._show_recovery(event["result"])
        elif tag == "recover_apply":
            self.status_var.set("Восстановление выполнено")

    def _on_task_failed(self, event):
        self._idle()
        error = event["error"]
        message = str(error) if isinstance(error, TifJpgError) else "{}: {}".format(
            error.__class__.__name__, error)
        self.errors.add(message, "error")
        self.status_var.set("Ошибка")
        messagebox.showerror("Ошибка", message)

    # --------------------------------------------------------- восстановление

    def _check_recovery(self):
        self.worker.submit("recovery", self.service.find_recovery)

    def _show_recovery(self, found):
        if not found:
            return
        decisions = recovery_dialog.ask(self.root, found)
        if not decisions:
            return
        self._busy("Восстановление…")
        self.worker.submit("recover_apply", self._apply_recovery, decisions)

    def _apply_recovery(self, decisions):
        return [self.service.recover(item, action) for item, action in decisions]

    # --------------------------------------------------------------- частное

    def _selected_root(self):
        root = self.root_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("Папка не выбрана", "Сначала выберите существующую корневую папку.")
            return None
        return root

    def _busy(self, message):
        self.status_var.set(message)
        for button in (self.scan_button, self.start_button, self.accept_button, self.rollback_all_button):
            button.configure(state="disabled")
        self.folders.disable_all_rollback()

    def _idle(self):
        self.scan_button.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        for folder in self.processed_folders:
            self.folders.set_rollback_enabled(folder, True)

    def _update_progress(self):
        self.progress.configure(value=min(self.done_files, self.total_files))
        self.status_var.set("Готово файлов: {} из {}".format(self.done_files, self.total_files))

    def _on_close(self):
        if self.worker.busy:
            if not messagebox.askyesno("Работа не завершена",
                                       "Обработка ещё идёт. Остановить её и закрыть программу?"):
                return
            self.service.cancel()
            return
        if self.processed_folders and self.accept_button["state"] == "normal":
            if not messagebox.askyesno(
                    "Результат не принят",
                    "Есть обработанные папки, результат не принят.\n"
                    "Закрыть программу? Откат останется доступен через журнал."):
                return
        self.service.close()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def run():
    try:
        MainWindow().run()
    except TifJpgError as exc:
        _fatal(str(exc))
        return 2
    return 0


def _fatal(message):
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("TifJpg", message)
        root.destroy()
    except Exception:
        sys.stderr.write(message + "\n")
