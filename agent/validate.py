#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-flight validation of research tool arguments."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from data.workspace import WorkspacePathAuthorization

from agent.tools.exposure import RESEARCH_TOOL_NAMES


def validate_tool_args(
    name: str,
    args: Dict[str, Any],
    allowed_roots: Optional[list[Path]] = None,
    workspace_authorization: Optional["WorkspacePathAuthorization"] = None,
) -> Optional[str]:
    """Return an error string if args are invalid for the research workbench."""

    del allowed_roots, workspace_authorization
    if name not in RESEARCH_TOOL_NAMES:
        return "Tool is not available in the research workbench."
    if not isinstance(args, dict):
        return "Tool arguments must be a JSON object."
    if name == "run_r_code" and not str(args.get("code") or "").strip():
        return "'run_r_code' requires non-empty 'code'."
    if name == "ask_user":
        question = str(args.get("question") or "").strip()
        options = args.get("options")
        if not question:
            return "'ask_user' requires a question."
        if not isinstance(options, list) or not (2 <= len(options) <= 6):
            return "'ask_user' requires 2-6 options."
    return None
