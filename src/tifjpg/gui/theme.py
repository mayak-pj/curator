"""Светлая и тёмная темы окна (ARCHITECTURE.md, раздел 18)."""

import sys
from tkinter import ttk

LIGHT = {
    "background": "#f7f7f5",
    "surface": "#ffffff",
    "border": "#d8dadd",
    "text": "#1f2328",
    "muted": "#57606a",
    "colours": {
        "success": "#1a7f37",
        "error": "#b42318",
        "warning": "#9a6700",
        "neutral": "#57606a",
        "running": "#0969da",
    },
}

DARK = {
    "background": "#1e1f22",
    "surface": "#2b2d31",
    "border": "#45474e",
    "text": "#e3e5e8",
    "muted": "#9aa0a6",
    "colours": {
        "success": "#3fb950",
        "error": "#f85149",
        "warning": "#d29922",
        "neutral": "#9aa0a6",
        "running": "#58a6ff",
    },
}

FONT = ("Segoe UI", 9) if sys.platform == "win32" else ("Helvetica", 12)
FONT_BOLD = FONT + ("bold",)
FONT_MONO = ("Consolas", 9) if sys.platform == "win32" else ("Menlo", 11)

_active = LIGHT


def set_theme(dark):
    """Переключить палитру без обращения к tkinter — используется тестами."""
    global _active
    _active = DARK if dark else LIGHT


def is_dark():
    return _active is DARK


def apply(root, dark=False):
    """Применить палитру к реальному окну. Требует созданный root."""
    set_theme(dark)
    style = ttk.Style(root)
    # У "vista" много хрома рисуется нативно и не подчиняется configure() —
    # для тёмной темы это выглядело бы наполовину светлым, поэтому в тёмном
    # режиме используем "clam", который красит сам всё, включая кнопки.
    wanted = "clam" if dark else "vista"
    if wanted not in style.theme_names():
        wanted = "clam" if "clam" in style.theme_names() else style.theme_use()
    style.theme_use(wanted)

    p = _active
    root.configure(background=p["background"])
    style.configure(".", background=p["background"], foreground=p["text"], font=FONT)
    style.configure("TFrame", background=p["background"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("TLabel", background=p["background"], foreground=p["text"])
    style.configure("Surface.TLabel", background=p["surface"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Heading.TLabel", font=FONT_BOLD)
    style.configure("TButton", padding=(10, 4), background=p["surface"], foreground=p["text"])
    style.map("TButton", background=[("active", p["border"]), ("disabled", p["background"])])
    style.configure("TEntry", fieldbackground=p["surface"], foreground=p["text"])
    style.configure("TLabelframe", background=p["background"])
    style.configure("TLabelframe.Label", background=p["background"], foreground=p["muted"])
    style.configure("Horizontal.TProgressbar", background=p["colours"]["running"])
    for key, value in p["colours"].items():
        style.configure("{}.Surface.TLabel".format(key), background=p["surface"], foreground=value)
    return style


def colour(key):
    return _active["colours"].get(key, _active["text"])


def background():
    return _active["background"]


def surface():
    return _active["surface"]


def border():
    return _active["border"]


def text():
    return _active["text"]


def muted():
    return _active["muted"]
