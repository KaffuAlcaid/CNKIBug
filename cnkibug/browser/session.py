#保存单轮抓取状态
from __future__ import annotations

from typing import Any

from ..core.events import EventSink, NULL_EVENTS


class ScrapeSession:
    def __init__(self, events: EventSink = NULL_EVENTS) -> None:
        self.page: Any | None = None
        self.events = events
        self.stop_requested = False
        self.verify_timeout = False
        self.stop_reason = ""

    def request_stop(self, reason: str = "", verify_timeout: bool = False) -> None:
        self.stop_requested = True
        if reason:
            self.stop_reason = reason
        if verify_timeout:
            self.verify_timeout = True


def require_page(session: ScrapeSession) -> Any:
    if session.page is None:
        raise RuntimeError("浏览器页面未初始化")
    return session.page
