from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError

from .citation import fetch_gbt_citation
from .selectors import (
    SELECTOR_RESULT_ROWS,
    query_all,
    query_first,
)
from .metrics import count_missing_fields


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
    detail_success: int = 0
    detail_failed: int = 0
    keywords_present: int = 0
    abstracts_present: int = 0
    cancelled: bool = False

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
    stop_requested: Callable[[], bool] | None = None,
) -> PageParseResult:
    result = PageParseResult()
    none_text_fields: set[str] = set()
    if _stop_requested(stop_requested):
        result.cancelled = True
        return result

    rows = query_all(page, "result_rows")
    result.rows_seen = len(rows)
    pending_seen = set(seen)
    pending_stats = {key: 0 for key in stats}
    pending_stats["rows_seen"] = result.rows_seen

    for row_index, row in enumerate(rows, start=1):
        if _stop_requested(stop_requested):
            result.cancelled = True
            return result
        try:
            title_el = query_first(row, "title")
            if not title_el:
                result.skipped_no_title += 1
                pending_stats["skipped_no_title"] += 1
                continue
            title = title_el.inner_text().strip()
            if not title:
                result.skipped_no_title += 1
                pending_stats["skipped_no_title"] += 1
                continue

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

            if _stop_requested(stop_requested):
                result.cancelled = True
                return result

            dedup_key = detail_url if detail_url else (title, source, date)
            if dedup_key in pending_seen:
                result.duplicates += 1
                pending_stats["duplicates"] += 1
                continue

            record = [title, authors, source, date, detail_url]
            if include_citation:
                log_ref = f"{citation_log_ref} row={row_index}".strip()
                if log_titles:
                    log_ref = f"{log_ref} title={title!r}"
                citation = fetch_gbt_citation(page, row, log_ref=log_ref)
                if _stop_requested(stop_requested):
                    result.cancelled = True
                    return result
                record.append(citation)
                if citation:
                    result.citation_success += 1
                else:
                    result.citation_failed += 1

            if _stop_requested(stop_requested):
                result.cancelled = True
                return result
            pending_seen.add(dedup_key)
            count_missing_fields(record, pending_stats)
            result.records.append(record)
        except PlaywrightError:
            if _stop_requested(stop_requested):
                result.cancelled = True
                return result
            result.parse_errors += 1
            pending_stats["row_parse_errors"] += 1
            continue

    if _stop_requested(stop_requested):
        result.cancelled = True
        return result

    seen.update(pending_seen)
    for key, value in pending_stats.items():
        stats[key] += value

    if none_text_fields:
        _logger.warning(
            "结果字段节点存在但无文本: fields=%s rows=%d",
            ",".join(sorted(none_text_fields)),
            result.rows_seen,
        )
    return result


def _stop_requested(callback: Callable[[], bool] | None) -> bool:
    return bool(callback is not None and callback())


def record_dedup_key(record: list) -> Any:
    detail_url = str(record[4]).strip() if len(record) > 4 else ""
    if detail_url:
        return detail_url
    title = str(record[0]).strip() if record else ""
    source = str(record[2]).strip() if len(record) > 2 else ""
    date = str(record[3]).strip() if len(record) > 3 else ""
    return title, source, date
