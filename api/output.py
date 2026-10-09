"""Blueprint for local exported files and persisted tool results."""

import logging
import os
import re
from pathlib import Path

from infrastructure.paths import data_path

from flask import Blueprint, send_file, abort, request, jsonify, Response
from .local_access import session_for_request

log = logging.getLogger(__name__)

bp = Blueprint("output", __name__)

_EXPORT_DIR = str(data_path("outputs", "exports"))


def _resolve_filepath(
    filename: str,
    *,
    sid: str = "",
    allow_default_exports: bool = True,
) -> str | None:
    """按优先级查找文件，返回存在的绝对路径或 None。

    查找顺序：
      1. 当前已授权 session 的 workspace artifacts_dir
      2. 开发模式下的默认 outputs/exports/
    """
    # 安全校验：禁止路径穿越
    if ".." in filename or re.search(r"[\\/\x00]", filename):
        return None

    # 1. Prefer the immutable Workspace identity embedded in new links. This
    # remains valid after the session switches to another Workspace.
    workspace_id = (request.args.get("workspace_id") or "").strip()
    if workspace_id:
        try:
            from data.workspace import workspace_manager

            if not sid or workspace_id != workspace_manager.workspace_id_for_session(sid):
                return None
            root = workspace_manager.root_for_workspace(workspace_id)
            if root is not None:
                artifacts_dir = (root / "artifacts").resolve()
                resolved = (artifacts_dir / filename).resolve()
                resolved.relative_to(artifacts_dir)
                if resolved.is_file():
                    return str(resolved)
        except (OSError, ValueError):
            pass

    # 2. Legacy links: current session workspace artifacts_dir.
    if sid:
        try:
            from data.workspace import workspace_manager

            runtime = workspace_manager.get(sid)
            if runtime is not None:
                artifacts_path = os.path.join(str(runtime.artifacts_dir), filename)
                # 二次校验：resolve 后必须在 artifacts_dir 内（防符号链接逃逸）
                try:
                    resolved = Path(artifacts_path).resolve(strict=True)
                    resolved.relative_to(runtime.artifacts_dir.resolve())
                    if resolved.is_file():
                        return str(resolved)
                except (OSError, ValueError):
                    log.debug("[output] session artifact path rejected")
        except OSError as e:
            log.warning("[output] workspace lookup failed: %s", e)

    # 3. Compatibility path for exports created before workspace storage.
    if not allow_default_exports:
        return None
    default_path = os.path.join(_EXPORT_DIR, filename)
    if os.path.isfile(default_path):
        return default_path

    return None


@bp.get("/api/export/<path:filename>")
def download_export(filename: str):
    """Serve an exported file.

    Local callers may provide ``sid`` to constrain the export to one session.
    """
    sid = (request.args.get("sid") or "").strip()
    if sid:
        session_for_request(sid)
    filepath = _resolve_filepath(
        filename,
        sid=sid,
        allow_default_exports=True,
    )
    if filepath is None:
        abort(404)

    return send_file(filepath, as_attachment=True, download_name=filename)


@bp.get("/api/session/<sid>/tool-results/<artifact_id>")
def read_tool_result(sid: str, artifact_id: str):
    """Read a B6 persisted result after validating it belongs to the session."""
    from agent.tools.results import load_tool_result_artifact
    from data.workspace import workspace_manager

    sess = session_for_request(sid)
    allowed = any(item.get("artifact_id") == artifact_id for item in getattr(sess, "recent_artifacts", []))
    if not allowed:
        abort(404)
    artifact = next(
        (item for item in getattr(sess, "recent_artifacts", []) if item.get("artifact_id") == artifact_id),
        {},
    )
    workspace_id = str(artifact.get("workspace_id") or "")
    runtime = workspace_manager.get_by_workspace(workspace_id) if workspace_id else workspace_manager.get(sid)
    workspace_root = workspace_manager.root_for_workspace(workspace_id) if workspace_id else None
    record = load_tool_result_artifact(
        artifact_id,
        runtime=runtime,
        workspace_root=workspace_root,
    )
    if record is None:
        abort(404)
    if request.args.get("format") == "json":
        return jsonify(record)
    return Response(
        str(record.get("data", "")),
        content_type=str(record.get("content_type") or "text/plain; charset=utf-8"),
        headers={
            "X-Artifact-Id": artifact_id,
            "X-Tool-Name": str(record.get("tool") or ""),
            "X-Content-SHA256": str(record.get("sha256") or ""),
        },
    )
