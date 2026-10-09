from __future__ import annotations

import json
import os
import stat


def test_deepseek_builtin_config_is_locked(tmp_path, monkeypatch):
    import LLM.llm_config_manager as llm_config

    config_file = tmp_path / "llm_config.json"
    monkeypatch.setattr(llm_config, "LLM_CONFIG_FILE", config_file)
    monkeypatch.setattr(llm_config, "CONFIG_DIR", tmp_path)

    manager = llm_config.LLMConfigManager()

    assert set(manager.DEFAULT_CONFIGS) == {"deepseek"}
    assert manager.set_config("deepseek", "sk-test") is True

    config = manager.get_config("deepseek")
    assert config is not None
    assert config.base_url == "https://api.deepseek.com"
    assert manager.DEFAULT_CONFIGS["deepseek"]["model"] == "deepseek-flash"
    assert config.model == "deepseek-flash"
    assert config.name == "DeepSeek V4.1 Flash"
    assert config.context_window == 1_000_000
    assert config.max_output_tokens == 393_216
    assert config.enable_thinking is True
    assert config.reasoning_effort == "max"
    assert manager.list_configs()["deepseek"]["has_api_key"] is True
    assert "sk-test" not in json.dumps(manager.list_configs())
    if os.name != "nt":
        assert stat.S_IMODE(config_file.stat().st_mode) == 0o600


def test_desktop_model_routes_manage_one_deepseek_key(tmp_path, monkeypatch):
    import LLM.llm_config_manager as llm_config
    import api.models as model_routes
    from api import create_app

    config_file = tmp_path / "llm_config.json"
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.setattr(llm_config, "LLM_CONFIG_FILE", config_file)
    monkeypatch.setattr(llm_config, "CONFIG_DIR", tmp_path)
    manager = llm_config.LLMConfigManager()
    monkeypatch.setattr(
        manager,
        "test_config",
        lambda provider="deepseek", api_key=None: {
            "success": True,
            "message": "DeepSeek 连接有效",
            "provider": provider,
        },
    )
    monkeypatch.setattr(model_routes, "config_manager", manager)
    client = create_app().test_client()

    assert client.get("/api/models").get_json()["deepseek"]["has_api_key"] is False
    saved = client.post(
        "/api/models/set-builtin",
        json={"provider": "deepseek", "api_key": "sk-desktop"},
    )
    assert saved.status_code == 200
    assert saved.get_json()["config"]["has_api_key"] is True
    assert "sk-desktop" not in saved.get_data(as_text=True)

    cleared = client.post("/api/models/clear-builtin", json={"provider": "deepseek"})
    assert cleared.status_code == 200
    assert client.get("/api/models").get_json()["deepseek"]["has_api_key"] is False


def test_desktop_rejects_an_invalid_deepseek_key_before_saving(tmp_path, monkeypatch):
    import LLM.llm_config_manager as llm_config
    import api.models as model_routes
    from api import create_app

    config_file = tmp_path / "llm_config.json"
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.setattr(llm_config, "LLM_CONFIG_FILE", config_file)
    monkeypatch.setattr(llm_config, "CONFIG_DIR", tmp_path)
    manager = llm_config.LLMConfigManager()
    monkeypatch.setattr(
        manager,
        "test_config",
        lambda provider="deepseek", api_key=None: {
            "success": False,
            "message": "DeepSeek API Key 无效或已失效",
            "provider": provider,
        },
    )
    monkeypatch.setattr(model_routes, "config_manager", manager)
    client = create_app().test_client()

    response = client.post(
        "/api/models/set-builtin",
        json={"provider": "deepseek", "api_key": "sk-invalid"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "DeepSeek API Key 无效或已失效",
        "code": "invalid_api_key",
    }
    assert manager.get_config("deepseek") is None
    assert not config_file.exists()


def test_deepseek_agent_call_options_enable_max_reasoning():
    from agent.agent import BusinessAgent

    agent = BusinessAgent(
        client=None,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
    )
    kwargs = {"model": "deepseek-flash", "messages": []}

    agent._apply_reasoning_options(kwargs)

    assert kwargs["reasoning_effort"] == "max"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}


def test_business_agent_has_no_mcp_surface():
    from agent.agent import BusinessAgent

    agent = BusinessAgent(client=None, model="deepseek-flash")

    assert not hasattr(agent, "_mcp_manager")


def test_deepseek_tool_reasoning_survives_session_history():
    from data.session import ChatSession

    session = ChatSession(session_id="deepseek-reasoning-history")
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "get_schema", "arguments": "{}"},
        }
    ]

    session.add_tool_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "完整保留这一轮工具调用的思考内容。",
                "tool_calls": tool_calls,
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": '{"ok": true}',
            },
        ]
    )

    assert session.history[0] == {
        "role": "assistant",
        "content": "",
        "reasoning_content": "完整保留这一轮工具调用的思考内容。",
        "tool_calls": tool_calls,
    }


def test_config_does_not_inherit_other_runtime_key(tmp_path, monkeypatch):
    import LLM.llm_config_manager as llm_config

    other = tmp_path / "old-desktop" / "config" / "llm_config.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"deepseek": {"api_key": "sk-other-runtime"}}))
    current = tmp_path / "new-runtime" / "config" / "llm_config.json"
    monkeypatch.setattr(llm_config, "LLM_CONFIG_FILE", current)
    manager = llm_config.LLMConfigManager()
    assert manager.get_config("deepseek") is None
    assert manager.set_config("deepseek", "sk-own-runtime")
    assert json.loads(current.read_text())["deepseek"]["api_key"] == "sk-own-runtime"
    assert json.loads(other.read_text())["deepseek"]["api_key"] == "sk-other-runtime"
