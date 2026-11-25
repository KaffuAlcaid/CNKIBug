from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size
from ttkbootstrap.widgets import ToolTip

from ..core.search_query import (
    MATCH_MODES,
    PUBLICATION_FILTERS,
    SEARCH_FIELDS,
    AdvancedQuery,
    ProfessionalQuery,
    SearchCondition,
)
from . import dialogs as messagebox
from .appearance import (
    Card,
    Divider,
    ScrollView,
    TextView,
    configure_window_surface,
    place_toplevel,
)


def _place_dialog(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    place_toplevel(window, parent, width, height, min_width=min(640, width), min_height=min(420, height))
    window.transient(parent)


@dataclass
class _ConditionRow:
    frame: ttk.Frame
    operator: tk.StringVar
    field: tk.StringVar
    text: tk.StringVar
    match: tk.StringVar
    operator_box: ttk.Combobox
    first_label: ttk.Label
    match_box: ttk.Combobox
    delete_button: ttk.Button


class AdvancedSearchDialog:
    def __init__(self, parent: tk.Misc, query: AdvancedQuery | None = None) -> None:
        self.result: AdvancedQuery | None = None
        self.window = ttk.Toplevel(master=parent)
        self.window.withdraw()
        self.window.title("高级检索（实验性）")
        self.window._cnkibug_surface = "canvas"
        configure_window_surface(self.window, "canvas")
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._rows: list[_ConditionRow] = []

        # Flush Header
        header = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (20, 14)))
        header.pack(fill=tk.X)
        ttk.Label(header, text="高级检索", style="CardHeading.TLabel").pack(side=tk.LEFT)
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
        ttk.Button(footer, text="重置条件", command=self._reset, style="GhostMuted.TButton").pack(side=tk.LEFT)
        ttk.Button(
            footer, text="保存条件" if query else "添加到任务", command=self._accept,
            style="Primary.TButton", width=14,
        ).pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self.window.destroy, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        # Scrollable Content on Canvas
        outer = ttk.Frame(self.window, style="Canvas.TFrame", padding=scale_size(self.window, (20, 14)))
        outer.pack(fill=tk.BOTH, expand=True)
        card = Card(outer)
        card.pack(fill=tk.BOTH, expand=True)
        content = self._content = ScrollView(
            card.body, surface="card", height=1, width=1, padding=scale_size(card.body, (16, 14, 16, 14)),
        )
        content.pack(fill=tk.BOTH, expand=True)
        content.disable_scrolling()
        for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind(event_name, self._scroll)

        ttk.Label(content, text="检索条件", style="CardMuted.TLabel").pack(
            anchor=tk.W, pady=(0, scale_size(self.window, 8)),
        )
        self._rows_frame = ttk.Frame(content, style="Card.TFrame")
        self._rows_frame.pack(fill=tk.X)
        row_actions = ttk.Frame(content, style="Card.TFrame")
        row_actions.pack(fill=tk.X, pady=(scale_size(self.window, 2), scale_size(self.window, 14)))
        self._add_button = ttk.Button(
            row_actions, text="+ 添加条件", command=self._add_row, style="SoftCompact.TButton",
        )
        self._add_button.pack(side=tk.LEFT)
        ToolTip(self._add_button, text="最多支持 10 条检索条件")

        Divider(content).pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))

        ttk.Label(content, text="限定与扩展", style="CardMuted.TLabel").pack(
            anchor=tk.W, pady=(0, scale_size(self.window, 6)),
        )
        flags = ttk.Frame(content, style="Card.TFrame")
        flags.pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))
        self._publications = {}
        for column, (key, (label, _value)) in enumerate(PUBLICATION_FILTERS.items()):
            variable = tk.BooleanVar(value=False)
            self._publications[key] = variable
            ttk.Checkbutton(
                flags, text=label, variable=variable, style="Card.TCheckbutton",
            ).grid(row=0, column=column, sticky=tk.W, padx=(0, scale_size(self.window, 18)), pady=scale_size(self.window, 5))
        self._bilingual = tk.BooleanVar(value=True)
        self._synonym = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            flags, text="中英文扩展", variable=self._bilingual, style="Card.TCheckbutton",
            command=lambda: self._synonym.set(False) if self._bilingual.get() else None,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=scale_size(self.window, 5))
        ttk.Checkbutton(
            flags, text="同义词扩展", variable=self._synonym, style="Card.TCheckbutton",
            command=lambda: self._bilingual.set(False) if self._synonym.get() else None,
        ).grid(row=1, column=2, columnspan=2, sticky=tk.W, pady=scale_size(self.window, 5))

        Divider(content).pack(fill=tk.X, pady=(0, scale_size(self.window, 12)))

        dates = ttk.Frame(content, style="Card.TFrame")
        dates.pack(fill=tk.X, pady=(0, scale_size(self.window, 4)))
        ttk.Label(dates, text="发表时间", style="CardMuted.TLabel").grid(
            row=0, column=0, padx=(0, scale_size(self.window, 14)),
        )
        self._date_from = ttk.DateEntry(
            dates, dateformat="%Y-%m-%d", width=12, popup_title="开始日期", bootstyle="secondary",
        )
        self._date_from.grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(dates, text="至", style="CardMuted.TLabel").grid(
            row=0, column=2, padx=scale_size(self.window, 10),
        )
        self._date_to = ttk.DateEntry(
            dates, dateformat="%Y-%m-%d", width=12, popup_title="结束日期", bootstyle="secondary",
        )
        self._date_to.grid(row=0, column=3, sticky=tk.EW)
        dates.columnconfigure(1, weight=1)
        dates.columnconfigure(3, weight=1)
        for picker in (self._date_from, self._date_to):
            picker.bind("<<DateEntrySelected>>", lambda _event: self.window.grab_set())

        self._reset()
        if query:
            self._clear_rows()
            for condition in query.conditions:
                self._add_row(condition)
            self._bilingual.set(query.bilingual)
            self._synonym.set(query.synonym)
            for key, variable in self._publications.items():
                variable.set(key in query.publications)
            self._date_from.entry.insert(0, query.date_from)
            self._date_to.entry.insert(0, query.date_to)
        _place_dialog(self.window, parent, 920, 560)
        self.window.deiconify()
        self.window.grab_set()

    def show(self) -> AdvancedQuery | None:
        self.window.wait_window()
        return self.result

    def _scroll(self, event: tk.Event) -> None:
        if isinstance(event.widget, ttk.Combobox):
            return
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            units = -3 if getattr(event, "delta", 0) > 0 else 3
        self._content.yview_scroll(units, "units")

    def _add_row(self, condition: SearchCondition | None = None) -> None:
        if len(self._rows) >= 10:
            return
        condition = condition or SearchCondition()
        frame = ttk.Frame(self._rows_frame, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=(0, scale_size(self.window, 8)), padx=(0, scale_size(self.window, 4)))
        frame.columnconfigure(2, weight=1)
        operator = tk.StringVar(value=condition.operator)
        field = tk.StringVar(value=SEARCH_FIELDS[condition.field])
        text = tk.StringVar(value=condition.text)
        match = tk.StringVar(value=MATCH_MODES[condition.match])
        operator_box = ttk.Combobox(frame, textvariable=operator, values=("AND", "OR", "NOT"), state="readonly", width=6)
        operator_box.grid(row=0, column=0, padx=(0, scale_size(self.window, 8)))
        first_label = ttk.Label(frame, text="条件 1", style="BadgeNeutral.TLabel", width=6)
        first_label.grid(row=0, column=0, padx=(0, scale_size(self.window, 8)), sticky="ew")
        field_box = ttk.Combobox(frame, textvariable=field, values=list(SEARCH_FIELDS.values()), state="readonly", width=10)
        field_box.grid(row=0, column=1, sticky=tk.EW)
        entry = ttk.Entry(frame, textvariable=text)
        entry.grid(row=0, column=2, sticky=tk.EW, padx=scale_size(self.window, 6))
        match_box = ttk.Combobox(frame, textvariable=match, values=list(MATCH_MODES.values()), state="readonly", width=6)
        match_box.grid(row=0, column=3)
        delete = ttk.Button(frame, text="×", width=2, style="DangerGhost.TButton")
        delete.grid(row=0, column=4, padx=(scale_size(self.window, 6), 0))
        row = _ConditionRow(frame, operator, field, text, match, operator_box, first_label, match_box, delete)
        delete.configure(command=lambda: self._delete_row(row))
        ToolTip(delete, text="删除条件")
        field_box.bind("<<ComboboxSelected>>", lambda _event: self._field_changed(row))
        if condition.field == "SU":
            match_box.configure(state="disabled")
        self._rows.append(row)
        self._sync_rows()

    def _field_changed(self, row: _ConditionRow) -> None:
        row.match.set("模糊" if row.field.get() == "作者单位" else "精确")
        row.match_box.configure(state="disabled" if row.field.get() == "主题" else "readonly")

    def _delete_row(self, row: _ConditionRow) -> None:
        if len(self._rows) == 1:
            return
        row.frame.destroy()
        self._rows.remove(row)
        self._sync_rows()

    def _sync_rows(self) -> None:
        for index, row in enumerate(self._rows):
            row.frame.columnconfigure(0, minsize=row.operator_box.winfo_reqwidth() + scale_size(self.window, 8))
            if index == 0:
                row.operator.set("AND")
                row.operator_box.grid_remove()
                row.first_label.grid()
            else:
                row.first_label.grid_remove()
                row.operator_box.grid()
            row.delete_button.configure(state="disabled" if len(self._rows) == 1 else "normal")
        self._add_button.configure(state="disabled" if len(self._rows) >= 10 else "normal")

    def _clear_rows(self) -> None:
        for row in self._rows:
            row.frame.destroy()
        self._rows.clear()

    def _reset(self) -> None:
        self._clear_rows()
        for field in ("SU", "AU", "LY"):
            self._add_row(SearchCondition(field=field))
        for variable in self._publications.values():
            variable.set(False)
        self._bilingual.set(True)
        self._synonym.set(False)
        self._date_from.entry.delete(0, tk.END)
        self._date_to.entry.delete(0, tk.END)
        self._content.yview_moveto(0)

    def _accept(self) -> None:
        fields = {label: key for key, label in SEARCH_FIELDS.items()}
        matches = {label: key for key, label in MATCH_MODES.items()}
        conditions = []
        for row in self._rows:
            if row.text.get().strip():
                conditions.append(SearchCondition(
                    field=fields[row.field.get()], text=row.text.get().strip(),
                    operator=row.operator.get() if conditions else "AND", match=matches[row.match.get()],
                ))
        try:
            self.result = AdvancedQuery(
                conditions=tuple(conditions),
                date_from=self._date_from.entry.get().strip(),
                date_to=self._date_to.entry.get().strip(),
                bilingual=self._bilingual.get(), synonym=self._synonym.get(),
                publications=tuple(key for key, variable in self._publications.items() if variable.get()),
            )
        except ValueError as error:
            messagebox.showerror("检索条件无效", str(error), parent=self.window)
            return
        self.window.destroy()


