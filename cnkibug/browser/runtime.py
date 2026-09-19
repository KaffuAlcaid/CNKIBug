from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..core.events import EventSink, NULL_EVENTS
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .cache import discard_cookie_state, prepare_cookie_state
from .environment import browser_candidates, browser_launch_options


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
    failures = []
    for candidate in browser_candidates(p):
        name = candidate.name
        try:
            _logger.info("浏览器启动开始: channel=%s", name)
            with events.activity("少女祈祷中..."):
                browser = p.chromium.launch(
                    args=["--start-maximized"], **browser_launch_options(candidate),
                )
            channel = candidate.channel or "chromium"
            events.emit("browser_launched", channel=channel, browser_name=name)
            _logger.info("浏览器启动成功: channel=%s", name)
            return BrowserLaunchResult(browser, channel)
        except PlaywrightError as error:
            failures.append(f"{name}: {error}")
            _logger.warning("浏览器启动失败: browser=%s error=%s", name, error)
            if candidate.channel == "msedge":
                events.emit("browser_edge_failed", error=str(error))
        except Exception:
            _logger.exception("浏览器启动出现非预期异常")
            raise
    raise BrowserLaunchError("没有可用的浏览器：\n" + "\n".join(failures))


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
