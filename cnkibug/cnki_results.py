from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError

from .citation_fetcher import fetch_gbt_citation
from .cnki_page import (
    SELECTOR_RESULT_ROWS,
    query_all,
    query_first,
)
from .scrape_logging import count_missing_fields


_logger = logging.getLogger("cnkibug.cnki_results")


@dataclass
class PageParseResult:
    records: list[list[str]] = field(default_factory=list)
    rows_seen: int = 0
    duplicates: int = 0
    skipped_no_title: int = 0
    parse_errors: int = 0
    citation_success: int = 0
    citation_failed: int = 0

    @property
    def records_added(self) -> int:
        return len(self.records)


def parse_result_rows(
    page: Any,
    seen: set[Any],
    stats: dict[str, int],
    *,
    include_citation: bool = False,
    citation_log_ref: str = "",
    log_titles: bool = False,
) -> PageParseResult:
    result = PageParseResult()
    none_text_fields: set[str] = set()
    rows = query_all(page, "result_rows")
    result.rows_seen = len(rows)
    stats["rows_seen"] += result.rows_seen

    for row_index, row in enumerate(rows, start=1):
        try:
            title_el = query_first(row, "title")
            if not title_el:
                result.skipped_no_title += 1
                stats["skipped_no_title"] += 1
                continue
            title = title_el.inner_text().strip()

            href = title_el.get_attribute("href") or ""
            detail_url = urljoin(page.url, href) if href else ""

            author_parts = []
            for author_el in query_all(row, "author"):
                author_text = author_el.text_content()
                if author_text is None:
                    none_text_fields.add("author")
                name = (author_text or "").strip()
                if name:
                    author_parts.append(name)
            authors = "; ".join(author_parts)

            source_el = query_first(row, "source")
            source_text = source_el.text_content() if source_el else ""
            if source_el and source_text is None:
                none_text_fields.add("source")
            source = " ".join((source_text or "").split())

            date_el = query_first(row, "date")
            date_text = date_el.text_content() if date_el else ""
            if date_el and date_text is None:
                none_text_fields.add("date")
            date = (date_text or "").strip()

            dedup_key = detail_url if detail_url else (title, source, date)
            if dedup_key in seen:
                result.duplicates += 1
                stats["duplicates"] += 1
                continue
            seen.add(dedup_key)

            record = [title, authors, source, date, detail_url]
            count_missing_fields(record, stats)
            if include_citation:
                log_ref = f"{citation_log_ref} row={row_index}".strip()
                if log_titles:
                    log_ref = f"{log_ref} title={title!r}"
                citation = fetch_gbt_citation(page, row, log_ref=log_ref)
                record.append(citation)
                if citation:
                    result.citation_success += 1
                else:
                    result.citation_failed += 1
            result.records.append(record)
        except PlaywrightError:
            result.parse_errors += 1
            stats["row_parse_errors"] += 1
            continue

    if none_text_fields:
        _logger.warning(
            "结果字段节点存在但无文本: fields=%s rows=%d",
            ",".join(sorted(none_text_fields)),
            result.rows_seen,
        )
    return result


def record_dedup_key(record: list) -> Any:
    detail_url = str(record[4]).strip() if len(record) > 4 else ""
    if detail_url:
        return detail_url
    title = str(record[0]).strip() if record else ""
    source = str(record[2]).strip() if len(record) > 2 else ""
    date = str(record[3]).strip() if len(record) > 3 else ""
    return title, source, date


def get_first_result_href(page: Any) -> str:
    try:
        rows = query_all(page, "result_rows")
        if not rows:
            return ""
        first_title = query_first(rows[0], "title")
        if not first_title:
            return ""
        return first_title.get_attribute("href") or ""
    except PlaywrightError:
        return ""


def get_first_result_title(page: Any) -> str:
    try:
        rows = query_all(page, "result_rows")
        if not rows:
            return ""
        first_title = query_first(rows[0], "title")
        if not first_title:
            return ""
        return first_title.inner_text().strip()
    except PlaywrightError:
        return ""


def get_next_page_marker(page: Any) -> str:
    try:
        next_btn = query_first(page, "next_page")
        if not next_btn:
            return ""
        return next_btn.get_attribute("data-curpage") or ""
    except PlaywrightError:
        return ""


def wait_result_page_advanced(
    page: Any,
    old_href: str,
    old_next_page: str,
    timeout: int = 15000,
) -> bool:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        new_href = get_first_result_href(page)
        if old_href and new_href and new_href != old_href:
            return True

        new_next_page = get_next_page_marker(page)
        if old_next_page and new_next_page and new_next_page != old_next_page:
            return True

        time.sleep(0.25)
    return False
