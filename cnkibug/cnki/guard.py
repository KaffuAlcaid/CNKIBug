from __future__ import annotations

import logging
import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from ..core.events import EventSink, NULL_EVENTS
from ..core.settings import ScraperSettings


VERIFY_NONE = "none"
VERIFY_PASSED = "passed"
VERIFY_TIMEOUT = "timeout"

_logger = logging.getLogger("cnkibug.cnki_guard")


def handle_verify(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> str:
    if "/verify" not in page.url:
        return VERIFY_NONE

    _logger.warning("检测到安全验证，等待用户手动完成")
    events.emit("progress_paused")
    events.emit("verify_required")

    waited = 0.0
    interval = 1.0
    next_notice = float(settings.verify_notice_interval_sec)
    while "/verify" in page.url:
        if waited >= settings.verify_wait_timeout_sec:
            _logger.warning("安全验证等待超时: waited_sec=%d", int(waited))
            events.emit("verify_timeout")
            return VERIFY_TIMEOUT
        if waited >= next_notice:
            remaining = int(settings.verify_wait_timeout_sec - waited)
            _logger.info("仍在等待安全验证: waited_sec=%d remaining_sec=%d", int(waited), remaining)
            events.emit("verify_waiting", remaining=remaining)
            next_notice += settings.verify_notice_interval_sec
        time.sleep(interval)
        waited += interval
    events.emit("verify_passed")
    events.emit("progress_resumed")
    _logger.info("安全验证已通过: waited_sec=%d", int(waited))
    return VERIFY_PASSED


def handle_verify_with_progress(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> str:
    return handle_verify(page, settings, events)


def print_page_debug(
    page: Any,
    context: str,
    events: EventSink = NULL_EVENTS,
) -> None:
    try:
        url = str(page.url)
    except PlaywrightError:
        url = "<无法读取>"
    try:
        title = str(page.title())
    except PlaywrightError:
        title = "<无法读取>"
    events.emit("page_debug", context=context, url=url, title=title)
