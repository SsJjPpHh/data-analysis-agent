# -*- coding: utf-8 -*-
"""Unit tests for the conversation run relay buffer (api/conversation_run.py).

The buffer decouples the analysis worker from SSE consumers: a refresh or
network drop closes the consumer without touching the producer, and a
re-attaching consumer resumes from an absolute frame index.
"""

from __future__ import annotations

import threading
import time

import pytest

from api.conversation_run import ConversationRun


def test_publish_then_drain_after_finish():
    run = ConversationRun("j1")
    run.publish("data: a\n\n")
    run.publish("data: b\n\n")
    run.finish()
    assert list(run.stream(after=0)) == ["data: a\n\n", "data: b\n\n"]
    assert run.finished is True


def test_stream_resumes_from_after_and_tails_live_frames():
    run = ConversationRun("j1")
    for i in range(3):
        run.publish(f"f{i}\n\n")
    consumer = run.stream(after=1)
    assert next(consumer) == "f1\n\n"
    assert next(consumer) == "f2\n\n"
    # Producer stays live while the consumer is between frames.
    run.publish("f3\n\n")
    assert next(consumer) == "f3\n\n"
    run.finish()
    with pytest.raises(StopIteration):
        next(consumer)


def test_stream_waits_for_producer_and_finish_ends_it():
    run = ConversationRun("j1")
    frames_seen: list[str] = []

    def producer():
        time.sleep(0.05)
        run.publish("late\n\n")
        run.finish()

    thread = threading.Thread(target=producer, daemon=True)
    thread.start()
    for frame in run.stream(after=0):
        frames_seen.append(frame)
    thread.join(timeout=2)
    assert frames_seen == ["late\n\n"]


def test_truncation_drops_oldest_and_absolute_index_stays_consistent():
    run = ConversationRun("j1", max_bytes=1024)
    frames = [f"data: event-{i:02d}-{'x' * 120}\n\n" for i in range(12)]
    for frame in frames:
        run.publish(frame)
    run.finish()
    replayed = list(run.stream(after=0))
    # Oldest frames were dropped; the newest frame always survives.
    assert 0 < len(replayed) < len(frames)
    assert replayed[-1] == frames[-1]
    # Absolute indexing stays consistent after trims.
    first_abs = 12 - len(replayed)
    assert list(run.stream(after=first_abs)) == replayed


def test_consumer_disconnect_mid_stream_does_not_disturb_producer():
    run = ConversationRun("j1")
    run.publish("f0\n\n")
    consumer = run.stream(after=0)
    assert next(consumer) == "f0\n\n"
    consumer.close()  # client gone; generator suspended at a yield
    run.publish("f1\n\n")
    run.finish()
    assert list(run.stream(after=1)) == ["f1\n\n"]


def test_last_index_tracks_published_frames():
    run = ConversationRun("j1")
    assert run.last_index == 0
    run.publish("a\n\n")
    run.publish("b\n\n")
    assert run.last_index == 2
    run.publish("c\n\n")
    assert run.last_index == 3
