from __future__ import annotations

import os
import tkinter as tk
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tkinter import filedialog
from typing import Any

import ttkbootstrap as ttk
from ttkbootstrap.utility import scale_size
from ttkbootstrap.widgets import ToolTip

from ..core.estimate import estimate_seconds, format_eta
from ..core.search_query import (
    AdvancedQuery,
    ProfessionalQuery,
    SearchOptions,
    load_advanced_queries,
    load_professional_queries,
)
from ..fileio.keyword_input import (
    MAX_KEYWORDS,
    KeywordImportError,
    KeywordImportResult,
    dedupe_keywords,
    load_keywords_txt,
)
from ..fileio.paths import get_real_desktop_path
from ..fileio.search_plans import read_search_plan, write_search_plan
from . import dialogs as messagebox
from .advanced import AdvancedSearchDialog
from .appearance import Card, Divider, ScrollView
from .professional import ProfessionalSearchDialog
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
    professional_queries: dict[str, ProfessionalQuery] = field(default_factory=dict)


@dataclass
class _KeywordRow:
    frame: ttk.Frame
    number: ttk.Label
    badge: ttk.Label
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
        super().__init__(parent, style="Canvas.TFrame")
        self.root = self.winfo_toplevel()
        self._running = False
        self._keyword_rows: list[_KeywordRow] = []
        self._advanced_queries: dict[str, AdvancedQuery] = {}
        self._professional_queries: dict[str, ProfessionalQuery] = {}
        self._search_options: SearchOptions | None = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._workspace = ttk.Frame(self, style="Canvas.TFrame", padding=scale_size(self, (20, 16, 20, 16)))
        self._workspace.grid(row=0, column=0, sticky="nsew")
        self._stacked: bool | None = None
        self._workspace.bind("<Configure>", self._arrange_columns)
        self.root.bind("<MouseWheel>", self._scroll_form, add="+")
        self.root.bind("<Button-4>", self._scroll_form, add="+")
        self.root.bind("<Button-5>", self._scroll_form, add="+")

        # Left Card: Search items
        self._keyword_card = Card(self._workspace)
        keyword_body = self._keyword_card.body
        keyword_body.columnconfigure(0, weight=1)
        keyword_body.rowconfigure(3, weight=1)

        heading = ttk.Frame(keyword_body, style="Card.TFrame", padding=scale_size(self, (16, 12, 12, 12)))
        heading.grid(row=0, column=0, sticky="ew")
        ttk.Label(heading, text="检索项", style="CardSection.TLabel").pack(side=tk.LEFT)
        self._count_badge_var = tk.StringVar(value="0 项")
        ttk.Label(heading, textvariable=self._count_badge_var, style="BadgeNeutral.TLabel").pack(
            side=tk.LEFT, padx=(scale_size(self, 8), 0),
        )
        self._plan_button = ttk.Menubutton(heading, text="检索方案", style="GhostCompact.TMenubutton")
        self._plan_button.pack(side=tk.RIGHT)
        plans = tk.Menu(self._plan_button, tearoff=False)
        plans.add_command(label="载入方案", command=self._load_plan)
        plans.add_command(label="保存方案", command=self._save_plan)
        self._plan_button.configure(menu=plans)

        Divider(keyword_body).grid(row=1, column=0, sticky="ew")

        actions = ttk.Frame(keyword_body, style="Card.TFrame", padding=scale_size(self, (16, 10, 16, 8)))
        actions.grid(row=2, column=0, sticky="ew")
        gap = scale_size(self, 6)
        self._add_keyword_button = ttk.Button(
            actions, text="+ 普通检索项", command=self._add_keyword, style="SoftCompact.TButton",
        )
        self._add_keyword_button.pack(side=tk.LEFT)
        self._advanced_button = ttk.Button(
            actions, text="高级检索", command=self._open_advanced, style="SoftCompact.TButton",
        )
        self._advanced_button.pack(side=tk.LEFT, padx=(gap, 0))
        self._professional_button = ttk.Button(
            actions, text="专业检索", command=self._open_professional, style="SoftCompact.TButton",
        )
        self._professional_button.pack(side=tk.LEFT, padx=(gap, 0))
        self._import_button = ttk.Button(
            actions, text="导入 TXT", command=self._import_txt, style="GhostCompact.TButton",
        )
        self._import_button.pack(side=tk.LEFT, padx=(gap, 0))

        self._keyword_list = ScrollView(
            keyword_body, surface="card", height=1, width=1, padding=scale_size(self, (16, 4, 16, 10)),
        )
        self._keyword_list.grid(row=3, column=0, sticky="nsew")
        self._keyword_list.disable_scrolling()
        self._keyword_hint_frame = Card(self._keyword_list, subtle=True, padding=scale_size(self, (14, 12)))
        self._keyword_hint = ttk.Label(
            self._keyword_hint_frame.body,
            text="在上方输入关键词，或使用「高级检索 / 专业检索 / 导入 TXT」建立任务",
            style="CardSubtleMuted.TLabel",
            justify=tk.CENTER,
            anchor=tk.CENTER,
        )
        self._keyword_hint.pack(fill=tk.X)
        self._keyword_list.container.bind(
            "<Configure>",
            lambda event: self._keyword_hint.configure(wraplength=max(120, event.width - scale_size(self, 64))),
            add="+",
        )

        Divider(keyword_body).grid(row=4, column=0, sticky="ew")
        tip_bar = ttk.Frame(keyword_body, style="CardSubtle.TFrame", padding=scale_size(self, (16, 8)))
        tip_bar.grid(row=5, column=0, sticky="ew")
        ttk.Label(
            tip_bar, text="提示：在普通检索框按 Enter 可快速新增下一项", style="CardSubtleSmall.TLabel",
        ).pack(side=tk.LEFT)

        # Right Card: Collection & Save settings
        self._settings_card = Card(self._workspace)
        settings_body = self._settings_card.body
        settings_body.columnconfigure(0, weight=1)
        settings_body.rowconfigure(2, weight=1)

        settings_header = ttk.Frame(settings_body, style="Card.TFrame", padding=scale_size(self, (16, 12)))
        settings_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(settings_header, text="采集与保存", style="CardSection.TLabel").pack(side=tk.LEFT)
        ttk.Label(settings_header, text="任务参数", style="CardSmall.TLabel").pack(side=tk.RIGHT)

        Divider(settings_body).grid(row=1, column=0, sticky="ew")

        settings_row = self._settings_view = ScrollView(
            settings_body, surface="card", height=1, width=1, padding=scale_size(self, (16, 12, 16, 14)),
        )
        settings_row.grid(row=2, column=0, sticky="nsew")
        settings_row.disable_scrolling()
        settings_row.columnconfigure(0, weight=1)

        # Pages per query
        pages_row = ttk.Frame(settings_row, style="Card.TFrame")
        pages_row.grid(row=0, column=0, sticky="ew", pady=(0, scale_size(self, 12)))
        ttk.Label(pages_row, text="每项页数", style="CardMuted.TLabel").pack(side=tk.LEFT)
        ttk.Label(pages_row, text="页", style="CardMuted.TLabel").pack(side=tk.RIGHT, padx=(scale_size(self, 6), 0))
        self._pages_var = tk.StringVar(value="1")
        self._pages_var.trace_add("write", lambda *_args: self._update_keyword_summary())
        self._pages_entry = ttk.Spinbox(pages_row, from_=1, to=100000, textvariable=self._pages_var, width=8)
        self._pages_entry.pack(side=tk.RIGHT)

        # Scope & sort card with live summary
        scope_card = Card(settings_row, subtle=True, padding=scale_size(self, (12, 10)))
        scope_card.grid(row=1, column=0, sticky="ew", pady=(0, scale_size(self, 14)))
        scope_head = ttk.Frame(scope_card.body, style="CardSubtle.TFrame")
        scope_head.pack(fill=tk.X)
        ttk.Label(scope_head, text="检索范围与排序", style="CardSubtle.TLabel").pack(side=tk.LEFT)
        self._search_options_button = ttk.Button(
            scope_head, text="更改", command=self._open_search_options, style="GhostSubtleLink.TButton",
        )
        self._search_options_button.pack(side=tk.RIGHT)
        self._scope_summary_var = tk.StringVar(value="默认范围 · 沿用知网当前设置")
        self._scope_summary_label = ttk.Label(
            scope_card.body, textvariable=self._scope_summary_var, style="CardSubtleSmall.TLabel", justify=tk.LEFT,
        )
        self._scope_summary_label.pack(fill=tk.X, pady=(scale_size(self, 4), 0))
        scope_card.body.bind(
            "<Configure>",
            lambda event: self._scope_summary_label.configure(wraplength=max(120, event.width - scale_size(self, 8))),
            add="+",
        )

        # Output format
        ttk.Label(settings_row, text="输出格式", style="CardMuted.TLabel").grid(row=2, column=0, sticky="w")
        formats = ttk.Frame(settings_row, style="Card.TFrame")
        formats.grid(row=3, column=0, sticky="ew", pady=(scale_size(self, 6), scale_size(self, 14)))
        self._format_var = tk.StringVar(value="excel")
        self._excel_radio = ttk.Radiobutton(
            formats, text="Excel (.xlsx)", variable=self._format_var, value="excel",
            command=self._sync_option_states, style="Card.TRadiobutton",
        )
        self._csv_radio = ttk.Radiobutton(
            formats, text="CSV (.csv)", variable=self._format_var, value="csv",
            command=self._sync_option_states, style="Card.TRadiobutton",
        )
        self._excel_radio.pack(side=tk.LEFT, padx=(0, scale_size(self, 18)))
        self._csv_radio.pack(side=tk.LEFT)

        # Save directory
        ttk.Label(settings_row, text="保存位置", style="CardMuted.TLabel").grid(row=4, column=0, sticky="w")
        output_row = ttk.Frame(settings_row, style="Card.TFrame")
        output_row.grid(row=5, column=0, sticky="ew", pady=(scale_size(self, 6), scale_size(self, 14)))
        output_row.columnconfigure(0, weight=1)
        self._output_var = tk.StringVar(value=output_dir or get_real_desktop_path())
        self._output_entry = ttk.Entry(output_row, textvariable=self._output_var)
        self._output_entry.grid(row=0, column=0, sticky="ew")
        self._browse_button = ttk.Button(
            output_row, text="浏览", command=self._choose_output_dir, style="SoftCompact.TButton", width=5,
        )
        self._browse_button.grid(row=0, column=1, padx=(scale_size(self, 8), 0))

        Divider(settings_row).grid(row=6, column=0, sticky="ew", pady=(0, scale_size(self, 12)))

        # Collection options
        ttk.Label(settings_row, text="采集内容", style="CardMuted.TLabel").grid(row=7, column=0, sticky="w")
        extras = ttk.Frame(settings_row, style="Card.TFrame")
        extras.grid(row=8, column=0, sticky="ew", pady=(scale_size(self, 8), scale_size(self, 8)))
        self._citation_var = tk.BooleanVar(value=False)
        self._details_var = tk.BooleanVar(value=False)
        self._txt_var = tk.BooleanVar(value=False)
        self._split_var = tk.BooleanVar(value=False)
        self._citation_check = ttk.Checkbutton(
            extras, text="GB/T 7714 引用", variable=self._citation_var,
            command=self._update_keyword_summary, style="Card.TCheckbutton",
        )
        self._citation_check.pack(anchor=tk.W)
        self._details_check = ttk.Checkbutton(
            extras, text="摘要与关键词", variable=self._details_var,
            command=self._details_changed, style="Card.TCheckbutton",
        )
        self._details_check.pack(anchor=tk.W, pady=(scale_size(self, 8), 0))

        # Disclosure link instead of misleading checkbox
        self._show_more = tk.BooleanVar(value=False)
        self._more_toggle = ttk.Button(
            settings_row, text="▸ 更多输出选项", command=self._toggle_more_clicked, style="GhostMuted.TButton",
        )
        self._more_toggle.grid(row=9, column=0, sticky="w")
        self._more_options = ttk.Frame(settings_row, style="Card.TFrame", padding=scale_size(self, (14, 6, 0, 0)))
        self._split_check = ttk.Checkbutton(
            self._more_options, text="每个检索项独立保存 Excel", variable=self._split_var, style="Card.TCheckbutton",
        )
        self._split_check.pack(anchor=tk.W)
        self._txt_check = ttk.Checkbutton(
            self._more_options, text="另存论文关键词 TXT", variable=self._txt_var,
            command=self._txt_changed, style="Card.TCheckbutton",
        )
        self._txt_check.pack(anchor=tk.W, pady=(scale_size(self, 6), 0))

        # Flush bottom action bar
        Divider(self).grid(row=1, column=0, sticky="ew")
        action_row = ttk.Frame(self, style="Card.TFrame", padding=scale_size(self, (22, 12)))
        action_row.grid(row=2, column=0, sticky="ew")
        action_row.columnconfigure(0, weight=1)
        self._keyword_status_var = tk.StringVar(value="当前任务：0 项 · 请输入至少一个检索项")
        self._keyword_status = ttk.Label(
            action_row, textvariable=self._keyword_status_var, style="CardMuted.TLabel", width=1,
        )
        self._keyword_status.grid(row=0, column=0, sticky="ew", padx=(0, scale_size(self, 14)))
        self._keyword_status.bind(
            "<Configure>", lambda event: self._keyword_status.configure(wraplength=max(1, event.width)),
        )
        self._review_button = ttk.Button(
            action_row, text="检查并开始检索", command=on_review, style="Primary.TButton", width=18,
        )
        self._review_button.grid(row=0, column=1, sticky="e")

        self._form_controls = [
            self._add_keyword_button,
            self._advanced_button,
            self._professional_button,
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
            self._more_toggle,
            self._review_button,
        ]
        for control in (
            self._pages_entry, self._search_options_button, self._excel_radio,
            self._csv_radio, self._output_entry, self._browse_button,
            self._citation_check, self._details_check, self._more_toggle, self._split_check, self._txt_check,
        ):
            control.bind("<FocusIn>", lambda event: self._see_in_view(self._settings_view, event.widget))
        self._set_keywords([])
        self._sync_option_states()
        self._update_scope_summary()

    def _arrange_columns(self, event: tk.Event) -> None:
        stacked = event.width < scale_size(self, 780)
        if stacked == self._stacked:
            return
        self._stacked = stacked
        gap = scale_size(self, 14)
        self._workspace.columnconfigure(0, weight=7 if not stacked else 1, uniform="" if stacked else "task")
        self._workspace.columnconfigure(1, weight=0 if stacked else 5, uniform="" if stacked else "task")
        self._workspace.rowconfigure(0, weight=1)
        self._workspace.rowconfigure(1, weight=1 if stacked else 0)
        self._keyword_card.grid(
            row=0, column=0, sticky="nsew",
            padx=(0, 0 if stacked else gap), pady=(0, gap if stacked else 0),
        )
        self._settings_card.grid(
            row=1 if stacked else 0, column=0 if stacked else 1, sticky="nsew",
        )

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
            "载入检索方案", "载入方案将替换当前检索项和输出选项；继续？", parent=self.root,
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

    def _toggle_more_clicked(self) -> None:
        self._show_more.set(not self._show_more.get())
        self._toggle_more_options()

    def _toggle_more_options(self) -> None:
        if self._show_more.get():
            self._more_toggle.configure(text="▾ 更多输出选项")
            self._more_options.grid(row=10, column=0, sticky="ew")
        else:
            self._more_toggle.configure(text="▸ 更多输出选项")
            self._more_options.grid_remove()

    def show(self, before: tk.Misc | None = None) -> None:
        if before is not None:
            self.pack(fill=tk.BOTH, expand=True, before=before)
        else:
            self.pack(fill=tk.BOTH, expand=True)
        self._settings_view.yview_moveto(0)

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
            messagebox.showerror("任务设置错误", "请至少输入一个关键词或检索句", parent=self.root)
            return None
        try:
            max_pages = int(self._pages_var.get().strip())
        except ValueError:
            max_pages = 0
        if max_pages <= 0:
            messagebox.showerror("任务设置错误", "抓取页数必须是大于 0 的整数", parent=self.root)
            return None
        output_text = os.path.expanduser(os.path.expandvars(self._output_var.get().strip()))
        if not output_text:
            messagebox.showerror("任务设置错误", "请选择保存位置", parent=self.root)
            return None
        output_dir = Path(output_text).resolve()
        if output_dir.exists() and not output_dir.is_dir():
            messagebox.showerror("任务设置错误", "保存位置不是文件夹", parent=self.root)
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
            professional_queries=dict(getattr(self, "_professional_queries", {})),
        )

    def populate_resume(self, state: dict[str, Any]) -> None:
        self._search_options = SearchOptions.from_dict(state.get("search_options"))
        keywords = state.get("keywords", [])
        self._advanced_queries = load_advanced_queries(state.get("advanced_queries", {}), keywords)
        self._professional_queries = load_professional_queries(
            state.get("professional_queries", {}), keywords, self._advanced_queries,
        )
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
        self._update_scope_summary()
        self._update_keyword_summary()

    def _open_search_options(self) -> None:
        if self._running:
            return
        dialog = SearchOptionsDialog(self.root, self._search_options)
        self._search_options = dialog.result
        self._update_scope_summary()
        self._update_keyword_summary()

    def _update_scope_summary(self) -> None:
        if self._search_options is None:
            self._scope_summary_var.set("默认范围 · 沿用知网当前设置")
        else:
            self._scope_summary_var.set(self._search_options.summary())

    def _scroll_form(self, event: tk.Event) -> None:
        if not self.winfo_ismapped():
            return
        try:
            pointer = self.root.winfo_containing(*self.root.winfo_pointerxy())
        except KeyError:
            return
        if isinstance(pointer, (ttk.Spinbox, ttk.Combobox)):
            return
        view = None
        current = pointer
        while current is not None and current is not self:
            if current in {self._keyword_list.container, self._settings_view.container}:
                view = self._keyword_list if current is self._keyword_list.container else self._settings_view
                break
            current = getattr(current, "master", None)
        if view is None:
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
        view.yview_scroll(units, "units")

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
            messagebox.showwarning("重复检索项", f"“{keyword}”在任务列表中重复，请编辑或移除", parent=self.root)
            rows = [row for row in self._keyword_rows if row.keyword == keyword]
            self._focus_keyword(rows[-1])
            return None
        return merged.keywords

    def _set_keywords(self, keywords: list[str], status: str | None = None) -> None:
        self._advanced_queries = {key: query for key, query in self._advanced_queries.items() if key in keywords}
        self._professional_queries = {
            key: query for key, query in getattr(self, "_professional_queries", {}).items() if key in keywords
        }
        for row in self._keyword_rows:
            row.frame.destroy()
        self._keyword_rows.clear()
        for keyword in keywords or [""]:
            self._append_keyword_row(
                keyword,
                query_key=keyword if keyword in self._advanced_queries or keyword in self._professional_queries else None,
            )
        self._keyword_list.yview_moveto(0)
        self._sync_keyword_action_states()
        if status:
            self._keyword_status_var.set(status)

    def _append_keyword_row(self, keyword: str, *, query_key: str | None = None) -> _KeywordRow:
        frame = ttk.Frame(self._keyword_list, style="Card.TFrame")
        frame.pack(fill=tk.X, pady=(0, scale_size(self, 7)), padx=(0, scale_size(self, 4)))
        frame.columnconfigure(2, weight=1)
        number = ttk.Label(frame, width=3, anchor=tk.CENTER, style="CardMuted.TLabel")
        number.grid(row=0, column=0, padx=(0, scale_size(self, 4)))

        professional = query_key in getattr(self, "_professional_queries", {})
        queries = self._professional_queries if professional else self._advanced_queries
        badge_text = "专业" if professional else "高级" if query_key else "普通"
        badge_style = "BadgePurple.TLabel" if professional else "BadgeInfo.TLabel" if query_key else "BadgeNeutral.TLabel"
        badge = ttk.Label(frame, text=badge_text, style=badge_style, width=5)
        badge.grid(row=0, column=1, padx=(0, scale_size(self, 8)))

        display = queries[query_key].summary() if query_key else keyword
        if professional:
            display = f"{query_key}：{' '.join(display.splitlines())}"
        text = tk.StringVar(value=display)
        entry = ttk.Entry(frame, textvariable=text)
        entry.grid(row=0, column=2, columnspan=1 if query_key else 2, sticky="ew")
        delete = ttk.Button(frame, text="×", width=2, style="DangerGhost.TButton")
        delete.grid(row=0, column=4, padx=(scale_size(self, 4), 0))
        row = _KeywordRow(frame, number, badge, text, entry, delete, query_key=query_key)
        delete.configure(command=lambda: self._delete_keyword(row))
        ToolTip(delete, text="移除检索项")
        if query_key:
            edit = self._open_professional if professional else self._open_advanced
            row.edit_button = ttk.Button(
                frame, text="编辑", width=4, command=lambda: edit(row), style="GhostCompact.TButton",
            )
            row.edit_button.grid(row=0, column=3, padx=(scale_size(self, 4), 0))
            ToolTip(row.edit_button, text="编辑专业检索式" if professional else "编辑高级检索条件")
            entry.bind("<Double-1>", lambda _event: edit(row))
        else:
            entry.bind("<Return>", lambda _event: self._next_keyword(row))
            text.trace_add("write", lambda *_args: self._update_keyword_summary())
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
        self._update_keyword_summary()

    def _update_keyword_summary(self) -> None:
        count = len(self._keywords)
        self._count_badge_var.set(f"{count} 项")
        if count:
            self._keyword_hint_frame.pack_forget()
            try:
                pages = max(1, int(self._pages_var.get().strip()))
            except (ValueError, AttributeError):
                pages = 1
            include_citation = bool(getattr(self, "_citation_var", None) and self._citation_var.get())
            include_details = bool(
                (getattr(self, "_details_var", None) and self._details_var.get())
                or (getattr(self, "_txt_var", None) and self._txt_var.get())
            )
            page_size = self._search_options.page_size if self._search_options else 20
            low, high = estimate_seconds(
                pages, count,
                include_citation=include_citation,
                include_details=include_details,
                page_size=page_size,
            )
            self._keyword_status_var.set(
                f"当前任务：{count} 项 × {pages} 页 = {count * pages} 页 · 预计耗时 {format_eta(low, high)}"
            )
        else:
            self._keyword_status_var.set("当前任务：0 项 · 请输入至少一个检索项")
            if self._keyword_rows:
                self._keyword_hint_frame.pack(
                    fill=tk.X, pady=(scale_size(self, 12), 0), padx=(0, scale_size(self, 4)),
                    after=self._keyword_rows[-1].frame,
                )

    def _see_keyword(self, row: _KeywordRow) -> None:
        self._see_in_view(self._keyword_list, row.frame)

    @staticmethod
    def _see_in_view(view: ScrollView, widget: tk.Misc) -> None:
        view.update_idletasks()
        top = widget.winfo_rooty() - view.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = -view.winfo_y()
        visible_height = view.container.winfo_height()
        height = max(1, view.winfo_height(), visible_height)
        if top < visible_top:
            view.yview_moveto(top / height)
        elif bottom > visible_top + visible_height:
            view.yview_moveto((bottom - visible_height) / height)

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
            messagebox.showerror("任务列表已满", f"检索项不能超过 {MAX_KEYWORDS} 个", parent=self.root)
            return
        keyword = row.query_key if row is not None else None
        original = self._advanced_queries.get(keyword) if keyword is not None else None
        query = AdvancedSearchDialog(self.root, original).show()
        if query is None:
            return
        for existing, value in self._advanced_queries.items():
            if existing != keyword and value == query:
                messagebox.showwarning("重复检索项", "相同的高级检索条件已在当前任务中", parent=self.root)
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
            messagebox.showerror("任务列表已满", f"检索项不能超过 {MAX_KEYWORDS} 个", parent=self.root)
            return
        row = self._append_keyword_row("")
        self._sync_keyword_action_states()
        self._focus_keyword(row)

    def _open_professional(self, row: _KeywordRow | None = None) -> None:
        if self._running:
            return
        if row is None and len(self._keywords) >= MAX_KEYWORDS:
            messagebox.showerror("任务列表已满", f"检索项不能超过 {MAX_KEYWORDS} 个", parent=self.root)
            return
        previous = row.query_key if row else None
        name = previous
        if name is None:
            number = 1
            while f"专业检索 {number}" in self._keywords:
                number += 1
            name = f"专业检索 {number}"
        result = ProfessionalSearchDialog(
            self.root, name, self._professional_queries.get(previous), self._search_options,
        ).show()
        if result is None:
            return
        name, query = result
        if name != previous and name in self._keywords:
            messagebox.showwarning("重复检索项", "该名称已在当前任务中，请使用不同名称", parent=self.root)
            return
        if any(key != previous and value == query for key, value in self._professional_queries.items()):
            messagebox.showwarning("重复检索项", "相同的专业检索式已在当前任务中", parent=self.root)
            return
        if previous is not None:
            self._professional_queries.pop(previous)
        self._professional_queries[name] = query
        if row is None:
            row = self._append_keyword_row(name, query_key=name)
        else:
            row.query_key = name
            row.text.set(f"{name}：{' '.join(query.expression.splitlines())}")
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
            getattr(self, "_professional_queries", {}).pop(row.query_key, None)
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
                "当前任务中已有检索项\n\n选择“是”追加导入，选择“否”替换当前列表",
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
            self._professional_queries.clear()
        self._set_keywords(
            merged.keywords,
            f"已从 TXT 载入 {len(imported.keywords)} 项；当前任务：{len(merged.keywords)} 项{duplicate_text}",
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
        self._update_keyword_summary()

    def _txt_changed(self) -> None:
        if self._txt_var.get():
            self._details_var.set(True)
        self._update_keyword_summary()

    def _sync_option_states(self) -> None:
        state = tk.DISABLED if self._format_var.get() == "csv" or self._running else tk.NORMAL
        self._split_check.configure(state=state)
        if state == tk.DISABLED:
            self._split_var.set(False)
