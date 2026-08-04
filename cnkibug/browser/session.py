#保存单轮抓取状态
from __future__ import annotations

import time
from threading import Event
from typing import Any

from ..core.events import EventSink, NULL_EVENTS


class ScrapeSession:
    def __init__(
        self,
        events: EventSink = NULL_EVENTS,
        cancel_event: Event | None = None,
    ) -> None:
        self.page: Any | None = None
        self.events = events
        self._stop_requested = False
        self._cancel_event = cancel_event
        self.verify_timeout = False
        self.stop_reason = ""

    @property
    def stop_requested(self) -> bool:
        return self.acknowledge_stop_request()

    def acknowledge_stop_request(self, reason: str = "用户请求停止") -> bool:
        if self._stop_requested:
            return True
        if not (
            (self._cancel_event is not None and self._cancel_event.is_set())
            or self.events.cancel_requested()
        ):
            return False
        self.request_stop(reason)
        return True

    def wait_interruptibly(self, seconds: float) -> bool:
        if self.acknowledge_stop_request():
            return False
        wait_seconds = max(0.0, seconds)
        if self._cancel_event is not None:
            self._cancel_event.wait(wait_seconds)
        else:
            time.sleep(wait_seconds)
        return not self.acknowledge_stop_request()

    def request_stop(self, reason: str = "", verify_timeout: bool = False) -> None:
        self._stop_requested = True
        if reason:
            self.stop_reason = reason
        if verify_timeout:
            self.verify_timeout = True


def require_page(session: ScrapeSession) -> Any:
    if session.page is None:
        raise RuntimeError("浏览器页面未初始化")
    return session.page
