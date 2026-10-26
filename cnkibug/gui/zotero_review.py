from __future__ import annotations

import tkinter as tk
from threading import Event

import ttkbootstrap as ttk

from ..fileio.zotero_library import ZoteroReviewRow
from .appearance import TextView


def _window(parent: tk.Misc, title: str, width: int, height: int) -> ttk.Toplevel:
    window = ttk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    width = min(width, max(320, parent.winfo_screenwidth() - 80))
    height = min(height, max(280, parent.winfo_screenheight() - 100))
    x = max(0, min(parent.winfo_rootx() + (parent.winfo_width() - width) // 2, window.winfo_screenwidth() - width))
    y = max(0, min(parent.winfo_rooty() + (parent.winfo_height() - height) // 2, window.winfo_screenheight() - height))
    window.geometry(f"{width}x{height}+{x}+{y}")
    window.minsize(min(width, 660), min(height, 440))
    window.transient(parent)
    return window


class ZoteroReviewDialog:
    def __init__(self, parent: tk.Misc, destination: str, rows: list[ZoteroReviewRow], cancel: Event):
        self.result: list[int] | None = None
        self.rows = {row.index: row for row in rows}
        self.checked = {row.index for row in rows if row.status == "new"}
        self.cancel = cancel
        self._poll_id = None
        self.window = _window(parent, "发送到 Zotero", 980, 680)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        body = ttk.Frame(self.window, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="发送到 Zotero", style="Heading.TLabel").pack(anchor=tk.W)
        target = ttk.Label(body, text=f"目标分类：{destination}", wraplength=850)
        target.pack(fill=tk.X, pady=(8, 14))
        target.bind("<Configure>", lambda event: target.configure(wraplength=max(240, event.width - 10)))

        footer = ttk.Frame(body)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))
        self.summary = tk.StringVar(self.window)
        ttk.Label(footer, textvariable=self.summary).pack(side=tk.LEFT)
        self.send_button = ttk.Button(footer, command=self._accept, bootstyle="primary")
        self.send_button.pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self._close, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=10)

        comparison = ttk.Frame(body)
        comparison.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        comparison.columnconfigure((0, 1), weight=1, uniform="comparison")
        self.comparison = []
        for column, title in enumerate(("本次文献", "匹配条目")):
            panel = ttk.Frame(comparison)
            panel.grid(row=0, column=column, sticky="nsew", padx=(0, 12) if column == 0 else 0)
            ttk.Label(panel, text=title, style="Section.TLabel").pack(anchor=tk.W, pady=(0, 5))
            view = TextView(panel, height=5, state=tk.DISABLED)
            view.pack(fill=tk.BOTH, expand=True)
            self.comparison.append(view.text)
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.tables: dict[str, ttk.Treeview] = {}
        for key, label in (("all", "全部"), ("new", "新增"), ("duplicate", "重复"), ("review", "待确认")):
            page = ttk.Frame(self.notebook, padding=(0, 6, 0, 0))
            count = sum(key == "all" or row.status == key for row in rows)
            self.notebook.add(page, text=f"{label} {count}")
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            table = ttk.Treeview(page, columns=("checked", "title", "status", "reason"), show="headings", selectmode="browse")
            for name, caption, width in (
                ("checked", "发送", 48), ("title", "文献", 430),
                ("status", "结果", 80), ("reason", "匹配依据", 240),
            ):
                table.heading(name, text=caption)
                table.column(name, width=width, minwidth=40 if name == "checked" else 80, stretch=name == "title")
            table.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(page, orient=tk.VERTICAL, command=table.yview)
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(page, orient=tk.HORIZONTAL, command=table.xview)
            horizontal.grid(row=1, column=0, sticky="ew")
            table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            table.bind("<Button-1>", lambda event, view=table: self._click(event, view))
            table.bind("<space>", lambda event, view=table: self._space(view))
            table.bind("<<TreeviewSelect>>", lambda event, view=table: self._show_selection(view))
            self.tables[key] = table
            for row in rows:
                if key == "all" or row.status == key:
                    table.insert("", tk.END, iid=str(row.index), values=self._values(row))
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._update_summary()
        self.window.deiconify()
        self.window.grab_set()
        self._poll_id = self.window.after(100, self._check_cancel)

    def _values(self, row: ZoteroReviewRow) -> tuple[str, ...]:
        checked = "—" if row.status == "duplicate" else "☑" if row.index in self.checked else "☐"
        status = {"new": "可新增", "duplicate": "已存在", "review": "待确认"}[row.status]
        reason = "将作为新条目发送" if row.status == "review" and row.index in self.checked else row.reason
        return checked, row.paper.title, status, reason

    def _toggle(self, index: int) -> None:
        row = self.rows[index]
        if row.status == "duplicate":
            return
        if index in self.checked:
            self.checked.remove(index)
        else:
            self.checked.add(index)
        for table in self.tables.values():
            if table.exists(str(index)):
                table.item(str(index), values=self._values(row))
        self._update_summary()

    def _click(self, event, table) -> None:
        item = table.identify_row(event.y)
        if item and table.identify_column(event.x) == "#1":
            self._toggle(int(item))

    def _space(self, table) -> str:
        selected = table.selection()
        if selected:
            self._toggle(int(selected[0]))
        return "break"

    def _show_selection(self, table) -> None:
        selected = table.selection()
        if not selected:
            return
        row = self.rows[int(selected[0])]
        for text, paper in zip(self.comparison, (row.paper, row.existing)):
            value = "未发现匹配条目" if paper is None else "\n".join((
                paper.title, f"{paper.authors or '作者未收录'} · {paper.publication_date or '年份未收录'}",
                f"{paper.source or '来源未收录'} · {paper.document_type or '类型未收录'}",
                f"DOI：{paper.doi or '未收录'}",
            ))
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            text.insert("1.0", value)
            text.configure(state=tk.DISABLED)

    def _tab_changed(self, _event=None) -> None:
        table = list(self.tables.values())[self.notebook.index(self.notebook.select())]
        if table.get_children() and not table.selection():
            table.selection_set(table.get_children()[0])
        self._show_selection(table)

    def _update_summary(self) -> None:
        count = len(self.checked)
        self.summary.set(f"准备发送 {count} 篇，跳过 {len(self.rows) - count} 篇")
        all_duplicates = all(row.status == "duplicate" for row in self.rows.values())
        self.send_button.configure(
            text="完成" if all_duplicates else f"发送 {count} 篇",
            state=tk.NORMAL if count or all_duplicates else tk.DISABLED,
        )

    def _accept(self) -> None:
        if not self.cancel.is_set():
            self.result = sorted(self.checked)
        self._close()

    def _check_cancel(self) -> None:
        self._poll_id = None
        if self.cancel.is_set():
            self._close()
        else:
            self._poll_id = self.window.after(100, self._check_cancel)

    def _close(self) -> None:
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()

    def show(self) -> list[int] | None:
        self.window.wait_window()
        return self.result


class ZoteroLibraryDialog:
    def __init__(self, parent: tk.Misc, destination: str, libraries: list[dict], cancel: Event):
        self.result: str | None = None
        self.cancel = cancel
        self._poll_id = None
        self.window = _window(parent, "确认 Zotero 查重文库", 680, 280)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"当前发送目标：{destination}", wraplength=600).pack(anchor=tk.W)
        ttk.Label(body, text="请选择对应的群组文库用于查重。").pack(anchor=tk.W, pady=(12, 8))
        self.libraries = libraries
        self.choice = ttk.Combobox(body, state="readonly", values=[f"{item['name']}（群组 {item['id']}）" for item in libraries])
        self.choice.pack(fill=tk.X)
        buttons = ttk.Frame(body)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(18, 0))
        self.confirm = ttk.Button(buttons, text="确认文库", command=self._accept, state=tk.DISABLED, bootstyle="primary")
        self.confirm.pack(side=tk.RIGHT)
        self.choice.bind("<<ComboboxSelected>>", lambda _event: self.confirm.configure(state=tk.NORMAL))
        ttk.Button(buttons, text="取消", command=self._close, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=10)
        self.window.deiconify()
        self.window.grab_set()
        self._poll_id = self.window.after(100, self._check_cancel)

    def _accept(self) -> None:
        index = self.choice.current()
        if index >= 0 and not self.cancel.is_set():
            self.result = self.libraries[index]["id"]
        self._close()

    def _check_cancel(self) -> None:
        self._poll_id = None
        if self.cancel.is_set():
            self._close()
        else:
            self._poll_id = self.window.after(100, self._check_cancel)

    def _close(self) -> None:
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
        self.window.destroy()

    def show(self) -> str | None:
        self.window.wait_window()
        return self.result
