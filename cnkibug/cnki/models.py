from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_NOT_STARTED = "not_started"

BASE_RECORD_SIZE = 5


def append_article_details(
    record: list,
    keywords: list[str],
    abstract: str,
) -> None:
    record.extend(("\n".join(keywords), abstract))


def record_citation(record: Sequence, include_citation: bool) -> str:
    if not include_citation or len(record) <= BASE_RECORD_SIZE:
        return ""
    return str(record[BASE_RECORD_SIZE])


def record_article_details(
    record: Sequence,
    include_citation: bool,
) -> tuple[str, str]:
    start = BASE_RECORD_SIZE + int(include_citation)
    keywords = str(record[start]) if len(record) > start else ""
    abstract = str(record[start + 1]) if len(record) > start + 1 else ""
    return keywords, abstract


@dataclass
class KeywordResult:
    keyword: str
    index: int
    total: int
    records: list
    status: str
    reason: str = ""


def make_keyword_result(
    keyword: str,
    index: int,
    total: int,
    records: list,
    status: str,
    reason: str = "",
) -> KeywordResult:
    return KeywordResult(
        keyword=keyword,
        index=index,
        total=total,
        records=records,
        status=status,
        reason=reason,
    )
