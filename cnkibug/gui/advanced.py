from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip

from ..core.search_query import (
    AdvancedQuery, MATCH_MODES, PUBLICATION_FILTERS, SEARCH_FIELDS, SearchCondition,
)


def _place_dialog(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    width = min(width, max(1, parent.winfo_screenwidth() - 80))
    height = min(height, max(1, parent.winfo_screenheight() - 80))
    x = max(0, min(parent.winfo_rootx() + (parent.winfo_width() - width) // 2,
                   parent.winfo_screenwidth() - width))
    y = max(0, min(parent.winfo_rooty() + (parent.winfo_height() - height) // 2,
                   parent.winfo_screenheight() - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(680, width), min(450, height))
    window.transient(parent)


@dataclass
class _ConditionRow:
    frame: ttk.Frame
    operator: tk.StringVar
    field: tk.StringVar
    text: tk.StringVar
    match: tk.StringVar
    operator_box: ttk.Combobox
    match_box: ttk.Combobox
    delete_button: ttk.Button


class AdvancedSearchDialog:
    def __init__(self, parent: tk.Misc, query: AdvancedQuery | None = None) -> None:
        self.result: AdvancedQuery | None = None
        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title("高级检索（实验性）")
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        self._rows: list[_ConditionRow] = []
        outer = ttk.Frame(self.window, padding=20)
        outer.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 18))
        ttk.Label(header, text="高级检索", font=("TkDefaultFont", 16, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="实验性", bootstyle="warning").pack(side=tk.RIGHT)

        footer = ttk.Frame(outer)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
        ttk.Button(footer, text="重置条件", command=self._reset, bootstyle="secondary-link").pack(side=tk.LEFT)
        ttk.Button(
            footer, text="保存条件" if query else "添加到任务", command=self._accept,
            bootstyle="primary", width=14,
        ).pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self.window.destroy, bootstyle="secondary-outline").pack(
            side=tk.RIGHT, padx=(0, 10),
        )
        ttk.Separator(outer).pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0,
                                 background=ttk.Style().colors.bg, yscrollincrement=20)
        scrollbar = ttk.Scrollbar(body, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        content = ttk.Frame(self._canvas)
        content_id = self._canvas.create_window((0, 0), window=content, anchor=tk.NW)
        content.bind("<Configure>", lambda _event: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda event: self._canvas.itemconfigure(content_id, width=event.width))
        for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.window.bind(event_name, self._scroll)

        self._rows_frame = ttk.Frame(content)
        self._rows_frame.pack(fill=tk.X)
        row_actions = ttk.Frame(content)
        row_actions.pack(fill=tk.X, pady=(0, 14))
        self._add_button = ttk.Button(row_actions, text="+", width=3, command=self._add_row,
                                      bootstyle="secondary-outline")
        self._add_button.pack(side=tk.RIGHT, padx=(0, 4))
        ToolTip(self._add_button, text="添加条件")

        flags = ttk.Frame(content)
        flags.pack(fill=tk.X, pady=(4, 8))
        self._publications = {}
        for column, (key, (label, _value)) in enumerate(PUBLICATION_FILTERS.items()):
            variable = tk.BooleanVar(value=False)
            self._publications[key] = variable
            ttk.Checkbutton(flags, text=label, variable=variable).grid(row=0, column=column, sticky=tk.W, padx=(0, 16), pady=6)
        self._bilingual = tk.BooleanVar(value=True)
        self._synonym = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            flags, text="中英文扩展", variable=self._bilingual,
            command=lambda: self._synonym.set(False) if self._bilingual.get() else None,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=6)
        ttk.Checkbutton(
            flags, text="同义词扩展", variable=self._synonym,
            command=lambda: self._bilingual.set(False) if self._synonym.get() else None,
        ).grid(row=1, column=2, columnspan=2, sticky=tk.W, pady=6)

        dates = ttk.Frame(content)
        dates.pack(fill=tk.X, pady=(14, 6))
        ttk.Label(dates, text="发表时间").grid(row=0, column=0, padx=(0, 14))
        self._date_from = ttk.DateEntry(dates, dateformat="%Y-%m-%d", width=12, popup_title="开始日期")
        self._date_from.grid(row=0, column=1, sticky=tk.EW)
        ttk.Label(dates, text="至").grid(row=0, column=2, padx=10)
        self._date_to = ttk.DateEntry(dates, dateformat="%Y-%m-%d", width=12, popup_title="结束日期")
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
        _place_dialog(self.window, parent, 980, 570)
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
        self._canvas.yview_scroll(units, "units")

    def _add_row(self, condition: SearchCondition | None = None) -> None:
        if len(self._rows) >= 10:
            return
        condition = condition or SearchCondition()
        frame = ttk.Frame(self._rows_frame)
        frame.pack(fill=tk.X, pady=(0, 12), padx=(0, 4))
        frame.columnconfigure(2, weight=1)
        operator = tk.StringVar(value=condition.operator)
        field = tk.StringVar(value=SEARCH_FIELDS[condition.field])
        text = tk.StringVar(value=condition.text)
        match = tk.StringVar(value=MATCH_MODES[condition.match])
        operator_box = ttk.Combobox(frame, textvariable=operator, values=("AND", "OR", "NOT"), state="readonly", width=6)
        operator_box.grid(row=0, column=0, padx=(0, 8))
        field_box = ttk.Combobox(frame, textvariable=field, values=list(SEARCH_FIELDS.values()), state="readonly", width=10)
        field_box.grid(row=0, column=1, sticky=tk.EW)
        entry = ttk.Entry(frame, textvariable=text)
        entry.grid(row=0, column=2, sticky=tk.EW, padx=6)
        match_box = ttk.Combobox(frame, textvariable=match, values=list(MATCH_MODES.values()), state="readonly", width=6)
        match_box.grid(row=0, column=3)
        delete = ttk.Button(frame, text="-", width=3, bootstyle="secondary-outline")
        delete.grid(row=0, column=4, padx=(8, 0))
        row = _ConditionRow(frame, operator, field, text, match, operator_box, match_box, delete)
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
            row.frame.columnconfigure(0, minsize=row.operator_box.winfo_reqwidth() + 8)
            if index == 0:
                row.operator.set("AND")
                row.operator_box.grid_remove()
            else:
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
        self._canvas.yview_moveto(0)

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


