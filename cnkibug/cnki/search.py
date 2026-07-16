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
from .guard import VERIFY_TIMEOUT, handle_verify, handle_verify_with_progress, print_page_debug
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


def warmup(session: ScrapeSession, settings: ScraperSettings) -> bool:
    page = require_page(session)
    events = session.events
    _logger.info("预热开始")
    try:
        with events.activity("少女祈祷中..."):
            page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
            page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)
        _logger.info("预热首页加载完成")
        if handle_verify(page, settings, events) == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            _logger.warning("预热因安全验证超时停止")
        if not session.stop_requested:
            with events.activity("少女祈祷中..."):
                page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
                page.wait_for_load_state("load", timeout=settings.timeout_load_ms)
                page.fill(
                    SELECTOR_SEARCH_INPUT,
                    WARMUP_KEYWORD,
                    timeout=settings.timeout_selector_ms,
                )
                time.sleep(random.uniform(0.5, 1.5))
                page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
                page.wait_for_selector(
                    SELECTOR_RESULT_ROWS,
                    timeout=settings.timeout_selector_ms,
                )
            _logger.info("预热检索完成")
            if handle_verify(page, settings, events) == VERIFY_TIMEOUT:
                session.request_stop("安全验证等待超时", verify_timeout=True)
                _logger.warning("预热检索后因安全验证超时停止")
        if session.stop_requested:
            _logger.warning("预热停止")
            return False
        events.emit("message", text="[*] 预热完成，开始正式抓取。", level="dim")
        _logger.info("预热成功")
        return True
    except (PlaywrightTimeoutError, PlaywrightError) as warmup_err:
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
        page.goto(CNKI_HOME_URL, timeout=settings.timeout_goto_ms)
        page.wait_for_load_state("domcontentloaded", timeout=settings.timeout_load_ms)


def open_search_page(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> None:
    with events.activity("少女祈祷中..."):
        page.goto(CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
        page.wait_for_load_state("load", timeout=settings.timeout_load_ms)


def submit_search(
    page: Any,
    keyword: str,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
) -> None:
    with events.activity("少女祈祷中..."):
        page.fill(SELECTOR_SEARCH_INPUT, keyword, timeout=settings.timeout_selector_ms)
        time.sleep(random.uniform(0.5, 1.5))
        page.click(SELECTOR_SEARCH_BUTTON, timeout=settings.timeout_selector_ms)
        time.sleep(random.uniform(1, 2))


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
    page = require_page(session)
    events = session.events
    try:
        open_home_page(page, settings, events)
    except PlaywrightTimeoutError:
        _logger.warning("关键词首页预热超时，跳过: %s", keyword_ref)
        events.emit("message", text="[!] 预热请求超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热超时")
    except PlaywrightError as exc:
        _logger.warning("关键词首页预热失败，跳过: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 预热请求失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热失败")
    if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因首页安全验证超时停止: %s", keyword_ref)
        return SearchResult(SEARCH_STOPPED, "安全验证等待超时")

    try:
        open_search_page(page, settings, events)
    except PlaywrightTimeoutError:
        _logger.warning("检索页加载超时，跳过关键词: %s", keyword_ref)
        events.emit("message", text="[!] 检索页加载超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载超时")
    except PlaywrightError as exc:
        _logger.warning("检索页加载失败，跳过关键词: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 检索页加载失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载失败")
    if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词因检索页安全验证超时停止: %s", keyword_ref)
        return SearchResult(SEARCH_STOPPED, "安全验证等待超时")

    submit_search(page, keyword, settings, events)
    _logger.info("关键词检索已提交: %s", keyword_ref)
    if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("关键词提交后因安全验证超时停止: %s", keyword_ref)
        return SearchResult(SEARCH_STOPPED, "安全验证等待超时")

    while True:
        try:
            outcome = wait_search_outcome(page, settings)
        except PlaywrightTimeoutError:
            _logger.warning("关键词结果加载超时，跳过: %s", keyword_ref)
            print_page_debug(page, f"关键词「{keyword}」结果加载超时", events)
            events.emit(
                "message",
                text=f"[!] 关键词「{keyword}」结果加载超时，跳过。",
                level="warning",
            )
            return SearchResult(SEARCH_FAILED, "结果加载超时")

        if outcome != "verify":
            return SearchResult(outcome)

        _logger.warning("等待检索结果期间检测到安全验证: %s", keyword_ref)
        if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            return SearchResult(SEARCH_STOPPED, "安全验证等待超时")
