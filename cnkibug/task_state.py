from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from .runtime import get_paths
from .scrape_report import KeywordResult


LAST_TASK_FILENAME = "last_task.json"
TASK_STATE_VERSION = 1

_logger = logging.getLogger("cnkibug.task_state")


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
    return raw


def save_last_task(state: dict[str, Any]) -> Path | None:
    path = get_last_task_path()
    if path is None:
        _logger.warning("运行路径未初始化，跳过 last_task 保存")
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
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
    completed[result.keyword] = {
        "status": result.status,
        "reason": result.reason,
        "records": result.records,
    }
    return state


def completed_results(state: dict[str, Any]) -> dict[str, list]:
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


def describe_task(state: dict[str, Any]) -> str:
    keywords = state.get("keywords", [])
    completed = state.get("completed", {})
    keyword_count = len(keywords) if isinstance(keywords, list) else 0
    completed_count = len(completed) if isinstance(completed, dict) else 0
    return (
        f"关键词 {keyword_count} 个，已完成 {completed_count} 个，"
        f"每词 {state.get('max_pages')} 页，保存方式 {state.get('save_mode')}"
    )


def _is_valid_task_state(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if raw.get("version") != TASK_STATE_VERSION:
        return False
    if not isinstance(raw.get("ts"), str) or not raw["ts"]:
        return False
    if raw.get("save_mode") not in {"single", "multi_split", "multi_merge"}:
        return False
    if not isinstance(raw.get("max_pages"), int) or raw["max_pages"] <= 0:
        return False
    keywords = raw.get("keywords")
    if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
        return False
    completed = raw.get("completed")
    return isinstance(completed, dict)