def confirm_advanced_task(parent: tk.Misc, summary: str, queries: dict[str, AdvancedQuery]) -> bool:
    accepted = False
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title("开始前确认")
    outer = ttk.Frame(window, padding=18)
    outer.pack(fill=tk.BOTH, expand=True)
    footer = ttk.Frame(outer)
    footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

    def accept() -> None:
        nonlocal accepted
        accepted = True
        window.destroy()

    ttk.Button(footer, text="开始抓取", command=accept, bootstyle="primary").pack(side=tk.RIGHT)
    ttk.Button(footer, text="返回", command=window.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=10)
    colors = ttk.Style().colors
    text = ScrolledText(
        outer, wrap=tk.WORD, font="TkDefaultFont", padx=12, pady=12,
        background=colors.inputbg, foreground=colors.inputfg, insertbackground=colors.inputfg,
        selectbackground=colors.selectbg, selectforeground=colors.selectfg,
    )
    text.pack(fill=tk.BOTH, expand=True)
    text.insert(tk.END, summary)
    for name, query in queries.items():
        text.insert(tk.END, f"\n\n{name}\n{query.summary()}")
    text.configure(state=tk.DISABLED)
    window.bind("<Escape>", lambda _event: window.destroy())
    _place_dialog(window, parent, 800, 620)
    window.deiconify()
    window.grab_set()
    window.wait_window()
    return accepted
