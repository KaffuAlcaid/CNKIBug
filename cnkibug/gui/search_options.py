from __future__ import annotations

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..core.search_query import LANGUAGES, RESOURCE_TYPES, SORT_MODES, SearchOptions
from . import dialogs as messagebox
from .appearance import Card, Divider, configure_window_surface, place_toplevel


class SearchOptionsDialog:
    def __init__(self, parent, options: SearchOptions | None) -> None:
        self.result = options
        current = options or SearchOptions()
        window = self.window = ttk.Toplevel(parent)
        window.withdraw()
        window.title("检索范围与排序")
        window._cnkibug_surface = "card"
        configure_window_surface(window, "card")
        window.transient(parent)
        window.resizable(False, False)

        footer_wrap = ttk.Frame(window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        buttons = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(window, (20, 12)))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="保存", command=self._save, style="Primary.TButton", width=9).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=window.destroy, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(window, 8)),
        )

        body = ttk.Frame(window, style="Card.TFrame", padding=scale_size(window, (20, 16)))
        body.pack(fill=tk.BOTH, expand=True)
        self.enabled = tk.BooleanVar(window, value=options is not None)
        ttk.Checkbutton(
            body, text="按以下设置检索（不勾选时沿用知网默认设置）",
            variable=self.enabled, style="Card.TCheckbutton",
        ).pack(anchor=tk.W, pady=(0, scale_size(window, 12)))

        scope_card = Card(body, subtle=True, padding=scale_size(window, (14, 10)))
        scope_card.pack(fill=tk.X)
        ttk.Label(scope_card.body, text="检索范围", style="CardSubtleMuted.TLabel").pack(
            anchor=tk.W, pady=(0, scale_size(window, 6)),
        )
        grid_frame = ttk.Frame(scope_card.body, style="CardSubtle.TFrame")
        grid_frame.pack(fill=tk.X)
        self.resources = {}
        for index, name in enumerate(RESOURCE_TYPES):
            value = tk.BooleanVar(window, value=name in current.resources)
            self.resources[name] = value
            ttk.Checkbutton(
                grid_frame, text=name, variable=value, style="CardSubtle.TCheckbutton",
            ).grid(
                row=index // 4, column=index % 4, sticky="w",
                padx=(0, scale_size(window, 14)), pady=scale_size(window, 4),
            )

        self.sort = tk.StringVar(window, value=SORT_MODES[current.sort])
        self.language = tk.StringVar(window, value=LANGUAGES[current.language])
        self.page_size = tk.StringVar(window, value=str(current.page_size))
        for label, variable, choices in (
            ("排序方式", self.sort, list(SORT_MODES.values())),
            ("资源语种", self.language, list(LANGUAGES.values())),
            ("每页论文条数", self.page_size, ["10", "20", "50"]),
        ):
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill=tk.X, pady=(scale_size(window, 12), 0))
            ttk.Label(row, text=label, style="Card.TLabel", width=16).pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=22).pack(side=tk.RIGHT)

        window.bind("<Escape>", lambda _event: window.destroy())
        window.update_idletasks()
        place_toplevel(window, parent, window.winfo_reqwidth(), window.winfo_reqheight(), scaled=False)
        window.deiconify()
        window.grab_set()
        window.focus_set()
        parent.wait_window(window)

    def _save(self) -> None:
        try:
            self.result = SearchOptions(
                resources=tuple(name for name, value in self.resources.items() if value.get()),
                sort=next(key for key, value in SORT_MODES.items() if value == self.sort.get()),
                language=next(key for key, value in LANGUAGES.items() if value == self.language.get()),
                page_size=int(self.page_size.get()),
            ) if self.enabled.get() else None
        except ValueError as error:
            messagebox.showerror("检索设置", str(error), parent=self.window)
            return
        self.window.destroy()
