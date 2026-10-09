import json
from types import SimpleNamespace

from agent.agent import BusinessAgent
from tests.core.test_deepseek_vision import PNG_1X1


def _run_r_tool_call(index=0, id_="r-call-1"):
    function = SimpleNamespace(
        name="run_r_code",
        arguments=json.dumps({"code": "baa_plot('enrollment', p)"}),
    )
    return SimpleNamespace(index=index, id=id_, function=function)


class _ToolStream:
    def __iter__(self):
        delta = SimpleNamespace(
            content=None,
            reasoning_content=None,
            tool_calls=[_run_r_tool_call()],
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="tool_calls", delta=delta)],
        )


class _RunRThenInventoryStream:
    def __iter__(self):
        inventory = SimpleNamespace(
            index=1,
            id="inv-call-1",
            function=SimpleNamespace(
                name="get_r_package_inventory",
                arguments=json.dumps({"query": "ggplot2"}),
            ),
        )
        delta = SimpleNamespace(
            content=None,
            reasoning_content=None,
            tool_calls=[_run_r_tool_call(), inventory],
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="tool_calls", delta=delta)],
        )


class _FinalStream:
    def __iter__(self):
        delta = SimpleNamespace(
            content="图标题含 QK-447，ARM B 高于 ARM A。",
            reasoning_content=None,
            tool_calls=None,
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", delta=delta)],
        )


class _Completions:
    def __init__(self, first_stream=None):
        self.calls = []
        self._first = first_stream or _ToolStream()

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return self._first
        return _FinalStream()


class _Client:
    def __init__(self):
        self.chat = SimpleNamespace(completions=_Completions())


class _FigureAgent(BusinessAgent):
    def __init__(self, *args, figure_payload: str, **kwargs):
        # This fixture exercises the explicitly enabled figure-feedback path.
        # Default-off and policy-change cases use the same fixture with a
        # different explicit policy in test_model_sharing_egress.py.
        kwargs.setdefault("model_sharing_policy", {"send_result_images": True})
        super().__init__(*args, **kwargs)
        self._figure_payload = figure_payload

    def _analysis_plan_allows_r(self):
        return True, ""

    def _tool_run_r_code(self, **_kwargs):
        return self._figure_payload

    def _tool_get_r_package_inventory(self, **_kwargs):
        return json.dumps({"ok": True, "packages": []}, ensure_ascii=False)


def _messages_have_image(calls) -> bool:
    for call in calls[1:]:
        for message in call.get("messages") or []:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            if any(block.get("type") == "image_url" for block in content if isinstance(block, dict)):
                return True
    return False


def _honesty_prompt_present(calls) -> bool:
    needle = "不要声称你看到了图"
    for call in calls[1:]:
        for message in call.get("messages") or []:
            content = message.get("content")
            if isinstance(content, str) and needle in content:
                return True
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and needle in str(block.get("text") or ""):
                        return True
    return False


