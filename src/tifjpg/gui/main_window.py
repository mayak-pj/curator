"""Главное окно (ARCHITECTURE.md, раздел 18).

Окно только отображает события сервиса и отправляет команды. Вся работа
идёт в фоновом потоке, поэтому интерфейс не замирает на больших снимках.
"""

import json
import math
import os
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tifjpg import __version__
from tifjpg.app import events as ev
from tifjpg.app.service import ProcessingService
from tifjpg.domain import states
from tifjpg.domain.errors import TifJpgError
from tifjpg.gui import recovery_dialog, status as status_module, theme
from tifjpg.gui.bridge import TASK_DONE, TASK_FAILED, Worker
from tifjpg.gui.error_panel import ErrorPanel
from tifjpg.gui.folder_list import FolderList

# Подписи шагов конвертации и сетевых шагов (executor.py шлёт их байтовым
# прогрессом) — без них статус часами не меняется на "encode 100 %", пока
# файл на самом деле ещё копируется по сети.
STAGE_LABELS = {
    "histogram": "анализ снимка",
    "encode": "сохранение JPEG",
    states.STAGED: "чтение оригинала",
    states.JPEG_UPLOADED: "запись JPEG",
    states.SOURCE_COPIED: "копирование оригинала в архив",
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


def _progress_bucket(stage):
    return "convert" if stage in ("histogram", "encode") else stage


class _Lightbulb(tk.Canvas):
    """Переключатель темы: горит — светлая, погасла — тёмная.

    Рисуем сами, а не берём эмодзи-символ лампочки: у обычных шрифтов
    Windows 7 нет цветных эмодзи, вместо значка был бы квадратик-заглушка.
    """

    SIZE = 34
    _GLOW = "#fff3cf"
    _GLASS_LIT = "#ffd35c"
    _OUTLINE_LIT = "#c9971f"
    _BASE = "#8a8f98"

    def __init__(self, master, command):
        super().__init__(master, width=self.SIZE, height=self.SIZE, highlightthickness=0, cursor="hand2")
        self._command = command
        self.bind("<Button-1>", lambda _event: self._command())

    def set_lit(self, lit):
        self.configure(background=theme.background())
        self.delete("all")
        cx, cy, r = self.SIZE // 2, self.SIZE // 2 - 3, 9

        if lit:
            self.create_oval(cx - r - 5, cy - r - 5, cx + r + 5, cy + r + 5,
                             fill=self._GLOW, outline="")
            for angle in (-55, -20, 20, 55):
                radians = math.radians(angle - 90)
                x1, y1 = cx + (r + 3) * math.cos(radians), cy + (r + 3) * math.sin(radians)
                x2, y2 = cx + (r + 8) * math.cos(radians), cy + (r + 8) * math.sin(radians)
                self.create_line(x1, y1, x2, y2, fill=self._OUTLINE_LIT, width=2, capstyle="round")
            glass_fill, glass_outline = self._GLASS_LIT, self._OUTLINE_LIT
        else:
            glass_fill, glass_outline = theme.background(), theme.muted()

        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=glass_fill, outline=glass_outline, width=2)
        base_top = cy + r - 2
        self.create_rectangle(cx - 5, base_top, cx + 5, base_top + 7,
                              fill=self._BASE if lit else theme.muted(), outline="")
        for offset in (2, 4, 6):
            self.create_line(cx - 5, base_top + offset, cx + 5, base_top + offset,
                             fill=theme.background())
        self.create_polygon(cx - 4, base_top + 7, cx + 4, base_top + 7, cx + 2, base_top + 11,
                            cx - 2, base_top + 11, fill=self._BASE if lit else theme.muted(), outline="")


