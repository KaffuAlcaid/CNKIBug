from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import ttkbootstrap as ttk

from ..core.search_query import AdvancedQuery, SearchOptions, load_advanced_queries
from ..fileio.keyword_input import (
    KeywordImportError,
    KeywordImportResult,
    dedupe_keywords,
    load_keywords_txt,
)
from ..fileio.paths import get_real_desktop_path
from .advanced import AdvancedSearchDialog
from .search_options import SearchOptionsDialog


@dataclass(frozen=True)
class GuiTaskRequest:
    keywords: list[str]
    max_pages: int
    save_mode: str
    include_citation: bool
    include_details: bool
    detail_txt_export: bool
    output_dir: Path | None
    advanced_queries: dict[str, AdvancedQuery] = field(default_factory=dict)
    search_options: SearchOptions | None = None


def _resolve_save_mode(keyword_count: int, output_format: str, split_excel: bool) -> str:
    if keyword_count == 1:
        return "single_csv" if output_format == "csv" else "single"
    if output_format == "csv":
        return "multi_csv"
    return "multi_split" if split_excel else "multi_merge"


def _merge_task_keywords(
    existing: list[str],
    incoming: list[str],
    *,
    replace: bool = False,
) -> KeywordImportResult:
    return dedupe_keywords([*([] if replace else existing), *incoming])


