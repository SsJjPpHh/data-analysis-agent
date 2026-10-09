import json
from types import SimpleNamespace

from agent.agent import BusinessAgent


class _ToolStream:
    def __init__(self, call_id: str):
        self._call_id = call_id

    def __iter__(self):
        function = SimpleNamespace(
            name="run_r_code",
            arguments=json.dumps({"code": "stop('same failure')"}),
        )
        tool_call = SimpleNamespace(
            index=0,
            id=self._call_id,
            function=function,
        )
        delta = SimpleNamespace(
            content=None,
            reasoning_content=None,
            tool_calls=[tool_call],
        )
        choice = SimpleNamespace(finish_reason="tool_calls", delta=delta)
        yield SimpleNamespace(usage=None, choices=[choice])


class _Completions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _ToolStream(f"call-{self.calls}")


class _Client:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())


class _MultiToolStream:
    def __iter__(self):
        tool_calls = []
        for index in range(3):
            tool_calls.append(
                SimpleNamespace(
                    index=index,
                    id=f"batch-call-{index}",
                    function=SimpleNamespace(
                        name="run_r_code",
                        arguments=json.dumps({"code": "stop('same failure')"}),
                    ),
                )
            )
        delta = SimpleNamespace(
            content=None,
            reasoning_content=None,
            tool_calls=tool_calls,
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="tool_calls", delta=delta)],
        )


class _SingleBatchCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _MultiToolStream()


class _RepeatedFailureAgent(BusinessAgent):
    def _analysis_plan_allows_r(self):
        return True, ""

    def _tool_run_r_code(self, **_kwargs):
        payload = {
            "result": {
                "ok": False,
                "error": "same generated R failure",
                "execution_stage": "run",
                "tables": [],
                "figures": [],
                "texts": [],
                "models": [],
            },
            "execution": {"status": "failed"},
            "repair_diagnostics": {
                "category": "r_execution_error",
                "facts": ["same generated R failure"],
            },
        }
        return "ERROR: " + json.dumps(payload)


def test_agent_loop_stops_after_one_failed_repair_instead_of_repeating_forever():
    client = _Client()
    agent = _RepeatedFailureAgent(
        client=client,
        model="test-model",
        workspace_id="",
    )

    events = list(agent.run("运行分析", []))

    assert client.chat.completions.calls == 2
    assert len(
        [event for event in events if event.get("type") == "tool_audit"]
    ) == 2
    assert any(
        event.get("type") == "text" and "已停止自动重试" in event.get("content", "")
        for event in events
    )
    assert events[-1] == {"type": "done"}


def test_terminal_r_repair_state_skips_later_calls_in_the_same_model_batch():
    client = _Client()
    client.chat.completions = _SingleBatchCompletions()
    agent = _RepeatedFailureAgent(
        client=client,
        model="test-model",
        workspace_id="",
    )

    events = list(agent.run("运行分析", []))

    assert client.chat.completions.calls == 1
    assert len(
        [event for event in events if event.get("type") == "tool_audit"]
    ) == 2
    assert events[-1] == {"type": "done"}
