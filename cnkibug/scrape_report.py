from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from .ui import _console


STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"

_logger = logging.getLogger("cnkibug.report")


@dataclass
class KeywordResult:
    keyword: str
    index: int
    total: int
    records: list
    status: str
    reason: str = ""


@dataclass
class FieldStats:
    total_records: int = 0
    missing_title: int = 0
    missing_authors: int = 0
    missing_source: int = 0
    missing_date: int = 0
    missing_detail_url: int = 0


@dataclass
class TaskReport:
    total_keywords: int
    keyword_results: list[KeywordResult] = field(default_factory=list)
    stopped: bool = False
    verify_timeout: bool = False

    def add(self, result: KeywordResult) -> None:
        self.keyword_results.append(result)

    @property
    def completed_keywords(self) -> int:
        return len(self.keyword_results)

    @property
    def total_records(self) -> int:
        return sum(len(item.records) for item in self.keyword_results)

    def count_status(self, status: str) -> int:
        return sum(1 for item in self.keyword_results if item.status == status)

    def failed_items(self) -> list[KeywordResult]:
        return [
            item for item in self.keyword_results
            if item.status in {STATUS_FAILED, STATUS_STOPPED}
        ]


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


def collect_field_stats(all_results: dict[str, list]) -> FieldStats:
    stats = FieldStats()
    for records in all_results.values():
        for record in records:
            stats.total_records += 1
            if not _field_value(record, 0):
                stats.missing_title += 1
            if not _field_value(record, 1):
                stats.missing_authors += 1
            if not _field_value(record, 2):
                stats.missing_source += 1
            if not _field_value(record, 3):
                stats.missing_date += 1
            if not _field_value(record, 4):
                stats.missing_detail_url += 1
    return stats


def print_task_report(report: TaskReport, all_results: dict[str, list]) -> None:
    field_stats = collect_field_stats(all_results)
    success = report.count_status(STATUS_SUCCESS)
    empty = report.count_status(STATUS_EMPTY)
    failed = report.count_status(STATUS_FAILED)
    stopped = report.count_status(STATUS_STOPPED)

    _console.print("\n" + "=" * 50)
    _console.print("[bold cyan]本轮摘要[/bold cyan]")
    _console.print(f"  总关键词：{report.total_keywords}")
    _console.print(f"  已处理：{report.completed_keywords}")
    _console.print(f"  成功：{success}")
    _console.print(f"  无结果：{empty}")
    _console.print(f"  失败：{failed}")
    _console.print(f"  中止：{stopped}")
    _console.print(f"  总记录：{field_stats.total_records}")
    if report.verify_timeout:
        _console.print("  [yellow]安全验证等待超时：是[/yellow]")
    if report.stopped:
        _console.print("  [yellow]本轮已提前停止[/yellow]")

    if _has_missing_fields(field_stats):
        _console.print(
            "  字段缺失："
            f"标题 {field_stats.missing_title}，"
            f"作者 {field_stats.missing_authors}，"
            f"来源 {field_stats.missing_source}，"
            f"日期 {field_stats.missing_date}，"
            f"详情链接 {field_stats.missing_detail_url}"
        )

    failed_items = report.failed_items()
    if failed_items:
        _console.print("\n[yellow]失败/中止关键词：[/yellow]")
        for item in failed_items:
            reason = item.reason or "未记录原因"
            _console.print(f"  - 第 {item.index}/{item.total} 个关键词「{item.keyword}」：{reason}")
    _console.print("=" * 50 + "\n")

    _logger.info(
        "任务摘要: total_keywords=%d completed_keywords=%d success=%d empty=%d "
        "failed=%d stopped=%d total_records=%d stopped_flag=%s verify_timeout=%s "
        "missing_title=%d missing_authors=%d missing_source=%d missing_date=%d "
        "missing_detail_url=%d",
        report.total_keywords,
        report.completed_keywords,
        success,
        empty,
        failed,
        stopped,
        field_stats.total_records,
        report.stopped,
        report.verify_timeout,
        field_stats.missing_title,
        field_stats.missing_authors,
        field_stats.missing_source,
        field_stats.missing_date,
        field_stats.missing_detail_url,
    )
    for item in failed_items:
        _logger.warning(
            "关键词失败摘要: keyword_index=%d/%d status=%s records=%d reason=%s",
            item.index,
            item.total,
            item.status,
            len(item.records),
            item.reason or "未记录原因",
        )


def _field_value(record: Sequence, index: int) -> str:
    if index >= len(record):
        return ""
    return str(record[index]).strip()


def _has_missing_fields(stats: FieldStats) -> bool:
    return any((
        stats.missing_title,
        stats.missing_authors,
        stats.missing_source,
        stats.missing_date,
        stats.missing_detail_url,
    ))
