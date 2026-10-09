# -*- coding: utf-8 -*-
"""Research-loop provider projection, reasoning budgets and stream recovery.

Keep provider-required tool-call reasoning while excluding internal metadata;
cover continuation effort, template prompts, transient disconnects, explicit
cancellation and recovery after the model reaches its response limit.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest

from agent.analysis.run_control import (
    CallbackCancelHandle,
    research_run_controller,
)
from agent.agent import BusinessAgent


def _text_stream(content: str, reasoning: str | None = None):
    def __iter__(self):
        delta = SimpleNamespace(
            content=content,
            reasoning_content=reasoning,
            tool_calls=None,
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", delta=delta)],
        )

    return type("_Stream", (), {"__iter__": __iter__})


def _tool_call_stream(call_id: str, name: str = "run_r_code", args: dict | None = None):
    payload = json.dumps(args or {"code": "x <- 1"})

    def __iter__(self):
        tool_call = SimpleNamespace(
            index=0,
            id=call_id,
            function=SimpleNamespace(name=name, arguments=payload),
        )
        delta = SimpleNamespace(
            content=None,
            reasoning_content="round thinking",
            tool_calls=[tool_call],
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="tool_calls", delta=delta)],
        )

    return type("_Stream", (), {"__iter__": __iter__})


class _ScriptedCompletions:
    """Return queued streams; record every create() kwargs for assertions."""

    def __init__(self, script):
        self.script = list(script)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item()


class _Client:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=_ScriptedCompletions(script))


class _StubbedRAgent(BusinessAgent):
    def _analysis_plan_allows_r(self):
        return True, ""

    def _tool_run_r_code(self, **_kwargs):
        return json.dumps({"result": {"ok": True, "tables": [], "figures": [], "texts": [], "models": []}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


# ── Fix 1: wire-format projection ────────────────────────────────────────────


def test_messages_for_api_keeps_only_wire_fields():
    messages = [
        {"role": "system", "content": "s"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "deep thinking",
            "_compaction_summary": True,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]

    projected = BusinessAgent._messages_for_api(messages)

    assert projected[0] == {"role": "system", "content": "s"}
    # reasoning_content kept on last tool-call assistant; _compaction_summary stripped
    assert projected[1]["role"] == "assistant"
    assert projected[1].get("reasoning_content") == "deep thinking"
    assert "_compaction_summary" not in projected[1]
    assert projected[2] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
    # The internal list itself is untouched for display/bookkeeping use.
    assert messages[1]["reasoning_content"] == "deep thinking"


def test_single_tool_call_history_keeps_its_provider_reasoning_field():
    history = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "上一轮的完整思考内容",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "run_r_code", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"result": {"ok": true}}'},
    ]
    client = _Client([_text_stream("最终结论")])
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
    )

    events = list(agent.run("继续", history))

    sent = client.chat.completions.kwargs[0]["messages"]
    # reasoning_content kept on the LAST tool-call assistant (DeepSeek protocol)
    # but it should be the only one that has it
    assistants_with_rc = [
        m for m in sent
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("reasoning_content")
    ]
    assert len(assistants_with_rc) <= 1, "reasoning_content should only be on the last tool-call msg"
    assert events[-1] == {"type": "done"}


# ── Fix 2: per-round reasoning effort ────────────────────────────────────────


def test_continuation_rounds_downgrade_reasoning_effort():
    client = _Client(
        [
            _tool_call_stream("call-1"),
            _text_stream("最终结论"),
        ]
    )
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
    )

    list(agent.run("分析", []))

    kwargs_by_round = client.chat.completions.kwargs
    assert kwargs_by_round[0]["reasoning_effort"] == "max"
    assert kwargs_by_round[1]["reasoning_effort"] == "high"


def test_round_effort_env_override(monkeypatch):
    monkeypatch.setenv("BAA_TOOL_ROUND_REASONING_EFFORT", "low")
    client = _Client([_tool_call_stream("call-1"), _text_stream("最终结论")])
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
    )

    list(agent.run("分析", []))

    assert client.chat.completions.kwargs[1]["reasoning_effort"] == "low"


def test_downgraded_effort_rejection_falls_back_to_configured_effort():
    client = _Client(
        [
            _tool_call_stream("call-1"),
            Exception("Invalid value for reasoning_effort: high"),
            _text_stream("最终结论"),
        ]
    )
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
    )

    events = list(agent.run("分析", []))

    kwargs_by_round = client.chat.completions.kwargs
    assert len(kwargs_by_round) == 3
    assert kwargs_by_round[1]["reasoning_effort"] == "high"
    assert kwargs_by_round[2]["reasoning_effort"] == "max"
    assert any(event.get("type") == "text" and "最终结论" in event.get("content", "") for event in events)
    assert events[-1] == {"type": "done"}


# ── Fix 4a: stream reliability ───────────────────────────────────────────────


def _stream_error_type(message: str):
    def __iter__(self):
        raise httpx.RemoteProtocolError(message)
        yield  # pragma: no cover - makes this a generator

    return type("_Stream", (), {"__iter__": __iter__})


def test_transient_stream_drop_is_retried_without_failing_the_turn():
    client = _Client(
        [
            _stream_error_type("peer closed connection without sending complete message body"),
            _text_stream("最终结论"),
        ]
    )
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        workspace_id="",
    )

    events = list(agent.run("分析", []))

    assert len(client.chat.completions.kwargs) == 2
    assert not [event for event in events if event.get("type") == "error"]
    assert any(event.get("type") == "text" for event in events)
    assert events[-1] == {"type": "done"}


def test_visible_stream_drop_is_not_retried():
    def _partial_then_drop(self):
        delta = SimpleNamespace(content="部分可见回答", reasoning_content=None, tool_calls=None)
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason=None, delta=delta)],
        )
        raise httpx.RemoteProtocolError("peer closed connection")

    client = _Client([_partial_then_drop])
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        workspace_id="",
    )

    events = list(agent.run("分析", []))

    assert len(client.chat.completions.kwargs) == 1
    assert any(event.get("type") == "error" for event in events)
    assert events[-1] == {"type": "done"}


def test_user_stop_before_round_skips_llm_call():
    job_id = "stop-before-round"
    research_run_controller.begin(job_id)
    try:
        research_run_controller.cancel(job_id)
        client = _Client([_text_stream("不应该被生成")])
        agent = _StubbedRAgent(
            client=client,
            model="deepseek-flash",
            enable_thinking=True,
            workspace_id="",
            conversation_job_id=job_id,
        )

        events = list(agent.run("分析", []))

        assert client.chat.completions.kwargs == []
        assert not [event for event in events if event.get("type") == "error"]
        assert events[-1] == {"type": "done"}
    finally:
        research_run_controller.finish(job_id)


def test_stream_closed_by_user_stop_ends_cleanly():
    job_id = "stop-mid-stream"

    def _cancel_then_close(self):
        # Mirror the production race: the stop endpoint cancels the run,
        # which closes the registered stream while it is being consumed.
        research_run_controller.cancel(job_id)
        raise httpx.StreamClosed("Attempted to read or stream content, but the stream has been closed.")

    research_run_controller.begin(job_id)
    try:
        client = _Client([type("_Stream", (), {"__iter__": _cancel_then_close})])
        agent = _StubbedRAgent(
            client=client,
            model="deepseek-flash",
            enable_thinking=True,
            workspace_id="",
            conversation_job_id=job_id,
        )

        events = list(agent.run("分析", []))

        assert not [event for event in events if event.get("type") == "error"]
        assert events[-1] == {"type": "done"}
    finally:
        research_run_controller.finish(job_id)


# ── Fix 3: template fast path prompt section ─────────────────────────────────


def _plan(source: str):
    from agent.analysis.models import AnalysisPlan, RequiredOutput

    return AnalysisPlan(
        plan_id="plan-1",
        source=source,
        dataset_id="dataset-1",
        intent="分析",
        category="基础统计",
        method_family="classical_statistics",
        scenario="比较",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("primary", "table", required=True, tier="core"),),
        resource_class="standard",
    )


def test_template_plan_prompt_carries_one_pass_fast_path():
    from agent.agent import _analysis_plan_system_section

    section = _analysis_plan_system_section(_plan("template"))

    assert "[TEMPLATE EXECUTION FAST PATH]" in section
    assert "single run_r_code call" in section


def test_conversation_plan_prompt_has_no_template_fast_path():
    from agent.agent import _analysis_plan_system_section

    assert "[TEMPLATE EXECUTION FAST PATH]" not in _analysis_plan_system_section(_plan("conversation"))


# ── Fix 4b: stalled-stream watchdog ──────────────────────────────────────────


def test_stream_stall_watchdog_closes_stalled_stream():
    from agent.agent import _StreamStallWatchdog

    closed = []

    class _Closeable:
        def close(self):
            closed.append(True)

    watchdog = _StreamStallWatchdog(_Closeable(), idle_seconds=0.05, poll_seconds=0.01)
    watchdog.start()
    try:
        deadline = time.monotonic() + 2.0
        while not closed and time.monotonic() < deadline:
            time.sleep(0.005)
    finally:
        watchdog.stop()
    assert closed


def test_stream_stall_watchdog_touch_keeps_stream_open():
    from agent.agent import _StreamStallWatchdog

    closed = []

    class _Closeable:
        def close(self):
            closed.append(True)

    watchdog = _StreamStallWatchdog(_Closeable(), idle_seconds=0.2, poll_seconds=0.01)
    watchdog.start()
    try:
        for _ in range(20):
            time.sleep(0.02)
            watchdog.touch()
    finally:
        watchdog.stop()
    assert not closed


def test_read_timeout_is_treated_as_transient_and_retried():
    def _raise_read_timeout(self):
        raise httpx.ReadTimeout("timed out while reading response data")
        yield  # pragma: no cover - makes this a generator

    client = _Client([type("_Stream", (), {"__iter__": _raise_read_timeout}), _text_stream("最终结论")])
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        workspace_id="",
    )

    events = list(agent.run("分析", []))

    assert len(client.chat.completions.kwargs) == 2
    assert not [event for event in events if event.get("type") == "error"]
    assert events[-1] == {"type": "done"}


def test_stream_stall_seconds_env_clamps_low_values(monkeypatch):
    from infrastructure.llm_transport import stream_stall_seconds

    monkeypatch.delenv("BAA_LLM_STREAM_STALL_SECONDS", raising=False)
    assert stream_stall_seconds() == 180.0
    monkeypatch.setenv("BAA_LLM_STREAM_STALL_SECONDS", "240")
    assert stream_stall_seconds() == 240.0
    monkeypatch.setenv("BAA_LLM_STREAM_STALL_SECONDS", "1")
    assert stream_stall_seconds() == 30.0


def test_default_llm_timeout_shared_by_all_call_sites():
    from infrastructure.llm_transport import default_llm_timeout

    streaming = default_llm_timeout(read_seconds=300.0)
    ping = default_llm_timeout(read_seconds=120.0)

    assert streaming is not None and ping is not None
    assert streaming.connect == 15.0
    assert streaming.read == 300.0
    assert ping.read == 120.0


def test_compaction_summarizer_retries_transient_failure_and_carries_timeout():
    from agent import compaction

    attempts = []

    class _FlakyCompletions:
        def create(self, **kwargs):
            attempts.append(kwargs)
            if len(attempts) == 1:
                raise ConnectionError("connection reset by peer")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="摘要"))]
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=_FlakyCompletions()))
    summary = compaction._call_summarizer(client, "deepseek-flash", "历史内容")

    assert summary == "摘要"
    assert len(attempts) == 2
    assert attempts[-1].get("timeout") is not None


@pytest.mark.parametrize("configured,expected", [("medium", "high"), ("invalid", "max"), ("low", "low")])
def test_deepseek_request_uses_supported_effort(configured, expected):
    from LLM.llm_config_manager import deepseek_reasoning_options
    assert deepseek_reasoning_options(configured)["reasoning_effort"] == expected


def test_legacy_tool_round_override_is_normalized(monkeypatch):
    monkeypatch.setenv("BAA_TOOL_ROUND_REASONING_EFFORT", "medium")
    agent = BusinessAgent(client=_Client([]), model="deepseek-flash", workspace_id="")
    assert agent._round_reasoning_effort(1) == "high"


def test_effort_fallback_does_not_retry_auth_failure():
    client = _Client([Exception("Authentication failed"), _text_stream("unexpected retry")])
    agent = BusinessAgent(client=client, model="deepseek-flash", enable_thinking=True, workspace_id="")
    with pytest.raises(Exception, match="Authentication failed"):
        agent._open_llm_stream({"reasoning_effort": "high"}, "high")
    assert len(client.chat.completions.kwargs) == 1


# ── Length recovery: preserve execution, account for the exhausted round ──


def _reasoning_length_stream():
    class _Stream:
        def __iter__(self):
            for finish in (None, "length"):
                delta = SimpleNamespace(content=None, reasoning_content="internal reasoning", tool_calls=None)
                yield SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason=finish, delta=delta)])
            # OpenAI-compatible streaming reports usage in a final, choice-free
            # chunk. The length branch must not discard this billable round.
            yield SimpleNamespace(
                choices=[], usage=SimpleNamespace(prompt_tokens=192, completion_tokens=16000, total_tokens=16192),
            )
    return _Stream()


class _BudgetRecoveryAgent(_StubbedRAgent):
    def __init__(self, *args, artifact, **kwargs):
        super().__init__(*args, **kwargs)
        self.artifact = artifact
        self.executed_codes = []
        self.frozen_by_tool = False

    def _active_workflow_run_spec(self):
        # This stream-control fixture has no uploaded R dataset. Completion
        # still uses the production ResultValidator and an actual file receipt.
        return None

    def _tool_set_analysis_plan(self, args):
        assert args["required_outputs"] == [{"role": "primary", "kind": "table"}]
        self._analysis_plan = _plan("conversation")
        self.frozen_by_tool = True
        return json.dumps({"ok": True, "plan_id": self._analysis_plan.plan_id})

    def _tool_run_r_code(self, **kwargs):
        assert self._analysis_plan is not None, "Research execution must still follow the planning tool"
        code = kwargs["code"]
        self.executed_codes.append(code)
        failed = code == "stop('deliberate failure')"
        tables = [] if failed or code == "missing output" else [self.artifact]
        payload = {
            "job_id": "job-1", "execution": {
                "dataset_id": "dataset-1", "plan_id": "plan-1",
                "status": "failed" if failed else "succeeded", "diagnostic_status": {},
            },
            "result": {"ok": not failed, "tables": tables, "figures": [], "texts": [], "models": []},
        }
        if failed:
            payload["result"]["stderr"] = "Error: deliberate failure"
        return ("ERROR: " if failed else "") + json.dumps(payload)


def _budget_artifact(tmp_path):
    # Real file and server validation receipt; completion is not mocked.
    from tests.core.test_analysis_completion import _trusted_artifact
    return _trusted_artifact(tmp_path / "primary.csv", kind="table", role="primary")


def _planning_args():
    return {
        "intent": "summary", "category": "基础统计", "method_family": "classical_statistics", "scenario": "summary",
        "variable_bindings": {}, "analysis_inputs": [], "package_queries": [], "prerequisites": [],
        "assumptions": [], "diagnostics": [], "required_outputs": [{"role": "primary", "kind": "table"}],
        "resource_class": "standard",
    }


def _tool_names(kwargs):
    return {tool["function"]["name"] for tool in kwargs["tools"]}


def test_length_before_plan_keeps_planning_and_r_tools_then_validates_results(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    client = _Client([
        _reasoning_length_stream,
        _tool_call_stream("plan-1", "set_analysis_plan", _planning_args()),
        _tool_call_stream("r-1", args={"code": "produce primary"}),
        _text_stream("已根据实际工具结果完成解释"),
    ])
    agent = _BudgetRecoveryAgent(client, "deepseek-flash", artifact=_budget_artifact(tmp_path), workspace_id="",
                                 enable_thinking=True, reasoning_effort="max", max_output_tokens=16000)
    events = list(agent.run("执行分析", []))
    retry = client.chat.completions.kwargs[1]
    assert retry["tool_choice"] == "auto"
    assert {"set_analysis_plan", "run_r_code"} <= _tool_names(retry)
    assert retry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in retry and retry["max_tokens"] == 16000
    assert agent.frozen_by_tool and agent.executed_codes == ["produce primary"]
    assert [event["tool"] for event in events if event["type"] == "tool_start"] == ["set_analysis_plan", "run_r_code"]
    usage = next(event for event in events if event["type"] == "usage")
    assert {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")} == {
        "prompt_tokens": 192, "completion_tokens": 16000, "total_tokens": 16192,
    }
    assert events.index(usage) < next(i for i, event in enumerate(events) if event["type"] == "tool_start")
    audit = next(event for event in events if event["type"] == "tool_audit" and event["tool"] == "run_r_code")
    assert json.loads(audit["content"])["result_assessment"]["complete"] is True
    assert events[-1] == {"type": "done"}


@pytest.mark.parametrize("first_code", [None, "stop('deliberate failure')", "missing output"])
def test_length_with_unexecuted_failed_or_incomplete_plan_keeps_r_recovery(tmp_path, monkeypatch, first_code):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    script = [] if first_code is None else [_tool_call_stream("r-0", args={"code": first_code})]
    script += [_reasoning_length_stream, _tool_call_stream("r-1", args={"code": "produce primary"}), _text_stream("结果已验证")]
    client = _Client(script)
    agent = _BudgetRecoveryAgent(client, "deepseek-flash", artifact=_budget_artifact(tmp_path), workspace_id="",
                                 analysis_plan=_plan("conversation"), enable_thinking=True, max_output_tokens=16000)
    events = list(agent.run("继续完成分析", []))
    retry = client.chat.completions.kwargs[1 if first_code is None else 2]
    assert retry["tool_choice"] == "auto" and "run_r_code" in _tool_names(retry)
    assert "set_analysis_plan" not in _tool_names(retry)
    assert retry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "不得宣称已冻结、已执行或已完成分析" in retry["messages"][-1]["content"]
    assert agent.executed_codes[-1] == "produce primary"
    audits = [json.loads(event["content"].removeprefix("ERROR:").strip()) for event in events if event["type"] == "tool_audit"]
    if first_code is not None:
        assert audits[0]["result_assessment"]["complete"] is False
    assert audits[-1]["result_assessment"]["complete"] is True
    assert any(event["type"] == "usage" and event["completion_tokens"] == 16000 for event in events)
    assert events[-1] == {"type": "done"}


def test_length_recovery_does_not_publish_a_premature_final_before_r_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    client = _Client([
        _tool_call_stream("r-0", args={"code": "stop('deliberate failure')"}),
        _reasoning_length_stream, _text_stream("错误宣称：已经全部执行完成"),
        _tool_call_stream("r-1", args={"code": "produce primary"}), _text_stream("实际结果已验证"),
    ])
    agent = _BudgetRecoveryAgent(client, "deepseek-flash", artifact=_budget_artifact(tmp_path), workspace_id="",
                                 analysis_plan=_plan("conversation"), enable_thinking=True, max_output_tokens=16000)
    events = list(agent.run("执行并修复失败", []))
    assert agent.executed_codes == ["stop('deliberate failure')", "produce primary"]
    assert client.chat.completions.kwargs[3]["tool_choice"] == "auto"
    assert "run_r_code" in _tool_names(client.chat.completions.kwargs[3])
    assert not any("错误宣称" in str(event.get("content", "")) for event in events if event["type"] in {"text", "text_delta"})
    assert any(event["type"] == "text" and "实际结果已验证" in event["content"] for event in events)
    assert events[-1] == {"type": "done"}


def test_length_after_validated_complete_portfolio_can_force_final_without_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    client = _Client([
        _tool_call_stream("r-1", args={"code": "produce primary"}),
        _reasoning_length_stream, _text_stream("依据已验证的结果解释"),
    ])
    agent = _BudgetRecoveryAgent(client, "deepseek-flash", artifact=_budget_artifact(tmp_path), workspace_id="",
                                 analysis_plan=_plan("conversation"), enable_thinking=True, max_output_tokens=16000)
    events = list(agent.run("分析并解释", []))
    retry = client.chat.completions.kwargs[2]
    assert retry["tool_choice"] == "none"
    assert retry["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in retry
    assert "科研产物已通过本机执行和完整性验证" in retry["messages"][-1]["content"]
    assert agent.executed_codes == ["produce primary"]
    assert any(event["type"] == "usage" and event["completion_tokens"] == 16000 for event in events)
    assert any(event["type"] == "text" and "依据已验证的结果解释" in event["content"] for event in events)
    assert events[-1] == {"type": "done"}
