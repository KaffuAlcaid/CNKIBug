from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from ..cnki.keyword import scrape_keyword
from ..cnki.metrics import keyword_log_ref
from ..cnki.models import (
    STATUS_FAILED,
    STATUS_STOPPED,
    KeywordResult,
    make_keyword_result,
)
from ..fileio.exporter import save_all
from ..core.estimate import estimate_active_seconds
from .state import (
    keyword_checkpoint,
    mark_keyword_done,
    mark_keyword_progress,
    persist_task_state,
)
from .task import TaskContext


_logger = logging.getLogger("cnkibug.workflow.keyword_run")


def start_progress(task: TaskContext) -> None:
    pending = [
        keyword
        for keyword in task.keywords
        if keyword not in task.terminal_results
    ]
    if task.session.stop_requested or not pending:
        return
    eta_low, eta_high = estimate_active_seconds(
        task.max_pages,
        len(pending),
        include_citation=task.include_citation,
        include_details=task.include_details,
    )
    task.events.emit(
        "progress_started",
        low_seconds=eta_low,
        high_seconds=eta_high,
    )


def run_keywords(task: TaskContext) -> None:
    for index, keyword in enumerate(task.keywords, start=1):
        if task.session.stop_requested:
            break
        _run_keyword(task, keyword, index)


def _run_keyword(
    task: TaskContext,
    keyword: str,
    index: int,
) -> None:
    keyword_ref = keyword_log_ref(
        keyword,
        index,
        len(task.keywords),
        include_keyword=task.settings.log_keywords,
    )
    if keyword in task.terminal_results:
        _logger.info("关键词已在 last_task 中完成，跳过: %s", keyword_ref)
        return

    completed_page, checkpoint_records = _prepare_checkpoint(
        task,
        keyword,
        keyword_ref,
    )
    _update_keyword_progress(task, keyword, index, completed_page)
    _wait_between_keywords(task, index, len(task.keywords))
    historical_records = list(task.all_results.get(keyword, []))
    on_page_complete = _checkpoint_callback(
        task,
        keyword,
        keyword_ref,
    )

    result = _scrape_with_errors(
        task,
        keyword,
        keyword_ref,
        index,
        completed_page,
        checkpoint_records,
        on_page_complete,
    )
    _merge_historical_records(result, historical_records, keyword_ref)
    _record_keyword_result(task, result, keyword_ref)
    _save_incremental(task, index)


def _prepare_checkpoint(
    task: TaskContext,
    keyword: str,
    keyword_ref: str,
) -> tuple[int, list]:
    completed_page, checkpoint_records = keyword_checkpoint(task.state, keyword)
    if completed_page > task.max_pages:
        _logger.warning(
            "关键词页级断点超过请求页数，已从第一页重抓: "
            "%s completed_page=%d max_pages=%d",
            keyword_ref,
            completed_page,
            task.max_pages,
        )
        return 0, []
    if keyword in task.all_results:
        if completed_page:
            _logger.info(
                "关键词从页级断点恢复: %s completed_page=%d resume_page=%d records=%d",
                keyword_ref,
                completed_page,
                completed_page + 1,
                len(checkpoint_records),
            )
            task.events.emit(
                "message",
                text=(
                    f"[*] 关键词「{keyword}」将从第 {completed_page + 1} 页继续，"
                    f"已保留 {len(checkpoint_records)} 条记录。"
                ),
                level="dim",
            )
        else:
            _logger.warning(
                "关键词存在无页码的失败或中止结果，将从第一页重试: %s records=%d",
                keyword_ref,
                len(task.all_results[keyword]),
            )
            task.events.emit(
                "message",
                text=f"[*] 关键词「{keyword}」上次未完整完成，将从第一页重新抓取。",
                level="dim",
            )
    return completed_page, checkpoint_records


def _update_keyword_progress(
    task: TaskContext,
    keyword: str,
    index: int,
    completed_page: int,
) -> None:
    task.events.emit(
        "progress_updated",
        keyword=keyword,
        keyword_index=index,
        keyword_total=len(task.keywords),
        page=min(completed_page + 1, task.max_pages),
        page_total=task.max_pages,
        records=task.total_records,
    )


def _wait_between_keywords(task: TaskContext, index: int, total: int) -> None:
    if index <= 1:
        return
    wait_sec = random.uniform(5, 8)
    _logger.info(
        "关键词间隔等待: next_keyword_index=%d/%d wait_sec=%.1f",
        index,
        total,
        wait_sec,
    )
    with task.events.activity(f"少女祈祷中... 等待 {wait_sec:.1f} 秒"):
        time.sleep(wait_sec)


