"""共享的进程内存采样和展示文案。"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

import psutil


_MEBIBYTE = 1024 * 1024
_PROCESS_ERRORS = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)
_BROWSER_NAMES = frozenset({
    "chrome",
    "chrome.exe",
    "chromium",
    "chromium.exe",
    "google-chrome",
    "google-chrome-stable",
    "msedge",
    "msedge.exe",
})


@dataclass(frozen=True)
class MemorySample:
    program_bytes: int
    browser_bytes: int
    total_bytes: int
    peak_bytes: int


class MemorySampler:
    """按固定间隔汇总当前进程和 Playwright 主浏览器进程的 RSS。"""

    def __init__(
        self,
        *,
        interval_seconds: float = 1.0,
        clock: Callable[[], float] | None = None,
        process_factory: Callable[[int], psutil.Process] | None = None,
        pid: int | None = None,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._clock = clock or time.monotonic
        self._process_factory = process_factory or psutil.Process
        self._pid = os.getpid() if pid is None else pid
        self._lock = RLock()
        self._last_sampled_at: float | None = None
        self._sample: MemorySample | None = None
        self._peak_bytes = 0

    def reset(self) -> None:
        """开始新一轮任务前清除峰值，并让下一次读取立即重新采样。"""
        with self._lock:
            self._last_sampled_at = None
            self._sample = None
            self._peak_bytes = 0

    def sample(self, *, force: bool = False) -> MemorySample | None:
        """返回缓存或一次新采样；任务结束时可强制刷新。"""
        with self._lock:
            now = self._clock()
            if (
                not force
                and self._last_sampled_at is not None
                and now - self._last_sampled_at < self._interval_seconds
            ):
                return self._sample

            self._last_sampled_at = now
            sample = self._collect()
            if sample is None:
                self._sample = None
                return None

            self._peak_bytes = max(self._peak_bytes, sample.total_bytes)
            self._sample = MemorySample(
                program_bytes=sample.program_bytes,
                browser_bytes=sample.browser_bytes,
                total_bytes=sample.total_bytes,
                peak_bytes=self._peak_bytes,
            )
            return self._sample

    def _collect(self) -> MemorySample | None:
        try:
            process = self._process_factory(self._pid)
            program_bytes = _rss(process)
            children = process.children(recursive=True)
        except _PROCESS_ERRORS:
            return None
        except Exception:
            return None

        browser_bytes = 0
        for child in children:
            try:
                if _is_browser_root(child):
                    browser_bytes += _rss(child)
            except _PROCESS_ERRORS:
                continue
            except Exception:
                continue

        return MemorySample(
            program_bytes=program_bytes,
            browser_bytes=browser_bytes,
            total_bytes=program_bytes + browser_bytes,
            peak_bytes=0,
        )


def format_memory(sample: MemorySample | None) -> str:
    if sample is None:
        return "内存：暂不可用"
    return (
        f"内存约 {_to_mb(sample.total_bytes)} MB"
        f"（程序 {_to_mb(sample.program_bytes)} + 浏览器 {_to_mb(sample.browser_bytes)}）"
        f"｜本轮峰值 {_to_mb(sample.peak_bytes)} MB"
    )


def format_task_finished_memory(sample: MemorySample | None) -> str:
    if sample is None:
        return "任务结束后内存：暂不可用"
    return (
        f"任务结束后内存：{_to_mb(sample.total_bytes)} MB"
        f"｜本轮峰值 {_to_mb(sample.peak_bytes)} MB"
    )


def _rss(process: psutil.Process) -> int:
    return max(0, int(process.memory_info().rss))


def _is_browser_root(process: psutil.Process) -> bool:
    if process.name().lower() not in _BROWSER_NAMES:
        return False
    return not any(
        argument.startswith("--type=") and argument != "--type=browser"
        for argument in process.cmdline()
    )


def _to_mb(byte_count: int) -> int:
    return max(0, int(byte_count)) // _MEBIBYTE