def confirm_advanced_task(parent: tk.Misc, summary: str, queries: dict[str, AdvancedQuery | ProfessionalQuery]) -> bool:
    accepted = False
    window = ttk.Toplevel(master=parent)
    window.withdraw()
    window.title("开始前确认")
    window._cnkibug_surface = "card"
    configure_window_surface(window, "card")

    footer_wrap = ttk.Frame(window, style="Card.TFrame")
    footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
    Divider(footer_wrap).pack(fill=tk.X)
    footer = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(window, (20, 12)))
    footer.pack(fill=tk.X)

    def accept() -> None:
        nonlocal accepted
        accepted = True
        window.destroy()

    ttk.Button(footer, text="开始抓取", command=accept, style="Primary.TButton", width=11).pack(side=tk.RIGHT)
    ttk.Button(footer, text="返回", command=window.destroy, style="Soft.TButton", width=9).pack(
        side=tk.RIGHT, padx=(0, scale_size(window, 8)),
    )

    outer = ttk.Frame(window, style="Card.TFrame", padding=scale_size(window, (20, 16)))
    outer.pack(fill=tk.BOTH, expand=True)
    ttk.Label(outer, text="请确认任务内容", style="CardHeading.TLabel").pack(
        anchor=tk.W, pady=(0, scale_size(window, 12)),
    )
    text_view = TextView(outer)
    text_view.pack(fill=tk.BOTH, expand=True)
    text = text_view.text
    text.insert(tk.END, summary)
    for name, query in queries.items():
        text.insert(tk.END, f"\n\n{name}\n{query.summary()}")
    text.configure(state=tk.DISABLED)
    window.bind("<Escape>", lambda _event: window.destroy())
    _place_dialog(window, parent, 780, 580)
    window.deiconify()
    window.grab_set()
    window.wait_window()
    return accepted
