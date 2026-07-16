from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..browser.session import ScrapeSession, require_page
from ..core.settings import ScraperSettings
from .metrics import keyword_log_ref, missing_field_text, new_scrape_stats
from .models import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_STOPPED,
    STATUS_SUCCESS,
    KeywordResult,
    make_keyword_result,
)
from .pages import scrape_result_pages
from .pagination import get_first_result_title
from .results import record_dedup_key
from .resume import position_after_checkpoint
from .search import (
    SEARCH_EMPTY,
    SEARCH_FAILED,
    SEARCH_STOPPED,
    run_keyword_search,
)


_logger = logging.getLogger("cnkibug.cnki.keyword")


def scrape_keyword(
    session: ScrapeSession,
    keyword: str,
    max_pages: int,
    settings: ScraperSettings,
    keyword_index: int | None = None,
    keyword_total: int | None = None,
    start_page: int = 1,
    initial_records: list[list[str]] | None = None,
    on_page_complete: Callable[[int, list[list[str]]], None] | None = None,
    include_citation: bool = False,
) -> KeywordResult:
    current_start_page = start_page
    current_records = list(initial_records or [])
    while True:
        result = _scrape_keyword_attempt(
            session,
            keyword,
            max_pages,
            settings,
            keyword_index,
            keyword_total,
            start_page=current_start_page,
            initial_records=current_records,
            on_page_complete=on_page_complete,
            include_citation=include_citation,
        )
        if result is not None:
            return result
        current_start_page = 1
        current_records = []


def _scrape_keyword_attempt(
    session: ScrapeSession,
    keyword: str,
    max_pages: int,
    settings: ScraperSettings,
    keyword_index: int | None,
    keyword_total: int | None,
    *,
    start_page: int,
    initial_records: list[list[str]],
    on_page_complete: Callable[[int, list[list[str]]], None] | None,
    include_citation: bool,
) -> KeywordResult | None:
    page = require_page(session)
    events = session.events
    results = list(initial_records)
    keyword_ref = keyword_log_ref(
        keyword,
        keyword_index,
        keyword_total,
        include_keyword=settings.log_keywords,
    )
    stats = new_scrape_stats()
    seen: set[Any] = {record_dedup_key(record) for record in results}
    events.emit("message", text=f"\n[*] 目标关键词：{keyword}")
    _logger.info(
        "关键词开始: %s max_pages=%d start_page=%d initial_records=%d",
        keyword_ref,
        max_pages,
        start_page,
        len(results),
    )

    if start_page > max_pages:
        status = STATUS_SUCCESS if results else STATUS_FAILED
        reason = "" if results else "页级断点没有有效记录"
        _logger.info(
            "页级断点已覆盖请求页数: %s completed_page=%d "
            "max_pages=%d records=%d status=%s",
            keyword_ref,
            start_page - 1,
            max_pages,
            len(results),
            status,
        )
        return _result(keyword, keyword_index, keyword_total, results, status, reason)

    search = run_keyword_search(
        session,
        keyword,
        settings,
        keyword_ref,
    )
    if search.status == SEARCH_STOPPED:
        return _result(
            keyword,
            keyword_index,
            keyword_total,
            results,
            STATUS_STOPPED,
            search.reason,
        )
    if search.status == SEARCH_FAILED:
        return _result(
            keyword,
            keyword_index,
            keyword_total,
            results,
            STATUS_FAILED,
            search.reason,
        )
    if search.status == SEARCH_EMPTY:
        if results:
            _logger.warning(
                "页级恢复时检索结果变为空，保留断点等待重试: %s records=%d",
                keyword_ref,
                len(results),
            )
            return _result(
                keyword,
                keyword_index,
                keyword_total,
                results,
                STATUS_FAILED,
                "恢复时检索结果变为空",
            )
        _logger.info("关键词无结果: %s", keyword_ref)
        events.emit(
            "message",
            text=f"[!] 知网无「{keyword}」的检索结果，跳过。",
            level="warning",
        )
        return _result(
            keyword,
            keyword_index,
            keyword_total,
            results,
            STATUS_EMPTY,
            "知网无结果",
        )

    if start_page > 1 and not _restore_checkpoint(
        session,
        settings,
        keyword=keyword,
        keyword_ref=keyword_ref,
        start_page=start_page,
        results=results,
        on_page_complete=on_page_complete,
    ):
        if session.stop_requested:
            return _result(
                keyword,
                keyword_index,
                keyword_total,
                results,
                STATUS_STOPPED,
                session.stop_reason or "页级恢复停止",
            )
        return None

    pages = scrape_result_pages(
        session,
        settings,
        keyword=keyword,
        keyword_ref=keyword_ref,
        start_page=start_page,
        max_pages=max_pages,
        results=results,
        seen=seen,
        stats=stats,
        include_citation=include_citation,
        on_page_complete=on_page_complete,
    )
    if session.stop_requested:
        _logger.warning("关键词停止: %s total_records=%d", keyword_ref, len(results))
        events.emit(
            "message",
            text="[!] 用户中断，正在保存已抓取的数据...",
            level="warning",
        )
        return _result(
            keyword,
            keyword_index,
            keyword_total,
            results,
            STATUS_STOPPED,
            session.stop_reason or "已停止",
        )

    if pages.incomplete_reason:
        _logger.warning(
            "关键词部分完成，将在恢复时重试: %s records=%d reason=%s",
            keyword_ref,
            len(results),
            pages.incomplete_reason,
        )
        events.emit(
            "message",
            text=(
                f"[!] 当前关键词仅完成部分抓取，已保留 {len(results)} 条记录；"
                "下次恢复时将从最后保存的断点继续。"
            ),
            level="warning",
        )
        return _result(
            keyword,
            keyword_index,
            keyword_total,
            results,
            STATUS_FAILED,
            pages.incomplete_reason,
        )

    _logger.info(
        "关键词完成: %s total_records=%d rows_seen=%d duplicates=%d "
        "skipped_no_title=%d parse_errors=%d citation_success=%d "
        "citation_failed=%d missing_fields=(%s)",
        keyword_ref,
        len(results),
        stats["rows_seen"],
        stats["duplicates"],
        stats["skipped_no_title"],
        stats["row_parse_errors"],
        pages.citation_success,
        pages.citation_failed,
        missing_field_text(stats),
    )
    status = STATUS_SUCCESS if results else STATUS_FAILED
    reason = "" if results else "未解析到有效记录"
    return _result(keyword, keyword_index, keyword_total, results, status, reason)


