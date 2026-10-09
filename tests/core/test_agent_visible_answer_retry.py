from types import SimpleNamespace

from agent.agent import BusinessAgent


class _Stream:
    def __init__(self, *, finish_reason, content=None, reasoning=None):
        self._finish_reason = finish_reason
        self._content = content
        self._reasoning = reasoning

    def __iter__(self):
        delta = SimpleNamespace(
            content=self._content,
            reasoning_content=self._reasoning,
            tool_calls=None,
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason=self._finish_reason, delta=delta)],
        )


class _Completions:
    def __init__(self):
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        if len(self.kwargs) == 1:
            return _Stream(finish_reason="length", reasoning="internal chain of thought")
        return _Stream(finish_reason="stop", content="ARM A n=42")


class _Client:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())


def test_empty_thinking_budget_retries_for_a_visible_answer():
    client = _Client()
    agent = BusinessAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
        max_output_tokens=32,
    )

    events = list(agent.run("读图", []))
    texts = [event.get("content") for event in events if event.get("type") == "text"]

    assert len(client.chat.completions.kwargs) == 2
    assert "reasoning_effort" in client.chat.completions.kwargs[0]
    assert client.chat.completions.kwargs[1].get("tool_choice") == "auto"
    assert {tool["function"]["name"] for tool in client.chat.completions.kwargs[1]["tools"]} >= {
        "set_analysis_plan", "run_r_code",
    }
    assert client.chat.completions.kwargs[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in client.chat.completions.kwargs[1]
    assert "ARM A n=42" in texts
    assert events[-1] == {"type": "done"}
