from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .runtime import get_paths
from .scrape_report import STATUS_EMPTY, STATUS_SUCCESS, KeywordResult


LAST_TASK_FILENAME = "last_task.json"
TASK_STATE_VERSION = 2
_LEGACY_TASK_STATE_VERSION = 1

_logger = logging.getLogger("cnkibug.task_state")
_TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_EMPTY}


def get_last_task_path() -> Path | None:
    paths = get_paths()
    if paths is None:
        return None
    return paths.cache_dir / LAST_TASK_FILENAME


def make_task_state(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    ts: str,
) -> dict[str, Any]:
    return {
        "version": TASK_STATE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ts": ts,
        "save_mode": save_mode,
        "max_pages": max_pages,
        "keywords": list(keywords),
        "completed": {},
    }


def load_last_task() -> dict[str, Any] | None:
    path = get_last_task_path()
    if path is None or not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _logger.warning("last_task 读取失败: path=%s error=%s", path, exc)
        return None
    if not _is_valid_task_state(raw):
        _logger.warning("last_task 格式无效: path=%s", path)
        return None
    if raw.get("version") == _LEGACY_TASK_STATE_VERSION:
        raw = _upgrade_legacy_task_state(raw)
        _logger.info("last_task 已从版本 1 兼容升级到版本 2: path=%s", path)
    return raw


def save_last_task(state: dict[str, Any]) -> Path | None:
    path = get_last_task_path()
    if path is None:
        _logger.warning("运行路径未初始化，跳过 last_task 保存")
        return None
    tmp_path = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except OSError as exc:
        _logger.error("last_task 保存失败: path=%s error=%s", path, exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            _logger.warning(
                "last_task 临时文件清理失败: path=%s error=%s",
                tmp_path,
                cleanup_exc,
            )
        return None
    _logger.info("last_task 已保存: path=%s", path)
    return path


def delete_last_task() -> bool:
    path = get_last_task_path()
    if path is None:
        return False
    try:
        existed = path.exists()
        path.unlink(missing_ok=True)
        if existed:
            _logger.info("last_task 已删除: path=%s", path)
        return existed
    except OSError as exc:
        _logger.warning("last_task 删除失败: path=%s error=%s", path, exc)
        return False


def mark_keyword_done(state: dict[str, Any], result: KeywordResult) -> dict[str, Any]:
    completed = state.setdefault("completed", {})
    previous = completed.get(result.keyword, {})
    completed_page = previous.get("completed_page", 0) if isinstance(previous, dict) else 0
    completed[result.keyword] = {
        "status": result.status,
        "reason": result.reason,
        "records": result.records,
        "completed_page": completed_page,
    }
    return state


def mark_keyword_progress(
    state: dict[str, Any],
    keyword: str,
    completed_page: int,
    records: list,
) -> dict[str, Any]:
    completed = state.setdefault("completed", {})
    completed[keyword] = {
        "status": "in_progress",
        "reason": "",
        "records": list(records),
        "completed_page": completed_page,
    }
    return state


def keyword_checkpoint(state: dict[str, Any], keyword: str) -> tuple[int, list]:
    completed = state.get("completed", {})
    if not isinstance(completed, dict):
        return 0, []
    item = completed.get(keyword)
    if not isinstance(item, dict):
        return 0, []
    completed_page = item.get("completed_page", 0)
    records = item.get("records", [])
    if not isinstance(completed_page, int) or isinstance(completed_page, bool) or completed_page < 0:
        _logger.warning("关键词断点页码无效，已从第一页恢复: keyword=%r", keyword)
        completed_page = 0
    if not isinstance(records, list) or any(not isinstance(record, list) for record in records):
        _logger.warning("关键词断点记录无效，已忽略历史记录: keyword=%r", keyword)
        records = []
        completed_page = 0
    elif completed_page and not records:
        _logger.warning("关键词页级断点没有记录，已从第一页恢复: keyword=%r", keyword)
        completed_page = 0
    return completed_page, records


def stored_results(state: dict[str, Any]) -> dict[str, list]:
    completed = state.get("completed", {})
    if not isinstance(completed, dict):
        return {}
    results: dict[str, list] = {}
    for keyword, item in completed.items():
        if not isinstance(keyword, str) or not isinstance(item, dict):
            continue
        records = item.get("records", [])
        if isinstance(records, list):
            results[keyword] = records
    return results


def completed_results(state: dict[str, Any]) -> dict[str, list]:
    completed = state.get("completed", {})
    if not isinstance(completed, dict):
        return {}

    results: dict[str, list] = {}
    retryable_count = 0
    for keyword, item in completed.items():
        if not isinstance(keyword, str) or not isinstance(item, dict):
            continue
        if item.get("status") not in _TERMINAL_STATUSES:
            retryable_count += 1
            continue
        records = item.get("records", [])
        if isinstance(records, list):
            results[keyword] = records

    if retryable_count:
        _logger.info("恢复任务包含待重试关键词: count=%d", retryable_count)
    return results


def task_is_finished(state: dict[str, Any]) -> bool:
    keywords = state.get("keywords", [])
    completed = state.get("completed", {})
    if not isinstance(keywords, list) or not isinstance(completed, dict):
        return False
    return all(
        isinstance(completed.get(keyword), dict)
        and completed[keyword].get("status") in _TERMINAL_STATUSES
        for keyword in keywords
    )


def describe_task(state: dict[str, Any]) -> str:
    keywords = state.get("keywords", [])
    completed = state.get("completed", {})
    keyword_count = len(keywords) if isinstance(keywords, list) else 0
    completed_count = 0
    retryable_count = 0
    if isinstance(completed, dict):
        for item in completed.values():
            if not isinstance(item, dict):
                continue
            if item.get("status") in _TERMINAL_STATUSES:
                completed_count += 1
            else:
                retryable_count += 1
    retry_text = f"，待重试 {retryable_count} 个" if retryable_count else ""
    return (
        f"关键词 {keyword_count} 个，已完成 {completed_count} 个{retry_text}，"
        f"每词 {state.get('max_pages')} 页，保存方式 {state.get('save_mode')}"
    )


def _is_valid_task_state(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("version") not in {TASK_STATE_VERSION, _LEGACY_TASK_STATE_VERSION}:
        return False
    if not isinstance(raw.get("ts"), str) or not raw["ts"]:
        return False
    if raw.get("save_mode") not in {"single", "single_csv", "multi_split", "multi_merge", "multi_csv"}:
        return False
    if not isinstance(raw.get("max_pages"), int) or raw["max_pages"] <= 0:
        return False
    keywords = raw.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        return False
    completed = raw.get("completed")
    return isinstance(completed, dict)


def _upgrade_legacy_task_state(raw: dict[str, Any]) -> dict[str, Any]:
    raw["version"] = TASK_STATE_VERSION
    completed = raw.get("completed", {})
    if isinstance(completed, dict):
        for item in completed.values():
            if isinstance(item, dict):
                item.setdefault("completed_page", 0)
    return raw
