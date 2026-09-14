from __future__ import annotations

import logging
import random

from playwright.sync_api import Error as PlaywrightError

from ..browser.session import ScrapeSession, require_page
from ..core.settings import ScraperSettings
from .guard import (
    VERIFY_TIMEOUT,
    handle_verify_with_progress,
    verify_stop_reason,
)
from .pagination import (
    confirm_result_page_advanced,
    get_first_result_href,
    get_result_page_numbers,
)
from .selectors import query_first


_logger = logging.getLogger("cnkibug.cnki.resume")


def position_after_checkpoint(
    session: ScrapeSession,
    completed_page: int,
    settings: ScraperSettings,
    keyword_ref: str,
) -> bool:
    page = require_page(session)
    events = session.events
    for page_number in range(1, completed_page + 1):
        if session.acknowledge_stop_request(reason="用户请求停止"):
            return False
        try:
            next_btn = query_first(page, "next_page")
            if session.acknowledge_stop_request(reason="用户请求停止"):
                return False
            if not next_btn:
                _logger.warning(
                    "页级恢复定位失败，未找到下一页按钮: %s current_page=%d target_page=%d",
                    keyword_ref,
                    page_number,
                    completed_page + 1,
                )
                return False
            old_first_href = get_first_result_href(page)
            old_next_page = next_btn.get_attribute("data-curpage") or ""
            old_current_page, _ = get_result_page_numbers(page)
            if session.acknowledge_stop_request(reason="用户请求停止"):
                return False
            next_btn.click(timeout=settings.timeout_selector_ms)
            if session.acknowledge_stop_request(reason="用户请求停止"):
                return False
            advanced, verify_status = confirm_result_page_advanced(
                session,
                settings,
                old_href=old_first_href,
                old_next_page=old_next_page,
                old_current_page=old_current_page,
            )
            if verify_status == VERIFY_TIMEOUT:
                _logger.warning(
                    "页级恢复定位因安全验证超时停止: %s current_page=%d target_page=%d",
                    keyword_ref,
                    page_number,
                    completed_page + 1,
                )
            if session.acknowledge_stop_request(reason="用户请求停止"):
                return False
            if not advanced:
                _logger.warning(
                    "页级恢复定位失败，翻页变化未确认: %s current_page=%d target_page=%d",
                    keyword_ref,
                    page_number,
                    completed_page + 1,
                )
                return False
            verify_status = handle_verify_with_progress(
                page,
                settings,
                events,
            )
            if verify_stop_reason(session, verify_status):
                if verify_status == VERIFY_TIMEOUT:
                    _logger.warning(
                        "页级恢复定位因安全验证超时停止: %s current_page=%d target_page=%d",
                        keyword_ref,
                        page_number,
                        completed_page + 1,
                    )
                return False
            if not session.wait_interruptibly(random.uniform(1, 2)):
                return False
            _logger.info(
                "页级恢复已跳过完成页: %s page=%d target_page=%d",
                keyword_ref,
                page_number,
                completed_page + 1,
            )
        except PlaywrightError:
            if session.acknowledge_stop_request(reason="用户请求停止"):
                return False
            if session.acknowledge_page_closed(page):
                return False
            _logger.warning(
                "页级恢复定位出现页面异常: %s current_page=%d target_page=%d",
                keyword_ref,
                page_number,
                completed_page + 1,
                exc_info=True,
            )
            return False
    return not session.acknowledge_stop_request(reason="用户请求停止")
