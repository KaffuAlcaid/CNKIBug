#保存单轮抓取状态
from __future__ import annotations

from typing import Any


class ScrapeSession:
    def __init__(self) -> None:
        self.page: Any | None = None
        self.stop_requested = False
        self.verify_timeout = False
        self.stop_reason = ""

    def request_stop(self, reason: str = "", verify_timeout: bool = False) -> None:
        self.stop_requested = True
        if reason:
            self.stop_reason = reason
        if verify_timeout:
            self.verify_timeout = True
