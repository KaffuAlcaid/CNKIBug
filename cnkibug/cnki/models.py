from __future__ import annotations

from dataclasses import dataclass


STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_NOT_STARTED = "not_started"


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
