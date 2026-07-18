"""共享的 Rich Console 和终端展示组件。"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import RLock

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
)
from rich.text import Text

from ..core.estimate import estimate_progress
from ..core.memory import MemorySampler, format_memory

_console = Console(highlight=False)


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class EstimatedProgressDisplay:
    """Rich 展示层：按活动耗时渲染预计进度和真实抓取状态。"""

    def __init__(
        self,
        low_seconds: int,
        high_seconds: int,
        *,
        console: Console | None = None,
        clock: Callable[[], float] | None = None,
        wall_started_at: float | None = None,
        memory_sampler: MemorySampler | None = None,
    ) -> None:
        estimate_progress(0, low_seconds, high_seconds)
        self._low_seconds = low_seconds
        self._high_seconds = high_seconds
        self._console = console or _console
        self._clock = clock or time.monotonic
        self._wall_started_at = wall_started_at
        self._memory_sampler = memory_sampler or MemorySampler()
        self._actual_seconds: float | None = None
        self._lock = RLock()
        self._mode = "idle"
        self._elapsed = 0.0
        self._running_since: float | None = None
        self._visible = False
        self._stopped_progress = 0
        self._message = "准备开始"
        self._keyword = ""
        self._keyword_index = 0
        self._keyword_total = 0
        self._page = 0
        self._page_total = 0
        self._records = 0
        self._detail_index = 0
        self._detail_total = 0

        self._bar = Progress(
            SpinnerColumn(spinner_name="dots", style="bold cyan"),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(bar_width=30, style="cyan", complete_style="bright_cyan"),
            TaskProgressColumn(),
            console=self._console,
            auto_refresh=False,
        )
        self._task_id = self._bar.add_task("准备开始", total=100)
        self._live = Live(
            console=self._console,
            get_renderable=self._render,
            refresh_per_second=4,
            transient=False,
        )

    @property
    def elapsed_seconds(self) -> float:
        with self._lock:
            return self._elapsed_at(self._clock())

    @property
    def percentage(self) -> int:
        with self._lock:
            return self._percentage_at(self._clock())

    @property
    def status_text(self) -> str:
        with self._lock:
            _, headline, details = self._snapshot_at(self._clock())
            time_text = self._time_text_at(self._clock())
        return "\n".join((headline, time_text, *details, format_memory(self._memory_sampler.sample())))

    def start(self) -> None:
        with self._lock:
            if self._visible:
                return
            self._mode = "running"
            now = self._clock()
            self._running_since = now
            if self._wall_started_at is None:
                self._wall_started_at = now
            self._visible = True
        self._live.start(refresh=True)

    def update_status(
        self,
        *,
        keyword: str | None = None,
        keyword_index: int | None = None,
        keyword_total: int | None = None,
        page: int | None = None,
        page_total: int | None = None,
        records: int | None = None,
        detail_index: int | None = None,
        detail_total: int | None = None,
    ) -> None:
        with self._lock:
            if keyword is not None:
                self._keyword = keyword
            if keyword_index is not None:
                self._keyword_index = keyword_index
            if keyword_total is not None:
                self._keyword_total = keyword_total
            if page is not None:
                self._page = page
            if page_total is not None:
                self._page_total = page_total
            if records is not None:
                self._records = records
            if detail_index is not None:
                self._detail_index = detail_index
            if detail_total is not None:
                self._detail_total = detail_total
        self._refresh()

    def pause(self, message: str = "等待手动验证，任务计时已暂停") -> None:
        with self._lock:
            if self._mode != "running":
                return
            now = self._clock()
            self._freeze_running(now)
            self._mode = "paused"
            self._message = message
        self._refresh()

    def resume(self) -> None:
        with self._lock:
            if self._mode != "paused":
                return
            self._mode = "running"
            self._running_since = self._clock()
        self._refresh()

    def saving(self) -> None:
        with self._lock:
            if not self._visible:
                return
            self._freeze_running(self._clock())
            self._mode = "saving"
            self._message = "正在保存结果……"
        self._refresh()

    def complete(self) -> None:
        with self._lock:
            if not self._visible:
                return
            self._freeze_running(self._clock())
            self._mode = "completed"
            self._message = "已完成"
        self._refresh()

    def stop(self, message: str) -> None:
        with self._lock:
            if not self._visible:
                return
            now = self._clock()
            self._stopped_progress = self._percentage_at(now)
            self._freeze_running(now)
            self._mode = "stopped"
            self._message = message
        self._refresh()

    def finish(self, actual_seconds: float) -> None:
        with self._lock:
            self._actual_seconds = max(0.0, actual_seconds)
        self._refresh()

    def close(self) -> None:
        with self._lock:
            if not self._visible:
                return
        self._live.stop()
        with self._lock:
            self._visible = False

    def _refresh(self) -> None:
        with self._lock:
            visible = self._visible
        if visible:
            self._live.refresh()

    def _freeze_running(self, now: float) -> None:
        if self._mode == "running" and self._running_since is not None:
            self._elapsed += max(0.0, now - self._running_since)
            self._running_since = None

    def _elapsed_at(self, now: float) -> float:
        if self._mode == "running" and self._running_since is not None:
            return self._elapsed + max(0.0, now - self._running_since)
        return self._elapsed

    def _percentage_at(self, now: float) -> int:
        if self._mode == "completed":
            return 100
        if self._mode == "saving":
            return 99
        if self._mode == "stopped":
            return self._stopped_progress
        return estimate_progress(
            self._elapsed_at(now),
            self._low_seconds,
            self._high_seconds,
        )

    def _time_text_at(self, now: float) -> str:
        if self._actual_seconds is not None:
            return f"实际用时：{_format_duration(self._actual_seconds)}"
        started_at = self._wall_started_at if self._wall_started_at is not None else now
        return f"已用时：{_format_duration(max(0.0, now - started_at))}"

    def _snapshot_at(self, now: float) -> tuple[int, str, list[str]]:
        elapsed = self._elapsed_at(now)
        percentage = self._percentage_at(now)
        if self._mode == "running" and percentage == 99:
            overtime = elapsed - self._high_seconds
            headline = (
                "已超过预计时间，任务仍在运行 "
                f"+{_format_duration(overtime)}"
            )
        elif self._mode in {"paused", "saving", "completed", "stopped"}:
            headline = self._message
        elif self._mode == "running":
            headline = "预计进度"
        else:
            headline = "准备开始"

        details = []
        if self._keyword:
            details.append(
                f"当前关键词：{self._keyword}（{self._keyword_index}/{self._keyword_total}）"
            )
        if self._page_total:
            details.append(f"当前页面：第 {self._page}/{self._page_total} 页")
        if self._detail_total:
            details.append(f"当前详情：{self._detail_index}/{self._detail_total}")
        details.append(f"已获取：{self._records} 条")
        return percentage, headline, details

    def _render(self) -> Group:
        with self._lock:
            now = self._clock()
            percentage, headline, details = self._snapshot_at(now)
            time_text = self._time_text_at(now)
            show_interrupt_hint = self._mode in {"running", "paused"}
        memory_text = format_memory(self._memory_sampler.sample())
        self._bar.update(
            self._task_id,
            completed=percentage,
            description=headline,
        )
        renderables = [
            self._bar.get_renderable(),
            Text(time_text, style="bold"),
            Text("\n".join(details), style="dim"),
            Text(memory_text, style="dim"),
        ]
        if show_interrupt_hint:
            renderables.append(
                Text("按 Ctrl+C 可安全停止，已完成页会保存", style="bold yellow")
            )
        return Group(*renderables)


def print_browser_banner():

    _console.print(
        Panel.fit(
            "[bold yellow]浏览器已在新窗口打开[/bold yellow]\n"
            "· 全程请[bold]勿关闭[/bold]该浏览器窗口\n"
            "· 滑块 / 验证码[bold red]必须由你手动完成[/bold red]，程序不会自动验证\n"
            "· 验证通过后程序会自动继续；抓取过程中页面会自动翻页\n"
            "· 除完成验证外，请勿手动操作浏览器",
            title="[bold]⚠ 请切换到浏览器窗口[/bold]",
            border_style="yellow",
        )
    )


def print_verify_alert():
    """检测到知网安全验证(/verify)时输出高亮提醒。"""
    _console.print(
        Panel.fit(
            "[bold]检测到知网安全验证（滑块）[/bold]\n"
            "· 程序无法自动完成验证，请切换到浏览器窗口手动操作\n"
            "· 验证通过后程序会自动继续，无需在本窗口输入",
            title="[bold red]需要手动验证[/bold red]",
            border_style="red",
        )
    )