def _checkpoint_callback(
    task: TaskContext,
    keyword: str,
    keyword_ref: str,
) -> Callable[[int, list[list[str]]], None]:
    def save_page_checkpoint(completed: int, records: list[list[str]]) -> None:
        task.all_results[keyword] = list(records)
        mark_keyword_progress(task.state, keyword, completed, records)
        persist_task_state(
            task.state,
            f"关键词第 {completed} 页检查点",
            task.paths,
            task.events,
        )
        task.events.emit(
            "progress_updated",
            page=max(completed, 1),
            records=task.total_records,
        )
        if completed:
            _logger.info(
                "页级断点已保存: %s completed_page=%d records=%d",
                keyword_ref,
                completed,
                len(records),
            )
        else:
            _logger.warning(
                "页级恢复已回退到第一页: %s preserved_records=%d",
                keyword_ref,
                len(records),
            )

    return save_page_checkpoint


def _scrape_with_errors(
    task: TaskContext,
    keyword: str,
    keyword_ref: str,
    index: int,
    completed_page: int,
    checkpoint_records: list,
    on_page_complete: Callable[[int, list[list[str]]], None],
) -> KeywordResult:
    try:
        return scrape_keyword(
            task.session,
            keyword,
            task.max_pages,
            task.settings,
            index,
            len(task.keywords),
            start_page=completed_page + 1,
            initial_records=checkpoint_records if completed_page else [],
            on_page_complete=on_page_complete,
            include_citation=task.include_citation,
            detail_fetcher=task.detail_fetcher,
        )
    except PlaywrightTimeoutError as error:
        _logger.warning("关键词页面等待超时，跳过: %s error=%s", keyword_ref, error)
        task.events.emit(
            "message",
            text=f"[x] 关键词「{keyword}」页面等待超时，跳过: {error}",
            level="error",
        )
        return make_keyword_result(
            keyword,
            index,
            len(task.keywords),
            [],
            STATUS_FAILED,
            "关键词页面等待超时",
        )
    except PlaywrightError as error:
        _logger.warning("浏览器连接异常，停止后续关键词: %s error=%s", keyword_ref, error)
        task.events.emit(
            "message",
            text=f"[!] 浏览器连接已断开，停止后续关键词抓取: {error}",
            level="warning",
        )
        task.session.request_stop("浏览器连接异常")
        return make_keyword_result(
            keyword,
            index,
            len(task.keywords),
            [],
            STATUS_STOPPED,
            "浏览器连接异常",
        )
    except KeyboardInterrupt:
        task.session.request_stop("用户中断")
        _logger.warning("用户中断关键词循环: %s", keyword_ref)
        return make_keyword_result(
            keyword,
            index,
            len(task.keywords),
            [],
            STATUS_STOPPED,
            "用户中断",
        )


def _merge_historical_records(
    result: KeywordResult,
    historical_records: list,
    keyword_ref: str,
) -> None:
    if result.status not in {STATUS_FAILED, STATUS_STOPPED} or not historical_records:
        return
    current_count = len(result.records)
    merged_records = list(historical_records)
    seen_records = {tuple(record) for record in merged_records}
    for record in result.records:
        record_key = tuple(record)
        if record_key not in seen_records:
            seen_records.add(record_key)
            merged_records.append(record)
    result.records = merged_records
    _logger.warning(
        "关键词重试仍未完整完成，已合并保留部分结果: "
        "%s previous=%d current=%d merged=%d",
        keyword_ref,
        len(historical_records),
        current_count,
        len(merged_records),
    )


def _record_keyword_result(
    task: TaskContext,
    result: KeywordResult,
    keyword_ref: str,
) -> None:
    task.all_results[result.keyword] = result.records
    task.events.emit("progress_updated", records=task.total_records)
    task.report.add(result)
    mark_keyword_done(task.state, result)
    persist_task_state(task.state, "关键词结果更新", task.paths, task.events)
    _logger.info(
        "关键词结果已记录: %s status=%s records=%d stop_requested=%s",
        keyword_ref,
        result.status,
        len(result.records),
        task.session.stop_requested,
    )


def _save_incremental(task: TaskContext, index: int) -> None:
    try:
        result = save_all(
            task.save_mode,
            task.keywords,
            task.all_results,
            task.ts,
            include_citation=task.include_citation,
            include_details=task.include_details,
            detail_txt_export=task.detail_txt_export,
            log_save_path=task.settings.log_save_path,
            save_type="incremental",
        )
        _logger.info(
            "增量保存完成: completed_keywords=%d/%d total_records=%d "
            "attempted=%d saved=%d failed=%d",
            index,
            len(task.keywords),
            task.total_records,
            result.attempted,
            len(result.saved_paths),
            result.failed,
        )
        if result.failed:
            task.events.emit(
                "message",
                text=(
                    f"[!] 阶段性保存有 {result.failed} 个文件未成功写入，"
                    "最终保存时会再次尝试。"
                ),
                level="warning",
            )
        elif len(task.keywords) > 1 and result.saved_paths:
            task.events.emit(
                "message",
                text=f"[*] 已落盘阶段性结果（已完成 {index}/{len(task.keywords)} 个关键词）",
                level="dim",
            )
    except KeyboardInterrupt:
        task.session.request_stop("用户在增量保存期间中断")
        _logger.warning("用户在增量保存期间中断")
        raise
    except Exception:
        _logger.exception("增量保存失败")
