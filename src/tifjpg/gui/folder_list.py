"""Список папок: имя, статус цветом и кнопка «Откатить» у каждой строки."""

import os
import tkinter as tk
from tkinter import ttk

from tifjpg.gui import theme


class FolderList(ttk.Frame):
    def __init__(self, master, on_rollback=None, **kwargs):
        super().__init__(master, **kwargs)
        self._on_rollback = on_rollback
        self._rows = {}
        self._root = ""

        self._canvas = tk.Canvas(self, background=theme.SURFACE, highlightthickness=1,
                                 highlightbackground=theme.BORDER, height=220)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._inner = ttk.Frame(self._canvas, style="Surface.TFrame")
        self._window = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>",
                         lambda event: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda event: self._canvas.itemconfigure(self._window, width=event.width))
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._canvas.bind_all(sequence, self._on_wheel)

    # ------------------------------------------------------------------ API

    def clear(self):
        for row in self._inner.winfo_children():
            row.destroy()
        self._rows = {}

    def set_folders(self, plans, root, status_of):
        self.clear()
        self._root = root
        for plan in plans:
            text, colour = status_of(plan)
            self._add_row(plan.folder, text, colour)
        self._canvas.yview_moveto(0)

    def set_status(self, folder, text, colour):
        row = self._rows.get(folder)
        if row is None:
            self._add_row(folder, text, colour)
            return
        row["status"].configure(text=text, style="{}.Surface.TLabel".format(colour))

    def set_rollback_enabled(self, folder, enabled):
        row = self._rows.get(folder)
        if row is not None:
            row["button"].configure(state="normal" if enabled else "disabled")

    def disable_all_rollback(self):
        for row in self._rows.values():
            row["button"].configure(state="disabled")

    def show(self, folder):
        """Прокрутить список к папке, которая сейчас обрабатывается."""
        row = self._rows.get(folder)
        if row is None:
            return
        self._canvas.update_idletasks()
        total = max(self._inner.winfo_height(), 1)
        self._canvas.yview_moveto(max(0.0, (row["frame"].winfo_y() - 40) / total))

    # -------------------------------------------------------------- частное

    def _add_row(self, folder, text, colour):
        frame = ttk.Frame(self._inner, style="Surface.TFrame", padding=(8, 3))
        frame.pack(fill="x")
        frame.columnconfigure(0, weight=1)

        name = ttk.Label(frame, text=self._display_name(folder), style="Surface.TLabel",
                         font=theme.FONT_BOLD, anchor="w")
        name.grid(row=0, column=0, sticky="we")
        status = ttk.Label(frame, text=text, style="{}.Surface.TLabel".format(colour), anchor="w")
        status.grid(row=1, column=0, sticky="we")
        button = ttk.Button(frame, text="Откатить", width=11, state="disabled",
                            command=lambda path=folder: self._rollback(path))
        button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))

        separator = ttk.Separator(self._inner, orient="horizontal")
        separator.pack(fill="x")
        self._rows[folder] = {"frame": frame, "status": status, "button": button}

    def _display_name(self, folder):
        try:
            relative = os.path.relpath(folder, self._root)
        except ValueError:
            return folder
        return folder if relative.startswith("..") else relative

    def _rollback(self, folder):
        if self._on_rollback is not None:
            self._on_rollback(folder)

    def _on_wheel(self, event):
        if event.num == 4:
            self._canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(1, "units")
        else:
            self._canvas.yview_scroll(-1 * (event.delta // 120 or (1 if event.delta < 0 else -1)), "units")
