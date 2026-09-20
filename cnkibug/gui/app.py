from __future__ import annotations

import logging
import os
import tkinter as tk
from base64 import b64encode
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryFile
from threading import Event, Thread
from tkinter import messagebox
from typing import Any

import ttkbootstrap as ttk
from PIL import Image
from ttkbootstrap.dialogs import Messagebox

from ..app.runtime import cleanup_runtime_history, init_runtime, read_config, save_config
from ..core.runtime import appimage_path
from ..cnki.models import Paper, papers_from_results
from ..core.estimate import (
    LONG_TASK_WARNING_SECONDS,
    LONG_TASK_WARNING_TEXT,
    estimate_seconds,
    estimate_work_seconds,
    format_eta,
)
from ..core.memory import MemorySampler, format_memory
from ..core.settings import get_scraper_settings
from ..core.search_query import SearchOptions, load_advanced_queries
from ..core.version import APP_VERSION
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
from .advanced import confirm_advanced_task
from .settings import SettingsDialog
from .task_form import GuiTaskRequest, TaskForm
from .task_progress import TaskProgress


_logger = logging.getLogger("cnkibug.gui")

_PREFERRED_WINDOW_WIDTH = 900
_PREFERRED_WINDOW_HEIGHT = 1219
_WINDOW_MARGIN = 80
_EVENTS_PER_DRAIN = 100


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

        self._memory_sampler = MemorySampler()
        self._current_results: list[Paper] = []
        self._results_window = None
        self._active_include_citation = False
        self._active_output_dir: Path | None = None
        self._result_prompt_pending = False

        self._build_ui()
        self._apply_config(self.runtime.config)
        self._update_memory_status()
        self.root.after(100, self._drain_events)
        self.root.after(250, self._tick)
        self.root.after(200, self._finish_startup)

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

    def _finish_startup(self) -> None:
        from .updater import cleanup_completed_updates

        cleanup_completed_updates(self.runtime.paths.data_dir)
        if appimage_path() and not self.runtime.config.get("linux_setup_completed", False):
            from .environment import InitializationDialog

            InitializationDialog(self.root, self.runtime.config, self.runtime.paths.config_path, self._apply_config).show()
        try:
            if self.root.winfo_exists():
                self._offer_resume()
        except tk.TclError:
            pass

    def _ensure_browser_ready(self) -> bool:
        if not appimage_path():
            return True
        from ..browser.environment import browser_installed

        try:
            if browser_installed():
                return True
        except Exception as error:
            _logger.warning("浏览器准备检查失败: %s", error)
        messagebox.showinfo("浏览器尚未准备好", "请在设置的运行环境中检查或安装浏览器，然后继续。", parent=self.root)
        self._open_settings(selected_tab="运行环境")
        try:
            return browser_installed()
        except Exception:
            return False

    def _open_settings(self, *, selected_tab: str | None = None) -> None:
        if self._running or self._downloads_running() or (selected_tab is None and not self._task_form.winfo_ismapped()):
            return
        SettingsDialog(
            self.root, self.runtime.config, self.runtime.paths.config_path, self._apply_config,
            on_restart=self.root.destroy,
            get_output_dir=lambda: self._task_form.output_dir,
            can_check_environment=lambda: not (self._running or self._downloads_running()),
            selected_tab=selected_tab,
        ).show()

    def _apply_config(self, config: dict[str, Any]) -> None:
        if config.get("output_dir") and config.get("output_dir") != self.runtime.config.get("output_dir"):
            self._task_form.output_dir = config["output_dir"]
        self.settings = get_scraper_settings(config)
        self.runtime = replace(self.runtime, config=config.copy())
        logging.getLogger().setLevel(config["log_level"])
        style = self.root.style
        if style.theme_use() != config["gui_theme"]:
            style.theme_use(config["gui_theme"])
        self._task_form.apply_theme(style)
        self._task_progress.apply_theme(style)
        viewer = getattr(self, "_results_window", None)
        if viewer is not None:
            viewer.settings = self.settings

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
        ttk.Button(toolbar, text="论文结果", command=self._show_results, bootstyle="primary").pack(side=tk.RIGHT, padx=(0, 8))
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

        self._task_form = TaskForm(
            container,
            output_dir=self.runtime.config.get("output_dir", ""),
            on_review=self._review_task,
        )
        self._task_form.pack(fill=tk.BOTH, expand=True)
        self._task_progress = TaskProgress(
            container, on_stop=self._request_stop, on_new_task=self._show_form,
        )
        self._footer = ttk.Frame(container)
        self._footer.pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 0))
        self._memory_var = tk.StringVar(value="内存：正在读取")
        ttk.Label(self._footer, textvariable=self._memory_var, bootstyle="secondary").pack(side=tk.RIGHT)

    # 从表单生成任务请求，预览、恢复和启动流程都以同一请求模型为边界。
    def _review_task(self) -> None:
        request = self._task_form.collect_request()
        if request is None:
            return
        low, high = estimate_seconds(
            request.max_pages,
            len(request.keywords),
            include_citation=request.include_citation,
            include_details=request.include_details,
            page_size=request.search_options.page_size if request.search_options else 20,
        )
        preview = "、".join(request.keywords[:8])
        if len(request.keywords) > 8:
            preview += f" 等 {len(request.keywords)} 项"
        output_text = {
            "single": "一个 Excel 文件",
            "single_csv": "一个 CSV 文件",
            "multi_split": f"{len(request.keywords)} 个独立 Excel 文件",
            "multi_merge": "一个 Excel 文件，每个检索项一个 Sheet",
            "multi_csv": "一个 CSV 文件，包含命中检索项列",
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
        if request.search_options:
            summary += f"检索设置：{request.search_options.summary()}\n\n"
        summary += _long_task_warning(high)
        summary += "确认无误后才会启动浏览器和抓取任务。"
        confirmed = (
            confirm_advanced_task(self.root, summary, request.advanced_queries)
            if request.advanced_queries
            else messagebox.askokcancel("开始前确认", summary, parent=self.root)
        )
        if confirmed:
            self._start_task(request=request)

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
                page_size=(SearchOptions.from_dict(state.get("search_options")) or SearchOptions()).page_size,
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
                self._task_form.populate_resume(state)
                self._start_task(resume_state=state)
                return
            if delete_last_task(self.runtime.paths) or not last_task_path.exists():
                self._task_progress.append_log("已忽略并删除上次任务断点。", "warning")
                return
            messagebox.showerror(
                "断点删除失败",
                f"断点文件仍然存在，尚未忽略该任务：\n{last_task_path}",
                parent=self.root,
            )

    def _start_task(
        self,
        request: GuiTaskRequest | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> None:
        if self._running:
            return
        if self._downloads_running():
            messagebox.showinfo("下载正在运行", "请在论文下载结束后开始抓取。", parent=self.root)
            return
        if not self._ensure_browser_ready():
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
                search_options=SearchOptions.from_dict(resume_state.get("search_options")),
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
            if config.get("output_dir") != str(output_dir):
                config = save_config(self.runtime.paths.config_path, {**config, "output_dir": str(output_dir)})
        except (OSError, ValueError) as error:
            messagebox.showerror("无法读取配置", str(error), parent=self.root)
            return
        self._apply_config(config)
        task_settings = self.settings
        self._active_include_citation = request.include_citation
        self._active_output_dir = output_dir
        self._current_results = []
        self._result_prompt_pending = False
        viewer = getattr(self, "_results_window", None)
        if viewer is not None and viewer.window.winfo_exists():
            viewer.set_papers(self._current_results)
        self._cancel_event.clear()
        self._set_running(True)
        self._task_progress.prepare(request, resume_state)
        self._memory_sampler.reset()
        self._update_memory_status()
        self._task_form.pack_forget()
        self._settings_button.pack_forget()
        self._task_progress.pack(fill=tk.BOTH, expand=True)

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
                    **({"search_options": request.search_options} if request.search_options is not None else {}),
                )
            except Exception as error:
                _logger.exception("GUI 任务线程异常")
                self._event_queue.put(GuiEvent("worker_failed", {"error": str(error)}))
            finally:
                self._event_queue.put(GuiEvent("worker_done", {}))

        self._worker = Thread(target=worker, name="cnkibug-worker", daemon=True)
        self._worker.start()

    def _set_running(self, running: bool) -> None:
        self._running = running
        self._task_form.set_running(running)
        self._task_progress.set_running(running)
        if running:
            self._maintenance_actions.pack_forget()
        else:
            self._maintenance_actions.pack(fill=tk.X, pady=(6, 0))

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

    def _handle_event(self, event: GuiEvent) -> None:
        self._task_progress.handle_event(event)
        name = event.name
        payload = event.payload
        if name == "browser_launch_failed":
            messagebox.showerror("浏览器启动失败", str(payload.get("error", "未知错误")), parent=self.root)
        elif name == "verify_required":
            response_queue = payload.get("response_queue")
            if response_queue is not None:
                self._pending_confirms.append(response_queue)
            try:
                if not self._close_when_done:
                    self.root.deiconify()
                    self.root.attributes("-topmost", True)
                    self.root.after_idle(self.root.attributes, "-topmost", False)
                    self.root.lift()
                    self.root.focus_force()
                answer = False if self._close_when_done else Messagebox.show_question(
                    title="需要手动验证",
                    message="请在浏览器中完成安全验证后点击继续，恢复抓取论文结果。\n点击取消将停止任务并保存已抓取的结果。",
                    buttons=["继续:primary", "取消:secondary"],
                    default="继续",
                    parent=self.root,
                    localize=False,
                ) == "继续"
                if response_queue is not None:
                    response_queue.put(answer)
                if not answer:
                    self._cancel_event.set()
            finally:
                if response_queue is not None:
                    self._pending_confirms.remove(response_queue)
        elif name == "progress_completed":
            self._task_form.clear()
        elif name == "task_report":
            if "all_results" in payload and not getattr(self, "_result_prompt_pending", False):
                self._current_results = papers_from_results(payload["all_results"], getattr(self, "_active_include_citation", False))
                self._result_prompt_pending = True
        elif name == "export_finished":
            if "all_results" in payload:
                self._current_results = papers_from_results(payload["all_results"], getattr(self, "_active_include_citation", False))
                self._result_prompt_pending = True
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
            messagebox.showerror("任务异常结束", str(payload.get("error", "未知错误")), parent=self.root)
        elif name == "worker_done":
            viewer = getattr(self, "_results_window", None)
            if viewer is not None and viewer.window.winfo_exists():
                viewer.set_papers(self._current_results)
            self._set_running(False)
            if self._close_when_done:
                self._close_application()
            elif getattr(self, "_result_prompt_pending", False):
                self._result_prompt_pending = False
                completed = self._task_progress.completed
                title = "抓取完成" if completed else "任务已结束"
                count = len(self._current_results)
                if count and messagebox.askyesno(title, f"已取得 {count} 篇论文。\n\n是否展示论文详情？", parent=self.root, default=messagebox.NO):
                    self._show_results()
                elif not count and completed:
                    messagebox.showinfo(title, "未取得论文结果。", parent=self.root)

    def _downloads_running(self) -> bool:
        result = getattr(self, "_results_window", None)
        return bool(result is not None and result.busy)

    def _show_results(self) -> None:
        from .results import ResultsWindow

        viewer = getattr(self, "_results_window", None)
        if viewer is not None and viewer.window.winfo_exists():
            viewer.window.deiconify()
            viewer.window.lift()
            return
        self._results_window = ResultsWindow(
            self.root, self._current_results, settings=self.settings, paths=self.runtime.paths,
            get_output_dir=lambda: self._task_form.output_dir,
            initial_format="csv" if self._task_form.output_format == "csv" else "xlsx",
            can_download=lambda: not self._running,
            prepare_browser=self._ensure_browser_ready,
        )

    def _show_form(self) -> None:
        if self._running:
            return
        self._task_progress.pack_forget()
        self._task_form.show(self._footer)
        self._settings_button.pack(side=tk.RIGHT, padx=(0, 8))

    def _tick(self) -> None:
        self._task_progress.tick()
        self._update_memory_status()
        try:
            if self.root.winfo_exists():
                self.root.after(250, self._tick)
        except tk.TclError:
            return

    def _update_memory_status(self) -> None:
        self._memory_var.set(format_memory(self._memory_sampler.sample()))

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
            self._task_progress.disable_stop()
            self._task_progress.set_status("正在安全停止并保存结果")
            self._task_progress.append_log("已请求安全停止，请等待当前操作结束。", "warning")

    def _on_close(self) -> None:
        if self._downloads_running():
            if messagebox.askyesno("停止下载并退出", "停止当前论文下载并退出 CNKIBug？", parent=self.root):
                self._close_application()
            return
        if not messagebox.askyesno(
            "退出 CNKIBug",
            "任务仍在运行。退出前将安全停止并保存当前结果，是否继续？"
            if self._running else "确定要退出 CNKIBug 吗？",
            parent=self.root,
        ):
            return
        if not self._running:
            self._close_application()
            return
        self._close_when_done = True
        self._cancel_event.set()
        self._task_progress.disable_stop()
        self._task_progress.set_status("正在安全停止，完成后关闭窗口")
        for response_queue in list(self._pending_confirms):
            if response_queue.empty():
                response_queue.put(False)

    def _close_application(self) -> None:
        viewer = getattr(self, "_results_window", None)
        if viewer is not None and viewer._download_session.alive:
            viewer.shutdown()
            self.root.after(200, self._exit_after_download)
        else:
            self.root.destroy()

    def _exit_after_download(self) -> None:
        viewer = getattr(self, "_results_window", None)
        if viewer is not None and viewer._download_session.alive:
            self.root.after(200, self._exit_after_download)
        else:
            self.root.destroy()


def main(program_dir: Path, icon_path: Path | None = None) -> None:
    CNKIBugApp(program_dir, icon_path).run()
