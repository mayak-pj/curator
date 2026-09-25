"""Диалог восстановления после сбоя (ARCHITECTURE.md, раздел 14).

Показывается при запуске, если найдены незавершённые операции. Ни одно
действие не выполняется само: пользователь выбирает «Продолжить»,
«Откатить» или «Игнорировать», и «Игнорировать» ничего не удаляет.
"""

import tkinter as tk
from tkinter import ttk

from tifjpg.app.service import CONTINUE, IGNORE, ROLLBACK
from tifjpg.gui import theme

SITUATION_TEXT = {
    "phase_b_unfinished": "фиксация не завершена: часть файлов уже переименована",
    "phase_a_leftovers": "остались временные файлы предыдущего запуска",
    "consistent": "состояние согласовано",
}


class RecoveryDialog(tk.Toplevel):
    """Возвращает список (item, действие) — выполняет их вызывающая сторона."""

    def __init__(self, master, items):
        super().__init__(master)
        self.title("Обнаружена незавершённая операция")
        self.transient(master)
        self.resizable(True, False)
        self.configure(background=theme.BACKGROUND)
        self.decisions = []

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Предыдущий запуск завершился не полностью.",
                  style="Heading.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Выберите, что сделать с каждой папкой. «Игнорировать» ничего не удаляет.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 8))

        self._rows = {}
        for journal_path, item in items:
            self._add_row(frame, journal_path, item)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Применить", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="Ничего не делать", command=self.destroy).pack(side="right", padx=8)

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()

    def _add_row(self, master, journal_path, item):
        box = ttk.Labelframe(master, text=item.folder, padding=8)
        box.pack(fill="x", pady=4)
        ttk.Label(box, text=SITUATION_TEXT.get(item.situation, item.situation),
                  style="Muted.TLabel").pack(anchor="w")
        for detail in item.details[:5]:
            ttk.Label(box, text="• " + detail, style="Muted.TLabel").pack(anchor="w")

        choice = tk.StringVar(value=IGNORE)
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(6, 0))
        for text, value in (("Продолжить", CONTINUE), ("Откатить", ROLLBACK), ("Игнорировать", IGNORE)):
            ttk.Radiobutton(row, text=text, value=value, variable=choice).pack(side="left", padx=(0, 12))
        self._rows[id(item)] = (item, choice)

    def _apply(self):
        self.decisions = [(item, choice.get()) for item, choice in self._rows.values()
                          if choice.get() != IGNORE]
        self.destroy()


def ask(master, items):
    dialog = RecoveryDialog(master, items)
    master.wait_window(dialog)
    return dialog.decisions
