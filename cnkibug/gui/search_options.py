from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import ttkbootstrap as ttk

from ..core.search_query import LANGUAGES, RESOURCE_TYPES, SORT_MODES, SearchOptions


class SearchOptionsDialog:
    def __init__(self, parent, options: SearchOptions | None) -> None:
        self.result = options
        current = options or SearchOptions()
        window = self.window = ttk.Toplevel(parent)
        window.title("检索设置")
        window.transient(parent)
        window.resizable(False, False)
        body = ttk.Frame(window, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        self.enabled = tk.BooleanVar(window, value=options is not None)
        ttk.Checkbutton(body, text="按以下设置检索", variable=self.enabled).pack(anchor=tk.W, pady=(0, 12))
        scope = ttk.Labelframe(body, text="检索范围", padding=10)
        scope.pack(fill=tk.X)
        self.resources = {}
        for index, name in enumerate(RESOURCE_TYPES):
            value = tk.BooleanVar(window, value=name in current.resources)
            self.resources[name] = value
            ttk.Checkbutton(scope, text=name, variable=value).grid(row=index // 4, column=index % 4, sticky="w", padx=8, pady=5)
        self.sort = tk.StringVar(window, value=SORT_MODES[current.sort])
        self.language = tk.StringVar(window, value=LANGUAGES[current.language])
        self.page_size = tk.StringVar(window, value=str(current.page_size))
        for label, variable, choices in (("排序方式", self.sort, list(SORT_MODES.values())), ("资源语种", self.language, list(LANGUAGES.values())), ("每页论文条数", self.page_size, ["10", "20", "50"])):
            row = ttk.Frame(body)
            row.pack(fill=tk.X, pady=(12, 0))
            ttk.Label(row, text=label, width=16).pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=variable, values=choices, state="readonly", width=22).pack(side=tk.RIGHT)
        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(18, 0))
        ttk.Button(buttons, text="保存", command=self._save).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=window.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=8)
        window.grab_set()
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
