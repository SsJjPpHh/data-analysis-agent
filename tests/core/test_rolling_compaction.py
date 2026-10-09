# -*- coding: utf-8 -*-
"""Rolling compaction with cross-turn persistence.

2026-09-23 fix: ``compact_history`` only rewrote the caller's working copy,
so once the persisted session history crossed the compaction trigger every
subsequent turn re-ran the summarizer on the same prefix (an LLM call per
turn with zero cumulative value).  The session now stores a small rolling
record (summary + covered count + boundary fingerprint); these tests cover
record validation, zero-cost reuse, forward rolling, and the agent-level
``compaction_state`` event contract.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.compaction import (
    COMPACTION_SUMMARY_MARKER,
    _boundary_fingerprint,
    _compaction_state_valid,
    compact_history_rolling,
    reuse_compaction_view,
    reuse_view_is_small_enough,
)


def _msg(role: str, text: str, **extra) -> dict:
    return {"role": role, "content": text, **extra}


def _history(n: int, chars: int = 300) -> list:
    return [_msg("user" if i % 2 == 0 else "assistant", f"message-{i}-" + "x" * chars) for i in range(n)]


class _Response:
    def __init__(self, content: str):
        choices = [SimpleNamespace(message=SimpleNamespace(content=content))]
        self.choices = choices


class _ScriptedNonStream:
    """Record create() kwargs; return queued non-stream responses."""

    def __init__(self):
        self.kwargs = []
        self.responses = []

    def queue(self, content: str):
        self.responses.append(_Response(content))

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        assert kwargs.get("stream") is False
        return self.responses.pop(0)


class _Client:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_ScriptedNonStream())


def _state_for(history, covered, summary="s"):
    boundary = (
        _boundary_fingerprint(history[covered - 1]) if 0 < covered <= len(history) else "x"
    )
    return {
        "summary": summary,
        "covered": covered,
        "boundary": boundary,
    }


# ── Record validation ────────────────────────────────────────────────────────


def test_state_validity_requires_matching_boundary():
    history = _history(10)
    state = _state_for(history, 6)

    assert _compaction_state_valid(state, history)

    # History grew by appends — still valid (the reuse case).
    assert _compaction_state_valid(state, history + [_msg("user", "new")])

    # History replaced (fingerprint mismatch) — invalid.
    replaced = list(history)
    replaced[5] = _msg("user", "totally different content")
    assert not _compaction_state_valid(state, replaced)

    # Covered index outside the history — invalid.
    assert not _compaction_state_valid(_state_for(history, 99), history)
    assert not _compaction_state_valid({"summary": "s", "covered": 0, "boundary": "x"}, history)
    assert not _compaction_state_valid(None, history)
    assert not _compaction_state_valid("bogus", history)


# ── Zero-cost reuse ──────────────────────────────────────────────────────────


def test_reuse_view_rebuilds_summary_plus_uncovered_tail():
    history = _history(10)
    state = _state_for(history, 6, summary="earlier findings")

    view = reuse_compaction_view(history, state)

    assert view is not None
    assert view[0]["role"] == "system"
    assert view[0]["_compaction_summary"] is True
    assert COMPACTION_SUMMARY_MARKER in view[0]["content"]
    assert view[1:] == history[6:]
    # Raw history is never mutated by reuse.
    assert len(history) == 10


def test_reuse_view_never_opens_with_orphan_tool_message():
    history = _history(10)
    state = _state_for(history, 6)
    # The first uncovered message is a tool result whose assistant(tool_calls)
    # is inside the summarized region — the view must walk back to include
    # the assistant message so the API never sees an orphan tool message.
    history[6] = _msg("tool", "[tool_result] …", tool_call_id="call-1")
    history.insert(6, {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1"}]})

    view = reuse_compaction_view(history, state)

    assert view is not None
    assert view[1]["role"] == "assistant"
    assert "tool_calls" in view[1]


def test_reuse_view_returns_none_for_invalid_record():
    history = _history(10)
    assert reuse_compaction_view(history, None) is None
    assert reuse_compaction_view(history, {"summary": "s", "covered": 4}) is None


def test_reuse_view_size_gate():
    history = _history(10, chars=1000)
    view = reuse_compaction_view(history, _state_for(history, 6))
    assert view is not None
    assert reuse_view_is_small_enough(view, context_window=200_000)
    assert not reuse_view_is_small_enough(view, context_window=2000)
    assert not reuse_view_is_small_enough(view, context_window=0)


# ── Rolling summarization ────────────────────────────────────────────────────


def test_first_compaction_creates_state_record():
    client = _Client()
    client.chat.completions.queue("## 1. User Goals\nsummary-v1")
    history = _history(12)

    new_history, did_compact, state = compact_history_rolling(
        history, client, "deepseek-flash"
    )

    assert did_compact is True
    assert COMPACTION_SUMMARY_MARKER in new_history[0]["content"]
    assert state is not None
    assert 0 < state["covered"] < len(history)
    assert state["boundary"] == _boundary_fingerprint(history[state["covered"] - 1])
    # Only one summarizer call happened.
    assert len(client.chat.completions.kwargs) == 1
    # The verbatim tail survived untouched.
    assert new_history[1:] == history[state["covered"]:]


def test_rolling_merges_prior_summary_without_re_summarizing_it():
    client = _Client()
    client.chat.completions.queue("summary-v1")
    history = _history(12)
    _, _, state_v1 = compact_history_rolling(history, client, "deepseek-flash")
    assert state_v1 is not None

    # Two more turns arrive; the uncovered tail grows past the reuse gate.
    grown = history + _history(2, chars=3000)

    client.chat.completions.queue("summary-v2 (merged)")
    new_history, did_compact, state_v2 = compact_history_rolling(
        grown, client, "deepseek-flash", prior_state=state_v1
    )

    assert did_compact is True
    assert state_v2 is not None
    assert state_v2["covered"] > state_v1["covered"]
    assert state_v2["boundary"] == _boundary_fingerprint(grown[state_v2["covered"] - 1])
    # The summarizer prompt contained the previous summary and the roll
    # instruction, and only the uncovered segment — not the whole history.
    prompt = client.chat.completions.kwargs[-1]["messages"][1]["content"]
    assert "<previous_summary>" in prompt
    assert "summary-v1" in prompt
    assert "message-0-" not in prompt  # already-covered prefix not re-sent
    assert new_history[0]["content"].startswith(COMPACTION_SUMMARY_MARKER)


def test_invalid_prior_record_is_treated_as_absent():
    client = _Client()
    client.chat.completions.queue("summary-fresh")
    history = _history(12)
    bogus = {"summary": "stale", "covered": 5, "boundary": "nope"}

    _, did_compact, state = compact_history_rolling(
        history, client, "deepseek-flash", prior_state=bogus
    )

    assert did_compact is True
    assert state is not None and state["summary"] == "summary-fresh"
    prompt = client.chat.completions.kwargs[-1]["messages"][1]["content"]
    assert "<previous_summary>" not in prompt


def test_summarizer_failure_keeps_prior_state():
    client = _Client()

    class _Exploding:
        def create(self, **kwargs):
            raise RuntimeError("provider down")

    client.chat.completions = _Exploding()
    history = _history(12)
    prior = _state_for(history, 6, summary="keep-me")

    new_history, did_compact, state = compact_history_rolling(
        history, client, "deepseek-flash", prior_state=prior
    )

    assert did_compact is False
    assert new_history == history
    assert state == prior


# ── Agent-level contract ─────────────────────────────────────────────────────


def _stream_factory(content: str):
    def __iter__(self):
        delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", delta=delta)],
        )

    return type("_Stream", (), {"__iter__": __iter__})


def test_agent_run_yields_compaction_state_and_reuses_it_next_turn():
    """One summarizer call on turn 1; turn 2 reuses the record with zero
    summarizer calls; the record round-trips through the session field."""
    from agent.agent import BusinessAgent

    class _Completions:
        def __init__(self):
            self.kwargs = []

        def create(self, **kwargs):
            self.kwargs.append(kwargs)
            if kwargs.get("stream"):
                return _stream_factory("好的")()
            return _Response("## 1. User Goals\nrolling summary")

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _AgentClient:
        def __init__(self):
            self.chat = _Chat()

    agent = BusinessAgent(
        client=_AgentClient(),
        model="deepseek-flash",
        context_window=2000,  # tiny window so the ~5.9k-char history triggers 70%
        workspace_id="",
    )
    big_history = _history(8, chars=700)

    events_1 = list(agent.run("继续", list(big_history)))
    state_events = [e for e in events_1 if e.get("type") == "compaction_state"]
    assert state_events, "turn 1 must emit a compaction_state record"
    record = state_events[-1]
    assert record["covered"] > 0 and record["boundary"]
    summarizer_calls_after_turn_1 = len(
        [k for k in agent.client.chat.completions.kwargs if not k.get("stream")]
    )
    assert summarizer_calls_after_turn_1 == 1

    # Turn 2 with the persisted record: no further summarizer call.
    events_2 = list(
        agent.run("继续", list(big_history) + [_msg("user", "新消息")], compaction_state=dict(record))
    )
    assert not [e for e in events_2 if e.get("type") == "error"]
    summarizer_calls_after_turn_2 = len(
        [k for k in agent.client.chat.completions.kwargs if not k.get("stream")]
    )
    assert summarizer_calls_after_turn_2 == 1, "turn 2 must reuse the stored summary"

    # Turn 3 WITHOUT the record reproduces the old cost (guard against the
    # record silently not being consulted).
    list(agent.run("继续", list(big_history) + [_msg("user", "新消息")]))
    summarizer_calls_after_turn_3 = len(
        [k for k in agent.client.chat.completions.kwargs if not k.get("stream")]
    )
    assert summarizer_calls_after_turn_3 == 2
