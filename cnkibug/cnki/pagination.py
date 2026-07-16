from __future__ import annotations

import re
import time
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from .selectors import query_all, query_first


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


def get_result_page_numbers(page: Any) -> tuple[int | None, int | None]:
    current_page = None
    total_pages = None
    try:
        page_count = query_first(page, "page_count")
        if page_count:
            count_text = (page_count.text_content() or "").strip()
            match = re.search(r"(\d+)\s*/\s*(\d+)", count_text)
            if match:
                current_page = _positive_int(match.group(1))
                total_pages = _positive_int(match.group(2))
            if total_pages is None:
                total_pages = _positive_int(page_count.get_attribute("data-pagenum"))

        current_marker = query_first(page, "current_page")
        if current_page is None and current_marker:
            current_page = _positive_int(
                current_marker.get_attribute("data-curpage")
                or current_marker.get_attribute("value")
            )
    except PlaywrightError:
        return None, None
    return current_page, total_pages


def wait_result_page_advanced(
    page: Any,
    old_href: str,
    old_next_page: str,
    old_current_page: int | None = None,
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

        if old_current_page is not None:
            new_current_page, _ = get_result_page_numbers(page)
            if new_current_page is not None and new_current_page > old_current_page:
                return True

        time.sleep(0.25)
    return False


def _positive_int(value: Any) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
