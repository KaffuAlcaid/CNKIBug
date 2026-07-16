from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..browser.session import ScrapeSession
from ..cnki.models import STATUS_FAILED, make_keyword_result
from ..core.events import EventSink
from ..core.runtime import RuntimePaths
from ..core.settings import ScraperSettings
from .report import TaskReport
from .state import (
    completed_results,
    make_task_state,
    persist_task_state,
    stored_results,
)


_logger = logging.getLogger("cnkibug.workflow.task")


@dataclass
class TaskContext:
    keywords: list[str]
    max_pages: int
    save_mode: str
    include_citation: bool
    ts: str
    state: dict
    all_results: dict[str, list]
    terminal_results: dict[str, list]
    report: TaskReport
    settings: ScraperSettings
    paths: RuntimePaths
    events: EventSink
    session: ScrapeSession
    browser: Any | None = None
    browser_context: Any | None = None

    @property
    def total_records(self) -> int:
        return sum(len(items) for items in self.all_results.values())


def initialize_task(
    keywords: list[str],
    max_pages: int,
    save_mode: str,
    resume_state: dict | None,
    include_citation: bool,
    settings: ScraperSettings,
    paths: RuntimePaths,
    events: EventSink,
) -> TaskContext:
    if resume_state is not None:
        keywords = list(resume_state["keywords"])
        max_pages = int(resume_state["max_pages"])
        save_mode = str(resume_state["save_mode"])
        include_citation = bool(resume_state.get("include_citation", False))
        ts = str(resume_state["ts"])
        state = resume_state
        all_results = stored_results(state)
        terminal_results = completed_results(state)
        events.emit(
            "message",
            text=(
                "[*] 已载入上次未完成任务："
                f"共 {len(keywords)} 个关键词，已完成 {len(terminal_results)} 个。"
            ),
            level="dim",
        )
        _logger.info(
            "恢复未完成任务: keyword_count=%d completed=%d stored_results=%d "
            "max_pages=%d save_mode=%s include_citation=%s ts=%s",
            len(keywords),
            len(terminal_results),
            len(all_results),
            max_pages,
            save_mode,
            include_citation,
            ts,
        )
    else:
        all_results = {}
        terminal_results = {}
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        state = make_task_state(
            keywords,
            max_pages,
            save_mode,
            ts,
            include_citation=include_citation,
        )
        persist_task_state(state, "创建新任务", paths, events)

    report = TaskReport(
        total_keywords=len(keywords),
        include_citation=include_citation,
    )
    completed_state = state.get("completed", {})
    if isinstance(completed_state, dict):
        for index, keyword in enumerate(keywords, start=1):
            item = completed_state.get(keyword)
            if not isinstance(item, dict) or keyword not in terminal_results:
                continue
            report.add(make_keyword_result(
                keyword,
                index,
                len(keywords),
                item.get("records", []),
                str(item.get("status", STATUS_FAILED)),
                str(item.get("reason", "")),
            ))

    return TaskContext(
        keywords=keywords,
        max_pages=max_pages,
        save_mode=save_mode,
        include_citation=include_citation,
        ts=ts,
        state=state,
        all_results=all_results,
        terminal_results=terminal_results,
        report=report,
        settings=settings,
        paths=paths,
        events=events,
        session=ScrapeSession(events),
    )
