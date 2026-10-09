"""Single-user model configuration and per-session sharing settings."""

import logging
from flask import Blueprint, jsonify, request

from LLM.llm_config_manager import DEEPSEEK_PROVIDER
from agent.analysis.model_sharing import ModelSharingPolicy

from .local_access import session_for_request
from .state import config_manager


bp = Blueprint("models", __name__)
log = logging.getLogger(__name__)


@bp.get("/api/session/<sid>/model-sharing")
def get_model_sharing(sid: str):
    sess = session_for_request(sid)
    with sess._conversation_lock:
        policy = ModelSharingPolicy.from_dict(getattr(sess, "model_sharing_policy", None))
    return jsonify({"ok": True, "policy": policy.to_dict()})


@bp.post("/api/session/<sid>/model-sharing")
def set_model_sharing(sid: str):
    try:
        policy = ModelSharingPolicy.from_dict(request.get_json(silent=True), strict=True)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    sess = session_for_request(sid)
    if not sess.begin_session_mutation():
        return jsonify({"error": "当前分析或会话变更运行中，请完成后再修改模型外发设置。"}), 409
    try:
        previous = sess.model_sharing_policy
        previous_compaction = sess.compaction
        previous_prompt_tokens = sess.last_prompt_tokens
        previous_reasoning = sess.last_reasoning
        normalized = policy.to_dict()
        sess.model_sharing_policy = normalized
        if normalized != previous:
            sess.compaction = None
            sess.last_prompt_tokens = 0
            sess.last_reasoning = ""
        try:
            from .saved_sessions import persist_local_autosave

            persist_local_autosave(sess)
        except OSError:
            sess.model_sharing_policy = previous
            sess.compaction = previous_compaction
            sess.last_prompt_tokens = previous_prompt_tokens
            sess.last_reasoning = previous_reasoning
            log.exception("[model-sharing] unable to persist policy sid=%s", sess.session_id)
            return jsonify({"error": "模型外发设置保存失败，请重试。"}), 503
        return jsonify({"ok": True, "policy": policy.to_dict()})
    finally:
        sess.end_session_mutation()


@bp.get("/api/models")
def list_models():
    return jsonify(config_manager.list_configs())


@bp.get("/api/models/defaults")
def model_defaults():
    defaults = config_manager.DEFAULT_CONFIGS[DEEPSEEK_PROVIDER]
    return jsonify(
        {
            DEEPSEEK_PROVIDER: {
                "base_url": defaults["base_url"],
                "model": defaults["model"],
                "display_name": defaults["display_name"],
                "context_window": defaults["context_window"],
                "context_label": defaults["context_label"],
                "max_output_tokens": defaults["max_output_tokens"],
                "max_output_label": defaults["max_output_label"],
                "enable_thinking": defaults["enable_thinking"],
                "reasoning_effort": defaults["reasoning_effort"],
            }
        }
    )


@bp.post("/api/models/set-builtin")
def set_builtin():
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or DEEPSEEK_PROVIDER).strip()
    api_key = str(payload.get("api_key") or "").strip()
    if provider != DEEPSEEK_PROVIDER:
        return jsonify({"error": f"不支持的内置提供商: {provider}"}), 400
    if not api_key:
        return jsonify({"error": "DeepSeek API Key 不能为空"}), 400
    connection = config_manager.test_config(provider, api_key=api_key)
    if not connection.get("success"):
        detail = str(connection.get("message") or "").lower()
        authentication_failure = any(
            marker in detail
            for marker in (
                "authentication_error",
                "authentication fails",
                "invalid api key",
                "api key 无效",
                "api key is invalid",
            )
        ) or ("401" in detail and ("api key" in detail or "authentication" in detail))
        if authentication_failure:
            return jsonify(
                {
                    "error": "DeepSeek API Key 无效或已失效",
                    "code": "invalid_api_key",
                }
            ), 400
        return jsonify(
            {
                "error": "DeepSeek 连接测试未通过，请检查网络、账户额度和 API Key",
                "code": "deepseek_connection_failed",
            }
        ), 400
    if not config_manager.set_config(provider, api_key):
        return jsonify({"error": "DeepSeek API Key 保存失败"}), 500
    return jsonify(
        {
            "ok": True,
            "config": config_manager.list_configs()[DEEPSEEK_PROVIDER],
        }
    )


@bp.post("/api/models/clear-builtin")
def clear_builtin():
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or DEEPSEEK_PROVIDER).strip()
    ok, message = config_manager.clear_builtin_config(provider)
    return jsonify({"ok": ok, "message": message}), 200 if ok else 400


@bp.post("/api/models/test")
def test_model():
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or DEEPSEEK_PROVIDER).strip()
    temporary_key = str(payload.get("api_key") or "").strip() or None
    return jsonify(config_manager.test_config(provider, api_key=temporary_key))


@bp.post("/api/session/<sid>/model")
def set_session_model(sid: str):
    payload = request.get_json(silent=True) or {}
    provider = str(payload.get("provider") or DEEPSEEK_PROVIDER).strip()
    if provider != DEEPSEEK_PROVIDER:
        return jsonify({"error": f"不支持的模型: {provider}"}), 400
    if config_manager.get_config(provider) is None:
        return jsonify({"error": "未配置 DeepSeek API Key"}), 400
    session_for_request(sid).model_provider = provider
    return jsonify({"ok": True})
