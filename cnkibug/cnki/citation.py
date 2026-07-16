from __future__ import annotations

import logging
from typing import Any

from playwright.sync_api import Error as PlaywrightError


CITATION_TIMEOUT_MS = 5000

_QUOTE_BUTTON_SELECTOR = "a.icon-quote > i"
_QUOTE_POPUP_SELECTOR = ".layui-layer.quote-pop"
_QUOTE_CLOSE_SELECTOR = ".layui-layer-close"
_GBT_LABEL = "GB/T 7714"
_logger = logging.getLogger("cnkibug.citation_fetcher")


def _close_popup(popup: Any, timeout_ms: int) -> None:
    try:
        close_button = popup.locator(_QUOTE_CLOSE_SELECTOR).first
        if close_button.count():
            close_button.click(timeout=min(timeout_ms, 1000))
            popup.wait_for(state="hidden", timeout=min(timeout_ms, 1000))
    except PlaywrightError as exc:
        _logger.debug("引用弹层关闭失败: error=%s", exc)


def fetch_gbt_citation(
    page: Any,
    row: Any,
    *,
    log_ref: str,
    timeout_ms: int = CITATION_TIMEOUT_MS,
) -> str:
    popup = None
    try:
        existing_popup = page.locator(_QUOTE_POPUP_SELECTOR)
        if existing_popup.count():
            _close_popup(existing_popup.last, timeout_ms)

        quote_button = row.query_selector(_QUOTE_BUTTON_SELECTOR)
        if quote_button is None:
            _logger.warning("引用按钮不存在: %s", log_ref)
            return ""

        quote_button.click(timeout=timeout_ms)
        popup = page.locator(_QUOTE_POPUP_SELECTOR).last
        popup.wait_for(state="visible", timeout=timeout_ms)

        quote_row = popup.locator("tr").filter(has_text=_GBT_LABEL).first
        quote_cell = quote_row.locator("td.quote-r").first
        quote_cell.wait_for(state="visible", timeout=timeout_ms)
        citation = (quote_cell.inner_text(timeout=timeout_ms) or "").strip()
        if citation.startswith("[1]"):
            citation = citation[3:].lstrip()
        if not citation:
            _logger.warning("GB/T 引文内容为空: %s", log_ref)
        return citation
    except PlaywrightError as exc:
        _logger.warning("GB/T 引文抓取失败: %s error=%s", log_ref, exc)
        return ""
    finally:
        if popup is None:
            popup = page.locator(_QUOTE_POPUP_SELECTOR).last
        _close_popup(popup, timeout_ms)
