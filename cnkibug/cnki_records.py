#完成爬取结果去重、清洗和字段统计
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from .cnki_page import query_all, query_first
from .scrape_logging import count_missing_fields


@dataclass
class PageParseResult:
    records: list[list[str]] = field(default_factory=list)
    rows_seen: int = 0
    duplicates: int = 0
    skipped_no_title: int = 0
    parse_errors: int = 0

    @property
    def records_added(self) -> int:
        return len(self.records)


def parse_result_rows(
    page: Any,
    seen: set[Any],
    stats: dict[str, int],
) -> PageParseResult:
    """解析当前结果页的表格行，并维护跨页去重与统计。"""
    result = PageParseResult()
    rows = query_all(page, "result_rows")
    result.rows_seen = len(rows)
    stats["rows_seen"] += result.rows_seen

    for row in rows:
        try:
            title_el = query_first(row, "title")
            if not title_el:
                result.skipped_no_title += 1
                stats["skipped_no_title"] += 1
                continue
            title = title_el.inner_text().strip()

            href = title_el.get_attribute("href")

            author_parts = []
            for author_el in query_all(row, "author"):
                name = author_el.text_content().strip()
                if name:
                    author_parts.append(name)
            authors = "; ".join(author_parts)

            source_el = query_first(row, "source")
            source = " ".join(source_el.text_content().split()) if source_el else ""

            date_el = query_first(row, "date")
            date = date_el.text_content().strip() if date_el else ""

            dedup_key = href if href else (title, source, date)
            if dedup_key in seen:
                result.duplicates += 1
                stats["duplicates"] += 1
                continue
            seen.add(dedup_key)

            record = [title, authors, source, date]
            count_missing_fields(record, stats)
            result.records.append(record)
            stats["records_added"] += 1
        except PlaywrightError:
            result.parse_errors += 1
            stats["row_parse_errors"] += 1
            continue

    return result
