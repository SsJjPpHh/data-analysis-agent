"""Blueprint: conversation (SSE streaming) and chart serving."""

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict

from flask import Blueprint, request, Response, jsonify, stream_with_context, send_file

from .state import session_manager, config_manager, chart_store
from .conversation_run import ConversationRun
from .local_access import create_session_for_request, session_for_request
from agent.activation import ActivationContext
from agent.analysis.models import AnalysisPlan
from agent.analysis.plan_projection import plan_browser_projection
from agent.analysis.run_control import research_run_controller
from agent.agent import BusinessAgent
from agent.jobs import JobLimitExceeded
from agent.reasoning import split_reasoning_tags
from data.analysis_plan_store import AnalysisPlanStore
from data.conversation_images import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_TURN,
    load_conversation_image,
    load_conversation_images,
    save_conversation_image,
)
from data.research_results import ResearchResultStore
from data.research_workspace import default_research_workspace
from infrastructure.public_projection import redact_filesystem_paths

log = logging.getLogger(__name__)
bp = Blueprint("chat", __name__)


class ActivationRequestError(ValueError):
    def __init__(self, message: str, code: str = "invalid_activation") -> None:
        super().__init__(message)
        self.code = code


def _actionable_llm_error(message: str) -> str:
    """Translate known provider failures without exposing provider payloads or keys."""
    normalized = str(message or "").lower()
    authentication_markers = (
        "authentication_error",
        "authentication fails",
        "invalid api key",
        "api key is invalid",
    )
    if any(marker in normalized for marker in authentication_markers) or (
        "401" in normalized and ("api key" in normalized or "authentication" in normalized)
    ):
        return "DeepSeek API Key 无效或已失效，请在系统设置中重新填写有效的 API Key"
    return ""


