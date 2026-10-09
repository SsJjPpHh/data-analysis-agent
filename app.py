#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional loopback browser entry point for the local workbench."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))

try:
    from infrastructure import local_patches

    local_patches.apply()
except ImportError:
    pass

from infrastructure.logging_setup import setup_logging
from infrastructure.local_web import LOCAL_MAX_REQUEST_BODY_SIZE, local_web_threads
from infrastructure.paths import data_path


os.environ.setdefault("LOG_DIR", str(data_path("logs")))
setup_logging(level=logging.INFO)

from api import create_app


app = create_app()


def serve(host: str = "127.0.0.1", port: int = 5001) -> None:
    from waitress import serve as waitress_serve

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The local application may only bind to loopback")
    waitress_serve(
        app,
        host=host,
        port=port,
        send_bytes=1,
        inbuf_overflow=1024 * 1024,
        max_request_body_size=LOCAL_MAX_REQUEST_BODY_SIZE,
        connection_limit=100,
        channel_timeout=300,
        threads=local_web_threads(),
    )


if __name__ == "__main__":
    selected_port = int(os.environ.get("PORT") or os.environ.get("AGENT_PORT", "5001"))
    logging.getLogger(__name__).info("Research Statistics Workbench: http://127.0.0.1:%s", selected_port)
    serve(port=selected_port)
