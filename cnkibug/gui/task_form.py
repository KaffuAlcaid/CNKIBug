from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog
from typing import Any

import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip
from . import dialogs as messagebox

from ..core.search_query import AdvancedQuery, SearchOptions, load_advanced_queries
from ..fileio.keyword_input import (
    MAX_KEYWORDS,
    KeywordImportError,
    KeywordImportResult,
    dedupe_keywords,
    load_keywords_txt,
)
from ..fileio.paths import get_real_desktop_path
from ..fileio.search_plans import read_search_plan, write_search_plan
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


@dataclass
class _KeywordRow:
    frame: ttk.Frame
    number: ttk.Label
    text: tk.StringVar
    entry: ttk.Entry
    delete_button: ttk.Button
    query_key: str | None = None
    edit_button: ttk.Button | None = None

    @property
    def keyword(self) -> str:
        return self.query_key if self.query_key is not None else self.text.get().strip()


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
        self._keyword_rows: list[_KeywordRow] = []
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
        actions = ttk.Frame(heading)
        actions.pack(side=tk.LEFT, padx=(16, 0))
        self._plan_button = ttk.Menubutton(actions, text="检索方案", bootstyle="secondary-outline")
        self._plan_button.pack(side=tk.LEFT)
        plans = tk.Menu(self._plan_button, tearoff=False)
        plans.add_command(label="载入方案", command=self._load_plan)
        plans.add_command(label="保存方案", command=self._save_plan)
        self._plan_button.configure(menu=plans)
        self._import_button = ttk.Button(
            actions, text="导入 TXT", command=self._import_txt, bootstyle="secondary-outline",
        )
        self._import_button.pack(side=tk.LEFT, padx=(6, 0))
        self._add_keyword_button = ttk.Button(
            actions, text="+ 普通检索项", command=self._add_keyword, bootstyle="secondary-outline",
        )
        self._add_keyword_button.pack(side=tk.LEFT, padx=(6, 0))
        self._advanced_button = ttk.Button(
            actions, text="高级检索", command=self._open_advanced, bootstyle="secondary-outline",
        )
        self._advanced_button.pack(side=tk.LEFT, padx=(6, 0))

        list_frame = ttk.Frame(keyword_frame)
        list_frame.pack(fill=tk.X)
        self._keyword_canvas = tk.Canvas(
            list_frame, height=190, borderwidth=0, highlightthickness=0,
            background=self.root.style.colors.bg, yscrollincrement=20,
        )
        self._keyword_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        keyword_scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._keyword_canvas.yview)
        keyword_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._keyword_canvas.configure(yscrollcommand=keyword_scrollbar.set)
        self._keyword_list = ttk.Frame(self._keyword_canvas)
        keyword_window = self._keyword_canvas.create_window((0, 0), window=self._keyword_list, anchor=tk.NW)
        self._keyword_list.bind(
            "<Configure>",
            lambda _event: self._keyword_canvas.configure(scrollregion=self._keyword_canvas.bbox("all")),
        )
        self._keyword_canvas.bind(
            "<Configure>", lambda event: self._keyword_canvas.itemconfigure(keyword_window, width=event.width),
        )
        self._keyword_status_var = tk.StringVar(value="当前任务：0 项")
        ttk.Label(keyword_frame, textvariable=self._keyword_status_var, bootstyle="secondary").pack(
            anchor=tk.E, pady=(6, 0),
        )

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
            command=self._sync_option_states, bootstyle="primary",
        )
        self._csv_radio = ttk.Radiobutton(
            scope, text="CSV", variable=self._format_var, value="csv",
            command=self._sync_option_states, bootstyle="primary",
        )
        self._csv_radio.pack(side=tk.RIGHT)
        self._excel_radio.pack(side=tk.RIGHT, padx=(0, 16))
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
            self._add_keyword_button,
            self._advanced_button,
            self._import_button,
            self._plan_button,
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
        self._set_keywords([])
        self._sync_option_states()

    def _save_plan(self) -> None:
        if self._running:
            return
        request = self.collect_request()
        if request is None:
            return
        filename = filedialog.asksaveasfilename(
            parent=self.root, title="保存检索方案", initialfile="cnki-search-plan.json",
            defaultextension=".json", filetypes=[("检索方案", "*.json")],
        )
        if not filename:
            return
        task = asdict(request)
        task["output_dir"] = str(request.output_dir) if request.output_dir else None
        try:
            write_search_plan(filename, task)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法保存检索方案", str(error), parent=self.root)
            return
        self._keyword_status_var.set(f"方案已保存：{Path(filename).name}")

    def _load_plan(self) -> None:
        if self._running:
            return
        filename = filedialog.askopenfilename(parent=self.root, title="载入检索方案", filetypes=[("检索方案", "*.json")])
        if not filename:
            return
        try:
            task = read_search_plan(filename)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法载入检索方案", str(error), parent=self.root)
            return
        if self._keywords and not messagebox.askyesno(
            "载入检索方案", "载入方案将替换当前检索项和输出选项。继续？", parent=self.root,
        ):
            return
        self.populate_resume(task)
        self._keyword_status_var.set(f"方案：{Path(filename).name}；当前任务：{len(self._keywords)} 项")

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
        self._keyword_canvas.configure(background=style.colors.bg)

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

    def collect_request(self) -> GuiTaskRequest | None:
        keywords = self._collect_keywords()
        if keywords is None:
            return None
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
        canvas = self._form_canvas
        current = pointer
        while current is not None and current not in {
            self._form,
            self._form_canvas,
            self,
        }:
            if current is self._keyword_canvas:
                canvas = self._keyword_canvas
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
        canvas.yview_scroll(units, "units")

    @property
    def _keywords(self) -> list[str]:
        return [row.keyword for row in self._keyword_rows if row.keyword]

    def _collect_keywords(self) -> list[str] | None:
        try:
            merged = dedupe_keywords(self._keywords)
        except KeywordImportError as error:
            messagebox.showerror("任务设置错误", str(error), parent=self.root)
            return None
        if merged.duplicates:
            keyword = merged.duplicates[0]
            messagebox.showwarning("重复检索项", f"“{keyword}”在任务列表中重复，请编辑或移除。", parent=self.root)
            rows = [row for row in self._keyword_rows if row.keyword == keyword]
            self._focus_keyword(rows[-1])
            return None
        return merged.keywords

    def _set_keywords(self, keywords: list[str], status: str | None = None) -> None:
        self._advanced_queries = {key: query for key, query in self._advanced_queries.items() if key in keywords}
        for row in self._keyword_rows:
            row.frame.destroy()
        self._keyword_rows.clear()
        for keyword in keywords or [""]:
            self._append_keyword_row(keyword, query_key=keyword if keyword in self._advanced_queries else None)
        self._keyword_canvas.yview_moveto(0)
        self._sync_keyword_action_states()
        if status:
            self._keyword_status_var.set(status)

    def _append_keyword_row(self, keyword: str, *, query_key: str | None = None) -> _KeywordRow:
        frame = ttk.Frame(self._keyword_list)
        frame.pack(fill=tk.X, pady=(0, 6), padx=(0, 4))
        frame.columnconfigure(2, weight=1)
        number = ttk.Label(frame, width=4, anchor=tk.CENTER)
        number.grid(row=0, column=0)
        ttk.Label(frame, text="高级检索" if query_key else "普通检索", width=9).grid(row=0, column=1)
        text = tk.StringVar(value=self._advanced_queries[query_key].summary() if query_key else keyword)
        entry = ttk.Entry(frame, textvariable=text)
        entry.grid(row=0, column=2, columnspan=1 if query_key else 2, sticky="ew")
        delete = ttk.Button(frame, text="-", width=3, bootstyle="secondary-outline")
        delete.grid(row=0, column=4, padx=(6, 0))
        row = _KeywordRow(frame, number, text, entry, delete, query_key=query_key)
        delete.configure(command=lambda: self._delete_keyword(row))
        ToolTip(delete, text="移除检索项")
        if query_key:
            row.edit_button = ttk.Button(
                frame, text="编辑条件", width=8, command=lambda: self._open_advanced(row),
                bootstyle="secondary-outline",
            )
            row.edit_button.grid(row=0, column=3, padx=(6, 0))
            entry.bind("<Double-1>", lambda _event: self._open_advanced(row))
        else:
            entry.bind("<Return>", lambda _event: self._next_keyword(row))
            text.trace_add("write", lambda *_args: self._keyword_status_var.set(f"当前任务：{len(self._keywords)} 项"))
        entry.bind("<FocusIn>", lambda _event: self._see_keyword(row))
        if row.edit_button:
            row.edit_button.bind("<FocusIn>", lambda _event: self._see_keyword(row))
        delete.bind("<FocusIn>", lambda _event: self._see_keyword(row))
        self._keyword_rows.append(row)
        return row

    def _sync_keyword_action_states(self) -> None:
        state = tk.DISABLED if self._running else tk.NORMAL
        for index, row in enumerate(self._keyword_rows):
            row.number.configure(text=str(index + 1))
            row.entry.configure(state=tk.DISABLED if self._running else "readonly" if row.query_key else tk.NORMAL)
            row.delete_button.configure(state=state)
            if row.edit_button:
                row.edit_button.configure(state=state)
        self._keyword_status_var.set(f"当前任务：{len(self._keywords)} 项")

    def _see_keyword(self, row: _KeywordRow) -> None:
        self._keyword_canvas.update_idletasks()
        top = row.frame.winfo_y()
        bottom = top + row.frame.winfo_height()
        visible_top = self._keyword_canvas.canvasy(0)
        visible_height = self._keyword_canvas.winfo_height()
        height = max(1, self._keyword_list.winfo_height())
        if top < visible_top:
            self._keyword_canvas.yview_moveto(top / height)
        elif bottom > visible_top + visible_height:
            self._keyword_canvas.yview_moveto((bottom - visible_height) / height)

    def _focus_keyword(self, row: _KeywordRow) -> None:
        self._see_keyword(row)
        (row.edit_button or row.entry).focus_set()

    def _next_keyword(self, row: _KeywordRow) -> str:
        if self._running:
            return "break"
        index = self._keyword_rows.index(row)
        if index + 1 < len(self._keyword_rows):
            self._focus_keyword(self._keyword_rows[index + 1])
        else:
            self._add_keyword()
        return "break"

    def _open_advanced(self, row: _KeywordRow | None = None) -> None:
        if self._running:
            return
        if row is None and len(self._keywords) >= MAX_KEYWORDS:
            messagebox.showerror("任务列表已满", f"检索项不能超过 {MAX_KEYWORDS} 个。", parent=self.root)
            return
        keyword = row.query_key if row is not None else None
        original = self._advanced_queries.get(keyword) if keyword is not None else None
        query = AdvancedSearchDialog(self.root, original).show()
        if query is None:
            return
        for existing, value in self._advanced_queries.items():
            if existing != keyword and value == query:
                messagebox.showwarning("重复检索项", "相同的高级检索条件已在当前任务中。", parent=self.root)
                self._focus_keyword(next(item for item in self._keyword_rows if item.query_key == existing))
                return
        if keyword is None:
            keywords = set(self._keywords)
            number = 1
            while f"高级检索 {number}" in keywords:
                number += 1
            keyword = f"高级检索 {number}"
        self._advanced_queries[keyword] = query
        if row is None:
            row = self._append_keyword_row(keyword, query_key=keyword)
        else:
            row.text.set(query.summary())
        self._sync_keyword_action_states()
        self._focus_keyword(row)

    def _add_keyword(self) -> None:
        if self._running:
            return
        for row in self._keyword_rows:
            if not row.keyword:
                self._focus_keyword(row)
                return
        if len(self._keyword_rows) >= MAX_KEYWORDS:
            messagebox.showerror("任务列表已满", f"检索项不能超过 {MAX_KEYWORDS} 个。", parent=self.root)
            return
        row = self._append_keyword_row("")
        self._sync_keyword_action_states()
        self._focus_keyword(row)

    def _delete_keyword(self, row: _KeywordRow) -> None:
        if self._running:
            return
        index = self._keyword_rows.index(row)
        self._keyword_rows.remove(row)
        row.frame.destroy()
        if row.query_key is not None:
            self._advanced_queries.pop(row.query_key, None)
        if not self._keyword_rows:
            self._append_keyword_row("")
        self._sync_keyword_action_states()
        self._focus_keyword(self._keyword_rows[min(index, len(self._keyword_rows) - 1)])

    def _import_txt(self) -> None:
        if self._running:
            return
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
        keywords = self._collect_keywords() if append else []
        if keywords is None:
            return
        try:
            merged = _merge_task_keywords(
                keywords,
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
