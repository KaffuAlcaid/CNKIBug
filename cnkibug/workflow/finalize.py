from __future__ import annotations

import logging

from ..browser.cache import save_cookie_state
from ..fileio.exporter import SaveResult, save_all
from .report import build_task_report, save_task_report
from .state import delete_last_task, persist_task_state, task_is_finished
from .task import TaskContext


_logger = logging.getLogger("cnkibug.workflow.finalize")


def finalize_task(task: TaskContext) -> None:
    task.report.stopped = task.session.stop_requested
    task.report.verify_timeout = task.session.verify_timeout
    task.events.emit("progress_saving")

    close_browser(task)
    save_result, final_save_failed = _save_final_results(task)
    report_failed = _save_json_report(task, save_result, final_save_failed)
    final_save_failed = final_save_failed or report_failed
    _finish_progress(task, final_save_failed)
    _print_report(task)

    if task_is_finished(task.state) and not final_save_failed:
        delete_last_task(task.paths)
    else:
        persist_task_state(task.state, "任务收尾", task.paths, task.events)


def close_browser(task: TaskContext) -> None:
    if task.browser_context:
        try:
            save_cookie_state(
                task.browser_context,
                task.settings.session_cache_enabled,
                task.paths,
            )
            task.browser_context.close()
            _logger.info("浏览器上下文已关闭")
        except Exception:
            _logger.warning("浏览器上下文关闭失败", exc_info=True)
    if task.browser:
        try:
            task.browser.close()
            _logger.info("浏览器已关闭")
        except Exception:
            _logger.warning("浏览器关闭失败", exc_info=True)


def _save_final_results(task: TaskContext) -> tuple[SaveResult, bool]:
    result = SaveResult()
    try:
        _logger.info(
            "最终保存开始: keyword_count=%d total_records=%d stop_requested=%s",
            len(task.keywords),
            task.total_records,
            task.session.stop_requested,
        )
        result = save_all(
            task.save_mode,
            task.keywords,
            task.all_results,
            task.ts,
            include_citation=task.include_citation,
            log_save_path=task.settings.log_save_path,
            save_type="final",
        )
        failed = bool(result.failed)
        task.events.emit(
            "export_finished",
            result=result,
            all_results=task.all_results,
            save_mode=task.save_mode,
        )
        if result.saved_paths:
            _logger.info("最终保存成功: saved_files=%d", len(result.saved_paths))
        _logger.info(
            "抓取任务结束: completed_keywords=%d/%d total_records=%d "
            "stop_requested=%s save_attempted=%d save_failed=%d",
            len(task.all_results),
            len(task.keywords),
            task.total_records,
            task.session.stop_requested,
            result.attempted,
            result.failed,
        )
        return result, failed
    except KeyboardInterrupt:
        task.session.request_stop("用户在最终保存期间中断")
        _logger.warning("用户在最终保存期间中断，保留断点状态")
        task.events.emit(
            "message",
            text="[!] 最终保存被中断，已保留断点状态。",
            level="warning",
        )
        return result, True
    except Exception as error:
        _logger.exception("最终保存失败")
        task.events.emit(
            "message",
            text=f"[x] 最终保存失败：{error}",
            level="error",
        )
        task.events.emit(
            "message",
            text="请关闭已打开的同名结果文件，并检查桌面或程序目录写入权限。",
            level="warning",
        )
        return result, True


def _save_json_report(
    task: TaskContext,
    save_result: SaveResult,
    export_failed: bool,
) -> bool:
    try:
        payload = build_task_report(
            task.report,
            task.all_results,
            task.state,
            task.keywords,
            task.max_pages,
            task.save_mode,
            task.ts,
            save_result.saved_paths,
            export_failed,
            include_citation=task.include_citation,
        )
        report_path = save_task_report(payload, task.ts, task.paths)
        if report_path:
            task.events.emit(
                "message",
                text=f"[*] JSON 任务报告已保存至：{report_path}",
                level="dim",
            )
            return False
        task.events.emit("message", text="[x] JSON 任务报告保存失败。", level="error")
        return True
    except KeyboardInterrupt:
        task.session.request_stop("用户在 JSON 任务报告保存期间中断")
        _logger.warning("JSON 任务报告保存被用户中断")
        return True
    except Exception:
        _logger.exception("JSON 任务报告生成失败")
        task.events.emit(
            "message",
            text="[x] JSON 任务报告生成失败，详情见日志。",
            level="error",
        )
        return True


def _finish_progress(task: TaskContext, final_save_failed: bool) -> None:
    display_completed = (
        task_is_finished(task.state)
        and not final_save_failed
        and not task.session.stop_requested
    )
    if display_completed:
        task.events.emit("progress_completed")
    elif task.session.stop_requested:
        task.events.emit("progress_stopped", message="任务已停止，当前结果已保存")
    elif final_save_failed:
        task.events.emit("progress_stopped", message="保存结果或任务报告失败")
    else:
        task.events.emit("progress_stopped", message="任务未完整完成，已保留断点")
    task.events.emit("progress_closed")


def _print_report(task: TaskContext) -> None:
    try:
        task.events.emit(
            "task_report",
            report=task.report,
            all_results=task.all_results,
        )
    except KeyboardInterrupt:
        task.session.request_stop("用户在任务摘要输出期间中断")
        _logger.warning("用户在任务摘要输出期间中断，继续保存断点状态")
    except Exception:
        _logger.exception("任务摘要输出失败")
