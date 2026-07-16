from __future__ import annotations

import logging
import random
import time

from playwright.sync_api import Error as PlaywrightError

from ..browser.session import ScrapeSession, require_page
from ..core.settings import ScraperSettings
from .guard import VERIFY_PASSED, VERIFY_TIMEOUT, handle_verify_with_progress
from .pagination import (
    get_first_result_href,
    get_result_page_numbers,
    wait_result_page_advanced,
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
        try:
            next_btn = query_first(page, "next_page")
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
            next_btn.click(timeout=settings.timeout_selector_ms)
            advanced = wait_result_page_advanced(
                page,
                old_href=old_first_href,
                old_next_page=old_next_page,
                old_current_page=old_current_page,
                timeout=settings.timeout_selector_ms,
            )
            if not advanced:
                verify_status = handle_verify_with_progress(
                    page,
                    settings,
                    events,
                )
                if verify_status == VERIFY_TIMEOUT:
                    session.request_stop("安全验证等待超时", verify_timeout=True)
                    _logger.warning(
                        "页级恢复定位因安全验证超时停止: %s current_page=%d target_page=%d",
                        keyword_ref,
                        page_number,
                        completed_page + 1,
                    )
                    return False
                if verify_status == VERIFY_PASSED:
                    advanced = wait_result_page_advanced(
                        page,
                        old_href=old_first_href,
                        old_next_page=old_next_page,
                        old_current_page=old_current_page,
                        timeout=settings.timeout_selector_ms,
                    )
            if not advanced:
                _logger.warning(
                    "页级恢复定位失败，翻页变化未确认: %s current_page=%d target_page=%d",
                    keyword_ref,
                    page_number,
                    completed_page + 1,
                )
                return False
            if handle_verify_with_progress(
                page,
                settings,
                events,
            ) == VERIFY_TIMEOUT:
                session.request_stop("安全验证等待超时", verify_timeout=True)
                _logger.warning(
                    "页级恢复定位因安全验证超时停止: %s current_page=%d target_page=%d",
                    keyword_ref,
                    page_number,
                    completed_page + 1,
                )
                return False
            time.sleep(random.uniform(1, 2))
            _logger.info(
                "页级恢复已跳过完成页: %s page=%d target_page=%d",
                keyword_ref,
                page_number,
                completed_page + 1,
            )
        except PlaywrightError:
            _logger.warning(
                "页级恢复定位出现页面异常: %s current_page=%d target_page=%d",
                keyword_ref,
                page_number,
                completed_page + 1,
                exc_info=True,
            )
            return False
    return True
