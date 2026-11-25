from __future__ import annotations

import tkinter as tk
from pathlib import Path
from threading import Event
from tkinter import filedialog

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size

from ..fileio.pdf_links import PdfMatch, pdf_problem
from . import dialogs as messagebox
from .appearance import (
    Card,
    Divider,
    TextView,
    apply_zebra,
    configure_window_surface,
    get_checkbox_icons,
    place_toplevel,
    style_treeview,
)


def _window(parent, title: str, width: int, height: int, *, surface: str = "canvas", min_w: int = 600, min_h: int = 380):
    window = ttk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window._cnkibug_surface = surface
    configure_window_surface(window, surface)
    window.transient(parent)
    place_toplevel(window, parent, width, height, min_width=min(width, min_w), min_height=min(height, min_h))
    return window


class PdfFolderDialog:
    def __init__(self, parent, initial_directory: Path):
        self.result: tuple[Path, bool] | None = None
        self.window = _window(parent, "从文件夹批量关联 PDF", 640, 220, surface="card", min_w=520, min_h=200)
        self.window.resizable(True, False)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())

        footer_wrap = ttk.Frame(self.window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        buttons = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(self.window, (20, 12)))
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="扫描", command=self._accept, style="Primary.TButton", width=9).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=self.window.destroy, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        body = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (20, 16)))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="PDF 文件夹", style="CardMuted.TLabel").pack(anchor=tk.W)
        folder = ttk.Frame(body, style="Card.TFrame")
        folder.pack(fill=tk.X, pady=(scale_size(self.window, 8), scale_size(self.window, 12)))
        self.directory = tk.StringVar(self.window, value=str(initial_directory))
        ttk.Entry(folder, textvariable=self.directory).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder, text="浏览", command=self._browse, style="SoftCompact.TButton").pack(
            side=tk.RIGHT, padx=(scale_size(self.window, 8), 0),
        )
        self.recursive = tk.BooleanVar(self.window, value=False)
        ttk.Checkbutton(body, text="包含子文件夹", variable=self.recursive, style="Card.TCheckbutton").pack(anchor=tk.W)

    def _browse(self):
        path = filedialog.askdirectory(parent=self.window, initialdir=self.directory.get(), title="选择 PDF 文件夹")
        if path:
            self.directory.set(path)

    def _accept(self):
        value = self.directory.get().strip()
        try:
            path = Path(value).expanduser().resolve()
            if not value or not path.is_dir():
                raise ValueError("请选择存在的 PDF 文件夹")
        except (OSError, ValueError) as error:
            messagebox.showerror("文件夹不可用", str(error), parent=self.window)
            return
        self.result = path, self.recursive.get()
        self.window.destroy()

    def show(self):
        self.window.update_idletasks()
        self.window.deiconify()
        self.window.grab_set()
        self.window.wait_window()
        return self.result