class MainWindow:
    def __init__(self, service=None):
        self.root = tk.Tk()
        self.root.title("TifJpg {} — конвертер рентгенограмм".format(__version__))
        self.root.geometry("1000x720")
        self.root.minsize(780, 560)

        self.worker = Worker(lambda delay, callback: self.root.after(delay, callback), self._handle_event)
        self.service = service or ProcessingService(on_event=self.worker.post)
        self.scan_result = None
        self.summary = None
        self.total_files = 0
        self.done_files = 0
        self.processed_folders = []
        self._current_folder = None
        self._current_folder_files = 0
        self._file_stage_fraction = {}
        self._session_started_at = 0.0

        gui_state = self._load_gui_state()
        theme.apply(self.root, dark=bool(gui_state.get("dark")))

        self._build()
        last_root = gui_state.get("last_root")
        if last_root:
            self.root_var.set(last_root)
            self.current_var.set("Загружена последняя папка. «Проверить» покажет план, ничего не меняя.")

        self.worker.start_polling()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(400, self._check_recovery)

    # ------------------------------------------------------------- интерфейс

    def _build(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        chooser = ttk.Frame(outer)
        chooser.pack(fill="x")
        self._lightbulb = _Lightbulb(chooser, command=self._toggle_theme)
        self._lightbulb.pack(side="right", padx=(8, 0))
        self._lightbulb.set_lit(not theme.is_dark())
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
            self._current_folder = event["folder"]
            self._current_folder_files = event["files"]
            self.folders.set_status(event["folder"], *status_module.folder_status("running"))
            self.folders.show(event["folder"])
        elif kind == ev.FOLDER_FINISHED:
            self._on_folder_finished(event)
        elif kind == ev.FOLDER_SKIPPED:
            self.folders.set_status(event["folder"], "пропущена: занята другим пользователем", "warning")
            self.errors.add(event["reason"], "warning", event["folder"])
        elif kind == ev.FILE_STARTED:
            # Строка папки и строка снизу берут номер файла из одного и того
            # же события — иначе они на время расходятся (my_reports, 01.10.2026):
            # одна ещё показывает прошлый файл, другая — уже следующий.
            self._file_stage_fraction = {}
            if event.get("folder") == self._current_folder and self._current_folder_files:
                self.folders.set_status(self._current_folder, *status_module.folder_progress(
                    event.get("index", 0), self._current_folder_files))
        elif kind == ev.FILE_PREPARED:
            self.done_files += 1
            self._file_stage_fraction = {}
            self._update_progress()
        elif kind == ev.PROGRESS and event.get("stage") in STAGE_LABELS:
            fraction = event.get("fraction", 0)
            self._file_stage_fraction[_progress_bucket(event["stage"])] = fraction
            self.status_var.set("Файл #{}: {} {:.0f} %".format(
                event.get("index", "?"), STAGE_LABELS[event["stage"]], fraction * 100))
            self._update_progress()
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
        self._file_stage_fraction = {}
        self._session_started_at = time.monotonic()
        self.progress.configure(maximum=self.total_files, value=0)
        self._update_progress()

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
        self._save_gui_state(last_root=root)
        return root

    def _toggle_theme(self):
        dark = not theme.is_dark()
        theme.apply(self.root, dark=dark)
        self.folders.refresh_theme()
        self.errors.refresh_theme()
        self._lightbulb.set_lit(not dark)
        self._save_gui_state(dark=dark)

    def _load_gui_state(self):
        try:
            with open(self.service.paths.gui_state, encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_gui_state(self, **changes):
        # Последняя папка и тема — удобство, а не данные: не страшно, если не сохранится.
        state = self._load_gui_state()
        state.update(changes)
        try:
            with open(self.service.paths.gui_state, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False)
        except OSError:
            pass

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
        # Доля текущего файла по весам STAGE_WEIGHTS — без неё полоса стояла
        # бы на месте весь файл и прыгала целой единицей только в его конце.
        file_fraction = sum(weight * self._file_stage_fraction.get(key, 0.0)
                            for key, weight in STAGE_WEIGHTS.items())
        value = min(self.done_files + file_fraction, self.total_files)
        self.progress.configure(value=value)
        fraction = value / self.total_files if self.total_files else 0.0
        elapsed = time.monotonic() - self._session_started_at
        self.current_var.set(status_module.progress_line(self.done_files, self.total_files, fraction, elapsed))

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
