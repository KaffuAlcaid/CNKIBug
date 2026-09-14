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
from ..core.search_query import AdvancedQuery
from ..browser.session import ScrapeSession, require_page
from .guard import (
    handle_verify,
    handle_verify_with_progress,
    print_page_debug,
    verify_stop_reason,
)
from .selectors import (
    SELECTOR_NO_CONTENT,
    SELECTOR_RESULT_ROWS,
    SELECTOR_SEARCH_BUTTON,
    SELECTOR_SEARCH_INPUT,
)
from .advanced import submit_advanced_search


CNKI_HOME_URL = "https://www.cnki.net/"
CNKI_SEARCH_URL = "https://kns.cnki.net/kns8s/"
CNKI_ADVANCED_URL = "https://kns.cnki.net/kns8s/AdvSearch"
CNKI_OVERSEA_MARKER = "oversea.cnki.net"
WARMUP_KEYWORD = "焊接"

_logger = logging.getLogger("cnkibug.cnki.search")

SEARCH_RESULTS = "has_results"
SEARCH_EMPTY = "no_content"
SEARCH_FAILED = "failed"
SEARCH_STOPPED = "stopped"
SEARCH_OVERSEA = "oversea"


class CNKIOverseaRedirectError(RuntimeError):
    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(
            "检测到 CNKI 海外页面，当前抓取仅支持国内站点，"
            "请关闭代理或切换到中国大陆出口后重试。"
        )


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


def _stopped_result(session: ScrapeSession) -> SearchResult:
    session.acknowledge_stop_request()
    return SearchResult(SEARCH_STOPPED, session.stop_reason or "用户请求停止")


