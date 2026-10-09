# -*- coding: utf-8 -*-
"""Runtime policy for research workbench tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

ToolCategory = Literal["read", "analysis", "interaction"]
ExecutionMode = Literal["sync", "auto", "job"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    default_exposed: bool = True
    requires_runtime: bool = False
    concurrency_safe: bool = False
    execution_mode: ExecutionMode = "sync"
    job_threshold: str = ""

    def is_exposed(
        self, command: str = "", has_data_source: bool = False, has_workspace: bool = False, skill: str = ""
    ) -> bool:
        return self.default_exposed


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            if not spec.name:
                raise ValueError("tool name cannot be empty")
            if spec.name in self._specs:
                raise ValueError(f"duplicate tool spec: {spec.name}")
            if spec.execution_mode == "auto" and not spec.job_threshold:
                raise ValueError(f"auto tool requires job_threshold: {spec.name}")
            self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def all(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs.values())

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def exposed_names(
        self,
        command: str = "",
        has_data_source: bool = False,
        has_workspace: bool = False,
        skill: str = "",
    ) -> set[str]:
        return {
            spec.name
            for spec in self._specs.values()
            if spec.is_exposed(command, has_data_source, has_workspace, skill)
        }

    def validate_schema_names(self, schemas: Iterable[dict]) -> None:
        schema_names = {((schema.get("function") or {}).get("name") or "").strip() for schema in schemas}
        schema_names.discard("")
        missing_specs = schema_names - self.names()
        missing_schemas = self.names() - schema_names
        if missing_specs or missing_schemas:
            details = []
            if missing_specs:
                details.append(f"missing specs={sorted(missing_specs)}")
            if missing_schemas:
                details.append(f"missing schemas={sorted(missing_schemas)}")
            raise ValueError("tool registry/schema mismatch: " + "; ".join(details))


BUILTIN_TOOL_REGISTRY = ToolRegistry(
    [
        ToolSpec("inspect_dataset", "read", requires_runtime=True),
        ToolSpec("set_analysis_plan", "analysis", requires_runtime=True),
        ToolSpec(
            "run_r_code",
            "analysis",
            requires_runtime=True,
            execution_mode="auto",
            job_threshold="r_timeout_seconds_gt_60",
        ),
        ToolSpec("get_r_package_inventory", "read", requires_runtime=True),
        ToolSpec("get_r_package_help", "read", requires_runtime=True),
        ToolSpec("ask_user", "interaction"),
    ]
)


def get_tool_spec(name: str) -> ToolSpec | None:
    return BUILTIN_TOOL_REGISTRY.get(name)


def is_job_eligible(name: str) -> bool:
    spec = get_tool_spec(name)
    return bool(spec and spec.execution_mode in {"auto", "job"})
