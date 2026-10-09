"""Blueprint: save / load / delete persistent sessions."""

import json
import os
import logging
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request, jsonify
from .local_access import session_for_request

from .state import session_manager, config_manager
from agent.reasoning import split_reasoning_tags
from agent.vision import visible_message_text
from agent.analysis.model_sharing import ModelSharingPolicy
from infrastructure.paths import data_path

log = logging.getLogger(__name__)

bp = Blueprint("saved_sessions", __name__)

SAVE_DIR = data_path("outputs", "Session")

SAVE_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload) -> None:
    """Temp-file + atomic replace: a hard kill mid-write must never leave a
    truncated autosave that fails to restore (the old direct write_text was
    the only non-atomic state write in the project)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _visible_msg_count(history: list) -> int:
    """Count only user + assistant-with-text messages (exclude tool calls/results).

    sess.history now contains tool call chains (role=assistant with tool_calls,
    role=tool), but the user-facing "条数" should reflect actual conversation
    exchanges, not internal tool round-trips.
    """
    return sum(
        1
        for m in history
        if m.get("role") in ("user", "assistant")
        and not m.get("hidden")
        and m.get("kind") != "artifact_review"
        and visible_message_text(m.get("content"))
        and not m.get("tool_calls")  # not an intermediate tool-call entry
    )


def _visible_history(history: list, *, limit: int = 40) -> list[dict[str, str]]:
    """Project internal history down to browser-visible conversation text."""
    visible: list[dict[str, str]] = []
    for message in history or []:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        if message.get("tool_calls"):
            continue
        if message.get("hidden") or message.get("kind") == "artifact_review":
            continue
        content = visible_message_text(message.get("content"))
        if not content:
            continue
        visible.append({"role": str(message["role"]), "content": content})
    return visible[-max(1, min(100, int(limit))) :]


def _collect_chart_ids(history: list) -> list[str]:
    """Gather every chart_id referenced across the conversation history.

    Chart HTML itself is already written through to disk by _ChartStore
    (outputs/charts/<cid>.html), so it survives restarts on its own — we only
    need the id list (e.g. to keep sess.chart_ids in sync for export).
    """
    ids: list[str] = []
    for msg in history:
        for cid in msg.get("chart_ids") or []:
            if cid not in ids:
                ids.append(cid)
    return ids


def _normalize_reasoning_history(history: list) -> list:
    """Migrate legacy assistant messages that stored ``<think>`` in content."""
    for msg in history:
        if msg.get("role") != "assistant" or not msg.get("content"):
            continue
        visible, embedded = split_reasoning_tags(msg["content"])
        if embedded:
            msg["content"] = visible
            prior = (msg.get("reasoning") or "").strip()
            msg["reasoning"] = "\n\n".join(x for x in (prior, embedded) if x)
    return history


# ── helpers ────────────────────────────────────────────────────────────────


def _safe_stem(name: str) -> str:
    """Turn an arbitrary name into a filesystem-safe stem (keep CJK)."""
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name or "session"


def _recovery_state(sess) -> dict:
    return {
        "recent_sql": list(getattr(sess, "recent_sql", []))[-5:],
        "recent_artifacts": list(getattr(sess, "recent_artifacts", []))[-20:],
        "turn_activations": list(getattr(sess, "turn_activations", []))[-100:],
    }


def _session_payload(sess, *, name: str, autosave: bool = False) -> dict:
    from data.research_workspace import default_research_workspace

    workspace = default_research_workspace()
    workspace_record = workspace.current_record(sess.session_id) or {}
    payload = {
        "name": name,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": sess.session_id,
        "model_provider": sess.model_provider,
        "model_sharing_policy": ModelSharingPolicy.from_dict(getattr(sess, "model_sharing_policy", None)).to_dict(),
        "history": sess.history,
        "total_input_tokens": sess.total_input_tokens,
        "total_output_tokens": sess.total_output_tokens,
        "compaction": getattr(sess, "compaction", None),
        "recovery_state": _recovery_state(sess),
        "research_workspace": {
            "dataset_id": str(workspace_record.get("dataset_id") or ""),
            "workspace_generation": str(workspace_record.get("workspace_generation") or ""),
        },
    }
    if autosave:
        payload["autosave"] = True
    return payload


def _restore_research_workspace_binding(sess, data: dict) -> None:
    """Restore only when manifest, result state, and saved binding agree."""
    from data.research_workspace import default_research_workspace

    workspace = default_research_workspace()
    current = workspace.current_record(sess.session_id) or {}
    saved = data.get("research_workspace")
    saved = saved if isinstance(saved, dict) else {}
    result_state, result_exists, _saved_at = workspace.results(sess.session_id).load()
    result_generation = str(result_state.get("workspace_generation") or "")
    current_generation = str(current.get("workspace_generation") or "")
    bindings_match = bool(
        current.get("dataset_id")
        and current_generation
        and result_exists
        and saved.get("dataset_id") == current.get("dataset_id")
        and str(saved.get("workspace_generation") or "") == current_generation
        and result_generation == current_generation
        and str((result_state.get("dataset") or {}).get("dataset_id") or "")
        == str(current.get("dataset_id") or "")
    )
    if bindings_match:
        sess.research_dataset_id = str(current.get("dataset_id") or "")
        return

    # Keep only user-visible conversation text. Tool chains, chart ids, and
    # artifact references may belong to a replaced dataset generation.
    sess.history = _visible_history(sess.history, limit=100)
    sess.research_dataset_id = ""
    sess.chart_ids = []
    sess.recent_sql = []
    sess.recent_artifacts = []
    sess.turn_activations = []


def _available_provider(value: object) -> str:
    provider = str(value or "")
    return provider if config_manager.get_config(provider) else config_manager.get_default_provider()


def persist_local_autosave(sess) -> None:
    """Persist the current local conversation or remove its empty autosave."""
    path = SAVE_DIR / f"autosave_{sess.session_id}.json"
    policy = ModelSharingPolicy.from_dict(getattr(sess, "model_sharing_policy", None))
    if not sess.history and policy == ModelSharingPolicy():
        path.unlink(missing_ok=True)
        return
    payload = _session_payload(
        sess,
        name=f"自动保存_{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        autosave=True,
    )
    _atomic_write_json(path, payload)


def restore_local_autosave(sid: str):
    """Rehydrate the current local workspace after an application restart."""
    sess = session_for_request(sid)
    path = SAVE_DIR / f"autosave_{sess.session_id}.json"
    if not path.is_file():
        return sess, False
    if not sess.begin_session_mutation():
        return sess, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        history = data.get("history")
        if not isinstance(history, list):
            raise ValueError("autosave history must be a list")
        policy = ModelSharingPolicy.from_dict(data.get("model_sharing_policy"))
        total_input = max(0, int(data.get("total_input_tokens") or 0))
        total_output = max(0, int(data.get("total_output_tokens") or 0))
        sess.history = _normalize_reasoning_history(history)
        sess.model_provider = _available_provider(data.get("model_provider"))
        sess.model_sharing_policy = policy.to_dict()
        sess.total_input_tokens = total_input
        sess.total_output_tokens = total_output
        sess.last_prompt_tokens = 0
        sess.last_reasoning = ""
        sess.chart_ids = _collect_chart_ids(sess.history)
        # Old summaries were built under a potentially different sharing policy.
        # Keep local history, but never reuse that provider-facing cache.
        sess.compaction = None
        recovery = data.get("recovery_state") if isinstance(data.get("recovery_state"), dict) else {}
        sess.recent_sql = [str(item)[:4000] for item in recovery.get("recent_sql", [])][-5:]
        sess.recent_artifacts = []
        for artifact in recovery.get("recent_artifacts", [])[-20:]:
            if not isinstance(artifact, dict):
                continue
            item = dict(artifact)
            if item.get("artifact_id"):
                item["url"] = f"/api/session/{sess.session_id}/tool-results/{item['artifact_id']}"
            sess.recent_artifacts.append(item)
        sess.turn_activations = [
            dict(item) for item in recovery.get("turn_activations", [])[-100:] if isinstance(item, dict)
        ]
        _restore_research_workspace_binding(sess, data)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        log.exception("[session] unable to restore local autosave sid=%s", sess.session_id)
        return sess, False
    finally:
        sess.end_session_mutation()
    log.info("[session] restored local autosave sid=%s", sess.session_id)
    return sess, True


def _list_files() -> list[dict]:
    # Include both manual saves and autosave files; autosaves are flagged is_autosave=True
    files = sorted(SAVE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
            is_autosave = bool(meta.get("autosave")) or f.stem.startswith("autosave_")
            result.append(
                {
                    "filename": f.name,
                    "name": meta.get("name", f.stem),
                    "saved_at": meta.get("saved_at", ""),
                    "msg_count": _visible_msg_count(meta.get("history", [])),
                    "is_autosave": is_autosave,
                    "session_id": meta.get("session_id", ""),
                }
            )
        except Exception:
            continue
    return result


def _session_file(filename: str) -> Path:
    """Resolve a saved-session filename inside SAVE_DIR."""
    return SAVE_DIR / Path(filename).name


# ── API endpoints ──────────────────────────────────────────────────────────


@bp.get("/api/saved-sessions")
def list_sessions():
    return jsonify(_list_files())


@bp.post("/api/session/<sid>/autosave")
def autosave_session(sid: str):
    """Silent auto-save — overwrites a single per-session autosave file.

    Unlike /save, this never returns an error for empty history (just skips)
    and uses a fixed filename so old autosaves are replaced rather than
    accumulated.
    """
    sess = session_for_request(sid)
    if not sess or not sess.history:
        return jsonify({"ok": False, "reason": "empty"})

    body = request.json or {}
    req_name = body.get("name", "").strip()
    target_file = body.get("target_file", "").strip()  # filename to overwrite when loaded from a save

    ts_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auto_name = req_name if req_name else f"自动保存_{ts_label}"

    payload = _session_payload(sess, name=auto_name, autosave=True)

    # If the user loaded from an existing file, overwrite that file directly
    # so no new entry appears in the list.
    # Otherwise fall back to the per-session autosave file.
    if target_file:
        safe = Path(target_file).name  # strip any path traversal
        path = SAVE_DIR / safe
        if not path.exists():  # guard: don't create arbitrary files
            path = SAVE_DIR / f"autosave_{sid}.json"
    else:
        path = SAVE_DIR / f"autosave_{sid}.json"

    _atomic_write_json(path, payload)
    log.debug(
        "[session] autosave  sid=%s  file=%s  msg_count=%d", sid, path.name, _visible_msg_count(sess.history)
    )
    return jsonify({"ok": True, "saved_at": payload["saved_at"], "filename": path.name})


@bp.get("/api/session/<sid>/autosave")
def get_autosave(sid: str):
    """Check whether an autosave exists for this session."""
    session_for_request(sid)
    path = SAVE_DIR / f"autosave_{sid}.json"
    if not path.exists():
        return jsonify({"exists": False})
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        return jsonify(
            {
                "exists": True,
                "saved_at": meta.get("saved_at", ""),
                "msg_count": _visible_msg_count(meta.get("history", [])),
                "filename": path.name,
            }
        )
    except Exception:
        return jsonify({"exists": False})


@bp.post("/api/session/<sid>/save")
def save_session(sid: str):
    sess = session_for_request(sid)
    if not sess:
        log.warning("[session] save  sid=%s  error=session not found", sid)
        return jsonify({"error": "会话不存在"}), 404
    if not sess.history:
        return jsonify({"error": "对话为空，无需保存"}), 400

    name = (request.json or {}).get("name", "").strip()
    if not name:
        name = datetime.now().strftime("对话_%Y%m%d_%H%M%S")

    payload = _session_payload(sess, name=name)

    stem = _safe_stem(name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SAVE_DIR / f"{stem}_{ts}.json"
    _atomic_write_json(path, payload)

    log.info(
        "[session] saved  sid=%s  name=%r  file=%s  msg_count=%d",
        sid,
        name,
        path.name,
        _visible_msg_count(sess.history),
    )
    return jsonify({"ok": True, "filename": path.name, "name": name})


@bp.post("/api/session/<sid>/load")
def load_session(sid: str):
    filename = (request.json or {}).get("filename", "").strip()
    if not filename:
        return jsonify({"error": "未指定文件名"}), 400

    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"error": f"读取失败: {exc}"}), 500

    sess = session_for_request(sid)
    # Loading wholesale replaces history/result bindings; hold the same
    # mutation lease as /clear so a running analysis can't interleave writes
    # (or end up re-appending to the replaced history).
    if not sess.begin_session_mutation():
        return jsonify({"error": "当前分析运行中，请先停止或等待完成后再载入对话。"}), 409
    try:
        _apply_loaded_session(sess, data, sid)
    finally:
        sess.end_session_mutation()

    log.info(
        "[session] loaded  sid=%s  file=%s  name=%r  msg_count=%d  in_tokens=%d  out_tokens=%d",
        sid,
        filename,
        data.get("name", ""),
        _visible_msg_count(sess.history),
        sess.total_input_tokens,
        sess.total_output_tokens,
    )

    return jsonify(
        {
            "ok": True,
            "name": data.get("name", ""),
            "history": sess.history,
            "model_provider": sess.model_provider,
            "model_sharing_policy": sess.model_sharing_policy,
            "total_input": sess.total_input_tokens,
            "total_output": sess.total_output_tokens,
        }
    )


def _apply_loaded_session(sess, data: dict, sid: str) -> None:
    """Replace session conversation state from a saved payload."""
    sess.history = _normalize_reasoning_history(data.get("history", []))
    sess.model_provider = _available_provider(data.get("model_provider"))
    sess.model_sharing_policy = ModelSharingPolicy.from_dict(data.get("model_sharing_policy")).to_dict()
    sess.total_input_tokens = data.get("total_input_tokens", 0)
    sess.total_output_tokens = data.get("total_output_tokens", 0)
    sess.last_prompt_tokens = 0
    sess.last_reasoning = ""
    # Restored summaries may contain data shared under an older policy.
    sess.compaction = None

    sess.chart_ids = _collect_chart_ids(sess.history)
    recovery = data.get("recovery_state") or {}
    sess.recent_sql = [str(item)[:4000] for item in recovery.get("recent_sql", [])][-5:]
    sess.recent_artifacts = []
    for artifact in recovery.get("recent_artifacts", [])[-20:]:
        if not isinstance(artifact, dict):
            continue
        item = dict(artifact)
        if item.get("artifact_id"):
            item["url"] = f"/api/session/{sid}/tool-results/{item['artifact_id']}"
        sess.recent_artifacts.append(item)
    sess.turn_activations = [
        dict(item) for item in recovery.get("turn_activations", [])[-100:] if isinstance(item, dict)
    ]
    _restore_research_workspace_binding(sess, data)

    # Old BI saves may still contain data_source/workspace metadata. The local
    # research edition deliberately restores only the conversation and its
    # research recovery state.
    sess.data_source = None
    sess.workspace_id = ""


@bp.post("/api/saved-sessions/<filename>/rename")
@bp.patch("/api/saved-sessions/<filename>")
def rename_session(filename: str):
    """Rename a saved conversation by updating its display name in metadata."""
    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404

    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "名称不能为空"}), 400

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["name"] = name
        data["renamed_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        return jsonify({"error": f"重命名失败: {exc}"}), 500

    log.info("[session] renamed  file=%s  name=%r", path.name, name)
    return jsonify({"ok": True, "filename": path.name, "name": name})


@bp.delete("/api/saved-sessions/<filename>")
def delete_session(filename: str):
    path = _session_file(filename)
    if not path.exists() or path.suffix != ".json":
        return jsonify({"error": "文件不存在"}), 404
    path.unlink()
    # Chart HTML in outputs/charts/ is intentionally NOT deleted — it is shared
    # storage and may still be referenced by other saved conversations.
    return jsonify({"ok": True})
