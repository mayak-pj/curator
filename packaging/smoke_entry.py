"""Этап 2: проверка стека на Windows 7 (spike).

    tifjpg_smoke.exe           окно с результатами проверок и кнопкой «Конвертировать свой TIFF…»
    tifjpg_smoke.exe --no-gui  только автоматические проверки; отчёт в файл, код возврата 1 при ошибке

Пишет только в %LOCALAPPDATA%\\tifjpg-smoke и (если можно) smoke_report.txt рядом с exe.
Выбранный пользователем TIFF только читается.
"""

import ctypes
import datetime
import os
import platform
import queue
import sys
import tempfile
import threading
import time
import traceback

from tifjpg import __version__
from tifjpg.imaging import ConversionOptions, VipsTiffJpegConverter, validate_jpeg
from tifjpg.imaging.vips_runtime import bundled_vips_dir, load_pyvips, vips_version

WORK_DIR_NAME = "tifjpg-smoke"
CYRILLIC_PARTS = ("Тест", "Объект №1")
REPORT_NAME = "smoke_report.txt"
SCAN_REPORT_NAME = "scan_report.txt"


class Report:
    def __init__(self):
        self.lines = []
        self.failures = 0

    def add(self, status, name, detail=""):
        if status == "FAIL":
            self.failures += 1
        self.lines.append("[{:<4}] {}{}".format(status, name, ": " + detail if detail else ""))

    def text(self):
        header = "TifJpg smoke {} — {}".format(__version__, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        summary = "ИТОГ: {}".format("ошибок нет" if not self.failures else "ошибок: {}".format(self.failures))
        return "\n".join([header, ""] + self.lines + ["", summary])


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def work_dir():
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    path = os.path.join(base, WORK_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def peak_memory_mb():
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                        "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            kernel32 = ctypes.WinDLL("kernel32")
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.GetCurrentProcess.argtypes = []
            psapi = ctypes.WinDLL("psapi")
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return counters.PeakWorkingSetSize / 1e6
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / 1e6 if sys.platform == "darwin" else peak / 1e3
    except Exception:
        return None


def loaded_module_path(name):
    """Полный путь, откуда процесс фактически загрузил DLL (None, если не загружена)."""
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.GetModuleFileNameW.restype = wintypes.DWORD
    handle = kernel32.GetModuleHandleW(name)
    if not handle:
        return None
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetModuleFileNameW(handle, buffer, len(buffer))
    return buffer.value if length else None


def is_admin():
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None


def check(report, name, function):
    try:
        detail = function()
        report.add("OK", name, detail or "")
    except Exception as exc:
        report.add("FAIL", name, "{}: {}".format(exc.__class__.__name__, exc))
        report.lines.extend("         " + line for line in traceback.format_exc().splitlines())


def environment(report):
    report.add("INFO", "Программа", "{} ({})".format(app_dir(), "сборка" if getattr(sys, "frozen", False) else "исходники"))
    report.add("INFO", "ОС", platform.platform())
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        report.add("INFO", "Windows", "{}.{} build {} {}".format(version.major, version.minor, version.build, version.service_pack))
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        report.add("INFO", "UCRT (ucrtbase.dll)", "есть" if os.path.exists(os.path.join(system32, "ucrtbase.dll")) else "НЕТ")
    report.add("INFO", "Python", "{} {}".format(sys.version.split()[0], platform.architecture()[0]))
    report.add("INFO", "Права администратора", {True: "да", False: "нет", None: "не удалось определить"}[is_admin()])
    report.add("INFO", "Рабочая папка", work_dir())


def check_tk():
    import tkinter

    return "Tcl/Tk {}".format(tkinter.Tcl().eval("info patchlevel"))


def check_vips():
    load_pyvips()
    detail = "libvips {}".format(vips_version())
    if sys.platform != "win32":
        return detail
    loaded = loaded_module_path("libvips-42.dll")
    if getattr(sys, "frozen", False):
        expected = os.path.normcase(os.path.join(sys._MEIPASS, ""))
        if not loaded or not os.path.normcase(loaded).startswith(expected):
            raise RuntimeError("libvips-42.dll загружена не из сборки: {} (ожидалось {})".format(loaded, expected))
    return "{}, загружена из {}".format(detail, loaded)


def check_runtime_dlls():
    """Откуда загружены CRT и Python: UCRT должен быть системным (System32)."""
    parts = []
    for name in ("ucrtbase.dll", "vcruntime140.dll", "python38.dll"):
        parts.append("{} = {}".format(name, loaded_module_path(name) or "не загружена"))
    ucrt = loaded_module_path("ucrtbase.dll")
    if getattr(sys, "frozen", False) and ucrt and os.path.normcase(sys._MEIPASS) in os.path.normcase(ucrt):
        raise RuntimeError("ucrtbase.dll загружена из сборки, а не из системы: " + "; ".join(parts))
    return "; ".join(parts)


def check_app_dir_writable():
    probe = os.path.join(app_dir(), "smoke_write_probe.tmp")
    with open(probe, "w") as stream:
        stream.write("probe")
    os.remove(probe)
    return "запись рядом с exe возможна (нужна для журнала)"


def make_test_tiff(path, width, height, compression):
    vips = load_pyvips()
    ramp = vips.Image.xyz(width, height).extract_band(0).linear(65535.0 / (width - 1), 0).cast("ushort")
    ramp.copy(interpretation="grey16").tiffsave(path, compression=compression)


def check_conversion(compression):
    def run():
        folder = os.path.join(work_dir(), *CYRILLIC_PARTS)
        os.makedirs(folder, exist_ok=True)
        source = os.path.join(folder, "снимок {}.tif".format(compression))
        destination = os.path.join(folder, "Рентгенограмма {}.jpeg".format(compression))
        for path in (source, destination):
            if os.path.exists(path):
                os.remove(path)
        make_test_tiff(source, 4000, 3000, compression)
        result = VipsTiffJpegConverter().convert(source, destination, ConversionOptions())
        check_result = validate_jpeg(destination, result.width, result.height, result.bands)
        os.remove(source)
        os.remove(destination)
        return "{}x{} {} -> JPEG Q{} {:.1f} МБ за {:.2f} с, путь с кириллицей и №".format(
            result.width, result.height, compression, check_result.estimated_quality,
            result.output_bytes / 1e6, result.seconds)

    return run


def run_checks():
    report = Report()
    environment(report)
    check(report, "tkinter", check_tk)
    check(report, "libvips", check_vips)
    if sys.platform == "win32":
        check(report, "CRT и Python", check_runtime_dlls)
    check(report, "Конвертация несжатого TIFF", check_conversion("none"))
    check(report, "Конвертация LZW TIFF", check_conversion("lzw"))
    try:
        report.add("OK", "Папка программы", check_app_dir_writable())
    except OSError as exc:
        report.add("WARN", "Папка программы", "запись невозможна: {}".format(exc))
    memory = peak_memory_mb()
    report.add("INFO", "Пиковая память процесса", "{:.0f} МБ".format(memory) if memory else "н/д")
    return report


def write_text(name, text):
    """Сохранить текст рядом с exe, а если туда нельзя — в рабочую папку."""
    for folder in (app_dir(), work_dir()):
        path = os.path.join(folder, name)
        try:
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(text + "\n")
            return path
        except OSError:
            continue
    return None


def write_report(report):
    return write_text(REPORT_NAME, report.text())


class SmokeWindow:
    def __init__(self, report):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.report = report
        self.events = queue.Queue()
        self.busy = False

        self.root = tk.Tk()
        self.root.title("TifJpg — проверка на Windows 7")
        self.root.geometry("900x620")

        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)
        self.text = tk.Text(frame, wrap="word", font=("Consolas", 10))
        self.text.pack(fill="both", expand=True)

        self.progress = ttk.Progressbar(frame, maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))
        self.status = ttk.Label(frame, text="")
        self.status.pack(fill="x")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(8, 0))
        self.choose_button = ttk.Button(buttons, text="Конвертировать свой TIFF…", command=self.choose)
        self.choose_button.pack(side="left")
        self.scan_button = ttk.Button(buttons, text="Проверить папку (без изменений)…", command=self.choose_folder)
        self.scan_button.pack(side="left", padx=8)
        ttk.Button(buttons, text="Открыть папку с результатами", command=self.open_work_dir).pack(side="left", padx=8)
        ttk.Button(buttons, text="Закрыть", command=self.root.destroy).pack(side="right")

        self.refresh()
        self.root.after(100, self.poll)

    def refresh(self):
        self.show(self.report.text())
        path = write_report(self.report)
        self.status.configure(text="Отчёт: {}".format(path or "не удалось сохранить"))

    def show(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.configure(state="disabled")

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.choose_button.configure(state=state)
        self.scan_button.configure(state=state)

    def choose_folder(self):
        """Сухой прогон по дереву папок: только чтение, ничего не меняется."""
        from tkinter import filedialog

        if self.busy:
            return
        root = filedialog.askdirectory(title="Выберите корневую папку — файлы только читаются")
        if not root:
            return
        self.set_busy(True)
        self.progress["value"] = 0
        self.status.configure(text="Проверка папки…")
        threading.Thread(target=self.scan_folder, args=(root,), daemon=True).start()

    def scan_folder(self, root):
        from tifjpg.app.scan import render_report, scan_root

        started = time.monotonic()
        try:
            seen = [0]

            def on_folder(plan):
                seen[0] += 1
                if seen[0] % 20 == 0:
                    self.events.put(("scan_progress", seen[0], plan.folder))

            result = scan_root(root, on_folder=on_folder)
            text = render_report(result, full=True)
            path = write_text(SCAN_REPORT_NAME, text)
            detail = "{}: папок {}, к обработке {} файлов в {} папках, готовых {}, конфликтов {}; {:.1f} с; отчёт: {}".format(
                root, result.folders_seen, result.total_files, len(result.to_process),
                len(result.by_kind("done")), len(result.by_kind("conflict")),
                time.monotonic() - started, path)
            self.events.put(("scan_done", "OK", detail, text))
        except Exception as exc:
            self.events.put(("scan_done", "FAIL", "{}: {}: {}".format(root, exc.__class__.__name__, exc), ""))

    def open_work_dir(self):
        if sys.platform == "win32":
            os.startfile(work_dir())

    def choose(self):
        from tkinter import filedialog

        if self.busy:
            return
        source = filedialog.askopenfilename(
            title="Выберите TIFF (файл только читается)",
            filetypes=[("TIFF", "*.tif *.tiff *.TIF *.TIFF"), ("Все файлы", "*.*")],
        )
        if not source:
            return
        folder = os.path.join(work_dir(), "manual")
        os.makedirs(folder, exist_ok=True)
        index = 1
        while os.path.exists(os.path.join(folder, "Рентгенограмма {}.jpeg".format(index))):
            index += 1
        destination = os.path.join(folder, "Рентгенограмма {}.jpeg".format(index))
        self.busy = True
        self.choose_button.configure(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self.convert, args=(source, destination), daemon=True).start()

    def convert(self, source, destination):
        started = time.monotonic()
        try:
            result = VipsTiffJpegConverter().convert(
                source, destination, ConversionOptions(),
                progress=lambda stage, fraction: self.events.put(("progress", stage, fraction)),
            )
            validate_started = time.monotonic()
            check_result = validate_jpeg(destination, result.width, result.height, result.bands)
            detail = "{} -> {} | {}x{} {} {} | JPEG Q{} {:.1f} МБ | конвертация {:.1f} с, проверка {:.1f} с, всего {:.1f} с | пик памяти {} МБ".format(
                source, destination, result.width, result.height, result.source_format, result.compression,
                check_result.estimated_quality, result.output_bytes / 1e6, result.seconds,
                time.monotonic() - validate_started, time.monotonic() - started,
                "{:.0f}".format(peak_memory_mb()) if peak_memory_mb() else "н/д")
            self.events.put(("done", "OK", detail))
        except Exception as exc:
            self.events.put(("done", "FAIL", "{}: {}: {}".format(source, exc.__class__.__name__, exc)))

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    _, stage, fraction = event
                    self.progress["value"] = fraction * 100
                    self.status.configure(text="{}: {:.0f} %".format(stage, fraction * 100))
                elif event[0] == "scan_progress":
                    _, count, folder = event
                    self.status.configure(text="Проверено папок: {} — {}".format(count, folder))
                elif event[0] == "scan_done":
                    _, status, detail, text = event
                    self.report.add(status, "Проверка папки", detail)
                    self.set_busy(False)
                    write_report(self.report)
                    if text:
                        self.show(text)
                        self.status.configure(text=detail)
                    else:
                        self.refresh()
                else:
                    _, status, detail = event
                    self.report.add(status, "Ручная конвертация", detail)
                    self.set_busy(False)
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def run(self):
        self.root.mainloop()


def show_fatal(message):
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "TifJpg smoke", 0x10)
    elif sys.stderr:
        sys.stderr.write(message + "\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    report = run_checks()
    path = write_report(report)
    if "--no-gui" in argv:
        if sys.stdout:
            print(report.text())
            print("Отчёт: {}".format(path))
        return 1 if report.failures else 0
    try:
        SmokeWindow(report).run()
    except Exception:
        show_fatal("Не удалось открыть окно:\n{}\n\nОтчёт: {}".format(traceback.format_exc(), path))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
