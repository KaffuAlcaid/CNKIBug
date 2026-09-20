from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.search_query import SearchOptions, load_advanced_queries
from .keyword_input import dedupe_keywords


def _validate_task(task: Any) -> dict:
    if not isinstance(task, dict):
        raise ValueError("检索方案缺少任务设置。")
    keywords = task.get("keywords")
    if not isinstance(keywords, list) or not keywords or not all(isinstance(item, str) and item.strip() for item in keywords):
        raise ValueError("检索方案需要有效的检索项列表。")
    if dedupe_keywords(keywords).keywords != keywords:
        raise ValueError("检索方案中的检索项包含重复内容或首尾空白。")
    pages = task.get("max_pages")
    if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
        raise ValueError("检索方案的页数必须为正整数。")
    mode = task.get("save_mode")
    if not isinstance(mode, str) or mode not in {"single", "single_csv", "multi_merge", "multi_split", "multi_csv"}:
        raise ValueError("检索方案的输出格式无效。")
    if mode in {"single", "single_csv"} and len(keywords) != 1:
        raise ValueError("单检索项输出格式与检索项数量不符。")
    flags = {}
    for key in ("include_citation", "include_details", "detail_txt_export"):
        value = task.get(key, False)
        if not isinstance(value, bool):
            raise ValueError("检索方案的采集选项必须为布尔值。")
        flags[key] = value
    if flags["detail_txt_export"] and not flags["include_details"]:
        raise ValueError("关键词 TXT 导出需要同时采集论文详情。")
    output_dir = task.get("output_dir")
    if output_dir is not None and not isinstance(output_dir, str):
        raise ValueError("检索方案的保存位置无效。")
    advanced = load_advanced_queries(task.get("advanced_queries", {}), keywords)
    options = SearchOptions.from_dict(task.get("search_options"))
    return {
        "keywords": keywords, "max_pages": pages, "save_mode": mode, **flags,
        "output_dir": output_dir, "advanced_queries": {key: query.to_dict() for key, query in advanced.items()},
        "search_options": options.to_dict() if options else None,
    }


def read_search_plan(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("format") != "cnkibug-search-plan" or data.get("version") != 1:
        raise ValueError("请选择 CNKIBug 检索方案文件。")
    return _validate_task(data.get("task"))


def write_search_plan(path: str | Path, task: dict) -> None:
    from .exporter import _write_atomically

    data = {"format": "cnkibug-search-plan", "version": 1, "task": _validate_task(task)}
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _write_atomically(str(path), lambda temporary: Path(temporary).write_text(text, encoding="utf-8", newline="\n"))
