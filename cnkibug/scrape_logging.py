from __future__ import annotations

from typing import Any, Sequence


def keyword_log_ref(
    keyword: str,
    keyword_index: int | None = None,
    keyword_total: int | None = None,
    include_keyword: bool = False,
) -> str:
    parts = []
    if keyword_index is not None and keyword_total is not None:
        parts.append(f"keyword_index={keyword_index}/{keyword_total}")
    elif keyword_index is not None:
        parts.append(f"keyword_index={keyword_index}")
    if include_keyword:
        parts.append(f"keyword={keyword!r}")
    return " ".join(parts) if parts else "keyword=<hidden>"


def new_scrape_stats() -> dict[str, int]:
    return {
        "rows_seen": 0,
        "records_added": 0,
        "duplicates": 0,
        "skipped_no_title": 0,
        "row_parse_errors": 0,
        "missing_title": 0,
        "missing_authors": 0,
        "missing_source": 0,
        "missing_date": 0,
    }


def count_missing_fields(record: Sequence[Any], stats: dict[str, int]) -> None:
    fields = (
        ("missing_title", record[0]),
        ("missing_authors", record[1]),
        ("missing_source", record[2]),
        ("missing_date", record[3]),
    )
    for key, value in fields:
        if not str(value).strip():
            stats[key] += 1


def missing_field_text(stats: dict[str, int]) -> str:
    return (
        f"title={stats['missing_title']} authors={stats['missing_authors']} "
        f"source={stats['missing_source']} date={stats['missing_date']}"
    )
