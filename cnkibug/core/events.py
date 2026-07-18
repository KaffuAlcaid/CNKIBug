from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class EventSink:
    """Receives workflow events without tying core code to a UI toolkit."""

    def emit(self, name: str, **payload: Any) -> None:
        pass

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        return default

    def cancel_requested(self) -> bool:
        return False

    @contextmanager
    def activity(self, message: str) -> Iterator[None]:
        yield


NULL_EVENTS = EventSink()
