#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared transport policy for every LLM API call site.

The research loop, history compaction, and the connection test all talk to
the same provider. Each call must carry an explicit timeout: without one, a
wedged provider stream or request hangs the caller indefinitely — observed
2026-08-31, where a stalled call froze a running analysis for over two and
a half hours until the application was restarted.

For streaming calls the read timeout is a transport backstop; the agent loop
additionally runs a stall watchdog because SSE keep-alive bytes can defeat
socket-level read timeouts while no actual chunk is delivered.
"""

from __future__ import annotations

import os
from typing import Optional

STREAM_STALL_SECONDS_ENV = "BAA_LLM_STREAM_STALL_SECONDS"
DEFAULT_STREAM_STALL_SECONDS = 180.0
MIN_STREAM_STALL_SECONDS = 30.0


def stream_stall_seconds() -> float:
    """Max seconds a streaming call may deliver no chunk before aborting."""
    raw = str(os.getenv(STREAM_STALL_SECONDS_ENV, "") or "").strip()
    try:
        value = float(raw) if raw else DEFAULT_STREAM_STALL_SECONDS
    except ValueError:
        value = DEFAULT_STREAM_STALL_SECONDS
    return max(MIN_STREAM_STALL_SECONDS, value)


def default_llm_timeout(read_seconds: Optional[float] = None):
    """Return an httpx.Timeout for LLM calls (None when httpx is unavailable).

    ``read_seconds`` bounds inactivity on the socket. Streaming callers pass
    ``max(300, stream_stall_seconds())`` so the watchdog stays the primary
    abort path; short interactive calls (connection test) pass less.
    """
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with openai
        return None
    return httpx.Timeout(
        connect=15.0,
        read=float(read_seconds or 300.0),
        write=60.0,
        pool=15.0,
    )