def test_run_r_code_followup_attaches_session_png_to_the_next_llm_call(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "r-vision-chain"
    figure = data_path("outputs", "research", sid, "artifacts", "enrollment.png")
    figure.parent.mkdir(parents=True)
    figure.write_bytes(PNG_1X1)
    payload = json.dumps(
        {
            "artifact_previews": {
                "tables": [{"name": "counts", "preview_csv": "arm,n\nA,42\nB,58"}],
            },
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "enrollment",
                        "path": str(figure),
                        "kind": "figure",
                        "mime_type": "image/png",
                        "metadata": {"format_variant": "png_preview"},
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    client = _Client()
    agent = _FigureAgent(
        client=client,
        model="deepseek-flash",
        session_id=sid,
        workspace_id="",
        figure_payload=payload,
        enable_thinking=False,
    )

    events = list(agent.run("画图并解释", []))

    second = client.chat.completions.calls[1]["messages"]
    review_text = ""
    for message in second:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    review_text += str(block.get("text") or "")
    assert "请同时阅读本轮数值预览和附图" in review_text
    assert "A,42" in review_text
    assert _messages_have_image(client.chat.completions.calls)
    history_event = next(event for event in events if event.get("type") == "tool_history")
    history_blob = json.dumps(history_event["messages"], ensure_ascii=False)
    assert "data:image" not in history_blob
    assert any(
        message.get("kind") == "artifact_review" and message.get("hidden")
        for message in history_event["messages"]
    )
    assert any(event.get("type") == "text" and "QK-447" in event.get("content", "") for event in events)
    assert events[-1] == {"type": "done"}


def test_unreadable_r_figures_tell_the_model_not_to_claim_sight(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    payload = json.dumps(
        {
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "arm_pdf",
                        "path": str(tmp_path / "outside.pdf"),
                        "kind": "figure",
                        "mime_type": "application/pdf",
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    client = _Client()
    agent = _FigureAgent(
        client=client,
        model="deepseek-flash",
        session_id="r-vision-chain",
        workspace_id="",
        figure_payload=payload,
        enable_thinking=False,
    )

    list(agent.run("解释这张图", []))

    assert not _messages_have_image(client.chat.completions.calls)
    assert _honesty_prompt_present(client.chat.completions.calls)


def test_vision_followup_is_appended_after_all_tool_results(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "r-vision-chain"
    figure = data_path("outputs", "research", sid, "artifacts", "enrollment.png")
    figure.parent.mkdir(parents=True)
    figure.write_bytes(PNG_1X1)
    payload = json.dumps(
        {
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "enrollment",
                        "path": str(figure),
                        "kind": "figure",
                        "mime_type": "image/png",
                        "metadata": {"format_variant": "png_preview"},
                    }
                ],
            }
        },
        ensure_ascii=False,
    )
    client = _Client()
    client.chat.completions = _Completions(first_stream=_RunRThenInventoryStream())
    agent = _FigureAgent(
        client=client,
        model="deepseek-flash",
        session_id=sid,
        workspace_id="",
        figure_payload=payload,
        enable_thinking=False,
    )

    list(agent.run("画图并解释", []))

    second = client.chat.completions.calls[1]["messages"]
    assistant_idx = max(
        index
        for index, message in enumerate(second)
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    tail_roles = [message.get("role") for message in second[assistant_idx:]]
    assert tail_roles[:4] == ["assistant", "tool", "tool", "user"]
    assert _messages_have_image(client.chat.completions.calls)


def test_later_turn_rehydrates_saved_figure_and_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path

    sid = "r-vision-chain"
    figure = data_path("outputs", "research", sid, "artifacts", "enrollment.png")
    figure.parent.mkdir(parents=True)
    figure.write_bytes(PNG_1X1)
    payload = json.dumps(
        {
            "artifact_previews": {
                "tables": [{"name": "counts", "preview_csv": "arm,n\nA,42\nB,58"}],
            },
            "result": {
                "ok": True,
                "figures": [
                    {
                        "name": "enrollment",
                        "path": str(figure),
                        "kind": "figure",
                        "mime_type": "image/png",
                        "metadata": {"format_variant": "png_preview"},
                    }
                ],
            },
        },
        ensure_ascii=False,
    )
    first_client = _Client()
    first_agent = _FigureAgent(
        client=first_client,
        model="deepseek-flash",
        session_id=sid,
        workspace_id="",
        figure_payload=payload,
        enable_thinking=False,
    )
    events = list(first_agent.run("画图并解释", []))
    history = next(event["messages"] for event in events if event.get("type") == "tool_history")

    later_client = _Client()
    later_client.chat.completions = _Completions(first_stream=_FinalStream())
    later_agent = BusinessAgent(
        client=later_client,
        model="deepseek-flash",
        session_id=sid,
        workspace_id="",
        model_sharing_policy={"send_result_images": True},
        enable_thinking=False,
    )
    list(later_agent.run("再解释一次这张图", history))

    first_call = later_client.chat.completions.calls[0]["messages"]
    blob = json.dumps(first_call, ensure_ascii=False)
    assert "data:image/png;base64," in blob
    assert "A,42" in blob
