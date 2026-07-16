from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..core.events import EventSink, NULL_EVENTS
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .cache import discard_cookie_state, prepare_cookie_state


_logger = logging.getLogger("cnkibug.browser_runtime")


@dataclass(frozen=True)
class BrowserLaunchResult:
    browser: Any
    channel: str


class BrowserLaunchError(RuntimeError):
    pass


def launch_browser(
    p: Any,
    events: EventSink = NULL_EVENTS,
) -> BrowserLaunchResult:
    try:
        _logger.info("浏览器启动开始: channel=msedge")
        with events.activity("少女祈祷中..."):
            browser = p.chromium.launch(
                channel="msedge",
                headless=False,
                args=["--start-maximized"],
            )
        events.emit("browser_launched", channel="msedge")
        _logger.info("浏览器启动成功: channel=msedge")
        return BrowserLaunchResult(browser, "msedge")
    except PlaywrightError as edge_err:
        _logger.warning("Edge 启动失败，尝试备用 Chromium: %s", edge_err)
        events.emit("browser_edge_failed", error=str(edge_err))
        try:
            _logger.info("浏览器启动开始: channel=chromium")
            with events.activity("少女祈祷中..."):
                browser = p.chromium.launch(
                    headless=False,
                    args=["--start-maximized"],
                )
            events.emit("browser_launched", channel="chromium")
            _logger.info("浏览器启动成功: channel=chromium")
            return BrowserLaunchResult(browser, "chromium")
        except PlaywrightError as chromium_err:
            _logger.error("备用 Chromium 启动失败: %s", chromium_err)
            raise BrowserLaunchError(f"浏览器启动彻底失败: {chromium_err}") from chromium_err
        except Exception:
            _logger.exception("备用 Chromium 启动出现非预期异常")
            raise
    except Exception:
        _logger.exception("Edge 启动出现非预期异常")
        raise


def create_browser_context(
    browser: Any,
    settings: ScraperSettings,
    paths: RuntimePaths,
) -> Any:
    cookie_state_path = prepare_cookie_state(
        settings.session_cache_enabled,
        settings.session_cache_ttl_hours,
        paths,
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
