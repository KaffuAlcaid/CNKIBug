from __future__ import annotations

from typing import Any


SELECTOR_GROUPS: dict[str, list[str]] = {
    "search_input": ["input.search-input"],
    "search_button": ["input.search-btn"],
    "result_rows": ["table.result-table-list tbody tr"],
    "title": ["td.name a"],
    "author": ["td.author a.KnowledgeNetLink"],
    "source": ["td.source"],
    "date": ["td.date"],
    "no_content": ["#briefBox p.no-content"],
    "next_page": ["a#PageNext"],
    "page_count": ["span.countPageMark"],
    "current_page": [".pages a.cur[data-curpage]", "#curPageHid"],
}

SELECTOR_SEARCH_INPUT = SELECTOR_GROUPS["search_input"][0]
SELECTOR_SEARCH_BUTTON = SELECTOR_GROUPS["search_button"][0]
SELECTOR_RESULT_ROWS = SELECTOR_GROUPS["result_rows"][0]
SELECTOR_RESULT_TITLE = SELECTOR_GROUPS["title"][0]
SELECTOR_AUTHOR = SELECTOR_GROUPS["author"][0]
SELECTOR_SOURCE = SELECTOR_GROUPS["source"][0]
SELECTOR_DATE = SELECTOR_GROUPS["date"][0]
SELECTOR_NO_CONTENT = SELECTOR_GROUPS["no_content"][0]
SELECTOR_NEXT_PAGE = SELECTOR_GROUPS["next_page"][0]
SELECTOR_PAGE_COUNT = SELECTOR_GROUPS["page_count"][0]
SELECTOR_CURRENT_PAGE = SELECTOR_GROUPS["current_page"][0]


def query_first(parent: Any, group: str) -> Any | None:
    for candidate in SELECTOR_GROUPS[group]:
        element = parent.query_selector(candidate)
        if element:
            return element
    return None


def query_all(parent: Any, group: str) -> list[Any]:
    for candidate in SELECTOR_GROUPS[group]:
        elements = parent.query_selector_all(candidate)
        if elements:
            return elements
    return []
