from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..browser.session import ScrapeSession, require_page
from ..core.settings import ScraperSettings
from .guard import VERIFY_PASSED, VERIFY_TIMEOUT, handle_verify_with_progress, print_page_debug
from .metrics import missing_field_text
from .pagination import (
    get_first_result_href,
    get_result_page_numbers,
    wait_result_page_advanced,
)
from .results import PageParseResult, parse_result_rows
from .selectors import SELECTOR_RESULT_ROWS, query_first


ADVANCED = "advanced"
LAST_PAGE = "last_page"
FAILED = "failed"
STOPPED = "stopped"

_logger = logging.getLogger("cnkibug.cnki.pages")


@dataclass(frozen=True)
class PageStepResult:
    parsed: PageParseResult | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class PageAdvanceResult:
    status: str
    failure_reason: str | None = None


@dataclass(frozen=True)
class PageLoopResult:
    incomplete_reason: str | None = None
    citation_success: int = 0
    citation_failed: int = 0


def scrape_result_pages(
    session: ScrapeSession,
    settings: ScraperSettings,
    *,
    keyword: str,
    keyword_ref: str,
    start_page: int,
    max_pages: int,
    results: list[list[str]],
    seen: set[Any],
    stats: dict[str, int],
    include_citation: bool = False,
    on_page_complete: Callable[[int, list[list[str]]], None] | None = None,
) -> PageLoopResult:
    page = require_page(session)
    events = session.events
    incomplete_reason = None
    citation_success = 0
    citation_failed = 0

    for current_page in range(start_page, max_pages + 1):
        try:
            events.emit("progress_updated", page=current_page)
            step = process_result_page(
                session,
                settings,
                keyword_ref=keyword_ref,
                current_page=current_page,
                seen=seen,
                stats=stats,
                include_citation=include_citation,
            )
            if step.parsed is None:
                incomplete_reason = step.failure_reason
                break

            page_parse = step.parsed
            citation_success += page_parse.citation_success
            citation_failed += page_parse.citation_failed
            results.extend(page_parse.records)
            _log_page_result(
                page_parse,
                results,
                stats,
                keyword_ref,
                current_page,
                settings.log_scraped_records,
                session,
            )

            if on_page_complete is not None:
                on_page_complete(current_page, list(results))

            if current_page < max_pages:
                advance = advance_result_page(
                    session,
                    settings,
                    keyword=keyword,
                    keyword_ref=keyword_ref,
                    current_page=current_page,
                    max_pages=max_pages,
                )
                if advance.status == ADVANCED:
                    continue
                if advance.status == FAILED:
                    incomplete_reason = advance.failure_reason
                break

        except PlaywrightError:
            if page.is_closed():
                session.request_stop("浏览器页面已关闭")
                _logger.warning(
                    "浏览器页面已关闭，结束关键词: %s page=%d",
                    keyword_ref,
                    current_page,
                )
                events.emit(
                    "message",
                    text="[!] 检测到浏览器被手动关闭，正在为您安全中止并保存已抓取的数据...",
                    level="warning",
                )
                break
            _logger.warning(
                "结果页处理异常，提前结束关键词: %s page=%d",
                keyword_ref,
                current_page,
                exc_info=True,
            )
            incomplete_reason = f"第 {current_page} 页处理异常"
            events.emit(
                "message",
                text=f"[!] 第 {current_page} 页处理异常，已停止当前关键词，避免重复抓取旧页面。",
                level="warning",
            )
            break
        except KeyboardInterrupt:
            session.request_stop("用户中断")
            _logger.warning(
                "关键词抓取被用户中断: %s page=%d",
                keyword_ref,
                current_page,
            )
            break

    return PageLoopResult(
        incomplete_reason=incomplete_reason,
        citation_success=citation_success,
        citation_failed=citation_failed,
    )


