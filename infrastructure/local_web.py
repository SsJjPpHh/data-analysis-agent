"""Configuration shared by the loopback desktop web servers."""

from __future__ import annotations

import os


DEFAULT_LOCAL_WEB_THREADS = 8
# Waitress otherwise rejects request bodies larger than 1 GiB.  The product
# only binds to loopback and streams uploads to disk, so the practical limit is
# the destination disk capacity (which the upload endpoint checks explicitly).
LOCAL_MAX_REQUEST_BODY_SIZE = (1 << 63) - 1


def local_web_threads() -> int:
    """Keep UI/status requests responsive while an SSE analysis is open."""

    raw = str(os.getenv("BAA_WEB_THREADS") or DEFAULT_LOCAL_WEB_THREADS).strip()
    try:
        configured = int(raw)
    except (TypeError, ValueError, OverflowError):
        configured = DEFAULT_LOCAL_WEB_THREADS
    return min(32, max(4, configured))
