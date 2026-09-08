from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from ..core.events import EventSink, NULL_EVENTS
from ..core.settings import ScraperSettings


VERIFY_NONE = "none"
VERIFY_PASSED = "passed"
VERIFY_TIMEOUT = "timeout"
VERIFY_CANCELLED = "cancelled"
VERIFY_PAGE_CLOSED = "page_closed"

_logger = logging.getLogger("cnkibug.cnki_guard")


def _is_cnki_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == "cnki.net" or host.endswith(".cnki.net")


def _is_verify_page(page: Any) -> bool:
    try:
        url = str(page.url)
    except PlaywrightError:
        return False
    if not _is_cnki_url(url):
        return False

    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        path = ""
    if "/verify" in path:
        return True

    title = getattr(page, "title", None)
    if not callable(title):
        return False
    try:
        return "安全验证" in str(title())
    except PlaywrightError:
        return False


def handle_verify(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> str:
    if _page_closed(page):
        return VERIFY_PAGE_CLOSED
    if not _is_verify_page(page):
        return VERIFY_NONE

    _logger.warning("检测到安全验证，等待用户手动完成")
    events.emit("progress_paused")
    events.emit("verify_required")

    started_at = time.monotonic()
    waited = 0.0
    interval = 1.0
    next_notice = float(settings.verify_notice_interval_sec)
    while True:
        if _page_closed(page):
            _logger.info("安全验证等待因浏览器页面关闭而停止")
            events.emit("progress_resumed")
            return VERIFY_PAGE_CLOSED
        if not _is_verify_page(page):
            waited = max(0.0, time.monotonic() - started_at)
            break
        if events.cancel_requested():
            _logger.info("安全验证等待被用户停止")
            events.emit("progress_resumed")
            return VERIFY_CANCELLED
        waited = max(0.0, time.monotonic() - started_at)
        if waited >= settings.verify_wait_timeout_sec:
            _logger.warning("安全验证等待超时: waited_sec=%d", int(waited))
            events.emit("verify_timeout")
            return VERIFY_TIMEOUT
        if waited >= next_notice:
            remaining = int(settings.verify_wait_timeout_sec - waited)
            _logger.info("仍在等待安全验证: waited_sec=%d remaining_sec=%d", int(waited), remaining)
            events.emit("verify_waiting", remaining=remaining)
            next_notice += settings.verify_notice_interval_sec
        try:
            # Dispatch browser events so cached URL and closed state stay current.
            page.wait_for_timeout(interval * 1000)
        except PlaywrightError:
            if not _page_closed(page):
                raise
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


def _page_closed(page: Any) -> bool:
    is_closed = getattr(page, "is_closed", None)
    return bool(callable(is_closed) and is_closed())


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
