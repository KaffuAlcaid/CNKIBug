from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from tkinter.scrolledtext import ScrolledText
from typing import Any

import ttkbootstrap as ttk

from ..cnki.models import STATUS_EMPTY, STATUS_FAILED, STATUS_STOPPED, STATUS_SUCCESS
from ..core.estimate import estimate_progress, estimate_seconds, estimate_work_seconds, format_eta
from ..workflow.state import remaining_workload
from .events import GuiEvent
from .task_form import GuiTaskRequest


_MAX_LOG_LINES = 1000


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class TaskProgress(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_stop: Callable[[], None],
        on_new_task: Callable[[], None],
    ) -> None:
        super().__init__(parent, padding=(0, 8))
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

        progress_frame = self
        self._status_var = tk.StringVar(value="等待设置任务")
        ttk.Label(progress_frame, textvariable=self._status_var, font=("TkDefaultFont", 13, "bold")).pack(anchor=tk.W, pady=(0, 8))
        self._progress_var = tk.IntVar(value=0)
        progress_row = ttk.Frame(progress_frame)
        progress_row.pack(fill=tk.X, pady=(8, 2))
        progress_row.columnconfigure(0, weight=1)
        self._progress = ttk.Progressbar(
            progress_row,
            variable=self._progress_var,
            maximum=100,
            bootstyle="primary",
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
        detail_label = ttk.Label(progress_frame, textvariable=self._detail_var, wraplength=900)
        detail_label.pack(fill=tk.X, pady=(8, 10))
        self.bind("<Configure>", lambda event: detail_label.configure(wraplength=max(200, event.width - 12)))
        self._log_visible = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            progress_frame, text="运行记录", variable=self._log_visible, command=self._toggle_log,
        ).pack(anchor=tk.W, pady=(0, 8))
        self._log_frame = ttk.Frame(progress_frame)
        self._log_frame.pack(fill=tk.BOTH, expand=True)
        self._log = ScrolledText(
            self._log_frame, height=7, wrap=tk.WORD, state=tk.DISABLED,
            relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
        )
        self._log.pack(fill=tk.BOTH, expand=True)

        stop_row = self._stop_row = ttk.Frame(progress_frame)
        stop_row.pack(fill=tk.X, pady=(8, 0))
        self._new_task_button = ttk.Button(
            stop_row,
            text="返回任务设置",
            command=on_new_task,
            state=tk.DISABLED,
            bootstyle="secondary",
        )
        self._new_task_button.pack(side=tk.LEFT)
        self._stop_button = ttk.Button(
            stop_row,
            text="安全停止",
            command=on_stop,
            state=tk.DISABLED,
            bootstyle="danger-outline",
        )
        self._stop_button.pack(side=tk.RIGHT)

    @property
    def completed(self) -> bool:
        return self._progress_mode == "completed"

    def _toggle_log(self) -> None:
        if self._log_visible.get():
            self._log_frame.pack(fill=tk.BOTH, expand=True, before=self._stop_row)
        else:
            self._log_frame.pack_forget()

    def apply_theme(self, style: ttk.Style) -> None:
        colors = style.colors
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

    def set_running(self, running: bool) -> None:
        self._stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            self._stop_button.pack(side=tk.RIGHT)
        else:
            self._stop_button.pack_forget()

    def disable_stop(self) -> None:
        self._stop_button.configure(state=tk.DISABLED)

    def set_status(self, text: str) -> None:
        self._status_var.set(text)

    def prepare(self, request: GuiTaskRequest, resume_state: dict[str, Any] | None = None) -> None:
        self._reset_progress()
        self._set_total_eta(request, resume_state)
        self._clear_log()
        self._new_task_button.configure(state=tk.DISABLED)

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

    def handle_event(self, event: GuiEvent) -> None:
        name = event.name
        payload = event.payload
        if name == "task_started":
            self._task_started_at = time.monotonic()
            self._status_var.set("正在启动浏览器")
        elif name == "message":
            self.append_log(str(payload.get("text", "")), str(payload.get("level", "")))
        elif name == "activity_started":
            self._status_var.set(str(payload.get("message", "正在处理")))
        elif name == "browser_edge_failed":
            self.append_log("Edge 启动失败，正在尝试备用 Chromium。", "warning")
        elif name == "browser_launched":
            browser = payload.get("browser_name") or ("Microsoft Edge" if payload.get("channel") == "msedge" else "Chromium")
            self.append_log(f"已启动 {browser}。", "success")
        elif name == "browser_ready":
            self._status_var.set("浏览器已就绪")
            self.append_log("浏览器已打开；遇到滑块时请在浏览器窗口手动完成。")
        elif name == "browser_launch_failed":
            error = str(payload.get("error", "未知错误"))
            self._status_var.set("浏览器启动失败")
            self.append_log(f"浏览器启动失败：{error}", "error")
        elif name == "verify_required":
            self._status_var.set("等待手动完成安全验证")
        elif name == "verify_waiting":
            self._status_var.set(f"等待安全验证，剩余约 {payload.get('remaining', 0)} 秒")
        elif name == "verify_timeout":
            self.append_log("等待安全验证超时，将保存当前结果。", "warning")
        elif name == "verify_passed":
            self.append_log("安全验证已通过，继续抓取。", "success")
        elif name == "page_debug":
            self.append_log(f"页面异常：{payload.get('context', '未记录上下文')}", "warning")
            self.append_log(f"当前 URL：{payload.get('url', '<无法读取>')}")
            self.append_log(f"页面标题：{payload.get('title', '<无法读取>')}")
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
            self.append_log(
                f"本轮摘要：成功 {success}，无结果 {empty}，失败 {failed}，"
                f"中止 {stopped}，共 {total_records} 条。",
                level,
            )
            for item in report.failed_items():
                reason = item.reason or "未记录原因"
                self.append_log(
                    f"第 {item.index}/{item.total} 个检索项「{item.keyword}」：{reason}",
                    "error" if item.status == STATUS_FAILED else "warning",
                )
        elif name == "export_finished":
            result = payload["result"]
            if result.failed:
                self.append_log(
                    f"本轮有 {result.failed} 个结果文件未能成功保存。",
                    "error",
                )
            if result.keyword_txt_failed:
                self.append_log("关键词 TXT 未能成功保存，详情见日志。", "error")
            for path in result.saved_paths:
                self.append_log(f"已保存：{path}", "success")
            if result.keyword_txt_path:
                self.append_log(f"关键词 TXT 已保存：{result.keyword_txt_path}", "success")
        elif name == "worker_failed":
            error = str(payload.get("error", "未知错误"))
            self._status_var.set("任务异常结束")
            self.append_log(f"任务异常结束：{error}", "error")
        elif name == "worker_done":
            if self._actual_seconds is None and self._task_started_at is not None:
                self._actual_seconds = time.monotonic() - self._task_started_at
                self._time_var.set(f"实际用时：{_format_duration(self._actual_seconds)}")
            self._new_task_button.configure(state=tk.NORMAL)

    def tick(self) -> None:
        now = time.monotonic()
        if self._task_started_at is not None and self._actual_seconds is None:
            self._time_var.set(f"已用时：{_format_duration(now - self._task_started_at)}")
        if self._progress_mode in {"running", "paused"}:
            percentage = self._current_percentage(now)
            self._progress_var.set(percentage)
            self._progress_percent_var.set(f"{percentage}%")

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
                page_size=request.search_options.page_size if request.search_options else 20,
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
                page_size=request.search_options.page_size if request.search_options else 20,
            )
        self._total_eta_var.set(
            f"预计总耗时：{format_eta(self._total_eta_low, self._total_eta_high, compact=True)}"
        )

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

    def append_log(self, text: str, level: str = "") -> None:
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