def _ensure_supported_site(page: Any, stage: str) -> None:
    url = str(page.url)
    _logger.info("页面导航完成: stage=%s url=%s", stage, url)
    if CNKI_OVERSEA_MARKER in url.lower():
        _logger.error("检测到 CNKI 海外页面: stage=%s url=%s", stage, url)
        raise CNKIOverseaRedirectError(url)


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
            _ensure_supported_site(page, "预热首页")
        _logger.info("预热首页加载完成")
        stop_reason = verify_stop_reason(
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
            _ensure_supported_site(page, "预热检索页")
            stop_reason = verify_stop_reason(
                session,
                handle_verify(page, settings, events),
            )
            if stop_reason:
                _logger.warning("预热检索页因验证或停止请求结束: reason=%s", stop_reason)
                return False
            # Verification can navigate to a document whose form scripts are still loading.
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
            while True:
                outcome = wait_search_outcome(page, settings)
                if outcome == SEARCH_OVERSEA:
                    _ensure_supported_site(page, "预热结果页")
                if outcome != "verify":
                    break
                stop_reason = verify_stop_reason(
                    session,
                    handle_verify(page, settings, events),
                )
                if stop_reason:
                    _logger.warning("预热结果页因验证或停止请求结束: reason=%s", stop_reason)
                    return False
            if session.acknowledge_stop_request():
                return False
        _logger.info("预热检索完成")
        stop_reason = verify_stop_reason(
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
        if session.acknowledge_page_closed(page):
            _logger.warning("预热因浏览器页面关闭而停止")
            return False
        if "ERR_CERT_" in str(warmup_err):
            reason = "CNKI 连接证书校验失败，当前代理或网络环境不受支持，请关闭代理后重试。"
            session.request_stop(reason)
            _logger.error("预热因证书校验失败停止: %s", warmup_err)
            events.emit("message", text=f"[x] {reason}", level="error")
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
        _ensure_supported_site(page, "关键词首页")


def open_search_page(
    page: Any,
    settings: ScraperSettings,
    events: EventSink = NULL_EVENTS,
    *,
    advanced: bool = False,
) -> None:
    with events.activity("少女祈祷中..."):
        if events.cancel_requested():
            return
        page.goto(CNKI_ADVANCED_URL if advanced else CNKI_SEARCH_URL, timeout=settings.timeout_goto_ms)
        if events.cancel_requested():
            return
        page.wait_for_load_state("load", timeout=settings.timeout_load_ms)
        _ensure_supported_site(page, "关键词检索页")


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
            const href = location.href.toLowerCase();
            const host = location.hostname.toLowerCase();
            const path = location.pathname.toLowerCase();
            const isCnki = host === 'cnki.net' || host.endsWith('.cnki.net');
            if (href.includes('oversea.cnki.net')) return 'oversea';
            if (isCnki && (path.includes('/verify') ||
                (document.title || '').includes('安全验证'))) return 'verify';
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
    *,
    advanced_query: AdvancedQuery | None = None,
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
        if session.acknowledge_page_closed(page):
            return _stopped_result(session)
        _logger.warning("关键词首页预热超时，跳过: %s", keyword_ref)
        events.emit("message", text="[!] 预热请求超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热超时")
    except PlaywrightError as exc:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if session.acknowledge_page_closed(page):
            return _stopped_result(session)
        _logger.warning("关键词首页预热失败，跳过: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 预热请求失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "首页预热失败")
    if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
        return _stopped_result(session)
    stop_reason = verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词因首页安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    if session.acknowledge_stop_request():
        return _stopped_result(session)
    try:
        if advanced_query is None:
            open_search_page(page, settings, events)
        else:
            open_search_page(page, settings, events, advanced=True)
    except PlaywrightTimeoutError:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if session.acknowledge_page_closed(page):
            return _stopped_result(session)
        _logger.warning("检索页加载超时，跳过关键词: %s", keyword_ref)
        events.emit("message", text="[!] 检索页加载超时，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载超时")
    except PlaywrightError as exc:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if session.acknowledge_page_closed(page):
            return _stopped_result(session)
        _logger.warning("检索页加载失败，跳过关键词: %s error=%s", keyword_ref, exc)
        events.emit("message", text=f"[!] 检索页加载失败: {exc}，跳过该关键词。", level="warning")
        return SearchResult(SEARCH_FAILED, "检索页加载失败")
    if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
        return _stopped_result(session)
    stop_reason = verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词因检索页安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
        return _stopped_result(session)
    try:
        page.wait_for_load_state("load", timeout=settings.timeout_load_ms)
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if advanced_query is None:
            submit_search(page, keyword, settings, events)
        else:
            submit_advanced_search(page, advanced_query, settings, events)
    except PlaywrightError:
        if session.acknowledge_stop_request():
            return _stopped_result(session)
        if session.acknowledge_page_closed(page):
            return _stopped_result(session)
        raise
    if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
        return _stopped_result(session)
    _logger.info("关键词检索已提交: %s", keyword_ref)
    stop_reason = verify_stop_reason(
        session,
        handle_verify_with_progress(page, settings, events),
    )
    if stop_reason:
        _logger.warning("关键词提交后因安全验证停止: %s reason=%s", keyword_ref, stop_reason)
        return SearchResult(SEARCH_STOPPED, stop_reason)

    while True:
        if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
            return _stopped_result(session)
        try:
            _ensure_supported_site(page, "关键词结果页")
            outcome = wait_search_outcome(page, settings)
        except PlaywrightTimeoutError:
            if session.acknowledge_stop_request():
                return _stopped_result(session)
            if session.acknowledge_page_closed(page):
                return _stopped_result(session)
            _logger.warning("关键词结果加载超时，跳过: %s", keyword_ref)
            print_page_debug(page, f"关键词「{keyword}」结果加载超时", events)
            events.emit(
                "message",
                text=f"[!] 关键词「{keyword}」结果加载超时，跳过。",
                level="warning",
            )
            return SearchResult(SEARCH_FAILED, "结果加载超时")

        if session.acknowledge_stop_request() or session.acknowledge_page_closed(page):
            return _stopped_result(session)
        if outcome == SEARCH_OVERSEA:
            _ensure_supported_site(page, "关键词结果页")
        if outcome != "verify":
            return SearchResult(outcome)

        _logger.warning("等待检索结果期间检测到安全验证: %s", keyword_ref)
        stop_reason = verify_stop_reason(
            session,
            handle_verify_with_progress(page, settings, events),
        )
        if stop_reason:
            return SearchResult(SEARCH_STOPPED, stop_reason)
