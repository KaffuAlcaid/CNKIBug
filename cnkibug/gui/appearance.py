from __future__ import annotations

import tkinter as tk
from tkinter import font

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size
from ttkbootstrap.widgets.scrolled import ScrolledText


def apply_appearance(root: tk.Misc) -> None:
    style = ttk.Style()
    default = font.nametofont("TkDefaultFont", root=root)
    fonts = getattr(root, "_cnkibug_fonts", None)
    if fonts is None:
        fonts = root._cnkibug_fonts = {}
        for role, size in (("Title", 16), ("Heading", 12), ("Section", 11)):
            fonts[role] = font.Font(root=root, **default.actual())
            fonts[role].configure(size=size, weight="bold")
    for role, heading in fonts.items():
        style.configure(f"{role}.TLabel", font=heading)
    style.configure("TButton", padding=scale_size(root, (10, 5)))
    style.configure("TNotebook.Tab", padding=scale_size(root, (12, 7)))
    row_height = max(default.metrics("linespace") + scale_size(root, 12), scale_size(root, 32))
    style.configure("Results.Treeview", rowheight=row_height, borderwidth=0)
    style.configure("Results.Treeview.Heading", padding=scale_size(root, (8, 8)))
    style.configure("Settings.Treeview", rowheight=row_height + scale_size(root, 4), borderwidth=0)


class TextView(ScrolledText):
    def __init__(self, master: tk.Misc, *, height: int = 7, **kwargs) -> None:
        kwargs.setdefault("font", "TkDefaultFont")
        kwargs.setdefault("wrap", tk.WORD)
        kwargs.setdefault("relief", tk.FLAT)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, height=height, padding=1, bootstyle="secondary", **kwargs)
        self.text.configure(
            padx=scale_size(master, 10), pady=scale_size(master, 8),
            spacing1=scale_size(master, 2), spacing3=scale_size(master, 3),
        )
        self.bind("<<ThemeChanged>>", self._apply_theme, add="+")
        self._apply_theme()

    def _apply_theme(self, _event: tk.Event | None = None) -> None:
        colors = ttk.Style().colors
        self.text.configure(
            background=colors.inputbg, foreground=colors.inputfg,
            insertbackground=colors.inputfg, selectbackground=colors.selectbg,
            selectforeground=colors.selectfg, inactiveselectbackground=colors.selectbg,
        )
