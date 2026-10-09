# -*- coding: utf-8 -*-
"""In-memory event relay for one running conversation analysis.

The analysis itself runs on a background thread (decoupled from the HTTP
request that started it).  Every browser-bound SSE frame is published here;
any number of SSE consumers can tail the same buffer:

  - the original ``POST /chat`` stream,
  - re-attach streams opened after a page refresh or network drop
    (``GET /api/session/<sid>/jobs/<jid>/stream?after=N``).

Disconnecting a consumer never affects the analysis.  Frames are stored as
pre-serialized ``data: {...}\\n\\n`` strings with absolute indexes, so a
re-attaching client can resume exactly where it left off.  The buffer lives
on the session and is replaced when the next turn starts.
"""

from __future__ import annotations

import threading
from typing import Iterator


class ConversationRun:
    """Thread-safe publish/subscribe buffer for one conversation turn."""

    #: Drop oldest frames beyond this budget (text deltas dominate; a full
    #: turn is normally well under 1 MB).  ``stream(after=...)`` stays
    #: consistent via absolute indexing.
    MAX_BUFFER_BYTES = 8_000_000

    def __init__(self, job_id: str, max_bytes: int = MAX_BUFFER_BYTES):
        self.job_id = str(job_id)
        self._max_bytes = max(1024, int(max_bytes))
        self._condition = threading.Condition()
        self._events: list[str] = []
        self._base = 0  # absolute index of _events[0]
        self._bytes = 0
        self._finished = False

    # ── Producer side (worker thread) ────────────────────────────────────────

    def publish(self, frame: str) -> None:
        """Append one pre-serialized SSE frame and wake all consumers."""
        with self._condition:
            self._events.append(frame)
            # Frames are serialized with ensure_ascii=False: CJK text costs
            # ~3 bytes per char, so account bytes, not characters.
            self._bytes += len(frame.encode("utf-8"))
            while self._bytes > self._max_bytes and len(self._events) > 1:
                self._bytes -= len(self._events[0])
                self._events.pop(0)
                self._base += 1
            self._condition.notify_all()

    def finish(self) -> None:
        """Mark the turn complete; consumers drain remaining frames then stop."""
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    # ── Consumer side (SSE relay threads) ────────────────────────────────────

    def stream(self, after: int = 0, idle_heartbeat_seconds: float = 5.0) -> Iterator[str]:
        """Yield frames from absolute index ``after`` until the turn ends.

        Blocks while no new frames are available; emits SSE comment
        heartbeats during silence so proxies keep the connection open and
        disconnects are noticed promptly.  ``after`` below the retained
        window resumes from the oldest surviving frame.
        """
        index = max(int(after), self._base)
        idle_ticks = 0.0
        while True:
            with self._condition:
                index = max(index, self._base)
                while index >= self._base + len(self._events) and not self._finished:
                    self._condition.wait(timeout=1.0)
                    idle_ticks += 1.0
                batch = self._events[index - self._base :]
                index = self._base + len(self._events)
                finished = self._finished
            if not batch:
                if finished:
                    return
                if idle_ticks >= idle_heartbeat_seconds:
                    idle_ticks = 0.0
                    yield ": keep-alive\n\n"
                continue
            idle_ticks = 0.0
            for frame in batch:
                yield frame

    @property
    def last_index(self) -> int:
        """Absolute index one past the newest frame."""
        with self._condition:
            return self._base + len(self._events)

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._finished
