from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from queue import Queue
from threading import Event
from typing import Any, Iterator

from ..core.events import EventSink


@dataclass(frozen=True)
class GuiEvent:
    name: str
    payload: dict[str, Any]


class GuiEventSink(EventSink):
    def __init__(self, event_queue: Queue[GuiEvent], cancel_event: Event) -> None:
        self._event_queue = event_queue
        self._cancel_event = cancel_event

    def emit(self, name: str, **payload: Any) -> None:
        self._event_queue.put(GuiEvent(name, payload))

    def confirm(self, prompt: str, *, default: bool = False) -> bool:
        response_queue: Queue[bool] = Queue()
        self.emit(
            "confirm_requested",
            prompt=prompt,
            default=default,
            response_queue=response_queue,
        )
        return response_queue.get()

    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    @contextmanager
    def activity(self, message: str) -> Iterator[None]:
        self.emit("activity_started", message=message)
        try:
            yield
        finally:
            self.emit("activity_finished", message=message)
