from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..core.events import EventSink, NULL_EVENTS
from ..core.settings import ScraperSettings
from ..browser.session import ScrapeSession, require_page
from .guard import (
    VERIFY_CANCELLED,
    VERIFY_TIMEOUT,
    handle_verify,
    handle_verify_with_progress,
    print_page_debug,
)
from .selectors import (
    SELECTOR_NO_CONTENT,
    SELECTOR_RESULT_ROWS,
    SELECTOR_SEARCH_BUTTON,
    SELECTOR_SEARCH_INPUT,
)


CNKI_HOME_URL = "https://www.cnki.net/"
CNKI_SEARCH_URL = "https://kns.cnki.net/kns8s/"
WARMUP_KEYWORD = "焊接"

_logger = logging.getLogger("cnkibug.cnki.search")

SEARCH_RESULTS = "has_results"
SEARCH_EMPTY = "no_content"
SEARCH_FAILED = "failed"
SEARCH_STOPPED = "stopped"


@dataclass(frozen=True)
class SearchResult:
    status: str
    reason: str = ""


def _wait_without_session(events: EventSink, seconds: float) -> bool:
    waited = 0.0
    wait_seconds = max(0.0, seconds)
    interval = 0.1
    while waited < wait_seconds:
        if events.cancel_requested():
            return False
        current_interval = min(interval, wait_seconds - waited)
        time.sleep(current_interval)
        waited += current_interval
    return not events.cancel_requested()


def _verify_stop_reason(session: ScrapeSession, verify_status: str) -> str:
    if verify_status == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        return "安全验证等待超时"
    if verify_status == VERIFY_CANCELLED:
        if not session.acknowledge_stop_request():
            session.request_stop("用户请求停止")
        return session.stop_reason or "用户请求停止"
    if session.acknowledge_stop_request():
        return session.stop_reason or "用户请求停止"
    return ""


def _stopped_result(session: ScrapeSession) -> SearchResult:
    session.acknowledge_stop_request()
    return SearchResult(SEARCH_STOPPED, session.stop_reason or "用户请求停止")


def warmup(session: ScrapeSession, settings: ScraperSettings) -> bool:
    events = session.events
    _logger.info("预热开始")
    if session.acknowledge_stop_request():
        _logger.warning("预热开始前已停止")
        return False
    page = require_page(session)
    try:
        with events.activity("少女祈祷中..."):
            if session.acknowledge_stop_request():
                return False
            page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
            if session.acknowledge_stop_request():
                return False
            page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)
            if session.acknowledge_stop_request():
                return False
        _logger.info("预热首页加载完成")
        stop_reason = _verify_stop_reason(
            session,
            handle_verify(page, settings, events),
        )
        if stop_reason:
            _logger.warning("预热因停止请求结束: reason=%s", stop_reason)
            return False

        with events.activity("少女祈祷中..."):
            if session.acknowledge_stop_request():
                return False
            page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
            if session.acknowledge_stop_request():
                return False
            page.wait_for_load_state("load", timeout=settings.timeout_load_ms)
            if session.acknowledge_stop_request():
                return False
            page.fill(
                SELECTOR_SEARCH_INPUT,
                WARMUP_KEYWORD,
                timeout=settings.timeout_selector_ms,
            )
            if not session.wait_interruptibly(random.uniform(0.5, 1.5)):
                return False
            page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
            if session.acknowledge_stop_request():
                return False
            page.wait_for_selector(
                SELECTOR_RESULT_ROWS,
                timeout=settings.timeout_selector_ms,
            )
            if session.acknowledge_stop_request():
                return False
        _logger.info("预热检索完成")
        stop_reason = _verify_stop_reason(
            session,
            handle_verify(page, settings, events),
        )
        if stop_reason:
            _logger.warning("预热检索后停止: reason=%s", stop_reason)
            return False
        if session.acknowledge_stop_request():
            _logger.warning("预热停止")
            return False
        events.emit("message", text="[*] 预热完成，开始正式抓取。", level="dim")
        _logger.info("预热成功")
        return True
    except (PlaywrightTimeoutError, PlaywrightError) as warmup_err:
        if session.acknowledge_stop_request():
            _logger.warning("预热因用户停止结束")
            return False
        _logger.warning("预热未完全成功，继续正式抓取: %s", warmup_err)
        events.emit(
            "message",
            text=f"[!] 预热搜索未完全成功 ({warmup_err})，继续正式抓取。",
            level="warning",
        )
        return False


