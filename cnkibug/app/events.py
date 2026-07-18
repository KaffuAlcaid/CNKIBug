from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from rich.text import Text

from ..core.events import EventSink
from .console import safe_input
from .errors import _popup_error
from .report_view import print_task_report
from .ui import (
    EstimatedProgressDisplay,
    _console,
    _format_duration,
    print_browser_banner,
    print_verify_alert,
)


_MESSAGE_STYLES = {
    "dim": "dim",
    "warning": "yellow",
    "error": "red",
    "success": "green",
}


class ConsoleEventSink(EventSink):
    def __init__(self) -> None:
        self._progress: EstimatedProgressDisplay | None = None
        self._task_started_at: float | None = None

    @contextmanager
    def activity(self, message: str) -> Iterator[None]:
        with _console.status(Text(message, style="bold magenta"), spinner="bouncingBar"):
            yield

    def emit(self, name: str, **payload: Any) -> None:
        if name == "task_started":
            self._task_started_at = time.monotonic()
        elif name == "message":
            self._message(str(payload.get("text", "")), str(payload.get("level", "")))
        elif name == "browser_edge_failed":
            self._message(
                f"[!] Edge 启动失败 ({payload['error']})，尝试备用 Chromium...",
                "warning",
            )
        elif name == "browser_launched":
            channel = "Microsoft Edge" if payload.get("channel") == "msedge" else "备用 Chromium 浏览器"
            self._message(f"[*] 已启动 {channel}", "dim")
        elif name == "browser_ready":
            print_browser_banner()
        elif name == "browser_launch_failed":
            self._browser_launch_failed(str(payload.get("error", "未知错误")))
        elif name == "verify_required":
            print_verify_alert()
        elif name == "verify_waiting":
            self._message(
                f"[*] 仍在等待手动完成安全验证…（剩余约 {payload['remaining']} 秒，完成后自动继续）",
                "dim",
            )
        elif name == "verify_timeout":
            self._message("[!] 等待安全验证超时，将保存已抓取的数据。", "warning")
        elif name == "verify_passed":
            self._message("[*] 验证已通过，继续抓取。", "success")
        elif name == "page_debug":
            self._message(f"[debug] {payload['context']}", "warning")
            self._message(f"当前 URL: {payload['url']}", "dim")
            self._message(f"页面标题: {payload['title']}", "dim")
        elif name == "progress_started":
            self._progress = EstimatedProgressDisplay(
                int(payload["low_seconds"]),
                int(payload["high_seconds"]),
                wall_started_at=self._task_started_at,
            )
            self._progress.start()
        elif name == "progress_updated" and self._progress is not None:
            self._progress.update_status(**payload)
        elif name == "progress_paused" and self._progress is not None:
            self._progress.pause()
        elif name == "progress_resumed" and self._progress is not None:
            self._progress.resume()
        elif name == "progress_saving" and self._progress is not None:
            self._progress.saving()
        elif name == "progress_completed" and self._progress is not None:
            self._progress.complete()
        elif name == "progress_stopped" and self._progress is not None:
            self._progress.stop(str(payload["message"]))
        elif name == "task_finished":
            actual_seconds = float(payload.get("elapsed_seconds", 0.0))
            if self._progress is not None:
                self._progress.finish(actual_seconds)
            else:
                self._message(f"实际用时：{_format_duration(actual_seconds)}", "dim")
        elif name == "progress_closed" and self._progress is not None:
            self._progress.close()
            self._progress = None
            self._task_started_at = None
        elif name == "task_report":
            print_task_report(payload["report"], payload["all_results"])
        elif name == "export_finished":
            self._print_export_result(**payload)

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        choice = safe_input(prompt).strip().lower()
        if not choice:
            return default
        return choice == "y"

    def _message(self, text: str, level: str = "") -> None:
        _console.print(Text(text, style=_MESSAGE_STYLES.get(level)))

    def _browser_launch_failed(self, error: str) -> None:
        if sys.platform == "win32":
            _popup_error([
                "==============================================",
                " [错误] 浏览器启动失败！",
                "----------------------------------------------",
                " 程序无法启动 Edge，也无法启动备用 Chromium。",
                "",
                f" 错误信息: {error}",
                "",
                " 建议：",
                "   1. 安装或重新安装 Microsoft Edge",
                "   2. 源码运行用户可执行 playwright install chromium",
                "   3. 检查系统权限或安全软件设置",
                "==============================================",
            ])
            return
        self._message(f"[FATAL] 浏览器启动失败: {error}", "error")
        self._message("建议执行：playwright install chromium", "warning")
        self._message(
            "Linux 若提示缺少系统依赖，可再执行：playwright install-deps chromium",
            "dim",
        )

    def _print_export_result(self, **payload: Any) -> None:
        result = payload["result"]
        all_results = payload["all_results"]
        save_mode = str(payload["save_mode"])
        total = sum(len(records) for records in all_results.values())
        if result.failed:
            self._message(f"[x] 本轮有 {result.failed} 个文件未能成功保存。", "error")
        if result.keyword_txt_failed:
            self._message("[x] 关键词 TXT 未能成功保存，详情见日志。", "error")
        if not result.saved_paths:
            if total == 0:
                self._message("[!] 未抓取到任何数据，不生成文件。", "warning")
            if result.keyword_txt_path:
                self._message(f"[*] 关键词 TXT 已保存至：{result.keyword_txt_path}", "success")
            return

        _console.print("\n" + "═" * 50)
        if save_mode == "multi_split":
            _console.print(
                f"[bold green][*] 全部抓取完毕，共 {total} 条数据，"
                f"生成 {len(result.saved_paths)} 个文件：[/bold green]"
            )
            for item in result.files:
                _console.print(
                    f"  · [cyan][{item.keyword}][/cyan] {item.record_count} 条  ->  {item.path}"
                )
        else:
            _console.print(f"[bold green][*] 共抓取 {total} 条数据。[/bold green]")
            label = "CSV 文件已保存至" if save_mode.endswith("csv") else "文件已保存至"
            _console.print(f"[*] {label}：")
            for path in result.saved_paths:
                _console.print(f"    [bold]>>> {path} <<<[/bold]")
            if save_mode == "multi_merge":
                for keyword, records in all_results.items():
                    if records:
                        _console.print(f"  · Sheet [cyan][{keyword}][/cyan]：{len(records)} 条")
        if result.keyword_txt_path:
            _console.print(f"[*] 关键词 TXT 已保存至：{result.keyword_txt_path}")
        _console.print("═" * 50 + "\n")
