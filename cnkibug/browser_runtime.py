#启动浏览器，创建playwright上下文，接入cookies和缓存
from __future__ import annotations

import logging
import sys
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from .errors import _popup_error
from .session_cache import discard_cookie_state, prepare_cookie_state
from .settings import ScraperSettings
from .ui import _console


_logger = logging.getLogger("cnkibug.browser_runtime")


def launch_browser(p: Any) -> Any:
    try:
        _logger.info("浏览器启动开始: channel=msedge")
        with _console.status(
            "[bold magenta]少女祈祷中...[/bold magenta]",
            spinner="bouncingBar",
        ):
            browser = p.chromium.launch(
                channel="msedge",
                headless=False,
                args=["--start-maximized"],
            )
        _console.print("[dim][*] 已启动 Microsoft Edge[/dim]")
        _logger.info("浏览器启动成功: channel=msedge")
        return browser
    except PlaywrightError as edge_err:
        _logger.warning("Edge 启动失败，尝试备用 Chromium: %s", edge_err)
        _console.print(f"[yellow][!] Edge 启动失败 ({edge_err})，尝试备用 Chromium...[/yellow]")
        try:
            _logger.info("浏览器启动开始: channel=chromium")
            with _console.status(
                "[bold magenta]少女祈祷中...[/bold magenta]",
                spinner="bouncingBar",
            ):
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized"],
                )
            _console.print("[dim][*] 已启动备用 Chromium 浏览器[/dim]")
            _logger.info("浏览器启动成功: channel=chromium")
            return browser
        except PlaywrightError as chromium_err:
            _logger.error("备用 Chromium 启动失败: %s", chromium_err)
            if sys.platform == "win32":
                _popup_error([
                    "==============================================",
                    " [错误] 浏览器启动失败！",
                    "----------------------------------------------",
                    " 程序无法启动 Edge，也无法启动备用 Chromium。",
                    "",
                    " 可能原因：",
                    "   · Edge 未安装或文件损坏",
                    "   · Playwright Chromium 未安装",
                    "   · 系统权限不足",
                    "   · 安全软件阻止了浏览器启动",
                    "",
                    " 建议：",
                    "   1. 安装或重新安装 Microsoft Edge",
                    "      https://www.microsoft.com/zh-cn/edge/download",
                    "   2. 源码运行用户可执行 playwright install chromium",
                    "   3. 以管理员身份运行本程序",
                    "==============================================",
                ])
            else:
                _console.print(f"[red][FATAL] 浏览器启动失败: {chromium_err}[/red]")
                _console.print("[yellow]建议执行：playwright install chromium[/yellow]")
                _console.print(
                    "[dim]Linux 若提示缺少系统依赖，可再执行："
                    "playwright install-deps chromium[/dim]"
                )
            raise RuntimeError(f"浏览器启动彻底失败: {chromium_err}")
        except Exception:
            _logger.exception("备用 Chromium 启动出现非预期异常")
            raise
    except Exception:
        _logger.exception("Edge 启动出现非预期异常")
        raise


def create_browser_context(browser: Any, settings: ScraperSettings) -> Any:
    cookie_state_path = prepare_cookie_state(
        settings.session_cache_enabled,
        settings.session_cache_ttl_hours,
    )
    context_options: dict[str, Any] = {
        "no_viewport": True,
    }
    if cookie_state_path is not None:
        context_options["storage_state"] = str(cookie_state_path)
    try:
        context = browser.new_context(**context_options)
    except PlaywrightError:
        if cookie_state_path is None:
            raise
        discard_cookie_state(cookie_state_path, "创建浏览器上下文失败")
        _logger.warning("cookies 会话缓存加载失败，已改用新会话", exc_info=True)
        context_options.pop("storage_state", None)
        context = browser.new_context(**context_options)
    _logger.info("浏览器上下文已创建: no_viewport=True")
    return context