def open_home_page(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> None:
    with events.activity("少女祈祷中..."):
        if events.cancel_requested():
            return
        page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
        if events.cancel_requested():
            return
        page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)


def open_search_page(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> None:
    with events.activity("少女祈祷中..."):
        if events.cancel_requested():
            return
        page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
        if events.cancel_requested():
            return
        page.wait_for_load_state("load", timeout=settings.timeout_load_ms)


def submit_search(
    page: Any,
    keyword: str,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> None:
    with events.activity("少女祈祷中..."):
        if events.cancel_requested():
            return
        page.fill(SELECTOR_SEARCH_INPUT, keyword, timeout=settings.timeout_selector_ms)
        if not _wait_without_session(events, random.uniform(0.5, 1.5)):
            return
        page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
        _wait_without_session(events, random.uniform(1, 2))


def wait_search_outcome(page: Any, settings: ScraperSettings) -> str:
    return page.wait_for_function(
        """(selectors) => {
            if (location.pathname.includes('/verify')) return 'verify';
            if (document.querySelector(selectors.resultRows)) return 'has_results';
            if (document.querySelector(selectors.noContent)) return 'no_content';
            return false;
        }""",
        arg={
            "resultRows": SELECTOR_RESULT_ROWS,
            "noContent": SELECTOR_NO_CONTENT,
        },
        timeout=settings.timeout_selector_ms,
    ).json_value()


def run_keyword_search(
    session: ScrapeSession,
    keyword: str,
    settings: ScraperSettings,
    keyword_ref: str,
) -> SearchResult:
    if session.acknowledge_stop_request():
        return _stopped_result(session)
    page = require_page(session)
    events = session.events
    try:
        open_home_page(page, settings, events)
    except PlaywrightTimeoutError:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        _logger.warning("关键词首页预热超时，跳过: %s", keyword_ref)
        events.emit("message", text="[!] 预热请求超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热超时")
    except PlaywrightError as exc:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        _logger.warning("关键词首页预热失败，跳过: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 预热请求失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热失败")
    if session.acknowledge_stop_request():
        return _stopped_result(session)
    stop_reason = _verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词因首页安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    if session.acknowledge_stop_request():
        return _stopped_result(session)
    try:
        open_search_page(page, settings, events)
    except PlaywrightTimeoutError:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        _logger.warning("检索页加载超时，跳过关键词: %s", keyword_ref)
        events.emit("message", text="[!] 检索页加载超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载超时")
    except PlaywrightError as exc:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        _logger.warning("检索页加载失败，跳过关键词: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 检索页加载失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载失败")
    if session.acknowledge_stop_request():
        return _stopped_result(session)
    stop_reason = _verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词因检索页安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    if session.acknowledge_stop_request():
        return _stopped_result(session)
    submit_search(page, keyword, settings, events)
    if session.acknowledge_stop_request():
        return _stopped_result(session)
    _logger.info("关键词检索已提交: %s", keyword_ref)
    stop_reason = _verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词提交后因安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    while True:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        try:
            outcome = wait_search_outcome(page, settings)
        except PlaywrightTimeoutError:
            if session.acknowledge_stop_request():
                return _stopped_result(session)
            _logger.warning("关键词结果加载超时，跳过: %s", keyword_ref)
            print_page_debug(page, f"关键词「{keyword}」结果加载超时", events)
            events.emit(
                "message",
                text=f"[!] 关键词「{keyword}」结果加载超时，跳过。",
                level="warning",
            )
            return SearchResult(SEARCH_FAILED, "结果加载超时")

        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if outcome != "verify":
            return SearchResult(outcome)

        _logger.warning("等待检索结果期间检测到安全验证: %s", keyword_ref)
        stop_reason = _verify_stop_reason(
            session,
            handle_verify_with_progress(page, settings, events),
        )
        if stop_reason:
            return SearchResult(SEARCH_STOPPED, stop_reason)
