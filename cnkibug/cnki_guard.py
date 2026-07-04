from __future__ import annotations

import logging
import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from . import window
from .settings import ScraperSettings
from .ui import _console, print_verify_alert


VERIFY_NONE = "none"
VERIFY_PASSED = "passed"
VERIFY_TIMEOUT = "timeout"

_logger = logging.getLogger("cnkibug.cnki_guard")


def handle_verify(page: Any, settings: ScraperSettings) -> str:
    if "/verify" not in page.url:
        return VERIFY_NONE

    _logger.warning("检测到安全验证，等待用户手动完成")
    window.bring_to_front()
    print_verify_alert()

    waited = 0.0
    interval = 1.0
    next_notice = float(settings.verify_notice_interval_sec)
    while "/verify" in page.url:
        if waited >= settings.verify_wait_timeout_sec:
            _logger.warning("安全验证等待超时: waited_sec=%d", int(waited))
            _console.print("[yellow][!] 等待安全验证超时，将保存已抓取的数据。[/yellow]")
            return VERIFY_TIMEOUT
        if waited >= next_notice:
            remaining = int(settings.verify_wait_timeout_sec - waited)
            _logger.info("仍在等待安全验证: waited_sec=%d remaining_sec=%d", int(waited), remaining)
            _console.print(
                f"[dim][*] 仍在等待手动完成安全验证…（剩余约 {remaining} 秒，完成后自动继续）[/dim]"
            )
            next_notice += settings.verify_notice_interval_sec
        time.sleep(interval)
        waited += interval
    _console.print("[green][*] 验证已通过，继续抓取。[/green]")
    _logger.info("安全验证已通过: waited_sec=%d", int(waited))
    return VERIFY_PASSED


def print_page_debug(page: Any, context: str) -> None:
    _console.print(f"[yellow][debug] {context}[/yellow]")
    try:
        _console.print(f"[dim]当前 URL: {page.url}[/dim]")
    except PlaywrightError:
        _console.print("[dim]当前 URL: <无法读取>[/dim]")
    try:
        _console.print(f"[dim]页面标题: {page.title()}[/dim]")
    except PlaywrightError:
        _console.print("[dim]页面标题: <无法读取>[/dim]")
