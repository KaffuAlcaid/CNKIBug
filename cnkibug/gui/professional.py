from __future__ import annotations

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..core.search_query import ProfessionalQuery, SearchOptions
from .advanced import _place_dialog
from .appearance import Divider, TextView, configure_window_surface


class ProfessionalSearchDialog:
    def __init__(
        self,
        parent: tk.Misc,
        name: str,
        query: ProfessionalQuery | None = None,
        options: SearchOptions | None = None,
    ):
        self.result: tuple[str, ProfessionalQuery] | None = None
        self.window = ttk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("专业检索（实验性）")
        self.window._cnkibug_surface = "card"
        configure_window_surface(self.window, "card")
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())

        # Flush Header
        header = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (20, 14)))
        header.pack(fill=tk.X)
        ttk.Label(header, text="专业检索", style="CardHeading.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="实验性", style="BadgeWarning.TLabel").pack(
            side=tk.LEFT, padx=(scale_size(self.window, 8), 0),
        )
        Divider(self.window).pack(fill=tk.X)

        # Flush Footer
        footer_wrap = ttk.Frame(self.window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        footer = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(self.window, (20, 12)))
        footer.pack(fill=tk.X)
        ttk.Button(
            footer, text="保存修改" if query else "添加到任务", command=self._accept,
            style="Primary.TButton", width=13,
        ).pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self.window.destroy, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        # Body
        body = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (20, 16)))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="检索项名称", style="CardMuted.TLabel").pack(
            anchor=tk.W, pady=(0, scale_size(self.window, 6)),
        )
        self.name = tk.StringVar(self.window, value=name)
        ttk.Entry(body, textvariable=self.name).pack(fill=tk.X)

        self.error = tk.StringVar(self.window)
        error_label = ttk.Label(body, textvariable=self.error, style="CardDanger.TLabel", wraplength=680)
        error_label.pack(side=tk.BOTTOM, fill=tk.X, pady=(scale_size(self.window, 6), 0))
        scope = ttk.Label(
            body,
            text=options.summary() if options else "检索范围与排序：沿用知网页面的设置",
            style="CardSmall.TLabel",
            wraplength=680,
        )
        scope.pack(side=tk.BOTTOM, fill=tk.X, pady=(scale_size(self.window, 10), 0))

        heading = ttk.Frame(body, style="Card.TFrame")
        heading.pack(fill=tk.X, pady=(scale_size(self.window, 16), scale_size(self.window, 6)))
        ttk.Label(heading, text="检索式", style="CardMuted.TLabel").pack(side=tk.LEFT)
        self.count = tk.StringVar(self.window, value="0 字符")
        ttk.Label(heading, textvariable=self.count, style="CardSmall.TLabel").pack(side=tk.RIGHT)

        view = TextView(body, height=10)
        view.pack(fill=tk.BOTH, expand=True)
        self.expression = view.text
        if query:
            self.expression.insert("1.0", query.expression)
        self.expression.bind("<<Modified>>", self._changed)
        self._changed()
        body.bind(
            "<Configure>",
            lambda event: (
                scope.configure(wraplength=max(240, event.width - 40)),
                error_label.configure(wraplength=max(240, event.width - 40)),
            ),
        )
        _place_dialog(self.window, parent, 760, 540)
        self.window.deiconify()
        self.window.grab_set()
        self.expression.focus_set()

    def _changed(self, _event=None) -> None:
        self.count.set(f"{len(self.expression.get('1.0', 'end-1c'))} 字符")
        self.expression.edit_modified(False)

    def _accept(self) -> None:
        name = self.name.get().strip()
        if not name:
            self.error.set("请输入检索项名称")
            return
        if "\n" in name or "\r" in name:
            self.error.set("检索项名称应为单行文字")
            return
        try:
            query = ProfessionalQuery(self.expression.get("1.0", "end-1c"))
        except ValueError as error:
            self.error.set(str(error))
            return
        self.result = name, query
        self.window.destroy()

    def show(self) -> tuple[str, ProfessionalQuery] | None:
        self.window.wait_window()
        return self.result
