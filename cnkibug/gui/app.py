from __future__ import annotations

import logging
import os
import time
import tkinter as tk
from base64 import b64encode
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryFile
from threading import Event, Thread
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from typing import Any

import ttkbootstrap as ttk
from PIL import Image

from ..app.runtime import cleanup_runtime_history, init_runtime, read_config
from ..cnki.models import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
)
from ..core.estimate import (
    LONG_TASK_WARNING_SECONDS,
    LONG_TASK_WARNING_TEXT,
    estimate_progress,
    estimate_seconds,
    estimate_work_seconds,
    format_eta,
)
from ..core.memory import MemorySampler, format_memory
from ..core.settings import ScraperSettings, get_scraper_settings
from ..core.search_query import AdvancedQuery, load_advanced_queries
from ..core.version import APP_VERSION
from ..fileio.keyword_input import (
    KeywordImportError,
    KeywordImportResult,
    dedupe_keywords,
    load_keywords_txt,
)
from ..fileio.paths import get_real_desktop_path, open_directory
from ..workflow.runner import scrape_cnki
from ..workflow.state import (
    delete_last_task,
    describe_task,
    get_last_task_path,
    load_last_task,
    remaining_workload,
)
from .events import GuiEvent, GuiEventSink
from .advanced import AdvancedSearchDialog, confirm_advanced_task
from .settings import SettingsDialog


_logger = logging.getLogger("cnkibug.gui")

_PREFERRED_WINDOW_WIDTH = 900
_PREFERRED_WINDOW_HEIGHT = 1219
_WINDOW_MARGIN = 80
_EVENTS_PER_DRAIN = 100
_MAX_LOG_LINES = 1000


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


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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


def _fit_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    width = min(_PREFERRED_WINDOW_WIDTH, max(1, screen_width - _WINDOW_MARGIN))
    height = min(_PREFERRED_WINDOW_HEIGHT, max(1, screen_height - _WINDOW_MARGIN))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y


def _prepare_output_directory(path: Path) -> Path:
    target = path.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    if not target.is_dir():
        raise NotADirectoryError(f"保存位置不是文件夹：{target}")
    with TemporaryFile(dir=target):
        pass
    return target


def _long_task_warning(high_seconds: int) -> str:
    if high_seconds <= LONG_TASK_WARNING_SECONDS:
        return ""
    return f"{LONG_TASK_WARNING_TEXT}\n\n"


