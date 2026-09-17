from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..core.events import EventSink, NULL_EVENTS
from ..core.settings import ScraperSettings
from .guard import (
    VERIFY_CANCELLED,
    VERIFY_PAGE_CLOSED,
    VERIFY_PASSED,
    VERIFY_TIMEOUT,
    handle_verify_with_progress,
)


DETAIL_READY_SELECTOR = ".brief h1"
KEYWORD_SELECTOR = "p.keywords a"
ABSTRACT_INPUT_SELECTOR = "#abstract_text"
ABSTRACT_VISIBLE_SELECTOR = "#ChDivSummary"

_logger = logging.getLogger("cnkibug.cnki.details")


@dataclass(frozen=True)
class ArticleDetails:
    keywords: list[str]
    abstract: str
    failed: bool = False
    verify_timeout: bool = False
    page_closed: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


class ArticleDetailFetcher:
    def __init__(
        self,
        browser_context: Any,
        settings: ScraperSettings,
        events: EventSink = NULL_EVENTS,
    ) -> None:
        self._browser_context = browser_context
        self._settings = settings
        self._events = events
        self._page: Any | None = None

    def fetch(self, url: str, *, log_ref: str) -> ArticleDetails:
        if self._cancel_requested():
            return ArticleDetails([], "", failed=True)
        if self._page_closed():
            return ArticleDetails([], "", failed=True, page_closed=True)
        if not url.strip():
            _logger.warning("论文详情链接为空: %s", log_ref)
            return ArticleDetails([], "", failed=True)

        try:
            page = self._get_page()
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._settings.timeout_goto_ms,
            )
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            verify_status = handle_verify_with_progress(
                page,
                self._settings,
                self._events,
            )
            if verify_status == VERIFY_TIMEOUT:
                _logger.warning("论文详情页安全验证超时: %s", log_ref)
                return ArticleDetails([], "", failed=True, verify_timeout=True)
            if verify_status == VERIFY_PAGE_CLOSED:
                return ArticleDetails([], "", failed=True, page_closed=True)
            if verify_status == VERIFY_CANCELLED:
                return ArticleDetails([], "", failed=True)
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)

            try:
                page.wait_for_selector(
                    DETAIL_READY_SELECTOR,
                    timeout=self._settings.timeout_selector_ms,
                )
            except PlaywrightTimeoutError:
                if verify_status != VERIFY_PASSED:
                    raise
                if self._cancel_requested():
                    return ArticleDetails([], "", failed=True)
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self._settings.timeout_goto_ms,
                )
                if self._cancel_requested():
                    return ArticleDetails([], "", failed=True)
                retry_verify_status = handle_verify_with_progress(
                    page,
                    self._settings,
                    self._events,
                )
                if retry_verify_status == VERIFY_TIMEOUT:
                    _logger.warning("论文详情页重新访问时安全验证超时: %s", log_ref)
                    return ArticleDetails([], "", failed=True, verify_timeout=True)
                if retry_verify_status == VERIFY_PAGE_CLOSED:
                    return ArticleDetails([], "", failed=True, page_closed=True)
                if retry_verify_status == VERIFY_CANCELLED:
                    return ArticleDetails([], "", failed=True)
                if self._cancel_requested():
                    return ArticleDetails([], "", failed=True)
                page.wait_for_selector(
                    DETAIL_READY_SELECTOR,
                    timeout=self._settings.timeout_selector_ms,
                )

            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            keywords = self._extract_keywords(page)
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            abstract = self._extract_abstract(page)
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            if self._page_closed():
                return ArticleDetails([], "", failed=True, page_closed=True)
            return ArticleDetails(keywords, abstract, metadata=self._extract_metadata(page))
        except PlaywrightError as error:
            if self._cancel_requested():
                return ArticleDetails([], "", failed=True)
            if self._page_closed():
                _logger.warning("论文详情页已关闭: %s", log_ref)
                return ArticleDetails([], "", failed=True, page_closed=True)
            _logger.warning("论文详情抓取失败: %s error=%s", log_ref, error)
            return ArticleDetails([], "", failed=True)

    def _get_page(self) -> Any:
        if self._page is None:
            self._page = self._browser_context.new_page()
        return self._page

    def _page_closed(self) -> bool:
        return self._page is not None and self._page.is_closed()

    def _cancel_requested(self) -> bool:
        return self._events.cancel_requested()

    @staticmethod
    def _extract_metadata(page: Any) -> dict[str, str]:
        metadata = page.evaluate(r"""() => {
            const text = selector => (document.querySelector(selector)?.textContent || '').trim();
            const meta = name => document.querySelector(`meta[name="${name}"]`)?.content?.trim() || '';
            const labeled = label => Array.from(document.querySelectorAll('.rowtit'))
                .find(el => el.textContent.replace(/[：:\s]/g, '') === label)
                ?.parentElement?.querySelector('p')?.textContent?.trim() || '';
            return {
                institutions: text('.wx-tit h3.author:not(#authorpart)'),
                funds: text('p.funds'), classification: text('p.clc-code'),
                doi: meta('citation_doi') || labeled('DOI') || text('p.doi') || text('.doi a'),
                volume: meta('citation_volume'), issue: meta('citation_issue'),
                first_page: meta('citation_firstpage'), last_page: meta('citation_lastpage'),
                identifiers: Array.from(document.querySelectorAll('.rowtit, .top-tip, .doc-top'))
                    .map(el => el.textContent || '').filter(t => /doi/i.test(t)).join(' ')
            };
        }""")
        match = re.search(r"\b10\.\d{4,9}/[^\s<>]+", metadata.get("doi", "") or metadata.get("identifiers", ""), re.I)
        metadata["doi"] = match.group().rstrip(".;；。") if match else ""
        metadata.pop("identifiers", None)
        first, last = metadata.pop("first_page", ""), metadata.pop("last_page", "")
        if first:
            metadata["pages"] = f"{first}-{last}" if last and last != first else first
        return {key: " ".join(value.split()) for key, value in metadata.items() if value}

    @staticmethod
    def _extract_keywords(page: Any) -> list[str]:
        keywords = []
        for raw_text in page.locator(KEYWORD_SELECTOR).all_inner_texts():
            keyword = raw_text.strip().rstrip(";；").strip()
            if keyword:
                keywords.append(keyword)
        return keywords

    @staticmethod
    def _extract_abstract(page: Any) -> str:
        abstract_input = page.locator(ABSTRACT_INPUT_SELECTOR).first
        if abstract_input.count():
            text = abstract_input.evaluate(
                """
                element => {
                    const container = document.createElement('div');
                    container.innerHTML = element.value || '';
                    return container.textContent || '';
                }
                """
            )
            normalized = " ".join(str(text or "").split())
            if normalized:
                return normalized

        visible = page.locator(ABSTRACT_VISIBLE_SELECTOR).first
        if visible.count():
            return " ".join((visible.inner_text() or "").split())
        return ""
