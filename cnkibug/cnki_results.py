from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .cnki_page import (
    SELECTOR_RESULT_ROWS,
    SELECTOR_RESULT_TITLE,
    query_all,
    query_first,
)
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


def wait_first_row_changed(page: Any, old_href: str, timeout: int = 15000) -> bool:
    try:
        page.wait_for_function(
            "(oldHref) => {"
            " const a = document.querySelector("
            f"'{SELECTOR_RESULT_ROWS} {SELECTOR_RESULT_TITLE}');"
            " return a && a.getAttribute('href')"
            " && a.getAttribute('href') !== oldHref; }",
            arg=old_href,
            timeout=timeout,
        )
        return True
    except PlaywrightTimeoutError:
        return False
