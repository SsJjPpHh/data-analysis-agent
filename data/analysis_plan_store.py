"""Atomic local storage for trusted, frozen analysis plans."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.analysis.models import AnalysisPlan, RequiredOutput, ResolvedInput
from infrastructure.paths import data_path


def _safe_hash(value: str, *, length: int = 32) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:length]


def _plan_from_dict(value: object) -> AnalysisPlan | None:
    if not isinstance(value, dict):
        return None
    try:
        outputs = tuple(
            RequiredOutput(
                **{
                    **item,
                    "conditions": tuple(item.get("conditions") or ()),
                    "acceptance": tuple(item.get("acceptance") or ()),
                    "legacy_roles": tuple(item.get("legacy_roles") or ()),
                }
            )
            for item in value.get("required_outputs") or []
            if isinstance(item, dict)
        )
        resolved = {
            str(input_id): ResolvedInput(**item)
            for input_id, item in dict(value.get("resolved_inputs") or {}).items()
            if isinstance(item, dict)
        }
        return AnalysisPlan(
            plan_id=str(value["plan_id"]),
            source=str(value["source"]),  # type: ignore[arg-type]
            dataset_id=str(value["dataset_id"]),
            intent=str(value["intent"]),
            category=str(value["category"]),
            method_family=str(value["method_family"]),
            scenario=str(value["scenario"]),
            variable_bindings={
                str(role): str(column) for role, column in dict(value.get("variable_bindings") or {}).items()
            },
            packages=tuple(str(item) for item in value.get("packages") or []),
            prerequisites=tuple(str(item) for item in value.get("prerequisites") or []),
            assumptions=tuple(str(item) for item in value.get("assumptions") or []),
            diagnostics=tuple(str(item) for item in value.get("diagnostics") or []),
            required_outputs=outputs,
            resource_class=str(value.get("resource_class") or "standard"),  # type: ignore[arg-type]
            resolved_inputs=resolved,
            template_id=str(value.get("template_id") or ""),
            workspace_generation=str(value.get("workspace_generation") or ""),
            profile_revision=str(value.get("profile_revision") or ""),
            portfolio_version=str(value.get("portfolio_version") or "1.0"),
            capability_evidence=tuple(str(item) for item in value.get("capability_evidence") or []),
        )
    except (KeyError, TypeError, ValueError):
        return None


class AnalysisPlanStore:
    """Persist a bounded set of plans for the lifetime of a local research session."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        max_plans_per_session: int = 512,
        max_age_seconds: int | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else data_path("analysis_plans")
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_plans_per_session = max(4, min(4096, int(max_plans_per_session)))
        self.max_age_seconds = (
            None if max_age_seconds is None else max(60, int(max_age_seconds))
        )

    def _session_dir(self, session_id: str) -> Path:
        return self.root / _safe_hash(session_id, length=24)

    def _path(self, session_id: str, plan_id: str) -> Path:
        return self._session_dir(session_id) / f"{_safe_hash(plan_id)}.json"

    def save(self, session_id: str, plan: AnalysisPlan) -> AnalysisPlan:
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(session_id, plan.plan_id)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"saved_at": time.time(), "plan": asdict(plan)},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
        self._prune(session_dir)
        return plan

    def _prune(self, session_dir: Path) -> None:
        records = sorted(
            session_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0,
            reverse=True,
        )
        cutoff = time.time() - self.max_age_seconds if self.max_age_seconds is not None else None
        for index, path in enumerate(records):
            try:
                expired = cutoff is not None and path.stat().st_mtime < cutoff
            except OSError:
                expired = True
            if expired or index >= self.max_plans_per_session:
                path.unlink(missing_ok=True)

    def load(
        self,
        session_id: str,
        plan_id: str,
        *,
        dataset_id: str,
        workspace_generation: str,
        profile_revision: str,
    ) -> AnalysisPlan | None:
        path = self._path(session_id, plan_id)
        try:
            decoded: Any = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(decoded, dict):
                return None
            record: dict[str, Any] = decoded
            saved_at = float(record.get("saved_at") or 0)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if self.max_age_seconds is not None and saved_at < time.time() - self.max_age_seconds:
            path.unlink(missing_ok=True)
            return None
        plan = _plan_from_dict(record.get("plan"))
        if plan is None or plan.plan_id != plan_id:
            return None
        if plan.dataset_id != str(dataset_id or ""):
            return None
        if plan.workspace_generation != str(workspace_generation or ""):
            return None
        if plan.profile_revision != str(profile_revision or ""):
            return None
        return plan