class CNKIBugApp:
    def __init__(self, program_dir: Path, icon_path: Path | None = None) -> None:
        self.root = ttk.Window(
            title="CNKIBug",
            themename="litera",
            iconphoto=None,
        )
        width, height, x, y = _fit_window_geometry(
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(760, width), min(700, height))
        self._icon_image: tk.PhotoImage | None = None
        self._set_window_icon(icon_path)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        try:
            self.runtime = init_runtime(program_dir=program_dir, app_version=APP_VERSION)
        except OSError as error:
            messagebox.showerror("CNKIBug", f"无法创建运行数据目录：\n{error}", parent=self.root)
            self.root.destroy()
            raise SystemExit(1) from error
        self.settings = get_scraper_settings(self.runtime.config)
        self.root.style.theme_use(self.runtime.config["gui_theme"])

        self._event_queue: Queue[GuiEvent] = Queue()
        self._cancel_event = Event()
        self._events = GuiEventSink(self._event_queue, self._cancel_event)
        self._worker: Thread | None = None
        self._running = False
        self._close_when_done = False
        self._pending_confirms: list[Queue[bool]] = []

        self._task_started_at: float | None = None
        self._actual_seconds: float | None = None
        self._active_elapsed = 0.0
        self._active_started_at: float | None = None
        self._eta_low = 1
        self._eta_high = 2
        self._total_eta_low = 0
        self._total_eta_high = 0
        self._progress_mode = "idle"
        self._stopped_progress = 0
        self._memory_sampler = MemorySampler()
        self._progress_state: dict[str, Any] = {
            "keyword": "",
            "keyword_index": 0,
            "keyword_total": 0,
            "page": 0,
            "page_total": 0,
            "records": 0,
            "detail_index": 0,
            "detail_total": 0,
        }
        self._log_line_count = 0
        self._keywords: list[str] = []
        self._advanced_queries: dict[str, AdvancedQuery] = {}

        self._build_ui()
        self._apply_config(self.runtime.config)
        self._update_memory_status()
        self.root.after(100, self._drain_events)
        self.root.after(250, self._tick)
        self.root.after(200, self._offer_resume)

    # 窗口生命周期与固定界面结构。
    def _set_window_icon(self, icon_path: Path | None) -> None:
        if icon_path is None or not icon_path.is_file():
            return
        try:
            with Image.open(icon_path) as image:
                png_data = BytesIO()
                image.save(png_data, format="PNG")
            self._icon_image = tk.PhotoImage(
                data=b64encode(png_data.getvalue()),
                master=self.root,
            )
            self.root.iconphoto(True, self._icon_image)
        except (OSError, tk.TclError) as error:
            _logger.warning("GUI 图标加载失败: %s", error)
        if os.name == "nt":
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError as error:
                _logger.warning("Windows GUI 图标加载失败: %s", error)

    def run(self) -> None:
        self.root.mainloop()

    def _show_info(self) -> None:
        messagebox.showinfo(
            "关于 CNKIBug",
            (
                f"CNKIBug\n\n版本：v{APP_VERSION}\n\n免责声明\n\n"
                "CNKIBug 是独立开发的开源工具，与中国知网（CNKI）及其关联方不存在隶属、授权、合作或背书关系。\n\n"
                "请在遵守适用法律法规、CNKI 用户协议及所在机构规定的前提下使用，并自行确认访问和处理相关内容的权限。\n\n"
                "本软件按“现状”提供，不保证结果完整、准确或持续可用。请合理控制任务规模和访问频率，相关使用风险由使用者依法承担。\n\n"
                "软件会在本地保存配置、日志、任务状态和浏览器会话信息，请妥善保管。\n\n"
                "如您下载并使用本软件，即视为您已阅读、理解并同意本免责声明及 MIT License。"
            ),
            parent=self.root,
        )

    def _open_settings(self) -> None:
        if self._running or not self._form_view.winfo_ismapped():
            return
        SettingsDialog(
            self.root, self.runtime.config, self.runtime.paths.config_path, self._apply_config,
            on_restart=self.root.destroy,
        ).show()

    def _apply_config(self, config: dict[str, Any]) -> None:
        self.settings = get_scraper_settings(config)
        self.runtime = replace(self.runtime, config=config.copy())
        logging.getLogger().setLevel(config["log_level"])
        style = self.root.style
        if style.theme_use() != config["gui_theme"]:
            style.theme_use(config["gui_theme"])
        colors = style.colors
        self._form_canvas.configure(background=colors.bg)
        self._log.configure(
            background=colors.inputbg, foreground=colors.inputfg, insertbackground=colors.inputfg,
            selectbackground=colors.selectbg, selectforeground=colors.selectfg,
        )
        log_colors = (
            {"warning": "#a56a00", "error": "#b42318", "success": "#19713f"}
            if style.theme.type == "light"
            else {"warning": colors.warning, "error": colors.danger, "success": colors.success}
        )
        for tag, color in log_colors.items():
            self._log.tag_configure(tag, foreground=color)

    def _open_log_directory(self) -> None:
        try:
            open_directory(self.runtime.paths.log_dir)
        except OSError as error:
            messagebox.showerror(
                "打开日志文件夹失败",
                f"{error}\n\n日志路径：{self.runtime.paths.log_dir}",
                parent=self.root,
            )

    def _cleanup_logs_and_reports(self) -> None:
        if self._running:
            return
        if not messagebox.askyesno(
            "清理日志与报告",
            (
                "将永久删除历史日志和任务报告。\n\n"
                "本日运行文件将保留。\n"
                "配置、浏览器会话、断点和抓取结果不会受影响。\n\n"
                "此操作不可撤销，是否继续？"
            ),
            icon="warning",
            parent=self.root,
        ):
            return

        result = cleanup_runtime_history(self.runtime)
        if result.failed:
            messagebox.showwarning(
                "清理完成",
                f"已删除 {result.deleted} 个历史文件，{result.failed} 个文件无法删除。",
                parent=self.root,
            )
        else:
            message = (
                f"已删除 {result.deleted} 个历史文件。"
                if result.deleted
                else "没有需要清理的历史文件。"
            )
            messagebox.showinfo("清理完成", message, parent=self.root)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="CNKIBug", font=("TkDefaultFont", 20, "bold")).pack(
            side=tk.LEFT,
            anchor=tk.NW,
        )
        header_actions = ttk.Frame(header)
        header_actions.pack(side=tk.RIGHT)
        toolbar = ttk.Frame(header_actions)
        toolbar.pack(fill=tk.X)
        ttk.Button(
            toolbar,
            text="信息",
            command=self._show_info,
            bootstyle="secondary-outline",
        ).pack(side=tk.RIGHT)
        self._settings_button = ttk.Button(
            toolbar, text="设置", command=self._open_settings, bootstyle="secondary-outline",
        )
        self._settings_button.pack(side=tk.RIGHT, padx=(0, 8))
        self._maintenance_actions = ttk.Frame(header_actions)
        self._maintenance_actions.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(
            self._maintenance_actions,
            text="打开日志文件夹",
            command=self._open_log_directory,
            bootstyle="secondary-outline",
        ).pack(fill=tk.X)
        ttk.Button(
            self._maintenance_actions,
            text="清理日志与报告",
            command=self._cleanup_logs_and_reports,
            bootstyle="danger-outline",
        ).pack(fill=tk.X, pady=(6, 0))

        self._form_view = ttk.Frame(container)
        self._form_view.pack(fill=tk.BOTH, expand=True)
        self._form_canvas = tk.Canvas(
            self._form_view,
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=20,
        )
        self._form_scrollbar = ttk.Scrollbar(
            self._form_view,
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

        keyword_frame = ttk.Labelframe(self._form, text="检索内容", padding=10)
        keyword_frame.pack(fill=tk.X, pady=(0, 10))

        advanced_row = ttk.Frame(keyword_frame)
        advanced_row.pack(fill=tk.X, pady=(0, 10))
        self._advanced_button = ttk.Button(
            advanced_row, text="高级检索", command=self._open_advanced,
            bootstyle="secondary-outline",
        )
        self._advanced_button.pack(side=tk.LEFT)
        ttk.Label(advanced_row, text="实验性", bootstyle="warning").pack(side=tk.LEFT, padx=8)

        entry_row = ttk.Frame(keyword_frame)
        entry_row.pack(fill=tk.X)
        entry_row.columnconfigure(0, weight=1)
        self._keyword_var = tk.StringVar()
        self._keyword_entry = ttk.Entry(
            entry_row,
            textvariable=self._keyword_var,
        )
        self._keyword_entry.grid(row=0, column=0, sticky="ew")
        self._keyword_entry.bind("<Return>", lambda _event: self._add_keyword())
        self._add_keyword_button = ttk.Button(
            entry_row,
            text="添加",
            command=self._add_keyword,
            bootstyle="primary",
        )
        self._add_keyword_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            keyword_frame,
            text="输入一个关键词或完整检索句；同一检索项内可用空格组合多个词(请在上方键入并添加)",
            bootstyle="secondary",
        ).pack(anchor=tk.W, pady=(6, 8))

        list_frame = ttk.Frame(keyword_frame)
        list_frame.pack(fill=tk.X)
        self._keyword_list = ttk.Treeview(
            list_frame,
            columns=("number", "type", "keyword"),
            show="headings",
            height=6,
            selectmode="browse",
        )
        self._keyword_list.heading("number", text="#")
        self._keyword_list.heading("type", text="类型")
        self._keyword_list.heading("keyword", text="当前任务检索项")
        self._keyword_list.column("number", width=48, minwidth=48, stretch=False, anchor=tk.CENTER)
        self._keyword_list.column("type", width=92, minwidth=92, stretch=False, anchor=tk.CENTER)
        self._keyword_list.column("keyword", minwidth=300, anchor=tk.W)
        self._keyword_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._keyword_list.bind("<<TreeviewSelect>>", self._keyword_selected)
        self._keyword_list.bind("<Double-1>", self._edit_selected_item)
        keyword_scrollbar = ttk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=self._keyword_list.yview,
        )
        keyword_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._keyword_list.configure(yscrollcommand=keyword_scrollbar.set)

        keyword_actions = ttk.Frame(keyword_frame)
        keyword_actions.pack(fill=tk.X, pady=(8, 0))
        self._modify_keyword_button = ttk.Button(
            keyword_actions,
            text="修改选中项",
            command=self._modify_keyword,
            state=tk.DISABLED,
            bootstyle="secondary",
        )
        self._modify_keyword_button.pack(side=tk.LEFT)
        self._delete_keyword_button = ttk.Button(
            keyword_actions,
            text="删除选中项",
            command=self._delete_keyword,
            state=tk.DISABLED,
            bootstyle="danger-outline",
        )
        self._delete_keyword_button.pack(side=tk.LEFT, padx=(8, 0))
        self._import_button = ttk.Button(
            keyword_actions,
            text="批量导入 TXT",
            command=self._import_txt,
            bootstyle="secondary",
        )
        self._import_button.pack(side=tk.RIGHT)
        self._keyword_status_var = tk.StringVar(value="当前任务：0 项")
        ttk.Label(
            keyword_frame,
            textvariable=self._keyword_status_var,
            bootstyle="secondary",
        ).pack(anchor=tk.W, pady=(6, 0))

        settings_row = ttk.Frame(self._form)
        settings_row.pack(fill=tk.X, pady=(0, 10))
        settings_row.columnconfigure(0, weight=1)
        settings_row.columnconfigure(1, weight=1)

        scope = ttk.Labelframe(settings_row, text="任务范围", padding=10)
        scope.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ttk.Label(scope, text="每个检索项抓取页数").grid(row=0, column=0, sticky=tk.W)
        self._pages_var = tk.StringVar(value="1")
        self._pages_entry = ttk.Entry(scope, textvariable=self._pages_var, width=10)
        self._pages_entry.grid(row=1, column=0, sticky=tk.W, pady=(3, 10))
        ttk.Label(scope, text="保存位置").grid(row=2, column=0, sticky=tk.W)
        output_row = ttk.Frame(scope)
        output_row.grid(row=3, column=0, sticky="ew", pady=(3, 0))
        output_row.columnconfigure(0, weight=1)
        self._output_var = tk.StringVar(value=get_real_desktop_path())
        self._output_entry = ttk.Entry(output_row, textvariable=self._output_var)
        self._output_entry.grid(row=0, column=0, sticky="ew")
        self._browse_button = ttk.Button(
            output_row,
            text="浏览",
            command=self._choose_output_dir,
            bootstyle="secondary",
        )
        self._browse_button.grid(row=0, column=1, padx=(6, 0))

        result_frame = ttk.Labelframe(settings_row, text="结果格式", padding=10)
        result_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self._format_var = tk.StringVar(value="excel")
        self._excel_radio = ttk.Radiobutton(
            result_frame,
            text="Excel",
            variable=self._format_var,
            value="excel",
            command=self._sync_option_states,
        )
        self._excel_radio.pack(anchor=tk.W, pady=2)
        self._csv_radio = ttk.Radiobutton(
            result_frame,
            text="CSV",
            variable=self._format_var,
            value="csv",
            command=self._sync_option_states,
        )
        self._csv_radio.pack(anchor=tk.W, pady=2)
        self._split_var = tk.BooleanVar(value=False)
        self._split_check = ttk.Checkbutton(
            result_frame,
            text="多个检索项分别保存为独立 Excel 文件",
            variable=self._split_var,
        )
        self._split_check.pack(anchor=tk.W, pady=(8, 2))

        extras = ttk.Labelframe(self._form, text="附加内容", padding=10)
        extras.pack(fill=tk.X, pady=(0, 10))
        self._citation_var = tk.BooleanVar(value=False)
        self._details_var = tk.BooleanVar(value=False)
        self._txt_var = tk.BooleanVar(value=False)
        self._citation_check = ttk.Checkbutton(
            extras,
            text="获取 GB/T 7714 引用格式",
            variable=self._citation_var,
        )
        self._citation_check.pack(anchor=tk.W, pady=2)
        self._details_check = ttk.Checkbutton(
            extras,
            text="获取论文关键词和摘要",
            variable=self._details_var,
            command=self._details_changed,
        )
        self._details_check.pack(anchor=tk.W, pady=2)
        self._txt_check = ttk.Checkbutton(
            extras,
            text="导出抓取到的论文关键词 TXT",
            variable=self._txt_var,
            command=self._txt_changed,
        )
        self._txt_check.pack(anchor=tk.W, padx=(24, 0), pady=2)

        action_row = ttk.Frame(self._form)
        action_row.pack(fill=tk.X, pady=(0, 10))
        self._review_button = ttk.Button(
            action_row,
            text="检查任务并继续",
            command=self._review_task,
            bootstyle="primary",
        )
        self._review_button.pack(side=tk.RIGHT)

        self._progress_frame = ttk.Labelframe(container, text="任务状态", padding=10)
        progress_frame = self._progress_frame
        self._status_var = tk.StringVar(value="等待设置任务")
        ttk.Label(progress_frame, textvariable=self._status_var, font=("TkDefaultFont", 11, "bold")).pack(anchor=tk.W)
        self._progress_var = tk.IntVar(value=0)
        progress_row = ttk.Frame(progress_frame)
        progress_row.pack(fill=tk.X, pady=(8, 2))
        progress_row.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(
            progress_row,
            variable=self._progress_var,
            maximum=100,
            bootstyle="info-striped",
        )
        self._progress.grid(row=0, column=0, sticky="ew")
        self._progress_percent_var = tk.StringVar(value="0%")
        ttk.Label(progress_row, textvariable=self._progress_percent_var, width=4).grid(
            row=0,
            column=1,
            sticky=tk.E,
            padx=(8, 0),
        )

        time_row = ttk.Frame(progress_frame)
        time_row.pack(fill=tk.X)
        time_row.columnconfigure(0, weight=1)
        time_row.columnconfigure(1, weight=1)
        self._time_var = tk.StringVar(value="已用时：00:00")
        ttk.Label(time_row, textvariable=self._time_var).grid(row=0, column=0, sticky=tk.W)
        self._total_eta_var = tk.StringVar(value="预计总耗时：--")
        ttk.Label(
            time_row,
            textvariable=self._total_eta_var,
            justify=tk.RIGHT,
            wraplength=340,
        ).grid(row=0, column=1, sticky=tk.E)
        self._detail_var = tk.StringVar(value="尚未开始")
        ttk.Label(progress_frame, textvariable=self._detail_var, bootstyle="secondary").pack(anchor=tk.W, pady=(2, 6))

        self._log = ScrolledText(progress_frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
        self._log.pack(fill=tk.BOTH, expand=True)

        stop_row = ttk.Frame(progress_frame)
        stop_row.pack(fill=tk.X, pady=(8, 0))
        self._new_task_button = ttk.Button(
            stop_row,
            text="返回任务设置",
            command=self._show_form,
            state=tk.DISABLED,
            bootstyle="secondary",
        )
        self._new_task_button.pack(side=tk.LEFT)
        self._stop_button = ttk.Button(
            stop_row,
            text="安全停止",
            command=self._request_stop,
            state=tk.DISABLED,
            bootstyle="danger-outline",
        )
        self._stop_button.pack(side=tk.RIGHT)

        self._footer = ttk.Frame(container)
        self._footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self._memory_var = tk.StringVar(value="内存：正在读取")
        ttk.Label(self._footer, textvariable=self._memory_var, bootstyle="secondary").pack(side=tk.RIGHT)

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

    def _update_form_scrollregion(self, _event: tk.Event | None = None) -> None:
        bounds = self._form_canvas.bbox("all")
        if bounds is not None:
            self._form_canvas.configure(scrollregion=bounds)

    def _resize_form_width(self, event: tk.Event) -> None:
        self._form_canvas.itemconfigure(self._form_window, width=event.width)

    def _scroll_form(self, event: tk.Event) -> None:
        if not self._form_view.winfo_ismapped():
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
            self._form_view,
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

    # 从表单生成任务请求，预览、恢复和启动流程都以同一请求模型为边界。
    def _review_task(self) -> None:
        request = self._collect_request()
        if request is None:
            return
        low, high = estimate_seconds(
            request.max_pages,
            len(request.keywords),
            include_citation=request.include_citation,
            include_details=request.include_details,
        )
        preview = "、".join(request.keywords[:8])
        if len(request.keywords) > 8:
            preview += f" 等 {len(request.keywords)} 项"
        output_text = {
            "single": "一个 Excel 文件",
            "single_csv": "一个 CSV 文件",
            "multi_split": f"{len(request.keywords)} 个独立 Excel 文件",
            "multi_merge": "一个 Excel 文件，每个检索项一个 Sheet",
            "multi_csv": "一个 CSV 文件，包含 keyword 列",
        }[request.save_mode]
        extras = []
        if request.include_citation:
            extras.append("GB/T 7714 引用")
        if request.include_details:
            extras.append("论文关键词和摘要")
        if request.detail_txt_export:
            extras.append("论文关键词 TXT")
        summary = (
            f"检索内容：{preview}\n"
            f"任务规模：{len(request.keywords)} 项 × {request.max_pages} 页"
            f" = {len(request.keywords) * request.max_pages} 页\n"
            f"结果文件：{output_text}\n"
            f"附加内容：{'、'.join(extras) if extras else '无'}\n"
            f"预计耗时：{format_eta(low, high)}\n"
            f"保存位置：{request.output_dir}\n\n"
        )
        summary += _long_task_warning(high)
        summary += "确认无误后才会启动浏览器和抓取任务。"
        confirmed = (
            confirm_advanced_task(self.root, summary, request.advanced_queries)
            if request.advanced_queries
            else messagebox.askokcancel("开始前确认", summary, parent=self.root)
        )
        if confirmed:
            self._start_task(request=request)

    def _collect_request(self) -> GuiTaskRequest | None:
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
        )

    def _offer_resume(self) -> None:
        state = load_last_task(self.runtime.paths)
        last_task_path = get_last_task_path(self.runtime.paths)
        if state is None:
            if last_task_path.exists():
                if delete_last_task(self.runtime.paths) or not last_task_path.exists():
                    messagebox.showwarning(
                        "未完成任务",
                        "检测到损坏的任务缓存，已删除。",
                        parent=self.root,
                    )
                else:
                    messagebox.showerror(
                        "断点删除失败",
                        f"损坏的断点文件无法删除，请手动删除后重新启动：\n{last_task_path}",
                        parent=self.root,
                    )
                    self.root.destroy()
            return
        while True:
            remaining_pages, pending_keywords = remaining_workload(
                state,
                [str(item) for item in state.get("keywords", [])],
                int(state.get("max_pages", 1)),
            )
            _, remaining_high = estimate_work_seconds(
                remaining_pages,
                pending_keywords,
                include_citation=bool(state.get("include_citation", False)),
                include_details=bool(state.get("include_details", False)),
            )
            choice = messagebox.askyesnocancel(
                "继续或忽略断点",
                (
                    f"{describe_task(state)}\n\n"
                    f"{_long_task_warning(remaining_high)}"
                    "选择“是”：继续上次任务。\n"
                    "选择“否”：忽略断点，删除断点文件并新建任务。\n"
                    "选择“取消”：保留断点并退出程序。"
                ),
                parent=self.root,
            )
            if choice is None:
                self.root.destroy()
                return
            if choice:
                self._populate_resume_form(state)
                self._start_task(resume_state=state)
                return
            if delete_last_task(self.runtime.paths) or not last_task_path.exists():
                self._append_log("已忽略并删除上次任务断点。", "warning")
                return
            messagebox.showerror(
                "断点删除失败",
                f"断点文件仍然存在，尚未忽略该任务：\n{last_task_path}",
                parent=self.root,
            )

    def _populate_resume_form(self, state: dict[str, Any]) -> None:
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
        output_dir = state.get("output_dir")
        if isinstance(output_dir, str) and output_dir:
            self._output_var.set(output_dir)
        self._sync_option_states()

    def _start_task(
        self,
        request: GuiTaskRequest | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        if self._running:
            return
        if resume_state is not None:
            stored_output_dir = resume_state.get("output_dir")
            request = GuiTaskRequest(
                keywords=list(resume_state["keywords"]),
                max_pages=int(resume_state["max_pages"]),
                save_mode=str(resume_state["save_mode"]),
                include_citation=bool(resume_state.get("include_citation", False)),
                include_details=bool(resume_state.get("include_details", False)),
                detail_txt_export=bool(resume_state.get("detail_txt_export", False)),
                output_dir=(
                    Path(stored_output_dir)
                    if isinstance(stored_output_dir, str) and stored_output_dir.strip()
                    else None
                ),
                advanced_queries=load_advanced_queries(resume_state.get("advanced_queries", {}), resume_state["keywords"]),
            )
        assert request is not None

        output_dir = request.output_dir or Path(get_real_desktop_path())
        try:
            output_dir = _prepare_output_directory(output_dir)
        except OSError as error:
            messagebox.showerror(
                "保存位置不可用",
                f"无法创建或写入保存位置：\n{output_dir}\n\n{error}",
                parent=self.root,
            )
            return
        request = replace(request, output_dir=output_dir)

        try:
            config = read_config(self.runtime.paths.config_path)
        except (OSError, ValueError) as error:
            messagebox.showerror("无法读取配置", str(error), parent=self.root)
            return
        self._apply_config(config)
        task_settings = self.settings
        self._cancel_event.clear()
        self._set_running(True)
        self._reset_progress()
        self._set_total_eta(request, resume_state)
        self._memory_sampler.reset()
        self._update_memory_status()
        self._clear_log()
        self._form_view.pack_forget()
        self._settings_button.pack_forget()
        self._progress_frame.pack(fill=tk.BOTH, expand=True)
        self._new_task_button.configure(state=tk.DISABLED)

        # 抓取在线程中运行；工作线程只投递事件，所有 Tk 控件仍由主线程更新。
        def worker() -> None:
            try:
                scrape_cnki(
                    request.keywords,
                    request.max_pages,
                    request.save_mode,
                    resume_state=resume_state,
                    include_citation=request.include_citation,
                    include_details=request.include_details,
                    detail_txt_export=request.detail_txt_export,
                    settings=task_settings,
                    paths=self.runtime.paths,
                    events=self._events,
                    output_dir=request.output_dir,
                    cancel_event=self._cancel_event,
                    **({"advanced_queries": request.advanced_queries} if request.advanced_queries else {}),
                )
            except Exception as error:
                _logger.exception("GUI 任务线程异常")
                self._event_queue.put(GuiEvent("worker_failed", {"error": str(error)}))
            finally:
                self._event_queue.put(GuiEvent("worker_done", {}))

        self._worker = Thread(target=worker, name="cnkibug-worker", daemon=True)
        self._worker.start()

    # 运行状态集中控制表单、停止按钮和格式相关控件，避免各事件分支各自切换。
    def _set_running(self, running: bool) -> None:
        self._running = running
        for control in self._form_controls:
            control.configure(state=tk.DISABLED if running else tk.NORMAL)
        self._stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            self._maintenance_actions.pack_forget()
        else:
            self._maintenance_actions.pack(fill=tk.X, pady=(6, 0))
        self._sync_keyword_action_states()
        if not running:
            self._sync_option_states()

    def _reset_progress(self) -> None:
        self._task_started_at = None
        self._actual_seconds = None
        self._active_elapsed = 0.0
        self._active_started_at = None
        self._progress_mode = "idle"
        self._stopped_progress = 0
        self._progress_var.set(0)
        self._progress_percent_var.set("0%")
        self._time_var.set("已用时：00:00")
        self._total_eta_low = 0
        self._total_eta_high = 0
        self._total_eta_var.set("预计总耗时：--")
        self._status_var.set("正在准备任务")
        self._detail_var.set("等待启动浏览器")
        for key in self._progress_state:
            self._progress_state[key] = "" if key == "keyword" else 0

    # Tk 控件只能在主线程修改，因此定时排空工作线程事件队列。
    def _drain_events(self) -> None:
        for _ in range(_EVENTS_PER_DRAIN):
            try:
                event = self._event_queue.get_nowait()
            except Empty:
                break
            self._handle_event(event)
        try:
            if self.root.winfo_exists():
                self.root.after(100, self._drain_events)
        except tk.TclError:
            return

    # 将后端事件投影为界面状态；这里是 GUI 状态转换的唯一入口。
    def _handle_event(self, event: GuiEvent) -> None:
        name = event.name
        payload = event.payload
        if name == "task_started":
            self._task_started_at = time.monotonic()
            self._status_var.set("正在启动浏览器")
        elif name == "message":
            self._append_log(str(payload.get("text", "")), str(payload.get("level", "")))
        elif name == "activity_started":
            self._status_var.set(str(payload.get("message", "正在处理")))
        elif name == "browser_edge_failed":
            self._append_log("Edge 启动失败，正在尝试备用 Chromium。", "warning")
        elif name == "browser_launched":
            browser = "Microsoft Edge" if payload.get("channel") == "msedge" else "备用 Chromium"
            self._append_log(f"已启动 {browser}。", "success")
        elif name == "browser_ready":
            self._status_var.set("浏览器已就绪")
            self._append_log("浏览器已打开；遇到滑块时请在浏览器窗口手动完成。")
        elif name == "browser_launch_failed":
            error = str(payload.get("error", "未知错误"))
            self._status_var.set("浏览器启动失败")
            self._append_log(f"浏览器启动失败：{error}", "error")
            messagebox.showerror("浏览器启动失败", error, parent=self.root)
        elif name == "verify_required":
            self._status_var.set("等待手动完成安全验证")
            messagebox.showwarning(
                "需要手动验证",
                "请切换到浏览器窗口完成知网滑块或安全验证。\n验证通过后程序会自动继续。",
                parent=self.root,
            )
        elif name == "verify_waiting":
            self._status_var.set(f"等待安全验证，剩余约 {payload.get('remaining', 0)} 秒")
        elif name == "verify_timeout":
            self._append_log("等待安全验证超时，将保存当前结果。", "warning")
        elif name == "verify_passed":
            self._append_log("安全验证已通过，继续抓取。", "success")
        elif name == "page_debug":
            self._append_log(f"页面异常：{payload.get('context', '未记录上下文')}", "warning")
            self._append_log(f"当前 URL：{payload.get('url', '<无法读取>')}")
            self._append_log(f"页面标题：{payload.get('title', '<无法读取>')}")
        elif name == "progress_started":
            self._eta_low = int(payload["low_seconds"])
            self._eta_high = int(payload["high_seconds"])
            self._progress_mode = "running"
            self._active_started_at = time.monotonic()
            self._status_var.set("预计进度")
        elif name == "progress_updated":
            self._progress_state.update(payload)
            self._update_detail_text()
        elif name == "progress_paused":
            self._freeze_active()
            self._progress_mode = "paused"
            self._status_var.set("等待手动验证，预计进度已暂停")
        elif name == "progress_resumed":
            self._progress_mode = "running"
            self._active_started_at = time.monotonic()
            self._status_var.set("预计进度")
        elif name == "progress_saving":
            self._freeze_active()
            self._progress_mode = "saving"
            self._progress_var.set(99)
            self._progress_percent_var.set("99%")
            self._status_var.set("正在保存结果")
        elif name == "progress_completed":
            self._freeze_active()
            self._progress_mode = "completed"
            self._progress_var.set(100)
            self._progress_percent_var.set("100%")
            self._status_var.set("任务已完成")
            self._set_keywords([])
            self._reset_keyword_editor()
        elif name == "progress_stopped":
            self._stopped_progress = self._current_percentage()
            self._freeze_active()
            self._progress_mode = "stopped"
            self._progress_var.set(self._stopped_progress)
            self._progress_percent_var.set(f"{self._stopped_progress}%")
            self._status_var.set(str(payload.get("message", "任务已停止")))
        elif name == "task_finished":
            self._actual_seconds = max(0.0, float(payload.get("elapsed_seconds", 0.0)))
            self._time_var.set(f"实际用时：{_format_duration(self._actual_seconds)}")
        elif name == "task_report":
            report = payload["report"]
            total_records = sum(
                len(records)
                for records in payload.get("all_results", {}).values()
            )
            success = report.count_status(STATUS_SUCCESS)
            empty = report.count_status(STATUS_EMPTY)
            failed = report.count_status(STATUS_FAILED)
            stopped = report.count_status(STATUS_STOPPED)
            level = "warning" if failed or stopped else "success"
            self._append_log(
                f"本轮摘要：成功 {success}，无结果 {empty}，失败 {failed}，"
                f"中止 {stopped}，共 {total_records} 条。",
                level,
            )
            for item in report.failed_items():
                reason = item.reason or "未记录原因"
                self._append_log(
                    f"第 {item.index}/{item.total} 个检索项「{item.keyword}」：{reason}",
                    "error" if item.status == STATUS_FAILED else "warning",
                )
        elif name == "export_finished":
            result = payload["result"]
            if result.failed:
                self._append_log(
                    f"本轮有 {result.failed} 个结果文件未能成功保存。",
                    "error",
                )
            if result.keyword_txt_failed:
                self._append_log("关键词 TXT 未能成功保存，详情见日志。", "error")
            for path in result.saved_paths:
                self._append_log(f"已保存：{path}", "success")
            if result.keyword_txt_path:
                self._append_log(f"关键词 TXT 已保存：{result.keyword_txt_path}", "success")
        elif name == "confirm_requested":
            response_queue = payload["response_queue"]
            if self._close_when_done:
                response_queue.put(False)
                return
            self._pending_confirms.append(response_queue)
            answer = messagebox.askyesno(
                "请确认",
                str(payload.get("prompt", "是否继续？")),
                default="yes" if payload.get("default") else "no",
                parent=self.root,
            )
            response_queue.put(answer)
            self._pending_confirms.remove(response_queue)
        elif name == "worker_failed":
            error = str(payload.get("error", "未知错误"))
            self._status_var.set("任务异常结束")
            self._append_log(f"任务异常结束：{error}", "error")
            messagebox.showerror("任务异常结束", error, parent=self.root)
        elif name == "worker_done":
            if self._actual_seconds is None and self._task_started_at is not None:
                self._actual_seconds = time.monotonic() - self._task_started_at
                self._time_var.set(f"实际用时：{_format_duration(self._actual_seconds)}")
            self._set_running(False)
            self._new_task_button.configure(state=tk.NORMAL)
            if self._close_when_done:
                self.root.destroy()

    def _show_form(self) -> None:
        if self._running:
            return
        self._progress_frame.pack_forget()
        self._form_view.pack(fill=tk.BOTH, expand=True, before=self._footer)
        self._form_canvas.yview_moveto(0)
        self._settings_button.pack(side=tk.RIGHT, padx=(0, 8))

    def _tick(self) -> None:
        now = time.monotonic()
        if self._task_started_at is not None and self._actual_seconds is None:
            self._time_var.set(f"已用时：{_format_duration(now - self._task_started_at)}")
        if self._progress_mode in {"running", "paused"}:
            percentage = self._current_percentage(now)
            self._progress_var.set(percentage)
            self._progress_percent_var.set(f"{percentage}%")
        self._update_memory_status()
        try:
            if self.root.winfo_exists():
                self.root.after(250, self._tick)
        except tk.TclError:
            return

    # 只累计真实抓取时间，安全验证暂停时间不进入预计进度。
    def _active_seconds(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        if self._progress_mode == "running" and self._active_started_at is not None:
            return self._active_elapsed + max(0.0, current - self._active_started_at)
        return self._active_elapsed

    def _freeze_active(self) -> None:
        if self._progress_mode == "running" and self._active_started_at is not None:
            self._active_elapsed += max(0.0, time.monotonic() - self._active_started_at)
            self._active_started_at = None

    def _current_percentage(self, now: float | None = None) -> int:
        if self._progress_mode == "completed":
            return 100
        if self._progress_mode == "saving":
            return 99
        if self._progress_mode == "stopped":
            return self._stopped_progress
        return estimate_progress(
            self._active_seconds(now),
            self._eta_low,
            self._eta_high,
        )

    def _set_total_eta(
        self,
        request: GuiTaskRequest,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        if resume_state is None:
            self._total_eta_low, self._total_eta_high = estimate_seconds(
                request.max_pages,
                len(request.keywords),
                include_citation=request.include_citation,
                include_details=request.include_details,
            )
        else:
            remaining_pages, pending_keywords = remaining_workload(
                resume_state,
                request.keywords,
                request.max_pages,
            )
            self._total_eta_low, self._total_eta_high = estimate_work_seconds(
                remaining_pages,
                pending_keywords,
                include_citation=request.include_citation,
                include_details=request.include_details,
            )
        self._total_eta_var.set(
            f"预计总耗时：{format_eta(self._total_eta_low, self._total_eta_high, compact=True)}"
        )

    def _update_memory_status(self) -> None:
        self._memory_var.set(format_memory(self._memory_sampler.sample()))

    def _update_detail_text(self) -> None:
        parts = []
        keyword = str(self._progress_state.get("keyword", ""))
        if keyword:
            parts.append(
                f"当前检索：{keyword} "
                f"({self._progress_state.get('keyword_index', 0)}/"
                f"{self._progress_state.get('keyword_total', 0)})"
            )
        if self._progress_state.get("page_total"):
            parts.append(
                f"第 {self._progress_state.get('page', 0)}/"
                f"{self._progress_state.get('page_total', 0)} 页"
            )
        if self._progress_state.get("detail_total"):
            parts.append(
                f"详情 {self._progress_state.get('detail_index', 0)}/"
                f"{self._progress_state.get('detail_total', 0)}"
            )
        parts.append(f"已获取 {self._progress_state.get('records', 0)} 条")
        self._detail_var.set("  |  ".join(parts))

    def _append_log(self, text: str, level: str = "") -> None:
        line = text.rstrip() + "\n"
        added_lines = line.count("\n")
        line_count = getattr(self, "_log_line_count", 0) + added_lines
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, line, level if level in {"warning", "error", "success"} else "")
        overflow = max(line_count - _MAX_LOG_LINES, 0)
        if overflow:
            self._log.delete("1.0", f"{overflow + 1}.0")
            line_count -= overflow
        self._log_line_count = line_count
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)
        self._log_line_count = 0

    # 停止和关闭都先通知工作线程收尾，避免丢失结果或留下浏览器进程。
    def _request_stop(self) -> None:
        if not self._running or self._cancel_event.is_set():
            return
        if messagebox.askyesno(
            "安全停止",
            "停止后会保存已完成页面和断点，是否继续？",
            parent=self.root,
        ):
            self._cancel_event.set()
            self._stop_button.configure(state=tk.DISABLED)
            self._status_var.set("正在安全停止并保存结果")
            self._append_log("已请求安全停止，请等待当前操作结束。", "warning")

    def _on_close(self) -> None:
        if not messagebox.askyesno(
            "退出 CNKIBug",
            "任务仍在运行。退出前将安全停止并保存当前结果，是否继续？"
            if self._running else "确定要退出 CNKIBug 吗？",
            parent=self.root,
        ):
            return
        if not self._running:
            self.root.destroy()
            return
        self._close_when_done = True
        self._cancel_event.set()
        self._stop_button.configure(state=tk.DISABLED)
        self._status_var.set("正在安全停止，完成后关闭窗口")
        for response_queue in list(self._pending_confirms):
            if response_queue.empty():
                response_queue.put(False)


def main(program_dir: Path, icon_path: Path | None = None) -> None:
    CNKIBugApp(program_dir, icon_path).run()