class PdfLinksReviewDialog:
    def __init__(self, parent, directory: str, rows: list[PdfMatch], warnings: list[str], cancel: Event):
        self.result: dict[int, str] | None = None
        self.rows = {row.index: row for row in rows}
        self.paths = {row.index: row.candidates[0] for row in rows if len(row.candidates) == 1}
        self.checked = {row.index for row in rows if row.status in {"indexed", "unique"}}
        self.reserved = {path.resolve() for row in rows if row.status == "existing" for path in row.candidates}
        self.cancel = cancel
        self._poll_id = None
        self.window = _window(parent, "确认 PDF 关联", 1080, 720, surface="canvas")
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())

        # Header
        header = ttk.Frame(self.window, style="Card.TFrame", padding=scale_size(self.window, (18, 12)))
        header.pack(fill=tk.X)
        ttk.Label(header, text="确认 PDF 关联", style="CardHeading.TLabel").pack(anchor=tk.W)
        location = ttk.Label(header, text=directory, style="CardMuted.TLabel", wraplength=960)
        location.pack(fill=tk.X, pady=(scale_size(self.window, 4), 0))
        location.bind("<Configure>", lambda event: location.configure(wraplength=max(200, event.width)))
        Divider(self.window).pack(fill=tk.X)

        # Flush Footer
        footer_wrap = ttk.Frame(self.window, style="Card.TFrame")
        footer_wrap.pack(side=tk.BOTTOM, fill=tk.X)
        Divider(footer_wrap).pack(fill=tk.X)
        footer = ttk.Frame(footer_wrap, style="Card.TFrame", padding=scale_size(self.window, (18, 12)))
        footer.pack(fill=tk.X)
        self.summary = tk.StringVar(self.window)
        ttk.Label(footer, textvariable=self.summary, style="CardMuted.TLabel").pack(side=tk.LEFT)
        self.confirm = ttk.Button(footer, text="关联所选", command=self._accept, style="Primary.TButton", width=11)
        self.confirm.pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self._close, style="Soft.TButton", width=9).pack(
            side=tk.RIGHT, padx=(0, scale_size(self.window, 8)),
        )

        body = ttk.Frame(self.window, style="Canvas.TFrame", padding=scale_size(self.window, (18, 12)))
        body.pack(fill=tk.BOTH, expand=True)

        # Details Card (bottom)
        details_card = Card(body, padding=scale_size(self.window, (14, 12)))
        details_card.pack(side=tk.BOTTOM, fill=tk.X, pady=(scale_size(self.window, 10), 0))
        self.info = TextView(details_card.body, height=3, state=tk.DISABLED)
        self.info.pack(fill=tk.X, pady=(0, scale_size(self.window, 10)))
        files = ttk.Frame(details_card.body, style="Card.TFrame")
        files.pack(fill=tk.X)
        ttk.Label(files, text="PDF 文件", style="CardMuted.TLabel").pack(
            side=tk.LEFT, padx=(0, scale_size(self.window, 8)),
        )
        self.choice = ttk.Combobox(files, state="readonly")
        self.choice.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.choice.bind("<<ComboboxSelected>>", self._choose)
        self.browse = ttk.Button(files, text="选择文件", command=self._browse, style="SoftCompact.TButton")
        self.browse.pack(side=tk.RIGHT, padx=(scale_size(self.window, 8), 0))

        if warnings:
            warn_card = Card(body, subtle=True, padding=scale_size(self.window, (12, 8)))
            warn_card.pack(side=tk.BOTTOM, fill=tk.X, pady=(scale_size(self.window, 8), 0))
            view = TextView(warn_card.body, height=3, state=tk.NORMAL, bordered=False)
            view.pack(fill=tk.X)
            view.text.insert("1.0", "部分目录或索引未读取：\n" + "\n".join(warnings))
            view.text.configure(state=tk.DISABLED)

        # Table Card
        table_card = Card(body)
        table_card.pack(fill=tk.BOTH, expand=True)
        actions = ttk.Frame(table_card.body, style="CardSubtle.TFrame", padding=scale_size(self.window, (12, 8)))
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="勾选唯一匹配", command=self._select_unique, style="SoftCompact.TButton").pack(side=tk.LEFT)
        ttk.Button(actions, text="清空勾选", command=self._clear, style="GhostSubtleCompact.TButton").pack(
            side=tk.LEFT, padx=(scale_size(self.window, 6), 0),
        )
        Divider(table_card.body).pack(fill=tk.X)

        listing = ttk.Frame(table_card.body, style="Card.TFrame")
        listing.pack(fill=tk.BOTH, expand=True)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)

        self._check_icons = get_checkbox_icons(self.window)
        self.table = ttk.Treeview(
            listing, columns=("title", "status", "file"), show="tree headings", selectmode="browse",
        )
        style_treeview(self.table)
        self.table.heading("#0", text="选", anchor=tk.CENTER)
        self.table.column("#0", width=scale_size(self.window, 48), minwidth=scale_size(self.window, 44), stretch=False, anchor=tk.CENTER)
        for name, label, width in (("title", "论文标题", 420), ("status", "匹配结果", 105), ("file", "文件", 340)):
            self.table.heading(name, text=label, anchor=tk.W)
            self.table.column(
                name,
                width=scale_size(self.window, width),
                minwidth=scale_size(self.window, 90),
                stretch=name in {"title", "file"},
                anchor=tk.W,
            )
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=self.table.yview, bootstyle="secondary")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(listing, orient=tk.HORIZONTAL, command=self.table.xview, bootstyle="secondary")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.bind("<Button-1>", self._click)
        self.table.bind("<space>", self._toggle_current)
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._show_selection())
        for row in rows:
            self.table.insert("", tk.END, iid=str(row.index), image=self._icon(row), values=self._values(row))
        apply_zebra(self.table)
        if rows:
            self.table.selection_set(str(rows[0].index))
        self._update_summary()
        self._show_selection()

    def _icon(self, row: PdfMatch) -> tk.PhotoImage:
        if row.status == "existing":
            return self._check_icons["disabled"]
        return self._check_icons["checked" if row.index in self.checked else "unchecked"]

    def _values(self, row: PdfMatch):
        label = {"existing": "已有 PDF", "indexed": "索引匹配", "unique": "唯一候选", "review": "待确认", "missing": "未匹配"}[row.status]
        path = self.paths.get(row.index)
        return row.paper.title, label, str(path) if path else f"{len(row.candidates)} 个候选" if row.candidates else ""

    def _update_summary(self):
        remaining = sum(row.status != "existing" and row.index not in self.checked for row in self.rows.values())
        self.summary.set(f"已勾选 {len(self.checked)} 篇 · 未关联 {remaining} 篇")
        self.confirm.configure(state=tk.NORMAL if self.checked else tk.DISABLED)
        for row in self.rows.values():
            self.table.item(str(row.index), image=self._icon(row), values=self._values(row))

    def _select_unique(self):
        self.checked = {index for index, row in self.rows.items() if row.status in {"indexed", "unique"}}
        self._update_summary()

    def _clear(self):
        self.checked.clear()
        self._update_summary()

    def _toggle(self, index: int):
        if self.rows[index].status == "existing":
            return
        if index in self.checked:
            self.checked.remove(index)
        elif index in self.paths:
            self.checked.add(index)
        self._update_summary()

    def _click(self, event):
        item = self.table.identify_row(event.y)
        if item and self.table.identify_column(event.x) == "#0":
            self._toggle(int(item))
            return "break"

    def _toggle_current(self, _event):
        selected = self.table.selection()
        if selected:
            self._toggle(int(selected[0]))
        return "break"

    def _show_selection(self):
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        row = self.rows[index]
        text = "\n".join((row.paper.title, f"{row.paper.authors}  ·  {row.paper.source}  ·  {row.paper.publication_date}", row.reason))
        self.info.text.configure(state=tk.NORMAL)
        self.info.text.delete("1.0", tk.END)
        self.info.text.insert("1.0", text)
        self.info.text.configure(state=tk.DISABLED)
        self.choice.configure(values=[str(path) for path in row.candidates], state=tk.DISABLED if row.status == "existing" else "readonly")
        self.choice.set(str(self.paths[index]) if index in self.paths else "")
        self.browse.configure(state=tk.DISABLED if row.status == "existing" else tk.NORMAL)

    def _choose(self, _event=None):
        selected = self.table.selection()
        if selected and self.choice.get():
            index = int(selected[0])
            self.paths[index] = Path(self.choice.get())
            self.checked.add(index)
            self._update_summary()

    def _browse(self):
        selected = self.table.selection()
        if not selected:
            return
        index = int(selected[0])
        value = filedialog.askopenfilename(parent=self.window, title="选择 PDF", filetypes=[("PDF", "*.pdf")])
        if not value:
            return
        problem = pdf_problem(value)
        if problem:
            messagebox.showerror("无法关联 PDF", problem, parent=self.window)
            return
        path = Path(value).resolve()
        row = self.rows[index]
        if path not in row.candidates:
            row.candidates.append(path)
        self.paths[index] = path
        row.status, row.reason = "review", "手动选择 PDF"
        self.checked.add(index)
        self._update_summary()
        self._show_selection()

    def _accept(self):
        used = set(self.reserved)
        for index in sorted(self.checked):
            path = self.paths[index].resolve()
            if path in used:
                messagebox.showwarning("PDF 关联冲突", f"同一文件已对应其他文献：\n{path}", parent=self.window)
                return
            used.add(path)
        if not self.cancel.is_set():
            self.result = {index: str(self.paths[index]) for index in sorted(self.checked)}
        self._close()

    def _check_cancel(self):
        self._poll_id = None
        if self.cancel.is_set():
            self._close()
        else:
            self._poll_id = self.window.after(100, self._check_cancel)

    def _close(self):
        if self._poll_id is not None:
            self.window.after_cancel(self._poll_id)
            self._poll_id = None
        self.window.destroy()

    def show(self):
        self.window.update_idletasks()
        self.window.deiconify()
        self.window.grab_set()
        self._poll_id = self.window.after(100, self._check_cancel)
        self.window.wait_window()
        return self.result
