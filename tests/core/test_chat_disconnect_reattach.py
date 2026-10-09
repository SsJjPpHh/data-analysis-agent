# -*- coding: utf-8 -*-
"""End-to-end tests for disconnect-resilient conversation analysis.

2026-09-23 fix: the analysis used to live inside the POST /chat SSE request —
a page refresh killed the run (cancel_tracked on stream close).  The analysis
now runs on a background worker and publishes into a conversation run buffer;
this suite proves that:

  1. the normal POST /chat stream behaves exactly as before;
  2. a consumer that closes the stream early does NOT cancel the analysis —
     it runs to completion;
  3. ``GET /jobs/<jid>/stream`` re-attaches and replays the full turn
     including the final ``done`` frame;
  4. ``after=N`` resumes from the requested absolute index;
  5. unknown jobs answer 404.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import types

import pytest


def _install_optional_import_stubs(monkeypatch):
    duckdb = types.ModuleType("duckdb")
    duckdb.DuckDBPyConnection = object
    duckdb.connect = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)


def _sse_frames(body: str) -> list[dict]:
    events = []
    for raw in body.split("\n\n"):
        line = next((item for item in raw.split("\n") if item.startswith("data:")), None)
        if not line:
            continue
        try:
            events.append(json.loads(line[5:].strip()))
        except json.JSONDecodeError:
            continue
    return events


class _GateAgent:
    """Yields one frame, then waits until the test releases it."""

    def __init__(self, release: threading.Event):
        self.release = release

    def run(self, *args, **kwargs):
        yield {"type": "text_delta", "content": "第一段"}
        self.release.wait(timeout=10)
        yield {"type": "text", "content": "完整分析结果"}
        yield {"type": "text", "content": "分析收尾"}


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app
    from api import chat as chat_module

    release = threading.Event()
    monkeypatch.setattr(chat_module, "_build_agent", lambda *a, **k: _GateAgent(release))
    client = create_app().test_client()
    return client, release


def _conversation_job(client, sid: str) -> dict:
    listing = client.get(f"/api/session/{sid}/jobs")
    jobs = listing.get_json().get("jobs", [])
    return next(job for job in jobs if job.get("type") == "conversation_analysis")


def _wait_terminal(client, sid: str, jid: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get(f"/api/session/{sid}/jobs/{jid}").get_json()
        status = (payload.get("job") or {}).get("status")
        if status in {"succeeded", "failed", "canceled"}:
            return payload["job"]
        time.sleep(0.05)
    raise AssertionError("job did not reach a terminal state")


def test_normal_stream_still_completes_with_done(app_client):
    client, release = app_client
    release.set()  # don't hold the turn
    response = client.post(
        "/api/session/reattach-session/chat",
        json={"message": "分析"},
    )
    assert response.status_code == 200
    events = _sse_frames(response.get_data(as_text=True))
    types_seen = [event.get("type") for event in events]
    assert "text_delta" in types_seen
    assert types_seen[-1] == "done"

    job = _conversation_job(client, "reattach-session")
    assert job["status"] == "succeeded"


def test_closed_stream_does_not_cancel_and_reattach_replays(app_client):
    client, release = app_client

    # The worker publishes frame 1, then parks until the test signals.  The
    # test closes the response without ever reading the body — a browser that
    # vanished before consuming anything.  The analysis must still complete.
    response = client.post(
        "/api/session/reattach-session/chat",
        json={"message": "分析"},
    )
    assert response.status_code == 200
    response.close()
    release.set()

    job = _conversation_job(client, "reattach-session")
    terminal = _wait_terminal(client, "reattach-session", job["id"])
    assert terminal["status"] == "succeeded", terminal.get("error")

    # Re-attach: full replay from 0 reconstructs the whole turn.
    stream = client.get(f"/api/session/reattach-session/jobs/{job['id']}/stream?after=0")
    assert stream.status_code == 200
    assert stream.mimetype == "text/event-stream"
    events = _sse_frames(stream.get_data(as_text=True))
    types_seen = [event.get("type") for event in events]
    assert "text_delta" in types_seen
    assert "text" in types_seen
    assert types_seen[-1] == "done"


def test_reattach_after_param_resumes_from_index(app_client):
    client, release = app_client
    release.set()
    client.post("/api/session/reattach-session/chat", json={"message": "分析"})
    job = _conversation_job(client, "reattach-session")
    _wait_terminal(client, "reattach-session", job["id"])

    stream = client.get(f"/api/session/reattach-session/jobs/{job['id']}/stream?after=1")
    events = _sse_frames(stream.get_data(as_text=True))
    all_events = _sse_frames(
        client.get(f"/api/session/reattach-session/jobs/{job['id']}/stream?after=0").get_data(
            as_text=True
        )
    )
    assert len(events) == max(0, len(all_events) - 1)


def test_reattach_unknown_job_answers_404(app_client):
    client, _ = app_client
    response = client.get("/api/session/reattach-session/jobs/no-such-job/stream")
    assert response.status_code == 404
