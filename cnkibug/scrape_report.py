from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .runtime import get_paths
from .ui import _console
from .version import APP_VERSION


STATUS_SUCCESS = "success"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_NOT_STARTED = "not_started"
REPORT_SCHEMA_VERSION = 2

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
    include_citation: bool = False

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


def collect_citation_stats(records: list) -> dict[str, int]:
    success = sum(
        1
        for record in records
        if len(record) > 5 and str(record[5]).strip()
    )
    failed = len(records) - success
    return {
        "success": success,
        "failed": failed,
        "empty": failed,
    }


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
    if report.include_citation:
        citation_stats = collect_citation_stats(
            [record for records in all_results.values() for record in records]
        )
        _console.print(
            "  引用格式："
            f"成功 {citation_stats['success']}，"
            f"失败/留空 {citation_stats['failed']}"
        )
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


def build_task_report(
    report: TaskReport,
    all_results: dict[str, list],
    task_state: dict,
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    ts: str,
    output_paths: list[str],
    export_failed: bool,
    include_citation: bool = False,
) -> dict:
    results_by_keyword = {item.keyword: item for item in report.keyword_results}
    completed_state = task_state.get("completed", {})
    if not isinstance(completed_state, dict):
        completed_state = {}

    keyword_reports = []
    status_counts = {
        STATUS_SUCCESS: 0,
        STATUS_EMPTY: 0,
        STATUS_FAILED: 0,
        STATUS_STOPPED: 0,
        STATUS_NOT_STARTED: 0,
    }
    for index, keyword in enumerate(keywords, start=1):
        result = results_by_keyword.get(keyword)
        state_item = completed_state.get(keyword)
        if result is not None:
            status = result.status
            reason = result.reason
            records = result.records
        elif isinstance(state_item, dict):
            raw_status = str(state_item.get("status", ""))
            if raw_status == "in_progress":
                status = STATUS_STOPPED if report.stopped else STATUS_FAILED
                reason = "任务在该关键词执行期间中止"
            elif raw_status in status_counts:
                status = raw_status
                reason = str(state_item.get("reason", ""))
            else:
                status = STATUS_FAILED
                reason = str(state_item.get("reason", "未知任务状态"))
            stored_records = state_item.get("records", [])
            records = stored_records if isinstance(stored_records, list) else []
        else:
            status = STATUS_NOT_STARTED
            reason = "任务在执行到该关键词前已结束"
            records = []

        status_counts[status] = status_counts.get(status, 0) + 1
        field_stats = collect_field_stats({keyword: records})
        keyword_report = {
            "keyword": keyword,
            "index": index,
            "status": status,
            "reason": reason,
            "record_count": len(records),
            "missing_fields": _field_stats_dict(field_stats),
        }
        if include_citation:
            keyword_report["citation"] = collect_citation_stats(records)
        keyword_reports.append(keyword_report)

    total_stats = collect_field_stats(all_results)
    all_records = [record for records in all_results.values() for record in records]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "task_id": ts,
        "created_at": str(task_state.get("created_at", "")),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "request": {
            "keyword_count": len(keywords),
            "max_pages_per_keyword": max_pages,
            "theoretical_max_pages": len(keywords) * max_pages,
            "save_mode": save_mode,
            "include_citation": include_citation,
        },
        "execution": {
            "stopped": report.stopped,
            "verify_timeout": report.verify_timeout,
            "status_counts": status_counts,
            "total_records": total_stats.total_records,
            "missing_fields": _field_stats_dict(total_stats),
            "citation": (
                collect_citation_stats(all_records)
                if include_citation
                else {"success": 0, "failed": 0, "empty": 0}
            ),
        },
        "exports": {
            "failed": export_failed,
            "paths": list(output_paths),
        },
        "keywords": keyword_reports,
    }


def save_task_report(payload: dict, ts: str) -> str | None:
    filename = f"cnki_task_report_{ts}.json"
    paths = get_paths()
    if paths is None:
        _logger.error("运行路径未初始化，无法保存 JSON 任务报告")
        return None
    target = paths.status_dir / filename

    try:
        _write_task_report(target, payload)
        _logger.info("JSON 任务报告已保存")
        return str(target.resolve())
    except OSError as save_error:
        _logger.error("JSON 任务报告保存失败: %s", save_error)
        return None


def _write_task_report(path: Path, payload: dict) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _field_stats_dict(stats: FieldStats) -> dict[str, int]:
    return {
        "title": stats.missing_title,
        "authors": stats.missing_authors,
        "source": stats.missing_source,
        "publication_date": stats.missing_date,
        "detail_url": stats.missing_detail_url,
    }


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
