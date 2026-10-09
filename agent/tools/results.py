# -*- coding: utf-8 -*-
"""Structured tool result envelopes.

Tool messages are still sent to the LLM as strings for provider compatibility,
but the string now carries a compact JSON envelope with a readable text body.
This gives logs/UI/tests a stable contract without forcing every tool
implementation to return a new object type.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.paths import data_path


log = logging.getLogger(__name__)

TOOL_RESULT_CHAR_BUDGET = 12_000
TOOL_RESULT_PREVIEW_CHARS = 2_800
TOOL_RESULT_PREVIEW_LINES = 24
_GLOBAL_RESULT_ROOT = data_path("outputs", "tool_results")


def classify_tool_error(raw: Any, tool: str = "") -> str:
    """Best-effort error taxonomy for tool outputs."""
    text = str(raw or "").strip()
    lower = text.lower()
    if not text:
        return ""
    if text.startswith("[ARG ERROR]"):
        return "argument_error"
    if "sql validation failed" in lower:
        return "sql_validation_error"
    if text.startswith("SQL Error:"):
        if "no such column" in lower or "column" in lower and "not found" in lower:
            return "field_not_found"
        if "no such table" in lower or "table" in lower and "not found" in lower:
            return "table_not_found"
        if "syntax" in lower or "parser" in lower:
            return "sql_syntax_error"
        return "sql_execution_error"
    if "no data source" in lower or "连接已断开" in text:
        return "datasource_disconnected"
    if "permission" in lower or "权限" in text:
        return "permission_error"
    if tool == "get_schema":
        if text.startswith("ERROR:") or text.startswith("工具执行错误"):
            return "tool_error"
        return ""
    if (
        lower.startswith("query returned no rows")
        or lower.startswith("empty result")
        or lower.startswith("no rows returned")
    ):
        return "empty_result"
    if text.startswith("[MCP ERROR]"):
        return "mcp_error"
    if text.startswith("ERROR:") or text.startswith("工具执行错误"):
        return "tool_error"
    return ""


@dataclass
class ToolResultEnvelope:
    tool: str
    ok: bool = True
    error: str = ""
    summary: str = ""
    data: Any = ""
    sources: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    debug: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "tool_result",
            "tool": self.tool,
            "ok": self.ok,
            "error": self.error,
            "summary": self.summary,
            "data": self.data,
            "sources": self.sources,
            "artifacts": self.artifacts,
            "debug": self.debug,
        }

    def to_model_text(self) -> str:
        """Provider-safe tool content.

        The first line is intentionally human-readable so older prompt habits
        still work; the JSON block gives future code a stable structure.
        """
        data = self.to_dict()
        readable = self.summary or str(self.data)[:240]
        return f"[TOOL_RESULT] {self.tool} {'OK' if self.ok else 'ERROR'}: {readable}\n" + json.dumps(
            data, ensure_ascii=False, default=str
        )


def _json_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str, indent=2)


def _preview_text(text: str) -> str:
    lines = text.splitlines()
    preview = "\n".join(lines[:TOOL_RESULT_PREVIEW_LINES])
    if len(preview) > TOOL_RESULT_PREVIEW_CHARS:
        preview = preview[:TOOL_RESULT_PREVIEW_CHARS]
    omitted_lines = max(0, len(lines) - TOOL_RESULT_PREVIEW_LINES)
    omitted_chars = max(0, len(text) - len(preview))
    suffix = f"\n…[full result persisted; omitted {omitted_lines} lines / {omitted_chars:,} chars]"
    return preview + suffix


def _result_root(runtime: Any = None) -> Path:
    if runtime is not None and getattr(runtime, "cache_dir", None):
        return Path(runtime.cache_dir) / "tool_results"
    return _GLOBAL_RESULT_ROOT


def persist_large_tool_result(
    session_id: str,
    tool: str,
    raw: Any,
    *,
    runtime: Any = None,
    threshold: int = TOOL_RESULT_CHAR_BUDGET,
    deduplicate: bool = False,
) -> tuple[Any, dict | None, dict]:
    """Persist oversized tool data and return a bounded model preview.

    The artifact file is self-describing and content-address-verified. The
    opaque artifact id is used for lookup; local filesystem paths never enter
    the model-visible envelope.
    """
    text = _json_text(raw)
    encoded = text.encode("utf-8")
    if len(text) <= max(1, int(threshold)):
        return raw, None, {"persisted": False, "chars": len(text)}

    digest = hashlib.sha256(encoded).hexdigest()
    artifact_id = f"tr_{digest[:32]}" if deduplicate else f"tr_{uuid.uuid4().hex}"
    root = _result_root(runtime)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{artifact_id}.json"
    temp = root / f".{artifact_id}.{os.getpid()}.tmp"
    record = {
        "version": 1,
        "artifact_id": artifact_id,
        "session_id": session_id,
        "workspace_id": str(getattr(runtime, "workspace_id", "") or ""),
        "tool": tool,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "size_bytes": len(encoded),
        "sha256": digest,
        "content_type": "text/plain; charset=utf-8",
        "data": text,
    }
    if not target.exists():
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
    artifact = {
        "type": "tool_result",
        "artifact_id": artifact_id,
        "name": f"{tool} 完整结果",
        "uri": f"artifact://tool-result/{artifact_id}",
        "url": f"/api/session/{session_id}/tool-results/{artifact_id}",
        "size_bytes": len(encoded),
        "sha256": digest,
        "workspace_id": str(getattr(runtime, "workspace_id", "") or ""),
    }
    debug = {
        "persisted": True,
        "artifact_id": artifact_id,
        "original_chars": len(text),
        "preview_chars": min(len(text), TOOL_RESULT_PREVIEW_CHARS),
    }
    return _preview_text(text), artifact, debug


def load_tool_result_artifact(
    artifact_id: str,
    *,
    runtime: Any = None,
    workspace_root: Path | None = None,
) -> dict | None:
    """Load and verify an artifact from the active workspace or global store."""
    if not artifact_id.startswith("tr_") or not artifact_id[3:].isalnum():
        return None
    roots = []
    if runtime is not None and getattr(runtime, "cache_dir", None):
        roots.append(Path(runtime.cache_dir) / "tool_results")
    elif workspace_root is not None:
        roots.append(Path(workspace_root) / ".baa_cache" / "tool_results")
    roots.append(_GLOBAL_RESULT_ROOT)
    for root in roots:
        path = root / f"{artifact_id}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            text = str(record.get("data", ""))
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if record.get("artifact_id") == artifact_id and digest == record.get("sha256"):
                return record
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return None


def extract_tool_result_references(text: str) -> list[str]:
    """Extract stable artifact URIs from a model-facing tool envelope."""
    refs = re.findall(r"artifact://tool-result/tr_[a-fA-F0-9]+", text or "")
    return list(dict.fromkeys(refs))


_SCIENTIFIC_HISTORY_TOOLS = frozenset({"run_r_code", "inspect_dataset"})
_HISTORY_KEY_ORDER = (
    "ok",
    "job_id",
    "error",
    "stderr_tail",
    "stderr",
    "artifact_previews",
    "result",
    "execution",
    "status",
    "summary",
    "dataset",
    "variables",
    "columns",
    "variable_index",
)
_HISTORY_PRIVATE_KEYS = frozenset(
    {
        "code",
        "stdout",
        "execution_snapshot",
        "reproducibility",
        "analysis_plan",
        "dataset_manifest",
        "path",
        "stored_path",
        "file_path",
        "source_path",
        "output_path",
        "artifacts_dir",
        "workdir",
        "cwd",
    }
)
_WITHHELD_HISTORY_DATA = "旧科研结果预览不是完整 JSON，未重放；完整产物保留在本机。"
_WITHHELD_HISTORY_TABLE = "[完整表头和首行超出历史预算，表格预览未发送]"


def _history_csv_preview(text: str, row_limit: int) -> str:
    """Keep complete columns and rows so later sharing policy can classify them."""
    if row_limit == 0:
        return _WITHHELD_HISTORY_TABLE
    try:
        reader = csv.reader(io.StringIO(text), strict=True)
        rows = []
        for row in reader:
            rows.append(row)
            if len(rows) >= row_limit + 1:
                break
    except (csv.Error, TypeError):
        return "[CSV 预览无法可靠解析，未重放]"
    if not rows or any(len(row) != len(rows[0]) for row in rows[1:]):
        return "[CSV 预览缺少完整表头或行，未重放]"
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows(rows)
    return output.getvalue().rstrip("\n")


def _bound_history_value(
    value: Any,
    *,
    field_limit: int,
    row_limit: int,
    item_limit: int,
    key_limit: int,
    parent: str = "",
    depth: int = 0,
) -> Any:
    """A shrinking view, never a source of reconstructed or reloaded raw data."""
    if depth > 12:
        return "[nested history details omitted]"
    if isinstance(value, dict):
        keys = [key for key in _HISTORY_KEY_ORDER if key in value]
        keys += [key for key in value if key not in keys]
        public = [key for key in keys if str(key).lower() not in _HISTORY_PRIVATE_KEYS]
        result = {}
        for key in public[:key_limit]:
            child = value[key]
            if key == "stderr":
                if isinstance(child, str) and child.strip():
                    result.setdefault("stderr_tail", child[-field_limit:])
                continue
            result[str(key)] = _bound_history_value(
                child,
                field_limit=field_limit,
                row_limit=row_limit,
                item_limit=item_limit,
                key_limit=key_limit,
                parent=str(key),
                depth=depth + 1,
            )
        return result
    if isinstance(value, (list, tuple)):
        return [
            _bound_history_value(
                child,
                field_limit=field_limit,
                row_limit=row_limit,
                item_limit=item_limit,
                key_limit=key_limit,
                parent=parent,
                depth=depth + 1,
            )
            for child in value[:item_limit]
        ]
    if isinstance(value, str):
        if parent == "preview_csv":
            # Never shrink a field/column: doing so can erase a sensitive
            # header, corrupt a P value, or turn raw data into a summary.
            return _history_csv_preview(value, row_limit)
        if parent in {"name", "tool_call_id", "artifact_id", "job_id", "dataset_id", "plan_id"}:
            return value
        if parent in {"error", "stderr_tail", "message", "detail"}:
            return value[-field_limit:]
        return value[:field_limit]
    return value


def _scientific_history_envelope(text: str, cap: int, refs: list[str], suffix: str) -> str | None:
    body = text.split("\n", 1)[1] if text.startswith("[TOOL_RESULT]") and "\n" in text else text
    try:
        envelope = json.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(envelope, dict) or envelope.get("tool") not in _SCIENTIFIC_HISTORY_TOOLS:
        return None
    # Keep this operation idempotent even when references use the same small
    # overhead permitted by the old string truncator.
    allowed = cap + len(suffix)
    if envelope.get("truncated") is True and envelope.get("history_cap") == cap and len(text) <= allowed:
        return text
    data = envelope.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (ValueError, TypeError):
            # Old broken JSON must not be restored from its prefix or from
            # a local artifact. The current guard can safely re-filter this.
            data = {"truncated": True, "message": _WITHHELD_HISTORY_DATA}
    prefix = "[TOOL_RESULT]\n" if text.startswith("[TOOL_RESULT]") else ""

    def render(view: Any, error_limit: int) -> str:
        result = {
            "tool": envelope["tool"],
            "ok": envelope.get("ok", True),
            "error": str(envelope.get("error") or "")[-max(128, error_limit) :],
            "data": view,
            "truncated": True,
            "history_cap": cap,
            "summary": "科研历史预览已裁剪；完整产物保留在本机。",
        }
        if "type" in envelope:
            result["type"] = envelope["type"]
        for key in ("id", "tool_call_id"):
            if key in envelope:
                result[key] = envelope[key]
        if refs:
            result["recoverable_artifacts"] = refs
        return prefix + json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    # Ordinary summaries retain more rows if they fit. Failure tails shrink
    # from the end, where R normally reports its decisive error. CSV headers
    # and cells remain exact at every stage.
    stages = (
        (256, 20, 20, 20),
        (128, 10, 8, 12),
        (96, 5, 4, 8),
        (64, 3, 2, 6),
        (48, 2, 1, 4),
        (32, 1, 1, 4),
        (16, 1, 1, 3),
        (128, 0, 4, 8),
        (64, 0, 2, 6),
        (32, 0, 1, 4),
        (16, 0, 1, 3),
    )
    for field_limit, row_limit, item_limit, key_limit in stages:
        view = _bound_history_value(
            data, field_limit=field_limit, row_limit=row_limit, item_limit=item_limit, key_limit=key_limit
        )
        candidate = render(view, field_limit)
        if len(candidate) <= cap:
            return candidate
    # Reference overhead is explicitly allowed by the existing caller contract.
    for field_limit, row_limit, item_limit, key_limit in stages:
        view = _bound_history_value(
            data, field_limit=field_limit, row_limit=row_limit, item_limit=item_limit, key_limit=key_limit
        )
        candidate = render(view, field_limit)
        if len(candidate) <= allowed:
            return candidate
    # Extremely small caps cannot carry a table and its schema. Preserve the
    # status without passing a partial scientific row off as a valid preview.
    candidate = render({"truncated": True, "message": "历史预算不足；详细结果未重放。"}, 32)
    if len(candidate) <= allowed:
        return candidate
    minimal = {"tool": envelope["tool"], "ok": envelope.get("ok", True), "truncated": True}
    if envelope.get("error"):
        minimal["error"] = str(envelope["error"])[-16:]
    if refs:
        minimal["recoverable_artifacts"] = refs
    # Production callers use at least 500 chars; this last view serves tiny
    # defensive caps while keeping JSON parseable and artifact ids recoverable.
    return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))


def truncate_tool_result_preserving_refs(text: str, cap: int) -> str:
    """Bound a tool message without discarding its recoverable artifact ids."""
    if len(text) <= cap:
        return text
    refs = extract_tool_result_references(text)
    suffix = f"\n…[result truncated for history, {len(text):,} chars total]"
    if refs:
        suffix += "\n[RECOVERABLE_ARTIFACTS] " + ", ".join(refs)
    scientific = _scientific_history_envelope(text, cap, refs, suffix)
    if scientific is not None:
        return scientific
    return text[:cap] + suffix


def _summarize_content(content: Any, max_chars: int = 220) -> str:
    def first_table_preview(item: Any) -> str:
        if isinstance(item, dict):
            preview = item.get("preview_csv")
            if isinstance(preview, str) and preview.strip():
                try:
                    rows = list(csv.reader(io.StringIO(preview)))[:2]
                except csv.Error:
                    rows = []
                if len(rows) >= 2:
                    compact_rows = [[str(field)[:28] for field in row[:4]] for row in rows]
                    output = io.StringIO()
                    csv.writer(output, lineterminator=" | ").writerows(compact_rows)
                    return "table preview: " + output.getvalue().rstrip(" | ")
            for child in item.values():
                found = first_table_preview(child)
                if found:
                    return found
        elif isinstance(item, (list, tuple)):
            for child in item:
                found = first_table_preview(child)
                if found:
                    return found
        return ""

    preview_summary = first_table_preview(content)
    if preview_summary:
        return preview_summary[:max_chars]
    if isinstance(content, str):
        text = content.strip()
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def make_tool_result(
    tool: str,
    raw: Any,
    *,
    ok: bool | None = None,
    error: str = "",
    summary: str = "",
    sources: list[dict] | None = None,
    artifacts: list[dict] | None = None,
    debug: dict | None = None,
    session_id: str = "",
    runtime: Any = None,
    result_char_budget: int = TOOL_RESULT_CHAR_BUDGET,
) -> ToolResultEnvelope:
    error = error or classify_tool_error(raw, tool=tool)
    if ok is None:
        ok = not bool(error)
    model_data = raw
    result_artifact = None
    budget_debug = {}
    if session_id and ok:
        try:
            model_data, result_artifact, budget_debug = persist_large_tool_result(
                session_id,
                tool,
                raw,
                runtime=runtime,
                threshold=result_char_budget,
                deduplicate=tool == "get_schema",
            )
        except Exception as exc:
            log.warning("[tool-result] persist failed tool=%s: %s", tool, exc)
            budget_debug = {"persisted": False, "error": str(exc)}
    result_artifacts = list(artifacts or [])
    if result_artifact is not None:
        result_artifacts.append(result_artifact)
    result_debug = dict(debug or {})
    if budget_debug:
        result_debug["result_budget"] = budget_debug
    return ToolResultEnvelope(
        tool=tool,
        ok=ok,
        error=error,
        summary=summary or _summarize_content(raw),
        data=model_data,
        sources=list(sources or []),
        artifacts=result_artifacts,
        debug=result_debug,
    )