def _restore_checkpoint(
    session: ScrapeSession,
    settings: ScraperSettings,
    *,
    keyword: str,
    keyword_ref: str,
    start_page: int,
    results: list[list[str]],
    on_page_complete: Callable[[int, list[list[str]]], None] | None,
) -> bool:
    page = require_page(session)
    _logger.info(
        "开始页级恢复定位: %s completed_page=%d resume_page=%d records=%d",
        keyword_ref,
        start_page - 1,
        start_page,
        len(results),
    )
    expected_first_title = str(results[0][0]).strip() if results and results[0] else ""
    current_first_title = get_first_result_title(page)
    checkpoint_matches = not (
        expected_first_title
        and current_first_title
        and expected_first_title != current_first_title
    )
    if not checkpoint_matches:
        _logger.warning(
            "页级恢复首页锚点变化，将从第一页重抓: %s completed_page=%d",
            keyword_ref,
            start_page - 1,
        )
    if checkpoint_matches and position_after_checkpoint(
        session,
        start_page - 1,
        settings,
        keyword_ref,
    ):
        return True
    if session.stop_requested:
        return False

    _logger.warning(
        "页级恢复定位失败，清空页级断点并从第一页重抓: "
        "%s completed_page=%d records=%d",
        keyword_ref,
        start_page - 1,
        len(results),
    )
    session.events.emit(
        "message",
        text=f"[!] 关键词「{keyword}」无法定位到第 {start_page} 页，将从第一页重新抓取。",
        level="warning",
    )
    if on_page_complete is not None:
        on_page_complete(0, list(results))
    return False


def _result(
    keyword: str,
    keyword_index: int | None,
    keyword_total: int | None,
    records: list[list[str]],
    status: str,
    reason: str = "",
) -> KeywordResult:
    return make_keyword_result(
        keyword,
        keyword_index or 0,
        keyword_total or 0,
        records,
        status,
        reason,
    )