def _bounded_string(value, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _bounded_int(value, default: int = 0, min_value: int = 0, max_value: int = 20) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(min_value, min(max_value, number))


def _bounded_string_list(value, limit: int = 12, item_limit: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _bounded_string(item, item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _bounded_dict_string_lists(value, allowed_keys: set[str]) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, object] = {}
    for key in allowed_keys:
        item = value.get(key)
        if isinstance(item, list):
            clean[key] = _bounded_string_list(item)
        elif isinstance(item, str):
            clean[key] = _bounded_string(item)
        elif isinstance(item, (int, float, bool)):
            clean[key] = item
    return clean


def _sanitize_template_context(value) -> dict:
    if not isinstance(value, dict):
        return {}
    contract = value.get("template_contract") if isinstance(value.get("template_contract"), dict) else {}
    applicability = contract.get("applicability") if isinstance(contract.get("applicability"), dict) else {}
    methodology = contract.get("methodology") if isinstance(contract.get("methodology"), dict) else {}
    output_contract = (
        contract.get("output_contract") if isinstance(contract.get("output_contract"), dict) else {}
    )
    execution = contract.get("execution") if isinstance(contract.get("execution"), dict) else {}
    raw_bindings = value.get("variable_bindings") if isinstance(value.get("variable_bindings"), dict) else {}
    variable_bindings = {
        _bounded_string(key, 80): _bounded_string(variable, 160)
        for key, variable in list(raw_bindings.items())[:20]
        if _bounded_string(key, 80) and _bounded_string(variable, 160)
    }
    clean = {
        "id": _bounded_string(value.get("id"), 120),
        "plan_id": _bounded_string(value.get("plan_id"), 80),
        "title": _bounded_string(value.get("title"), 180),
        "category": _bounded_string(value.get("category"), 80),
        "workflow": _bounded_string(value.get("workflow"), 80),
        "package_names": _bounded_string_list(value.get("package_names"), limit=16, item_limit=80),
        "variable_bindings": variable_bindings,
        "template_contract": {
            "schema_version": _bounded_string(contract.get("schema_version"), 40),
            "applicability": _bounded_dict_string_lists(
                applicability,
                {"data_types", "study_designs", "outcome_types", "variable_roles", "prerequisites"},
            ),
            "methodology": _bounded_dict_string_lists(
                methodology,
                {"assumptions", "diagnostics", "interpretation_requirements"},
            ),
            "output_contract": {
                "min_tables": _bounded_int(output_contract.get("min_tables"), 0, 0, 12),
                "min_figures": _bounded_int(output_contract.get("min_figures"), 0, 0, 12),
                "required_outputs": _bounded_string_list(output_contract.get("required_outputs"), limit=12),
                "requires_diagnostics": bool(output_contract.get("requires_diagnostics")),
                "requires_publication_interpretation": bool(
                    output_contract.get("requires_publication_interpretation", True)
                ),
            },
            "execution": {
                "status": _bounded_string(execution.get("status"), 40),
                "label": _bounded_string(execution.get("label"), 80),
                "cost": _bounded_string(execution.get("cost"), 40),
                "priority": _bounded_string(execution.get("priority"), 60),
            },
            "fallback_packages": _bounded_string_list(
                contract.get("fallback_packages"), limit=10, item_limit=80
            ),
        },
    }
    return clean if clean["id"] or clean["title"] else {}


def _trusted_template_context(plan: AnalysisPlan, plan_id: str) -> dict:
    """Expose the frozen plan without creating a second execution contract."""

    return plan_browser_projection(plan, plan_id=plan_id)


def _parse_tool_payload(content: str) -> dict:
    text = str(content or "").strip()
    start = text.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_tool_payload_from_event(event: dict) -> dict:
    """Recover full tool payloads even when SSE content is only a preview."""
    payload = _parse_tool_payload(str(event.get("content") or ""))
    if isinstance(payload.get("result"), dict):
        return payload

    for artifact in event.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("type") != "tool_result":
            continue
        artifact_id = str(artifact.get("artifact_id") or "")
        if not artifact_id:
            continue
        try:
            from agent.tools.results import load_tool_result_artifact

            record = load_tool_result_artifact(artifact_id)
        except Exception:
            log.exception("[chat] failed to load tool_result artifact id=%s", artifact_id)
            continue
        if not isinstance(record, dict):
            continue
        recovered = _parse_tool_payload(str(record.get("data") or ""))
        if isinstance(recovered.get("result"), dict):
            return recovered

    return payload if isinstance(payload, dict) else {}


def _resolve_activation(sess, payload: dict):
    """Research mode only accepts plain chat turns."""
    skill_name = str(payload.get("skill") or "").strip()
    command_name = str(payload.get("command") or "").strip().lstrip("/").lower()
    internal_action = str(payload.get("internal_action") or "").strip().lower()
    if skill_name or command_name or internal_action:
        raise ActivationRequestError("科研模式不支持旧技能、斜杠命令或内部确认流。", "unsupported_activation")
    return ActivationContext()


def _build_agent(
    sess,
    *,
    workspace_id: str | None = None,
    source_snapshot=None,
    analysis_plan: AnalysisPlan | None = None,
    prior_portfolio_manifest: dict | None = None,
    workspace_generation: str = "",
    conversation_job_id: str = "",
) -> BusinessAgent:
    provider = sess.model_provider or config_manager.get_default_provider()
    from LLM.llm_config_manager import get_llm_client

    client = get_llm_client(provider)
    cfg = config_manager.get_config(provider)
    if cfg is None:
        raise ValueError("未配置 DeepSeek API Key。")
    sess.model_provider = provider
    # Use cached schema when available; recompute only after data source changes
    # (cache is invalidated by add_source / remove_source / toggle_source / data_source setter).
    if source_snapshot is not None:
        combined_schema = source_snapshot.combined_schema
    elif hasattr(sess, "get_combined_schema"):
        if not getattr(sess, "_combined_schema_cache", None):
            sess._combined_schema_cache = sess.get_combined_schema()
        combined_schema = sess._combined_schema_cache
    else:
        combined_schema = None
    active_sources = (
        list(source_snapshot.sources)
        if source_snapshot is not None
        else ([e["source"] for e in sess._active_entries()] if hasattr(sess, "_active_entries") else [])
    )
    if not active_sources and sess.data_source:
        active_sources = [sess.data_source]

    # 若有数据源但 schema 仍为空，尝试实时获取一次。
    # 若获取后仍为空（SQL 数据源连接断开、文件丢失等），报错提示用户重新连接，
    # 而不是让 LLM 无声地空转后输出空回复。
    if active_sources and not combined_schema:
        if source_snapshot is None:
            try:
                combined_schema = sess.get_combined_schema()
                sess._combined_schema_cache = combined_schema
            except Exception as exc:
                log.warning("[chat] schema fetch failed error=%s", exc)
                combined_schema = None
        if not combined_schema:
            src_names = "、".join(getattr(s, "name", "未知数据源") for s in active_sources)
            raise ValueError(
                f"数据源「{src_names}」的连接已断开（可能由服务重启引起），请在侧边栏重新连接数据源后再试。"
            )

    # Build (or reuse cached) MergedDataSource when ≥2 sources are active.
    # This enables cross-source JOIN / UNION in the agent.
    merged_source = (
        source_snapshot.merged_source
        if source_snapshot is not None
        else (sess.get_merged_source() if hasattr(sess, "get_merged_source") else None)
    )

    src_names = [getattr(s, "name", "?") for s in active_sources]
    log.debug(
        "[chat] build_agent  provider=%s  model=%s  active_sources=%s  merged=%s",
        provider,
        cfg.model,
        src_names,
        merged_source is not None,
    )

    return BusinessAgent(
        client=client,
        model=cfg.model,
        data_source=(source_snapshot.primary if source_snapshot is not None else sess.data_source),
        combined_schema=combined_schema,
        all_sources=active_sources,
        merged_source=merged_source,
        enable_thinking=cfg.enable_thinking,
        thinking_budget=cfg.thinking_budget,
        reasoning_effort=getattr(cfg, "reasoning_effort", None),
        chart_store=chart_store,
        session_chart_ids=list(getattr(sess, "chart_ids", [])),
        session_id=sess.session_id,
        research_dataset_id=str(getattr(sess, "research_dataset_id", "") or ""),
        workspace_id=workspace_id,
        job_runner=sess.job_runner,
        context_window=getattr(cfg, "context_window", None),
        max_output_tokens=getattr(cfg, "max_output_tokens", None),
        analysis_plan=analysis_plan,
        prior_portfolio_manifest=prior_portfolio_manifest,
        workspace_generation=workspace_generation,
        conversation_job_id=conversation_job_id,
        model_sharing_policy=getattr(sess, "model_sharing_policy", None),
    )


def _research_result_store(sess) -> ResearchResultStore:
    return default_research_workspace().results(sess.session_id)


# ── Session lifecycle ──────────────────────────────────────────────────────


@bp.post("/api/session/new")
def new_session():
    sess = create_session_for_request()
    log.info("[session] created")
    return jsonify({"session_id": sess.session_id})


@bp.get("/api/session/<sid>/ping")
def ping_session(sid: str):
    sess = session_manager.get(sid)
    if not sess:
        log.debug("[session] ping alive=False")
        return jsonify({"alive": False}), 404
    from api.saved_sessions import _visible_msg_count

    cnt = _visible_msg_count(sess.history)
    log.debug("[session] ping alive=True msg_count=%d", cnt)
    return jsonify({"alive": True, "msg_count": cnt})


@bp.post("/api/session/<sid>/resume")
def resume_session(sid: str):
    sess = session_manager.get(sid)
    restored = False
    if not sess:
        from api.saved_sessions import restore_local_autosave

        sess, restored = restore_local_autosave(sid)
    from api.saved_sessions import _visible_msg_count

    return jsonify(
        {
            "ok": True,
            "session_id": sess.session_id,
            "restored": restored,
            "msg_count": _visible_msg_count(sess.history),
        }
    )


@bp.get("/api/session/<sid>/load-current")
def load_current_session(sid: str):
    sess = session_manager.get(sid)
    if not sess:
        log.warning("[session] load-current not found")
        return jsonify({"error": "session not found"}), 404
    from api.saved_sessions import _visible_history, _visible_msg_count

    cnt = _visible_msg_count(sess.history)
    log.info("[session] load-current msg_count=%d", cnt)
    return jsonify(
        {
            "history": _visible_history(sess.history),
            "total_input": sess.total_input_tokens,
            "total_output": sess.total_output_tokens,
            "msg_count": cnt,
        }
    )


@bp.post("/api/session/<sid>/clear")
def clear_history(sid: str):
    sess = session_for_request(sid)
    if not sess.begin_session_mutation():
        return jsonify({"error": "当前分析运行中，请先停止或等待完成后再清空对话。"}), 409
    try:
        old_count = len(sess.history)
        sess.clear_history()
        from api.saved_sessions import persist_local_autosave

        persist_local_autosave(sess)
        log.info("[session] clear  sid=%s  cleared=%d entries", sid, old_count)
        return jsonify({"ok": True})
    finally:
        sess.end_session_mutation()


# ── Chart serving ──────────────────────────────────────────────────────────


@bp.get("/api/chart/<chart_id>")
def serve_chart(chart_id: str):
    html = chart_store.get(chart_id)
    if not html:
        log.warning("[chart] not found  chart_id=%s", chart_id)
        return "Chart not found", 404
    return Response(html, mimetype="text/html")


# ── Stop ───────────────────────────────────────────────────────────────────


@bp.post("/api/session/<sid>/stop")
def stop_session(sid: str):
    sess = session_manager.get(sid)
    if sess:
        for job in sess.job_runner.list_jobs(active_only=True, limit=20, top_level_only=True):
            job_id = str(job.get("id") or "")
            if job_id:
                research_run_controller.cancel(job_id)
                sess.request_cancel(job_id)
                sess.job_runner.cancel(job_id)
        log.info("[session] stop requested  sid=%s", sid)
    return jsonify({"ok": True})


# ── Chat SSE ───────────────────────────────────────────────────────────────


# ── Conversation images (DeepSeek Flash vision) ─────────────────────────────


@bp.post("/api/session/<sid>/chat/images")
def upload_chat_images(sid: str):
    sess = session_for_request(sid)
    request.max_content_length = MAX_IMAGES_PER_TURN * MAX_IMAGE_BYTES + 1024 * 1024
    files = [upload for upload in request.files.getlist("file") if upload and upload.filename]
    if not files:
        return jsonify({"error": "请选择要解读的图片"}), 400
    if len(files) > MAX_IMAGES_PER_TURN:
        return jsonify({"error": f"一次最多附带 {MAX_IMAGES_PER_TURN} 张图片"}), 400
    saved = []
    try:
        for upload in files:
            data = upload.stream.read(MAX_IMAGE_BYTES + 1)
            record = save_conversation_image(sess.session_id, data, upload.filename)
            saved.append(record.as_public_dict())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "images": saved})


