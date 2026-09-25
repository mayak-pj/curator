"""Панель ошибок и предупреждений (ARCHITECTURE.md, раздел 18)."""

import datetime
import tkinter as tk
from tkinter import ttk

from tifjpg.gui import theme


class ErrorPanel(ttk.Labelframe):
    def __init__(self, master, **kwargs):
        super().__init__(master, text="Ошибки и предупреждения", padding=(8, 4), **kwargs)
        self._count = 0

        self._text = tk.Text(self, height=7, wrap="word", font=theme.FONT_MONO,
                             background=theme.SURFACE, foreground=theme.TEXT,
                             relief="solid", borderwidth=1, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set, state="disabled")
        self._text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._text.tag_configure("error", foreground=theme.colour("error"))
        self._text.tag_configure("warning", foreground=theme.colour("warning"))

    @property
    def count(self):
        return self._count

    def add(self, message, level="warning", folder=None):
        self._count += 1
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = "{}  {}{}\n".format(stamp, "{}: ".format(folder) if folder else "", message)
        self._text.configure(state="normal")
        self._text.insert("end", line, "error" if level == "error" else "warning")
        self._text.see("end")
        self._text.configure(state="disabled")
        self.configure(text="Ошибки и предупреждения ({})".format(self._count))

    def clear(self):
        self._count = 0
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self.configure(text="Ошибки и предупреждения")
