from __future__ import annotations

import tkinter as tk
from threading import Event
from tkinter import font

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..fileio.zotero_library import ZoteroReviewRow
from .appearance import (
    Card,
    Divider,
    TextView,
    apply_zebra,
    configure_window_surface,
    get_checkbox_icons,
    get_palette,
    place_toplevel,
    style_treeview,
)


def _window(parent: tk.Misc, title: str, width: int, height: int, *, surface: str = "canvas") -> ttk.Toplevel:
    window = ttk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window._cnkibug_surface = surface
    configure_window_surface(window, surface)
    place_toplevel(window, parent, width, height, min_width=min(width, 660), min_height=min(height, 440))
    window.transient(parent)
    return window


class ZoteroReviewDialog:
    def __init__(self, parent: tk.Misc, destination: str, rows: list[ZoteroReviewRow], cancel: Event):
        self.result: list[int] | None = None
        self.rows = {row.index: row for row in rows}
        self.checked = {row.index for row in rows if row.status == "new"}
        self.cancel = cancel
        self._poll_id = None
        self.window = _window(parent, "发送到 Zotero", 980, 680, surface="canvas")
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())

        # Header
        header = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (18, 12)))
        header.pack(fill=tk.X)
        ttk.Label(header, text="发送到 Zotero", style="CardHeading.TLabel").pack(anchor=tk.W)
        target = ttk.Label(header, text=f"目标分类：{destination}", style="CardMuted.TLabel", wraplength=860)
        target.pack(fill=tk.X, pady=(scale_size(self.window, 4), 0))
        target.bind("<Configure>", lambda event: target.configure(wraplength=max(240, event.width - 10)))
        Divider(self.window).pack(fill=tk.X)

        # Flush Footer
        footer_wrap = ttk.Frame(self.window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        footer = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(self.window, (18, 12)))
        footer.pack(fill=tk.X)
        self.summary = tk.StringVar(self.window)
        ttk.Label(footer, textvariable=self.summary, style="CardMuted.TLabel").pack(side=tk.LEFT)
        self.send_button = ttk.Button(footer, command=self._accept, style="Primary.TButton", width=11)
        self.send_button.pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self._close, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        # Main Canvas Body
        body = ttk.Frame(self.window, style="Canvas.TFrame", padding=scale_size(self.window, (18, 12)))
        body.pack(fill=tk.BOTH, expand=True)

        # Side-by-side comparison cards
        comparison = ttk.Frame(body, style="Canvas.TFrame")
        comparison.pack(side=tk.BOTTOM, fill=tk.X, pady=(scale_size(self.window, 10), 0))
        comparison.columnconfigure((0, 1), weight=1, uniform="comparison")
        default_font = font.nametofont("TkDefaultFont", root=self.window)
        field_offset = default_font.measure("发表日期  ")
        muted_color = get_palette().muted
        self.comparison = []
        for column, title in enumerate(("本次文献", "匹配条目")):
            panel = Card(comparison)
            panel.grid(row=0, column=column, sticky="nsew", padx=(0, scale_size(self.window, 10)) if column == 0 else 0)
            phead = ttk.Frame(panel.body, style="CardSubtle.TFrame", padding=scale_size(self.window, (12, 7)))
            phead.pack(fill=tk.X)
            ttk.Label(phead, text=title, style="CardSubtleBold.TLabel").pack(side=tk.LEFT)
            Divider(panel.body).pack(fill=tk.X)
            view = TextView(panel.body, height=8, state=tk.DISABLED, bordered=False)
            view.pack(fill=tk.BOTH, expand=True)
            view.text.tag_configure(
                "paper-title", font="TkHeadingFont", spacing3=default_font.metrics("linespace") // 2,
            )
            view.text.tag_configure("field", tabs=(field_offset,), lmargin2=field_offset)
            view.text.tag_configure("field-label", foreground=muted_color)
            view.text.tag_raise(tk.SEL)
            self.comparison.append(view.text)

        # Top Notebook Card
        list_card = Card(body)
        list_card.pack(fill=tk.BOTH, expand=True)
        self._check_icons = get_checkbox_icons(self.window)
        self.notebook = ttk.Notebook(list_card.body)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.tables: dict[str, ttk.Treeview] = {}
        for key, label in (("all", "全部"), ("new", "新增"), ("duplicate", "重复"), ("review", "待确认")):
            page = ttk.Frame(self.notebook, style="Card.TFrame")
            count = sum(key == "all" or row.status == key for row in rows)
            self.notebook.add(page, text=f"{label} {count}")
            page.columnconfigure(0, weight=1)
            page.rowconfigure(0, weight=1)
            table = ttk.Treeview(
                page, columns=("title", "status", "reason"), show="tree headings", selectmode="browse",
            )
            style_treeview(table)
            table.heading("#0", text="发", anchor=tk.CENTER)
            table.column("#0", width=scale_size(self.window, 48), minwidth=scale_size(self.window, 44), stretch=False, anchor=tk.CENTER)
            for name, caption, width in (
                ("title", "文献", 420),
                ("status", "结果", 86),
                ("reason", "匹配依据", 260),
            ):
                table.heading(name, text=caption, anchor=tk.W)
                table.column(
                    name,
                    width=scale_size(self.window, width),
                    minwidth=scale_size(self.window, 80),
                    stretch=name == "title",
                    anchor=tk.W,
                )
            table.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(page, orient=tk.VERTICAL, command=table.yview, bootstyle="secondary")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal = ttk.Scrollbar(page, orient=tk.HORIZONTAL, command=table.xview, bootstyle="secondary")
            horizontal.grid(row=1, column=0, sticky="ew")
            table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            table.bind("<Button-1>", lambda event, view=table: self._click(event, view))
            table.bind("<space>", lambda event, view=table: self._space(view))
            table.bind("<<TreeviewSelect>>", lambda event, view=table: self._show_selection(view))
            self.tables[key] = table
            for row in rows:
                if key == "all" or row.status == key:
                    table.insert("", tk.END, iid=str(row.index), image=self._icon(row), values=self._values(row))
            apply_zebra(table)
        self.notebook.bind("<<NotebookTabChanged>>", self._tab_changed)
        self._update_summary()
        self.window.deiconify()
        self.window.grab_set()
        self._poll_id = self.window.after(100, self._check_cancel)

    def _icon(self, row: ZoteroReviewRow) -> tk.PhotoImage:
        if row.status == "duplicate":
            return self._check_icons["disabled"]
        return self._check_icons["checked" if row.index in self.checked else "unchecked"]

    def _values(self, row: ZoteroReviewRow) -> tuple[str, ...]:
        status = {"new": "可新增", "duplicate": "已存在", "review": "待确认"}[row.status]
        reason = "将作为新条目发送" if row.status == "review" and row.index in self.checked else row.reason
        return row.paper.title, status, reason

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
                table.item(str(index), image=self._icon(row), values=self._values(row))
        self._update_summary()

    def _click(self, event, table) -> str | None:
        item = table.identify_row(event.y)
        if item and table.identify_column(event.x) == "#0":
            self._toggle(int(item))
            return "break"
        return None

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
            text.configure(state=tk.NORMAL)
            text.delete("1.0", tk.END)
            if paper is None:
                text.insert(tk.END, "未发现匹配条目")
            else:
                text.insert(tk.END, f"{paper.title}\n", ("paper-title",))
                for label, value in (
                    ("作者", paper.authors), ("发表日期", paper.publication_date),
                    ("来源", paper.source), ("文献类型", paper.document_type), ("DOI", paper.doi),
                ):
                    text.insert(tk.END, f"{label}\t", ("field", "field-label"))
                    text.insert(tk.END, f"{value or '未收录'}\n", ("field",))
            text.configure(state=tk.DISABLED)

    def _tab_changed(self, _event=None) -> None:
        table = list(self.tables.values())[self.notebook.index(self.notebook.select())]
        if table.get_children() and not table.selection():
            table.selection_set(table.get_children()[0])
        self._show_selection(table)

    def _update_summary(self) -> None:
        count = len(self.checked)
        self.summary.set(f"准备发送 {count} 篇 · 跳过 {len(self.rows) - count} 篇")
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
        self.window = _window(parent, "确认 Zotero 查重文库", 640, 260, surface="card")
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())

        footer_wrap = ttk.Frame(self.window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        buttons = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(self.window, (20, 12)))
        buttons.pack(fill=tk.X)
        self.confirm = ttk.Button(
            buttons, text="确认文库", command=self._accept, state=tk.DISABLED, style="Primary.TButton", width=11,
        )
        self.confirm.pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=self._close, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        body = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (20, 16)))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"当前发送目标：{destination}", style="Card.TLabel", wraplength=580).pack(anchor=tk.W)
        ttk.Label(body, text="请选择对应的群组文库用于查重", style="CardMuted.TLabel").pack(
            anchor=tk.W, pady=(scale_size(self.window, 12), scale_size(self.window, 8)),
        )
        self.libraries = libraries
        self.choice = ttk.Combobox(
            body, state="readonly", values=[f"{item['name']}（群组 {item['id']}）" for item in libraries],
        )
        self.choice.pack(fill=tk.X)
        self.choice.bind("<<ComboboxSelected>>", lambda _event: self.confirm.configure(state=tk.NORMAL))

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
