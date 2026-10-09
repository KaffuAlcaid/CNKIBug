from __future__ import annotations

import tkinter as tk
from tkinter import font

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size
from ttkbootstrap.style import Colors
from ttkbootstrap.widgets.scrolled import ScrolledFrame, ScrolledText


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
    colors = style.colors
    light = style.theme.type == "light"
    muted = "#5c6570" if light else "#b8bec8"
    border = "#9da5b0" if light else "#767e89"
    hover = "#edf1f5" if light else "#34383e"
    disabled = Colors.make_transparent(0.30, colors.fg, colors.bg)
    style.configure("secondary.TLabel", foreground=muted)
    style.configure("secondary.Link.TButton", foreground=muted)
    style.map("secondary.Link.TButton", foreground=[
        ("disabled", disabled), ("pressed !disabled", colors.fg), ("hover !disabled", colors.fg),
    ])
    for name in ("secondary.Outline.TButton", "secondary.Outline.TMenubutton"):
        style.configure(name, foreground=colors.fg, bordercolor=border, focuscolor=colors.primary)
        style.map(
            name,
            foreground=[("disabled", disabled), ("!disabled", colors.fg)],
            background=[("pressed !disabled", hover), ("hover !disabled", hover)],
            bordercolor=[("disabled", disabled), ("focus !disabled", colors.primary),
                         ("pressed !disabled", colors.primary), ("hover !disabled", border)],
            focuscolor=[("!disabled", colors.primary)],
            darkcolor=[("pressed !disabled", hover), ("hover !disabled", hover)],
            lightcolor=[("pressed !disabled", hover), ("hover !disabled", hover)],
        )
    style.configure("secondary.Outline.TMenubutton", arrowcolor=colors.fg)
    style.map("secondary.Outline.TMenubutton", arrowcolor=[("disabled", disabled), ("!disabled", colors.fg)])


class ScrollView(ScrolledFrame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.vscroll.configure(bootstyle="secondary")
        self.bind("<Configure>", lambda _event: self.yview(), add="+")

    def yview_moveto(self, fraction: float) -> None:
        super().yview_moveto(fraction)
        first, last = self.vscroll.get()
        needed = first > 0 or last < 1
        if needed and not self.vscroll.winfo_manager():
            self.show_scrollbars()
        elif not needed and self.vscroll.winfo_manager():
            self.hide_scrollbars()


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
