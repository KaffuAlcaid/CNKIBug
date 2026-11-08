from __future__ import annotations

import tkinter as tk
from pathlib import Path
from threading import Event
from tkinter import filedialog

import ttkbootstrap as ttk

from ..fileio.pdf_links import PdfMatch, pdf_problem
from . import dialogs as messagebox
from .appearance import TextView


def _window(parent, title: str, width: int, height: int):
    window = ttk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window.transient(parent)
    width = min(width, parent.winfo_screenwidth() - 80)
    height = min(height, parent.winfo_screenheight() - 100)
    window.geometry(f"{width}x{height}")
    window.minsize(min(width, 620), min(height, 400))
    return window


class PdfFolderDialog:
    def __init__(self, parent, initial_directory: Path):
        self.result: tuple[Path, bool] | None = None
        self.window = _window(parent, "从文件夹批量关联 PDF", 680, 200)
        self.window.resizable(True, False)
        self.window.bind("<Escape>", lambda _event: self.window.destroy())
        body = ttk.Frame(self.window, padding=20)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="PDF 文件夹").pack(anchor=tk.W)
        folder = ttk.Frame(body)
        folder.pack(fill=tk.X, pady=(8, 12))
        self.directory = tk.StringVar(self.window, value=str(initial_directory))
        ttk.Entry(folder, textvariable=self.directory).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(folder, text="浏览", command=self._browse, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=(8, 0))
        self.recursive = tk.BooleanVar(self.window, value=False)
        ttk.Checkbutton(body, text="包含子文件夹", variable=self.recursive).pack(anchor=tk.W)
        buttons = ttk.Frame(body)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))
        ttk.Button(buttons, text="扫描", command=self._accept).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="取消", command=self.window.destroy, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=8)

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
        self.window.position_center()
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
        self.window = _window(parent, "确认 PDF 关联", 1120, 720)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        body = ttk.Frame(self.window, padding=18)
        body.pack(fill=tk.BOTH, expand=True)
        location = ttk.Label(body, text=directory, wraplength=1000)
        location.pack(fill=tk.X, pady=(0, 10))
        location.bind("<Configure>", lambda event: location.configure(wraplength=max(200, event.width)))
        footer = ttk.Frame(body)
        footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        self.summary = tk.StringVar(self.window)
        ttk.Label(footer, textvariable=self.summary).pack(side=tk.LEFT)
        self.confirm = ttk.Button(footer, text="关联所选", command=self._accept)
        self.confirm.pack(side=tk.RIGHT)
        ttk.Button(footer, text="取消", command=self._close, bootstyle="secondary-outline").pack(side=tk.RIGHT, padx=8)
        details = ttk.Frame(body)
        details.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        self.info = TextView(details, height=4, state=tk.DISABLED)
        self.info.pack(fill=tk.X, pady=(0, 8))
        files = ttk.Frame(details)
        files.pack(fill=tk.X)
        ttk.Label(files, text="PDF 文件").pack(side=tk.LEFT, padx=(0, 8))
        self.choice = ttk.Combobox(files, state="readonly")
        self.choice.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.choice.bind("<<ComboboxSelected>>", self._choose)
        self.browse = ttk.Button(files, text="选择文件", command=self._browse, bootstyle="secondary-outline")
        self.browse.pack(side=tk.RIGHT, padx=(8, 0))
        if warnings:
            view = TextView(body, height=3, state=tk.NORMAL)
            view.pack(side=tk.BOTTOM, fill=tk.X, pady=(8, 0))
            view.text.insert("1.0", "部分目录或索引未读取：\n" + "\n".join(warnings))
            view.text.configure(state=tk.DISABLED)
        actions = ttk.Frame(body)
        actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(actions, text="勾选唯一匹配", command=self._select_unique, bootstyle="secondary-outline").pack(side=tk.LEFT)
        ttk.Button(actions, text="清空勾选", command=self._clear, bootstyle="secondary-outline").pack(side=tk.LEFT, padx=8)
        listing = ttk.Frame(body)
        listing.pack(fill=tk.BOTH, expand=True)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(listing, columns=("checked", "title", "status", "file"), show="headings", selectmode="browse")
        for name, label, width in (("checked", "关联", 48), ("title", "论文标题", 430), ("status", "匹配结果", 110), ("file", "文件", 340)):
            self.table.heading(name, text=label)
            self.table.column(name, width=width, minwidth=48 if name == "checked" else 90, stretch=name in {"title", "file"})
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(listing, orient=tk.HORIZONTAL, command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.table.bind("<Button-1>", self._click)
        self.table.bind("<space>", self._toggle_current)
        self.table.bind("<<TreeviewSelect>>", lambda _event: self._show_selection())
        for row in rows:
            self.table.insert("", tk.END, iid=str(row.index), values=self._values(row))
        if rows:
            self.table.selection_set(str(rows[0].index))
        self._update_summary()
        self._show_selection()

    def _values(self, row: PdfMatch):
        mark = "" if row.status == "existing" else "☑" if row.index in self.checked else "☐"
        label = {"existing": "已有 PDF", "indexed": "索引匹配", "unique": "唯一候选", "review": "待确认", "missing": "未匹配"}[row.status]
        path = self.paths.get(row.index)
        return mark, row.paper.title, label, str(path) if path else f"{len(row.candidates)} 个候选" if row.candidates else ""

    def _update_summary(self):
        remaining = sum(row.status != "existing" and row.index not in self.checked for row in self.rows.values())
        self.summary.set(f"已勾选 {len(self.checked)} 篇，未关联 {remaining} 篇")
        self.confirm.configure(state=tk.NORMAL if self.checked else tk.DISABLED)
        for row in self.rows.values():
            self.table.item(str(row.index), values=self._values(row))

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
        if item and self.table.identify_column(event.x) == "#1":
            self._toggle(int(item))

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
        text = "\n".join((row.paper.title, f"{row.paper.authors} | {row.paper.source} | {row.paper.publication_date}", row.reason))
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
        self.window.position_center()
        self.window.deiconify()
        self.window.grab_set()
        self._poll_id = self.window.after(100, self._check_cancel)
        self.window.wait_window()
        return self.result