def process_result_page(
    session: ScrapeSession,
    settings: ScraperSettings,
    *,
    keyword_ref: str,
    current_page: int,
    seen: set[Any],
    stats: dict[str, int],
    include_citation: bool,
) -> PageStepResult:
    page = require_page(session)
    events = session.events
    try:
        page.wait_for_selector(SELECTOR_RESULT_ROWS, timeout=settings.timeout_selector_ms)
    except PlaywrightTimeoutError:
        verify_status = handle_verify_with_progress(page, settings, events)
        if verify_status == VERIFY_PASSED:
            try:
                page.wait_for_selector(
                    SELECTOR_RESULT_ROWS,
                    timeout=settings.timeout_selector_ms,
                )
            except PlaywrightTimeoutError:
                reason = f"第 {current_page} 页验证通过后仍加载超时"
                _logger.warning(
                    "验证通过后结果页表格仍等待超时，提前结束关键词: %s page=%d",
                    keyword_ref,
                    current_page,
                )
                events.emit(
                    "message",
                    text=f"[!] 第 {current_page} 页验证通过后仍加载超时，已停止当前关键词，避免重复抓取旧页面。",
                    level="warning",
                )
                return PageStepResult(failure_reason=reason)
        elif verify_status == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            _logger.warning(
                "结果页等待时安全验证超时: %s page=%d",
                keyword_ref,
                current_page,
            )
            return PageStepResult()
        else:
            reason = f"第 {current_page} 页结果表格等待超时"
            _logger.warning(
                "结果页表格等待超时，提前结束关键词: %s page=%d",
                keyword_ref,
                current_page,
            )
            events.emit(
                "message",
                text=f"[!] 第 {current_page} 页等待超时（非验证），已停止当前关键词，避免重复抓取旧页面。",
                level="warning",
            )
            print_page_debug(page, f"第 {current_page} 页结果表格等待超时", events)
            return PageStepResult(failure_reason=reason)

    time.sleep(random.uniform(2, 5))
    if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning(
            "结果页解析前安全验证超时: %s page=%d",
            keyword_ref,
            current_page,
        )
        return PageStepResult()

    actual_page, _ = get_result_page_numbers(page)
    if actual_page is not None and actual_page != current_page:
        reason = f"页面实际为第 {actual_page} 页，与预期第 {current_page} 页不一致"
        _logger.warning(
            "结果页页码与预期不一致，提前结束关键词: "
            "%s expected_page=%d actual_page=%d",
            keyword_ref,
            current_page,
            actual_page,
        )
        events.emit(
            "message",
            text=(
                f"[!] 当前页面显示为第 {actual_page} 页，但程序预期第 {current_page} 页，"
                "已保留断点并停止当前关键词。"
            ),
            level="warning",
        )
        return PageStepResult(failure_reason=reason)

    citation_options = {}
    if include_citation:
        citation_options = {
            "include_citation": True,
            "citation_log_ref": f"{keyword_ref} page={current_page}",
            "log_titles": settings.log_scraped_records,
        }
    page_parse = parse_result_rows(page, seen, stats, **citation_options)
    unreadable_rows = page_parse.skipped_no_title + page_parse.parse_errors
    if page_parse.rows_seen == 0:
        reason = f"第 {current_page} 页结果行在解析时消失"
        _logger.warning(
            "结果页解析时未发现任何结果行，提前结束关键词: %s page=%d",
            keyword_ref,
            current_page,
        )
        events.emit(
            "message",
            text=f"[!] 第 {current_page} 页结果区域异常，已保留断点并停止当前关键词。",
            level="warning",
        )
        return PageStepResult(failure_reason=reason)
    if unreadable_rows == page_parse.rows_seen:
        reason = f"第 {current_page} 页全部结果均无法解析标题"
        _logger.warning(
            "结果页所有行均无法解析，提前结束关键词: "
            "%s page=%d rows=%d skipped_no_title=%d parse_errors=%d",
            keyword_ref,
            current_page,
            page_parse.rows_seen,
            page_parse.skipped_no_title,
            page_parse.parse_errors,
        )
        events.emit(
            "message",
            text=(
                f"[!] 第 {current_page} 页有结果，但论文标题均无法读取，"
                "可能是知网页面结构发生变化；已保留断点。"
            ),
            level="warning",
        )
        return PageStepResult(failure_reason=reason)
    return PageStepResult(parsed=page_parse)


