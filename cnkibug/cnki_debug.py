#调试文件
from __future__ import annotations

from typing import Any

from playwright.sync_api import Error as PlaywrightError

from .ui import _console


def print_page_debug(page: Any, context: str) -> None:
    """打印页面状态，辅助判断 CNKI 页面结构或验证策略是否变化。"""
    _console.print(f"[yellow][debug] {context}[/yellow]")
    try:
        _console.print(f"[dim]当前 URL: {page.url}[/dim]")
    except PlaywrightError:
        _console.print("[dim]当前 URL: <无法读取>[/dim]")
    try:
        _console.print(f"[dim]页面标题: {page.title()}[/dim]")
    except PlaywrightError:
        _console.print("[dim]页面标题: <无法读取>[/dim]")