@bp.get("/api/session/<sid>/chat/images/<image_id>")
def get_chat_image(sid: str, image_id: str):
    sess = session_for_request(sid)
    stored = load_conversation_image(sess.session_id, image_id)
    if stored is None:
        return jsonify({"error": "图片不存在"}), 404
    return send_file(stored.path, mimetype=stored.mime)


@bp.post("/api/session/<sid>/chat")
def chat_stream(sid: str):
    max_request_bytes = 256 * 1024
    request.max_content_length = max_request_bytes
    if request.content_length and request.content_length > max_request_bytes:
        return jsonify({"error": "分析请求过大"}), 413
    if not request.content_length:
        if not request.environ.get("wsgi.input_terminated"):
            return jsonify({"error": "请求缺少可验证的长度边界"}), 411
        raw_body = request.environ["wsgi.input"].read(max_request_bytes + 1)
        if len(raw_body) > max_request_bytes:
            return jsonify({"error": "分析请求过大"}), 413
        request._cached_data = raw_body
    d = request.get_json(silent=True) or {}
    if not isinstance(d, dict):
        return jsonify({"error": "请求格式无效"}), 400
    message = (d.get("message") or "").strip()
    image_ids = _bounded_string_list(d.get("image_ids"), limit=MAX_IMAGES_PER_TURN, item_limit=64)
    if not message and not image_ids:
        return jsonify({"error": "消息不能为空"}), 400
    if not message:
        from agent.vision import default_image_prompt

        message = default_image_prompt()
    try:
        max_message_chars = max(1000, int(os.getenv("BAA_CHAT_MAX_MESSAGE_CHARS", "32000")))
    except ValueError:
        max_message_chars = 32000
    if len(message) > max_message_chars:
        return jsonify({"error": f"消息过长，最多 {max_message_chars} 个字符"}), 413
    raw_data_context = d.get("data_context")
    data_context = raw_data_context if isinstance(raw_data_context, dict) else {}
    # The browser uses this compatibility field for both template runs and a
    # user-requested continuation of a free-form frozen plan. Its source is
    # trusted only after the plan is restored from the local plan store.
    plan_context = _sanitize_template_context(d.get("template_context"))
    template_context: dict = {}
    turn_id = _bounded_string(d.get("turn_id"), 120) or f"turn_{uuid.uuid4().hex[:16]}"

    sess = session_for_request(sid)
    from agent.analysis.model_sharing import ModelSharingPolicy

    sharing_policy = ModelSharingPolicy.from_dict(getattr(sess, "model_sharing_policy", None))
    if image_ids and not sharing_policy.send_attached_images:
        return jsonify({"error": "当前会话已关闭附件图片发送，请调整模型外发设置后重试。", "code": "attached_images_disabled"}), 400
    vision_images = load_conversation_images(sess.session_id, image_ids) if image_ids else []
    if image_ids and len(vision_images) != len(image_ids):
        return jsonify({"error": "附带的图片不存在或已失效"}), 400
    if not sess.begin_conversation():
        return jsonify({"error": "当前会话已有分析正在运行，请等待完成或先停止该任务。"}), 409

    conversation_job_id = ""
    source_snapshot = None
    frozen_analysis_plan: AnalysisPlan | None = None
    result_plan_context: dict = {}
    prior_portfolio_manifest: dict = {}
    fixed_research_workspace_generation = ""
    stream_prepared = False
    try:
        # Dataset selection, binding validation, and the frozen dataset id are
        # resolved while holding the same session lease used by mutations.
        research_workspace = default_research_workspace()
        current_dataset = research_workspace.current(sid)
        datasets = [current_dataset] if current_dataset is not None else []
        dataset_ids = {dataset.dataset_id for dataset in datasets}
        active_dataset_id = current_dataset.dataset_id if current_dataset is not None else ""
        fixed_research_workspace_generation = str(
            (research_workspace.current_record(sid) or {}).get("workspace_generation") or ""
        )
        research_result_store = _research_result_store(sess)
        result_state, result_exists, _saved_at = research_result_store.load()
        stored_dataset_id = (
            str((result_state.get("dataset") or {}).get("dataset_id") or "") if result_exists else ""
        )
        if stored_dataset_id in dataset_ids:
            active_dataset_id = stored_dataset_id
        elif active_dataset_id not in dataset_ids:
            active_dataset_id = ""
        requested_dataset_id = _bounded_string(d.get("dataset_id"), 32)
        if requested_dataset_id:
            if requested_dataset_id not in dataset_ids:
                return jsonify({"error": "所选数据集不存在"}), 404
            sess.research_dataset_id = requested_dataset_id
        elif active_dataset_id:
            sess.research_dataset_id = active_dataset_id
        else:
            if datasets:
                sess.research_dataset_id = datasets[0].dataset_id
            else:
                sess.research_dataset_id = ""
        activation = _resolve_activation(sess, d)
        fixed_research_dataset_id = str(getattr(sess, "research_dataset_id", "") or "")
        selected_dataset = next(
            (dataset for dataset in datasets if dataset.dataset_id == fixed_research_dataset_id),
            None,
        )
        if plan_context and selected_dataset is None:
            raise ActivationRequestError(
                "当前没有可用于该分析计划的数据，请重新上传数据并重新准备分析。",
                "invalid_analysis_plan",
            )
        if plan_context and selected_dataset is not None:
            summary = (
                selected_dataset.profile.get("summary") if isinstance(selected_dataset.profile, dict) else {}
            ) or {}
            plan_id = str(plan_context.get("plan_id") or "").strip()
            if not plan_id:
                raise ActivationRequestError(
                    "分析计划尚未完成冻结，请重新准备并确认关键输入。",
                    "invalid_analysis_plan",
                )
            frozen_analysis_plan = AnalysisPlanStore().load(
                sid,
                plan_id,
                dataset_id=selected_dataset.dataset_id,
                workspace_generation=fixed_research_workspace_generation,
                profile_revision=str(summary.get("profile_revision") or ""),
            )
            if frozen_analysis_plan is None:
                raise ActivationRequestError(
                    "分析计划不存在或与当前数据不一致，请重新准备分析。",
                    "invalid_analysis_plan",
                )
            if plan_context.get("id") and frozen_analysis_plan.template_id != str(
                plan_context.get("id") or ""
            ):
                raise ActivationRequestError(
                    "模板分析计划与所选模板不一致，请重新准备模板。",
                    "invalid_analysis_plan",
                )
            trusted_plan_context = _trusted_template_context(frozen_analysis_plan, plan_id)
            result_plan_context = trusted_plan_context
            if frozen_analysis_plan.source == "template":
                template_context = trusted_plan_context
                data_context = {**data_context, "template": template_context}
            else:
                # A free-form continuation receives the same frozen AnalysisPlan
                # in the agent system section, but must not be mislabeled or
                # reinterpreted as a catalog template.
                data_context = {key: value for key, value in data_context.items() if key != "template"}
            manifests = result_state.get("portfolio_manifests")
            manifests = manifests if isinstance(manifests, dict) else {}
            stored_manifest = manifests.get(frozen_analysis_plan.plan_id)
            prior_portfolio_manifest = (
                dict(stored_manifest) if isinstance(stored_manifest, dict) else {}
            )
        if fixed_research_dataset_id and stored_dataset_id != fixed_research_dataset_id:
            if selected_dataset is None:
                return jsonify({"error": "当前数据集不存在或无权访问"}), 404
            # Never attach orphaned turns from a reset/legacy empty state to a
            # newly frozen dataset.
            if result_state.get("analysis_turns"):
                research_result_store.reset()
            research_result_store.set_dataset(
                selected_dataset.to_dict(),
                (selected_dataset.profile.get("summary") or {}),
            )
        conversation_job_id = sess.job_runner.begin_tracked(
            "conversation_analysis",
            label="analysis_request",
        )
        research_run_controller.begin(conversation_job_id)
        conversation_job = sess.job_runner.get_status(conversation_job_id) or {}
        fixed_workspace_id = str(conversation_job.get("workspace_id") or "")
        from data.workspace import workspace_manager

        fixed_workspace_runtime = (
            workspace_manager.get_by_workspace(fixed_workspace_id) if fixed_workspace_id else None
        )
        source_snapshot = sess.acquire_data_source_snapshot()
        sess.record_activation(activation, message, conversation_job_id)
        sess.job_runner.append_tracked_event(
            conversation_job_id,
            {
                "type": "conversation_activation",
                "job_id": conversation_job_id,
                "activation": activation.to_record(),
            },
        )

        from api.saved_sessions import _visible_msg_count

        # Live event relay: the analysis runs on a background thread and
        # publishes every browser-bound SSE frame here, so a refreshed or
        # disconnected browser can re-attach without ending the analysis.
        conversation_run = ConversationRun(conversation_job_id)
        sess.conversation_run = conversation_run
        _turn_start = time.monotonic()
        log.info(
            "[chat] turn start activation=%s history=%d msgs",
            activation.kind,
            _visible_msg_count(sess.history),
        )
        stream_prepared = True
    except ActivationRequestError as exc:
        return jsonify({"error": str(exc), "code": exc.code}), 400
    except JobLimitExceeded as exc:
        return jsonify({"error": str(exc)}), 429
    except Exception:
        log.exception("[chat] conversation pre-stream setup failed")
        error_id = uuid.uuid4().hex[:12]
        if conversation_job_id:
            try:
                sess.job_runner.fail_tracked(
                    conversation_job_id,
                    f"分析上下文初始化失败（错误编号：{error_id}）。",
                )
            except Exception:
                log.exception("[chat] unable to mark failed pre-stream setup")
        return (
            jsonify({"error": f"分析上下文初始化失败，请稍后重试（错误编号：{error_id}）。"}),
            500,
        )
    finally:
        if not stream_prepared:
            if conversation_job_id:
                research_run_controller.finish(conversation_job_id)
            if source_snapshot is not None:
                try:
                    source_snapshot.release()
                except Exception:
                    log.exception("[chat] source snapshot release failed during setup")
            sess.end_conversation()

    def _sse(obj) -> str:
        from agent.events import serialize_event

        return f"data: {json.dumps(serialize_event(obj), ensure_ascii=False)}\n\n"

    runner = sess.job_runner
    resources_released = False
    resources_lock = threading.Lock()

    def _publish(event: dict) -> None:
        """Worker-side emit: serialize once, broadcast to every SSE consumer."""
        conversation_run.publish(_sse(event))

    def _finalize_worker() -> None:
        """Release conversation-wide resources when the turn truly ends.

        Runs on the analysis worker thread only.  A browser disconnecting
        does NOT reach this — the analysis keeps running (and stays
        re-attachable via the conversation run buffer) until it finishes or
        the user stops it explicitly.
        """
        nonlocal resources_released
        with resources_lock:
            if resources_released:
                return
            resources_released = True
        try:
            conversation_run.finish()
        except Exception:
            log.exception("[chat] unable to close conversation run buffer")
        try:
            current = runner.get_status(conversation_job_id)
            if current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                if sess.cancel_requested_for(conversation_job_id):
                    runner.cancel_tracked(conversation_job_id)
                    sess.clear_cancel(conversation_job_id)
        except Exception:
            log.exception("[chat] unable to finalize conversation job")
        try:
            sess.end_conversation()
        finally:
            try:
                source_snapshot.release()
            except Exception:
                log.exception("[chat] source snapshot release failed")
            research_run_controller.finish(conversation_job_id)

    def _produce() -> None:
        """Consume the agent turn, enrich events, persist results.

        Runs on a daemon worker thread, decoupled from the SSE response:
        browser-bound frames go through the conversation run buffer, so the
        analysis outlives any number of client disconnects.
        """

        def _public_failure(prefix: str) -> tuple[str, str]:
            error_id = uuid.uuid4().hex[:12]
            return error_id, f"{prefix}（错误编号：{error_id}）。"

        try:
            agent = _build_agent(
                sess,
                workspace_id=fixed_workspace_id,
                source_snapshot=source_snapshot,
                analysis_plan=frozen_analysis_plan,
                prior_portfolio_manifest=prior_portfolio_manifest,
                workspace_generation=fixed_research_workspace_generation,
                conversation_job_id=conversation_job_id,
            )
        except Exception as exc:
            log.exception("[chat] build_agent failed")
            safe_configuration_errors = {
                "未配置 DeepSeek API Key",
                "未配置 DeepSeek API Key。",
            }
            if isinstance(exc, ValueError) and str(exc) in safe_configuration_errors:
                error_message = str(exc)
            else:
                _error_id, error_message = _public_failure("分析服务初始化失败，请稍后重试")
            try:
                runner.fail_tracked(conversation_job_id, error_message)
            except Exception:
                log.exception("[chat] unable to mark failed pre-stream build")
            # Frames first, finalize second: _finalize_worker calls
            # conversation_run.finish(), which wakes blocked consumers —
            # publishing after it races their exit and drops the error.
            _publish({"type": "error", "message": error_message})
            _publish({"type": "done"})
            _finalize_worker()
            return

        _last_progress = {"ts": 0.0}

        def _publish_progress(text: str) -> None:
            now = time.monotonic()
            if now - _last_progress["ts"] < 5.0:
                return
            _last_progress["ts"] = now
            _publish({"type": "tool_progress", "content": str(text)[:200]})

        # Test doubles may not implement the publisher seam.
        _set_progress = getattr(agent, "set_progress_publisher", None)
        if callable(_set_progress):
            _set_progress(_publish_progress)

        collected: list[str] = []
        collected_reasoning: list[str] = []
        turn_chart_ids: list[str] = []
        completed_normally = False
        tool_calls_in_turn: list[str] = []
        step_count = 0
        pending_steps: dict[str, list[dict]] = {}
        artifact_signatures: set[str] = set()
        stream_error = ""
        r_attempted = False
        result_state_error = ""

        def _append_parent_artifact(artifact: dict) -> None:
            if not isinstance(artifact, dict) or not artifact:
                return
            signature = json.dumps(artifact, ensure_ascii=False, sort_keys=True, default=str)
            if signature in artifact_signatures:
                return
            artifact_signatures.add(signature)
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "artifact_created",
                    "job_id": conversation_job_id,
                    "artifact": artifact,
                },
            )

        def _collect_downloads(content: str) -> None:
            for name, url in re.findall(r"\[([^\]]+)\]\((/api/(?:export|output)/[^)]+)\)", content or ""):
                _append_parent_artifact(
                    {
                        "type": "file",
                        "name": name.replace("📥", "").strip(),
                        "url": url,
                    }
                )

        def _start_step(event: dict) -> None:
            nonlocal step_count
            step_count += 1
            tool = str(event.get("tool") or "unknown")
            step = {
                "step_id": f"step-{step_count}",
                "tool": tool,
                "display": event.get("display") or tool,
                "started_monotonic": time.monotonic(),
            }
            pending_steps.setdefault(tool, []).append(step)
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "conversation_step_started",
                    "job_id": conversation_job_id,
                    "step_id": step["step_id"],
                    "tool": tool,
                    "display": step["display"],
                    "step_number": step_count,
                },
            )
            runner.update_tracked(
                conversation_job_id,
                min(95, step_count * 4),
                f"已执行 {step_count} 个步骤",
            )

        def _finish_step(tool: str, ok: bool = True, error: str = "", elapsed=None) -> None:
            queue = pending_steps.get(tool) or []
            if not queue:
                return
            step = queue.pop(0)
            duration = elapsed
            if duration is None:
                duration = time.monotonic() - step["started_monotonic"]
            public_error = redact_filesystem_paths(error)[:1000]
            runner.append_tracked_event(
                conversation_job_id,
                {
                    "type": "conversation_step_finished",
                    "job_id": conversation_job_id,
                    "step_id": step["step_id"],
                    "tool": tool,
                    "display": step["display"],
                    "step_number": int(step["step_id"].split("-")[-1]),
                    "status": "succeeded" if ok else "failed",
                    "elapsed_seconds": round(float(duration or 0), 3),
                    "error": public_error,
                },
            )

        try:
            # Every data-backed conversation exposes the same readable schema
            # snapshot without forcing the model to call get_schema each turn.
            schema_text = str(source_snapshot.combined_schema or "")
            if schema_text:
                from agent.tools.results import persist_large_tool_result

                _preview, schema_artifact, _budget = persist_large_tool_result(
                    sid,
                    "get_schema",
                    schema_text,
                    runtime=fixed_workspace_runtime,
                    threshold=1,
                    deduplicate=True,
                )
                if schema_artifact:
                    schema_artifact["name"] = "get_schema 数据结构"
                    sess.record_tool_audit({"recovery": {}, "artifacts": [schema_artifact]})
                    _append_parent_artifact(schema_artifact)

            # Per-session temporary instruction — only injected when enabled.
            active_temp_prompt = (
                getattr(sess, "temp_prompt", "") if getattr(sess, "temp_prompt_enabled", False) else ""
            )
            workspace_status = (
                {"mounted": True, **fixed_workspace_runtime.to_dict()}
                if fixed_workspace_runtime is not None
                else {"mounted": False}
            )
            recovery_context = sess.build_recovery_context(workspace_status)

            conversation_scope = runner.conversation_scope(conversation_job_id)
            conversation_scope.__enter__()
        except Exception:
            log.exception("[chat] conversation stream setup failed")
            _error_id, error_message = _public_failure("分析上下文初始化失败，请稍后重试")
            try:
                runner.fail_tracked(conversation_job_id, error_message)
            except Exception:
                log.exception("[chat] unable to mark failed pre-stream build")
            # Frames first, finalize second: _finalize_worker calls
            # conversation_run.finish(), which wakes blocked consumers —
            # publishing after it races their exit and drops the error.
            _publish({"type": "error", "message": error_message})
            _publish({"type": "done"})
            _finalize_worker()
            return
        try:
            _publish(
                {
                    "type": "analysis_turn",
                    "turn_id": turn_id,
                    "title": (
                        frozen_analysis_plan.intent
                        if frozen_analysis_plan is not None
                        else template_context.get("title") or message[:80]
                    ),
                    "source": (
                        "template"
                        if frozen_analysis_plan is not None
                        and frozen_analysis_plan.source == "template"
                        else "natural_language"
                    ),
                    "template_id": template_context.get("id", ""),
                    "template_title": template_context.get("title", ""),
                    "analysis_plan": asdict(frozen_analysis_plan) if frozen_analysis_plan else None,
                }
            )
            for event in agent.run(
                message,
                list(sess.history),
                last_reasoning=getattr(sess, "last_reasoning", ""),
                last_prompt_tokens=getattr(sess, "last_prompt_tokens", 0),
                temp_prompt=active_temp_prompt,
                data_context=data_context,
                recovery_context=recovery_context,
                vision_images=vision_images,
                compaction_state=getattr(sess, "compaction", None),
            ):
                if sess.cancel_requested_for(conversation_job_id):
                    log.info("[chat] cancelled by user")
                    runner.cancel_tracked(conversation_job_id)
                    sess.clear_cancel(conversation_job_id)
                    _publish({"type": "stopped"})
                    return

                etype = event.get("type")
                if etype == "done":
                    # The worker owns the terminal frame: consumers must see
                    # the persisted analysis outcome before they stop reading.
                    continue
                if etype == "compaction_state":
                    # Rolling-summary record from the agent's compaction —
                    # persist on the session so later turns reuse it without
                    # another summarizer call.  Internal bookkeeping; never
                    # forwarded to the browser.
                    sess.compaction = {
                        "summary": str(event.get("summary") or ""),
                        "covered": max(0, int(event.get("covered") or 0)),
                        "boundary": str(event.get("boundary") or ""),
                        "sharing_fingerprint": str(event.get("sharing_fingerprint") or ""),
                    }
                    continue
                if etype == "tool_start":
                    _start_step(event)
                    if event.get("tool") == "run_r_code":
                        r_attempted = True
                elif etype == "tool_audit":
                    sess.record_tool_audit(event)
                    _finish_step(
                        str(event.get("tool") or "unknown"),
                        bool(event.get("ok", True)),
                        str(event.get("error") or ""),
                        event.get("elapsed_seconds"),
                    )
                    for artifact in event.get("artifacts") or []:
                        _append_parent_artifact(artifact)
                    _collect_downloads(str(event.get("content") or ""))
                    if event.get("tool") == "run_r_code":
                        r_attempted = True
                        tool_payload = _parse_tool_payload_from_event(event)
                        if tool_payload:
                            try:
                                state = _research_result_store(sess).append_r_payload(
                                    turn_id=turn_id,
                                    question=message,
                                    template_context=result_plan_context,
                                    payload=tool_payload,
                                    conversation_job_id=conversation_job_id,
                                    dataset_id=fixed_research_dataset_id,
                                )
                                event = {
                                    **event,
                                    "analysis_turn_id": turn_id,
                                    "result_state": {
                                        "schema_version": state.get("schema_version"),
                                        "overview": state.get("overview"),
                                        "latest_turn_id": turn_id,
                                    },
                                }
                            except Exception:
                                log.exception("[research-state] append R payload failed")
                                _error_id, state_error = _public_failure("结果状态保存失败")
                                result_state_error = state_error
                                event = {
                                    **event,
                                    "analysis_turn_id": turn_id,
                                    "result_state_error": state_error,
                                }
                    # Recovery metadata is server-only; do not expose full SQL
                    # or future internal context fields through browser SSE.
                    event = {key: value for key, value in event.items() if key != "recovery"}
                elif etype == "tool_end":
                    _finish_step(str(event.get("tool") or "unknown"))
                elif etype == "artifact_created" and event.get("artifact"):
                    _append_parent_artifact(event["artifact"])
                elif etype == "error":
                    internal_error = str(event.get("message") or "Conversation failed")
                    actionable_error = _actionable_llm_error(internal_error)
                    error_id, stream_error = _public_failure(
                        actionable_error or "分析服务暂时异常，请稍后重试"
                    )
                    log.error("[chat] agent error id=%s detail=%s", error_id, internal_error)
                    event = {**event, "message": stream_error}
                    if actionable_error:
                        event["code"] = "invalid_api_key"

                # Provider/fallback safety net. BusinessAgent normally separates
                # <think> during streaming, but never let an embedded block leak
                # into the final answer if a compatibility path returns it whole.
                if etype == "text":
                    visible_text, embedded_reasoning = split_reasoning_tags(event.get("content", ""))
                    if embedded_reasoning:
                        collected_reasoning.append(embedded_reasoning)
                        _publish({"type": "reasoning", "content": embedded_reasoning})
                    event = {**event, "content": visible_text}

                if etype == "chart_html":
                    cid = uuid.uuid4().hex
                    chart_store.put(cid, event["html"])
                    if not hasattr(sess, "chart_ids"):
                        sess.chart_ids = []
                    sess.chart_ids.append(cid)
                    turn_chart_ids.append(cid)
                    log.info("[chat] chart generated")
                    _publish({"type": "chart_ref", "chart_id": cid})
                    _append_parent_artifact(
                        {
                            "type": "chart",
                            "name": f"图表 {len(turn_chart_ids)}",
                            "url": f"/api/chart/{cid}",
                            "chart_id": cid,
                        }
                    )
                elif etype == "chart_placeholder":
                    pass
                elif etype == "usage":
                    sess.record_usage(
                        event.get("prompt_tokens", 0),
                        event.get("completion_tokens", 0),
                    )
                    cfg = config_manager.get_config(sess.model_provider)
                    enriched = {
                        **event,
                        "max_output_tokens": cfg.max_output_tokens if cfg else None,
                        "session_total_input": sess.total_input_tokens,
                        "session_total_output": sess.total_output_tokens,
                    }
                    if not enriched.get("context_window"):
                        enriched["context_window"] = cfg.context_window if cfg else None
                    _publish(enriched)
                else:
                    _publish(event)

                if etype == "text":
                    collected.append(event.get("content", ""))
                elif etype == "reasoning":
                    collected_reasoning.append(event.get("content", ""))
                elif etype == "tool_history":
                    msgs = event.get("messages", [])
                    sess.add_tool_messages(msgs)
                    names = [
                        m.get("tool_calls", [{}])[0].get("function", {}).get("name", "")
                        for m in msgs
                        if m.get("role") == "assistant" and m.get("tool_calls")
                    ]
                    tool_calls_in_turn.extend(n for n in names if n)
                elif etype == "tool_start":
                    pass  # already logged by agent.py

            for tool, queue in list(pending_steps.items()):
                while queue:
                    _finish_step(tool, ok=not bool(stream_error), error=stream_error)
            completed_normally = True
            sess.add_user(message, vision_images)
            sess.add_assistant(
                "".join(collected),
                reasoning="".join(collected_reasoning),
                chart_ids=turn_chart_ids,
            )
            try:
                from api.saved_sessions import persist_local_autosave

                persist_local_autosave(sess)
            except Exception:
                log.exception("[session] local autosave failed sid=%s", sid)
            final_answer = "".join(collected)
            if final_answer.strip():
                try:
                    _research_result_store(sess).attach_narrative(
                        turn_id=turn_id,
                        text=final_answer,
                        title=result_plan_context.get("title") or "论文结果解释",
                    )
                except Exception:
                    log.exception("[research-state] attach narrative failed turn=%s", turn_id)
                    _error_id, result_state_error = _public_failure("结果解释保存失败")
            _collect_downloads(final_answer)
            if stream_error:
                runner.fail_tracked(conversation_job_id, stream_error)
            else:
                active_plan = getattr(agent, "_analysis_plan", None) or frozen_analysis_plan
                outcome = _research_result_store(sess).conversation_outcome(
                    turn_id,
                    plan_id=str(getattr(active_plan, "plan_id", "") or ""),
                    r_attempted=r_attempted,
                )
                outcome["step_count"] = step_count
                outcome["artifact_count"] += len(turn_chart_ids)
                if result_state_error:
                    outcome.update(completed=False, analysis_status="failed")
                _publish({"type": "analysis_outcome", "job_id": conversation_job_id, **outcome})
                if outcome["completed"]:
                    runner.succeed_tracked(conversation_job_id, outcome)
                else:
                    runner.fail_tracked(
                        conversation_job_id,
                        result_state_error or (
                            "本轮 R 分析未成功；已保留结果和错误信息，可继续分析。"
                            if outcome["analysis_status"] == "failed" else
                            "本轮对话已结束，但科研产物尚未齐全，可继续完成当前方案。"
                        ),
                        result=outcome,
                    )

            elapsed = time.monotonic() - _turn_start
            log.info(
                "[chat] turn done elapsed=%.2fs tools=%s charts=%d total_in=%d total_out=%d",
                elapsed,
                tool_calls_in_turn or "none",
                len(turn_chart_ids),
                sess.total_input_tokens,
                sess.total_output_tokens,
            )

        except Exception as exc:
            _user_cancelled = False
            try:
                _user_cancelled = bool(
                    sess.cancel_requested_for(conversation_job_id)
                    or (
                        conversation_job_id
                        and research_run_controller.is_cancelled(conversation_job_id)
                    )
                )
            except Exception:
                pass
            if _user_cancelled:
                # A user stop closes the LLM stream; the resulting transport
                # error is the cancellation, not a service failure. The
                # finally block below finalizes the job as canceled.
                log.info(
                    "[chat] stream error after user stop; ending cleanly job=%s",
                    conversation_job_id,
                )
            else:
                log.exception("[chat] unhandled agent error")
                error_id, error_message = _public_failure(
                    _actionable_llm_error(str(exc)) or "分析服务暂时异常，请稍后重试"
                )
                runner.fail_tracked(conversation_job_id, error_message)
                log.error("[chat] public error id=%s", error_id)
                error_event = {"type": "error", "message": error_message}
                if _actionable_llm_error(str(exc)):
                    error_event["code"] = "invalid_api_key"
                _publish(error_event)

        finally:
            # The turn's terminal frame is published unconditionally so every
            # consumer — including one that re-attached mid-turn — sees a
            # well-formed end, whether the turn succeeded, failed, or was
            # stopped by the user.
            _publish({"type": "done"})
            try:
                conversation_scope.__exit__(None, None, None)
                current = runner.get_status(conversation_job_id)
                if current and current.get("status") not in {"succeeded", "failed", "canceled"}:
                    if sess.cancel_requested_for(conversation_job_id):
                        runner.cancel_tracked(conversation_job_id)
                        sess.clear_cancel(conversation_job_id)
                    elif not completed_normally:
                        runner.fail_tracked(
                            conversation_job_id,
                            "Conversation stream ended before completion.",
                        )
            finally:
                _finalize_worker()

    def generate():
        """SSE relay for the original POST /chat consumer.

        Never touches the analysis lifecycle: closing this generator (client
        disconnect) only stops relaying; the worker keeps running and the
        conversation run buffer stays available for re-attach.
        """
        try:
            yield from conversation_run.stream(after=0)
        finally:
            log.info("[chat] sse relay closed job=%s", conversation_job_id)

    worker = threading.Thread(
        target=_produce,
        name=f"chat-turn-{conversation_job_id}",
        daemon=True,
    )
    worker.start()
    response = Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
    return response