def advance_result_page(
    session: ScrapeSession,
    settings: ScraperSettings,
    *,
    keyword: str,
    keyword_ref: str,
    current_page: int,
    max_pages: int,
) -> PageAdvanceResult:
    page = require_page(session)
    events = session.events
    next_btn = query_first(page, "next_page")
    if not next_btn:
        actual_page, total_pages = get_result_page_numbers(page)
        if actual_page is not None and total_pages is not None and actual_page >= total_pages:
            _logger.info(
                "已确认到达结果末页: %s page=%d actual_page=%d total_pages=%d",
                keyword_ref,
                current_page,
                actual_page,
                total_pages,
            )
            events.emit(
                "message",
                text=f"[*] 已到结果末页（{actual_page}/{total_pages}）。",
                level="dim",
            )
            return PageAdvanceResult(LAST_PAGE)

        reason = f"第 {current_page} 页后未找到下一页且无法确认末页"
        _logger.warning(
            "下一页按钮缺失且无法确认末页，提前结束关键词: "
            "%s page=%d actual_page=%s total_pages=%s",
            keyword_ref,
            current_page,
            actual_page,
            total_pages,
        )
        events.emit(
            "message",
            text="[!] 没找到下一页按钮，也无法确认已经到达末页；已保留断点。",
            level="warning",
        )
        print_page_debug(page, f"第 {current_page} 页无法确认是否末页", events)
        return PageAdvanceResult(FAILED, reason)

    old_first_href = get_first_result_href(page)
    old_next_page = next_btn.get_attribute("data-curpage") or ""
    old_current_page, _ = get_result_page_numbers(page)
    next_btn.click(timeout=settings.timeout_selector_ms)
    page_advanced = False
    for confirm_attempt in range(1, settings.max_advance_fail + 1):
        if wait_result_page_advanced(
            page,
            old_href=old_first_href,
            old_next_page=old_next_page,
            old_current_page=old_current_page,
            timeout=settings.timeout_selector_ms,
        ):
            page_advanced = True
            break

        verify_status = handle_verify_with_progress(page, settings, events)
        if verify_status == VERIFY_TIMEOUT:
            session.request_stop("安全验证等待超时", verify_timeout=True)
            _logger.warning(
                "翻页确认期间安全验证超时: %s page=%d",
                keyword_ref,
                current_page,
            )
            return PageAdvanceResult(STOPPED)
        if verify_status == VERIFY_PASSED and wait_result_page_advanced(
            page,
            old_href=old_first_href,
            old_next_page=old_next_page,
            old_current_page=old_current_page,
            timeout=settings.timeout_selector_ms,
        ):
            page_advanced = True
            break

        _logger.warning(
            "翻页后未确认到结果变化: %s page=%d confirm_attempt=%d max_attempts=%d",
            keyword_ref,
            current_page,
            confirm_attempt,
            settings.max_advance_fail,
        )
        events.emit(
            "message",
            text=f"[!] 翻页结果尚未确认（{confirm_attempt}/{settings.max_advance_fail}）。",
            level="warning",
        )

    if not page_advanced:
        reason = f"第 {current_page} 页后翻页结果未确认"
        _logger.warning(
            "翻页结果无法确认，提前结束关键词: "
            "%s effective_pages=%d requested_pages=%d",
            keyword_ref,
            current_page,
            max_pages,
        )
        events.emit(
            "message",
            text=f"[x] 无法确认是否已进入下一页，已在第 {current_page} 页保留断点并停止关键词「{keyword}」。",
            level="error",
        )
        print_page_debug(page, f"第 {current_page} 页翻页结果无法确认", events)
        return PageAdvanceResult(FAILED, reason)

    time.sleep(random.uniform(1, 2))
    if handle_verify_with_progress(page, settings, events) == VERIFY_TIMEOUT:
        session.request_stop("安全验证等待超时", verify_timeout=True)
        _logger.warning("翻页后安全验证超时: %s page=%d", keyword_ref, current_page)
        return PageAdvanceResult(STOPPED)
    return PageAdvanceResult(ADVANCED)


def _log_page_result(
    page_parse: PageParseResult,
    results: list[list[str]],
    stats: dict[str, int],
    keyword_ref: str,
    current_page: int,
    log_records: bool,
    session: ScrapeSession,
) -> None:
    for record in page_parse.records:
        session.events.emit("message", text=f"  → {record[0]}", level="success")
    if log_records:
        _logger.info(
            "结果页完成: %s page=%d rows=%d added=%d duplicates=%d "
            "skipped_no_title=%d parse_errors=%d total_records=%d "
            "citation_success=%d citation_failed=%d missing_fields=(%s)",
            keyword_ref,
            current_page,
            page_parse.rows_seen,
            page_parse.records_added,
            page_parse.duplicates,
            page_parse.skipped_no_title,
            page_parse.parse_errors,
            len(results),
            page_parse.citation_success,
            page_parse.citation_failed,
            missing_field_text(stats),
        )
    else:
        _logger.info(
            "结果页完成: %s page=%d rows=%d added=%d total_records=%d",
            keyword_ref,
            current_page,
            page_parse.rows_seen,
            page_parse.records_added,
            len(results),
        )
