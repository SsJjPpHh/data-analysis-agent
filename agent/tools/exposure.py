# -*- coding: utf-8 -*-
"""Research-mode tool exposure: the six workbench tools only."""

from __future__ import annotations

from copy import deepcopy

from .registry import BUILTIN_TOOL_REGISTRY

RESEARCH_TOOL_NAMES = frozenset(
    {
        "inspect_dataset",
        "set_analysis_plan",
        "run_r_code",
        "get_r_package_inventory",
        "get_r_package_help",
        "ask_user",
    }
)


def _tool_name(schema: dict) -> str:
    return ((schema.get("function") or {}).get("name") or "").strip()


def filter_tools_for_turn(
    tools: list[dict],
    **_ignored,
) -> list[dict]:
    """Return only the research workbench tools.

    Legacy skill/command/MCP arguments are ignored. The frozen-plan path
    still hides ``set_analysis_plan`` in BusinessAgent after this filter.
    """

    allowed = RESEARCH_TOOL_NAMES & BUILTIN_TOOL_REGISTRY.names()
    filtered = [schema for schema in tools if _tool_name(schema) in allowed]
    return deepcopy(filtered)