class TaskForm(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        output_dir: str,
        on_review: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.root = self.winfo_toplevel()
        self._running = False
        self._keywords: list[str] = []
        self._advanced_queries: dict[str, AdvancedQuery] = {}
        self._search_options: SearchOptions | None = None

        self._form_canvas = tk.Canvas(
            self,
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=20,
        )
        self._form_scrollbar = ttk.Scrollbar(
            self,
            orient=tk.VERTICAL,
            command=self._form_canvas.yview,
        )
        self._form_canvas.configure(yscrollcommand=self._form_scrollbar.set)
        self._form_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._form_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._form = ttk.Frame(self._form_canvas)
        self._form_window = self._form_canvas.create_window(
            (0, 0),
            window=self._form,
            anchor=tk.NW,
        )
        self._form.bind("<Configure>", self._update_form_scrollregion)
        self._form_canvas.bind("<Configure>", self._resize_form_width)
        self.root.bind("<MouseWheel>", self._scroll_form, add="+")
        self.root.bind("<Button-4>", self._scroll_form, add="+")
        self.root.bind("<Button-5>", self._scroll_form, add="+")

        keyword_frame = ttk.Frame(self._form)
        keyword_frame.pack(fill=tk.X, pady=(0, 12))
        heading = ttk.Frame(keyword_frame)
        heading.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(heading, text="检索项", font=("TkDefaultFont", 11, "bold")).pack(side=tk.LEFT)
        self._advanced_button = ttk.Button(
            heading, text="高级检索", command=self._open_advanced, bootstyle="secondary-outline",
        )
        self._advanced_button.pack(side=tk.RIGHT, padx=(6, 0))
        self._import_button = ttk.Button(
            heading, text="导入 TXT", command=self._import_txt, bootstyle="secondary-outline",
        )
        self._import_button.pack(side=tk.RIGHT)

        entry_row = ttk.Frame(keyword_frame)
        entry_row.pack(fill=tk.X, pady=(0, 8))
        entry_row.columnconfigure(0, weight=1)
        self._keyword_var = tk.StringVar()
        self._keyword_entry = ttk.Entry(entry_row, textvariable=self._keyword_var)
        self._keyword_entry.grid(row=0, column=0, sticky="ew")
        self._keyword_entry.bind("<Return>", lambda _event: self._add_keyword())
        self._add_keyword_button = ttk.Button(
            entry_row, text="添加", command=self._add_keyword, bootstyle="secondary-outline", width=7,
        )
        self._add_keyword_button.grid(row=0, column=1, padx=(8, 0))

        list_frame = ttk.Frame(keyword_frame)
        list_frame.pack(fill=tk.X)
        self.root.style.configure("Task.Treeview", rowheight=30)
        self._keyword_list = ttk.Treeview(
            list_frame, columns=("number", "type", "keyword"), show="headings",
            height=5, selectmode="browse", style="Task.Treeview",
        )
        self._keyword_list.heading("number", text="#")
        self._keyword_list.heading("type", text="类型")
        self._keyword_list.heading("keyword", text="关键词 / 检索条件", anchor=tk.W)
        self._keyword_list.column("number", width=44, minwidth=44, stretch=False, anchor=tk.CENTER)
        self._keyword_list.column("type", width=90, minwidth=90, stretch=False, anchor=tk.CENTER)
        self._keyword_list.column("keyword", minwidth=220, anchor=tk.W)
        self._keyword_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._keyword_list.bind("<<TreeviewSelect>>", self._keyword_selected)
        self._keyword_list.bind("<Double-1>", self._edit_selected_item)
        keyword_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._keyword_list.yview)
        keyword_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._keyword_list.configure(yscrollcommand=keyword_scrollbar.set)

        keyword_actions = ttk.Frame(keyword_frame)
        keyword_actions.pack(fill=tk.X, pady=(6, 0))
        self._modify_keyword_button = ttk.Button(
            keyword_actions, text="编辑", command=self._modify_keyword,
            state=tk.DISABLED, bootstyle="secondary-outline", width=7,
        )
        self._modify_keyword_button.pack(side=tk.LEFT)
        self._delete_keyword_button = ttk.Button(
            keyword_actions, text="移除", command=self._delete_keyword,
            state=tk.DISABLED, bootstyle="secondary-outline", width=7,
        )
        self._delete_keyword_button.pack(side=tk.LEFT, padx=(6, 0))
        self._keyword_status_var = tk.StringVar(value="当前任务：0 项")
        ttk.Label(keyword_actions, textvariable=self._keyword_status_var, bootstyle="secondary").pack(side=tk.RIGHT)

        ttk.Separator(self._form).pack(fill=tk.X, pady=(0, 12))
        settings_row = ttk.Frame(self._form)
        settings_row.pack(fill=tk.X)
        settings_row.columnconfigure(1, weight=1)
        ttk.Label(settings_row, text="每项页数").grid(row=0, column=0, sticky=tk.W, padx=(0, 12))
        scope = ttk.Frame(settings_row)
        scope.grid(row=0, column=1, sticky="ew")
        self._pages_var = tk.StringVar(value="1")
        self._pages_entry = ttk.Spinbox(scope, from_=1, to=100000, textvariable=self._pages_var, width=7)
        self._pages_entry.pack(side=tk.LEFT)
        self._search_options_button = ttk.Button(
            scope, text="检索范围与排序", command=self._open_search_options, bootstyle="secondary-outline",
        )
        self._search_options_button.pack(side=tk.LEFT, padx=(8, 0))

        self._format_var = tk.StringVar(value="excel")
        self._excel_radio = ttk.Radiobutton(
            scope, text="Excel", variable=self._format_var, value="excel",
            command=self._sync_option_states, bootstyle="secondary-toolbutton",
        )
        self._csv_radio = ttk.Radiobutton(
            scope, text="CSV", variable=self._format_var, value="csv",
            command=self._sync_option_states, bootstyle="secondary-toolbutton",
        )
        self._csv_radio.pack(side=tk.RIGHT)
        self._excel_radio.pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Label(scope, text="输出格式").pack(side=tk.RIGHT, padx=(0, 8))

        ttk.Label(settings_row, text="保存位置").grid(row=1, column=0, sticky=tk.W, padx=(0, 12), pady=(10, 0))
        output_row = ttk.Frame(settings_row)
        output_row.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        output_row.columnconfigure(0, weight=1)
        self._output_var = tk.StringVar(value=output_dir or get_real_desktop_path())
        self._output_entry = ttk.Entry(output_row, textvariable=self._output_var)
        self._output_entry.grid(row=0, column=0, sticky="ew")
        self._browse_button = ttk.Button(
            output_row, text="浏览", command=self._choose_output_dir, bootstyle="secondary-outline", width=7,
        )
        self._browse_button.grid(row=0, column=1, padx=(8, 0))

        ttk.Label(settings_row, text="采集内容").grid(row=2, column=0, sticky=tk.W, padx=(0, 12), pady=(12, 0))
        extras = ttk.Frame(settings_row)
        extras.grid(row=2, column=1, sticky="ew", pady=(12, 0))
        self._citation_var = tk.BooleanVar(value=False)
        self._details_var = tk.BooleanVar(value=False)
        self._txt_var = tk.BooleanVar(value=False)
        self._split_var = tk.BooleanVar(value=False)
        self._citation_check = ttk.Checkbutton(extras, text="GB/T 7714 引用", variable=self._citation_var)
        self._citation_check.pack(side=tk.LEFT)
        self._details_check = ttk.Checkbutton(
            extras, text="摘要与关键词", variable=self._details_var, command=self._details_changed,
        )
        self._details_check.pack(side=tk.LEFT, padx=(18, 0))
        self._show_more = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            extras, text="更多选项", variable=self._show_more, command=self._toggle_more_options,
        ).pack(side=tk.RIGHT)
        self._more_options = ttk.Frame(settings_row, padding=(0, 10, 0, 0))
        self._split_check = ttk.Checkbutton(
            self._more_options, text="每个检索项独立保存 Excel", variable=self._split_var,
        )
        self._split_check.pack(anchor=tk.W)
        self._txt_check = ttk.Checkbutton(
            self._more_options, text="另存论文关键词 TXT", variable=self._txt_var, command=self._txt_changed,
        )
        self._txt_check.pack(anchor=tk.W, pady=(6, 0))

        action_row = ttk.Frame(self._form)
        action_row.pack(fill=tk.X, pady=(16, 4))
        self._review_button = ttk.Button(
            action_row, text="检查并开始检索", command=on_review, bootstyle="primary", width=18,
        )
        self._review_button.pack(side=tk.RIGHT)

        self._form_controls = [
            self._keyword_entry,
            self._add_keyword_button,
            self._advanced_button,
            self._import_button,
            self._pages_entry,
            self._output_entry,
            self._browse_button,
            self._excel_radio,
            self._csv_radio,
            self._split_check,
            self._citation_check,
            self._details_check,
            self._txt_check,
            self._review_button,
        ]
        self._sync_option_states()

    @property
    def output_dir(self) -> Path:
        return Path(self._output_var.get().strip() or get_real_desktop_path())

    @output_dir.setter
    def output_dir(self, value: str) -> None:
        self._output_var.set(value)

    @property
    def output_format(self) -> str:
        return self._format_var.get()

    def apply_theme(self, style: ttk.Style) -> None:
        self._form_canvas.configure(background=style.colors.bg)
        style.configure("Task.Treeview", rowheight=30)

    def _toggle_more_options(self) -> None:
        if self._show_more.get():
            self._more_options.grid(row=3, column=1, sticky="ew")
        else:
            self._more_options.grid_remove()

    def show(self, before: tk.Misc) -> None:
        self.pack(fill=tk.BOTH, expand=True, before=before)
        self._form_canvas.yview_moveto(0)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._search_options_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        for control in self._form_controls:
            control.configure(state=tk.DISABLED if running else tk.NORMAL)
        self._sync_keyword_action_states()
        if not running:
            self._sync_option_states()

    def clear(self) -> None:
        self._set_keywords([])
        self._reset_keyword_editor()

    def collect_request(self) -> GuiTaskRequest | None:
        pending_keyword = self._keyword_var.get().strip()
        selected_index = self._selected_keyword_index()
        if pending_keyword and (
            selected_index is None or pending_keyword != self._keywords[selected_index]
        ):
            messagebox.showwarning(
                "检索项尚未保存",
                "输入框中的内容尚未添加或修改，请先保存到当前任务列表。",
                parent=self.root,
            )
            self._keyword_entry.focus_set()
            return None
        keywords = list(self._keywords)
        if not keywords:
            messagebox.showerror("任务设置错误", "请至少输入一个关键词或检索句。", parent=self.root)
            return None
        try:
            max_pages = int(self._pages_var.get().strip())
        except ValueError:
            max_pages = 0
        if max_pages <= 0:
            messagebox.showerror("任务设置错误", "抓取页数必须是大于 0 的整数。", parent=self.root)
            return None
        output_text = os.path.expanduser(os.path.expandvars(self._output_var.get().strip()))
        if not output_text:
            messagebox.showerror("任务设置错误", "请选择保存位置。", parent=self.root)
            return None
        output_dir = Path(output_text).resolve()
        if output_dir.exists() and not output_dir.is_dir():
            messagebox.showerror("任务设置错误", "保存位置不是文件夹。", parent=self.root)
            return None

        save_mode = _resolve_save_mode(
            len(keywords),
            self._format_var.get(),
            self._split_var.get(),
        )
        include_details = self._details_var.get() or self._txt_var.get()
        return GuiTaskRequest(
            keywords=keywords,
            max_pages=max_pages,
            save_mode=save_mode,
            include_citation=self._citation_var.get(),
            include_details=include_details,
            detail_txt_export=self._txt_var.get(),
            output_dir=output_dir,
            advanced_queries=dict(self._advanced_queries),
            search_options=getattr(self, "_search_options", None),
        )

    def populate_resume(self, state: dict[str, Any]) -> None:
        self._search_options = SearchOptions.from_dict(state.get("search_options"))
        keywords = state.get("keywords", [])
        self._advanced_queries = load_advanced_queries(state.get("advanced_queries", {}), keywords)
        self._set_keywords([str(item) for item in keywords])
        self._reset_keyword_editor()
        self._pages_var.set(str(state.get("max_pages", 1)))
        save_mode = str(state.get("save_mode", "single"))
        self._format_var.set("csv" if save_mode.endswith("csv") else "excel")
        self._split_var.set(save_mode == "multi_split")
        self._citation_var.set(bool(state.get("include_citation", False)))
        self._details_var.set(bool(state.get("include_details", False)))
        self._txt_var.set(bool(state.get("detail_txt_export", False)))
        self._show_more.set(self._split_var.get() or self._txt_var.get())
        self._toggle_more_options()
        output_dir = state.get("output_dir")
        if isinstance(output_dir, str) and output_dir:
            self._output_var.set(output_dir)
        self._sync_option_states()

    def _open_search_options(self) -> None:
        if self._running:
            return
        dialog = SearchOptionsDialog(self.root, self._search_options)
        self._search_options = dialog.result

    def _update_form_scrollregion(self, _event: tk.Event | None = None) -> None:
        bounds = self._form_canvas.bbox("all")
        if bounds is not None:
            self._form_canvas.configure(scrollregion=bounds)

    def _resize_form_width(self, event: tk.Event) -> None:
        self._form_canvas.itemconfigure(self._form_window, width=event.width)

    def _scroll_form(self, event: tk.Event) -> None:
        if not self.winfo_ismapped():
            return
        try:
            pointer = self.root.winfo_containing(*self.root.winfo_pointerxy())
        except KeyError:
            # Tcl-created controls such as Combobox popdowns have no Python widget.
            return
        if pointer is self._keyword_list:
            return
        current = pointer
        while current is not None and current not in {
            self._form,
            self._form_canvas,
            self,
        }:
            current = getattr(current, "master", None)
        if current is None:
            return
        event_number = getattr(event, "num", None)
        if event_number == 4:
            units = -3
        elif event_number == 5:
            units = 3
        else:
            delta = int(getattr(event, "delta", 0))
            if not delta:
                return
            units = -3 if delta > 0 else 3
        self._form_canvas.yview_scroll(units, "units")

    # 关键词列表和输入框始终由这一组方法同步，避免可见内容与任务数据分离。
    def _selected_keyword_index(self) -> int | None:
        selection = self._keyword_list.selection()
        if not selection:
            return None
        return int(selection[0].removeprefix("keyword-"))

    def _select_keyword(self, index: int) -> None:
        item_id = f"keyword-{index}"
        self._keyword_list.selection_set(item_id)
        self._keyword_list.focus(item_id)
        self._keyword_list.see(item_id)

    def _set_keywords(self, keywords: list[str], status: str | None = None) -> None:
        self._keywords = list(keywords)
        self._advanced_queries = {key: query for key, query in self._advanced_queries.items() if key in self._keywords}
        items = self._keyword_list.get_children()
        if items:
            self._keyword_list.delete(*items)
        for index, keyword in enumerate(self._keywords):
            self._keyword_list.insert(
                "",
                tk.END,
                iid=f"keyword-{index}",
                values=(
                    index + 1,
                    "高级检索" if keyword in self._advanced_queries else "普通检索",
                    self._advanced_queries[keyword].summary() if keyword in self._advanced_queries else keyword,
                ),
            )
        self._keyword_status_var.set(status or f"当前任务：{len(self._keywords)} 项")
        self._sync_keyword_action_states()

    def _sync_keyword_action_states(self) -> None:
        state = tk.NORMAL if not self._running and self._selected_keyword_index() is not None else tk.DISABLED
        self._modify_keyword_button.configure(state=state)
        self._delete_keyword_button.configure(state=state)

    def _keyword_selected(self, _event: tk.Event | None = None) -> None:
        index = self._selected_keyword_index()
        if index is not None:
            keyword = self._keywords[index]
            self._keyword_var.set("" if keyword in self._advanced_queries else keyword)
        self._sync_keyword_action_states()

    def _edit_selected_item(self, event: tk.Event) -> None:
        if self._running:
            return
        item_id = self._keyword_list.identify_row(event.y)
        if not item_id:
            return
        self._keyword_list.selection_set(item_id)
        self._keyword_selected()
        index = self._selected_keyword_index()
        if index is not None and self._keywords[index] in self._advanced_queries:
            self._open_advanced(index)
        else:
            self._keyword_entry.focus_set()

    def _open_advanced(self, index: int | None = None) -> None:
        if self._running:
            return
        pending = self._keyword_var.get().strip()
        selected = self._selected_keyword_index()
        if pending and (selected is None or pending != self._keywords[selected]):
            messagebox.showwarning(
                "检索项尚未保存", "请先将输入框中的内容保存到任务列表。", parent=self.root,
            )
            return
        keyword = self._keywords[index] if index is not None else None
        original = self._advanced_queries.get(keyword) if keyword is not None else None
        query = AdvancedSearchDialog(self.root, original).show()
        if query is None:
            return
        for existing, value in self._advanced_queries.items():
            if existing != keyword and value == query:
                self._select_keyword(self._keywords.index(existing))
                messagebox.showwarning("重复检索项", "相同的高级检索条件已在当前任务中。", parent=self.root)
                return
        if keyword is None:
            number = 1
            while f"高级检索 {number}" in self._keywords:
                number += 1
            keyword = f"高级检索 {number}"
            try:
                keywords = _merge_task_keywords(self._keywords, [keyword]).keywords
            except KeywordImportError as error:
                messagebox.showerror("任务列表已满", str(error), parent=self.root)
                return
        else:
            keywords = list(self._keywords)
        self._advanced_queries[keyword] = query
        self._set_keywords(keywords)
        self._select_keyword(keywords.index(keyword))

    def _reset_keyword_editor(self, *, focus: bool = False) -> None:
        self._keyword_var.set("")
        if focus:
            self._keyword_entry.focus_set()

    def _add_keyword(self) -> None:
        keyword = self._keyword_var.get().strip()
        if not keyword:
            messagebox.showerror("添加失败", "请输入关键词或检索句。", parent=self.root)
            self._keyword_entry.focus_set()
            return
        try:
            merged = _merge_task_keywords(self._keywords, [keyword])
        except KeywordImportError as error:
            messagebox.showerror("添加失败", str(error), parent=self.root)
            return
        if merged.duplicates:
            index = self._keywords.index(keyword)
            self._select_keyword(index)
            messagebox.showwarning(
                "重复检索项",
                f"“{keyword}”已在当前任务中。",
                parent=self.root,
            )
            return
        self._set_keywords(merged.keywords)
        self._reset_keyword_editor(focus=True)

    def _modify_keyword(self) -> None:
        if self._running:
            return
        index = self._selected_keyword_index()
        if index is None:
            return
        if self._keywords[index] in self._advanced_queries:
            self._open_advanced(index)
            return
        keyword = self._keyword_var.get().strip()
        if not keyword:
            messagebox.showerror("修改失败", "检索项不能为空。", parent=self.root)
            return
        if keyword in self._keywords and self._keywords.index(keyword) != index:
            messagebox.showwarning(
                "重复检索项",
                f"“{keyword}”已在当前任务中。",
                parent=self.root,
            )
            return
        updated = list(self._keywords)
        updated[index] = keyword
        self._set_keywords(updated, f"已修改第 {index + 1} 项；当前任务：{len(updated)} 项")
        self._reset_keyword_editor(focus=True)

    def _delete_keyword(self) -> None:
        if self._running:
            return
        index = self._selected_keyword_index()
        if index is None:
            return
        deleted = self._keywords[index]
        updated = [*self._keywords[:index], *self._keywords[index + 1 :]]
        self._set_keywords(updated, f"已删除“{deleted}”；当前任务：{len(updated)} 项")
        self._reset_keyword_editor(focus=True)

    def _import_txt(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="选择关键词 TXT",
            filetypes=(("TXT 文件", "*.txt"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            imported = load_keywords_txt(path)
        except KeywordImportError as error:
            messagebox.showerror("导入失败", str(error), parent=self.root)
            return

        append = False
        if self._keywords:
            choice = messagebox.askyesnocancel(
                "导入 TXT",
                "当前任务中已有检索项。\n\n选择“是”追加导入，选择“否”替换当前列表。",
                parent=self.root,
            )
            if choice is None:
                return
            append = choice
        try:
            merged = _merge_task_keywords(
                self._keywords,
                imported.keywords,
                replace=not append,
            )
        except KeywordImportError as error:
            messagebox.showerror("导入失败", str(error), parent=self.root)
            return
        duplicate_count = imported.duplicate_count + merged.duplicate_count
        duplicate_text = f"；跳过 {duplicate_count} 个重复项" if duplicate_count else ""
        if not append:
            self._advanced_queries.clear()
        self._set_keywords(
            merged.keywords,
            f"已从 TXT 载入 {len(imported.keywords)} 项；当前任务：{len(merged.keywords)} 项"
            f"{duplicate_text}",
        )
        self._reset_keyword_editor()

    def _choose_output_dir(self) -> None:
        initial = os.path.expanduser(os.path.expandvars(self._output_var.get().strip()))
        path = filedialog.askdirectory(
            parent=self.root,
            title="选择保存位置",
            initialdir=initial if os.path.isdir(initial) else None,
        )
        if path:
            self._output_var.set(path)

    def _details_changed(self) -> None:
        if not self._details_var.get():
            self._txt_var.set(False)

    def _txt_changed(self) -> None:
        if self._txt_var.get():
            self._details_var.set(True)

    def _sync_option_states(self) -> None:
        state = tk.DISABLED if self._format_var.get() == "csv" or self._running else tk.NORMAL
        self._split_check.configure(state=state)
        if state == tk.DISABLED:
            self._split_var.set(False)
