from __future__ import annotations

from math import ceil


_SEC_PER_PAGE_LOW = 8
_SEC_PER_PAGE_HIGH = 12
_INTER_KEYWORD_LOW = 5
_INTER_KEYWORD_HIGH = 8
_RESULTS_PER_PAGE = 20
_SEC_PER_CITATION_LOW = 0.95
_SEC_PER_CITATION_HIGH = 1.25
_SEC_PER_DETAIL_LOW = 3
_SEC_PER_DETAIL_HIGH = 8
_STARTUP_OVERHEAD_LOW = 18
_STARTUP_OVERHEAD_HIGH = 25


def estimate_active_seconds(
    pages: int,
    keyword_count: int = 1,
    include_citation: bool = False,
    include_details: bool = False,
) -> tuple[int, int]:
    effective_keyword_count = max(keyword_count, 1)
    page_units = pages * effective_keyword_count
    transition_count = max(effective_keyword_count - 1, 0)
    low = page_units * _SEC_PER_PAGE_LOW + transition_count * _INTER_KEYWORD_LOW
    high = page_units * _SEC_PER_PAGE_HIGH + transition_count * _INTER_KEYWORD_HIGH
    if include_citation:
        expected_records = page_units * _RESULTS_PER_PAGE
        low += ceil(expected_records * _SEC_PER_CITATION_LOW)
        high += ceil(expected_records * _SEC_PER_CITATION_HIGH)
    if include_details:
        expected_records = page_units * _RESULTS_PER_PAGE
        low += expected_records * _SEC_PER_DETAIL_LOW
        high += expected_records * _SEC_PER_DETAIL_HIGH
    return low, high


def estimate_seconds(
    pages: int,
    keyword_count: int = 1,
    include_citation: bool = False,
    include_details: bool = False,
) -> tuple[int, int]:
    low, high = estimate_active_seconds(
        pages,
        keyword_count,
        include_citation=include_citation,
        include_details=include_details,
    )
    return low + _STARTUP_OVERHEAD_LOW, high + _STARTUP_OVERHEAD_HIGH


def estimate_progress(
    elapsed_seconds: float,
    low_seconds: int,
    high_seconds: int,
    completed: bool = False,
) -> int:
    if low_seconds <= 0:
        raise ValueError("low_seconds must be greater than 0")
    if high_seconds <= low_seconds:
        raise ValueError("high_seconds must be greater than low_seconds")
    if completed:
        return 100
    if elapsed_seconds <= 0:
        return 0
    if elapsed_seconds <= low_seconds:
        return int(90 * elapsed_seconds / low_seconds)
    if elapsed_seconds < high_seconds:
        return int(
            90
            + 9 * (elapsed_seconds - low_seconds) / (high_seconds - low_seconds)
        )
    return 99


def _fmt(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    if minutes > 0:
        return f"{minutes} 分 {secs} 秒"
    return f"{secs} 秒"


def format_eta(low: int, high: int) -> str:
    return f"约 {_fmt(low)} ~ {_fmt(high)}"
