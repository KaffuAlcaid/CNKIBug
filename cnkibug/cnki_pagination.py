#状态确认
from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import Error as PlaywrightError

from .cnki_page import (
    SELECTOR_RESULT_ROWS,
    SELECTOR_RESULT_TITLE,
    query_all,
    query_first,
)


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
    """等待翻页完成。"""
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
    """等待结果列表首行详情链接变为与 old_href 不同的值。"""
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
