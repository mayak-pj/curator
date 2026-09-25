"""Светлая тема окна (ARCHITECTURE.md, раздел 18)."""

import sys
from tkinter import ttk

BACKGROUND = "#f7f7f5"
SURFACE = "#ffffff"
BORDER = "#d8dadd"
TEXT = "#1f2328"
MUTED = "#57606a"

COLOURS = {
    "success": "#1a7f37",
    "error": "#b42318",
    "warning": "#9a6700",
    "neutral": MUTED,
    "running": "#0969da",
}

FONT = ("Segoe UI", 9) if sys.platform == "win32" else ("Helvetica", 12)
FONT_BOLD = FONT + ("bold",)
FONT_MONO = ("Consolas", 9) if sys.platform == "win32" else ("Menlo", 11)


def apply(root):
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    elif "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(background=BACKGROUND)
    style.configure(".", background=BACKGROUND, foreground=TEXT, font=FONT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("Surface.TFrame", background=SURFACE)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("Surface.TLabel", background=SURFACE)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Heading.TLabel", font=FONT_BOLD)
    style.configure("TButton", padding=(10, 4))
    style.configure("TLabelframe", background=BACKGROUND)
    style.configure("TLabelframe.Label", background=BACKGROUND, foreground=MUTED)
    for key, colour in COLOURS.items():
        style.configure("{}.Surface.TLabel".format(key), background=SURFACE, foreground=colour)
    return style


def colour(key):
    return COLOURS.get(key, TEXT)
