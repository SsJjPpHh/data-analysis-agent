"""Authoritative local research result state.

The result page is organized around analysis turns rather than raw artifact
type buckets.  This module intentionally stores metadata and small previews
only; large tables, figures, and text files remain as managed artifacts under
``outputs/research`` and are served by the research artifact API.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from infrastructure.paths import data_path
from agent.analysis.artifact_manifest import build_artifact_manifest


SCHEMA_VERSION = 2
DEFAULT_PAPER_GROUP = "Primary analysis"
PAPER_GROUPS = [
    "Baseline / Table 1",
    "Primary analysis",
    "Secondary analysis",
    "Subgroup analysis",
    "Sensitivity analysis",
    "Diagnostics / Quality",
    "Supplementary",
]
CORE_LIMIT = 3
VIRTUAL_TEXT_PREVIEW_LIMIT = 50_000
FIGURE_VARIANT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".pdf", ".tif", ".tiff"}
FIGURE_PREVIEW_RANK = {
    ".png": 0,
    ".jpg": 1,
    ".jpeg": 1,
    ".webp": 2,
    ".gif": 3,
    ".svg": 4,
    ".pdf": 10,
    ".tif": 11,
    ".tiff": 11,
}
FIGURE_FORMAT_ORDER = ["png", "jpg", "jpeg", "webp", "gif", "svg", "pdf", "tif", "tiff"]
_GENERATION_RE = re.compile(r"^[a-f0-9]{32}$")
_WORKFLOW_STATE_FIELDS = (
    "state_version",
    "run_id",
    "source_plan_id",
    "workflow_id",
    "variant",
    "contract_signature",
    "phase_status",
    "preflight",
    "runtime_attempts",
    "r_succeeded",
    "assessment",
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def short_hash(value: str, prefix: str = "ra") -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:18]
    return f"{prefix}_{digest}"


def result_state_path(session_id: str) -> Path:
    return data_path("outputs", "research", session_id, "analysis_state.json")


def generation_root(session_id: str, generation: str) -> Path:
    if not _GENERATION_RE.fullmatch(str(generation or "")):
        raise ValueError("Invalid research workspace generation")
    return data_path("outputs", "research", session_id, "generations", generation)


def generation_state_path(session_id: str, generation: str) -> Path:
    return generation_root(session_id, generation) / "analysis_state.json"


def _clean_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _artifact_metadata(artifact: dict[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _artifact_id(kind: str, artifact: dict[str, Any], turn_id: str = "") -> str:
    key = "|".join(
        [
            kind,
            str(artifact.get("path") or ""),
            str(artifact.get("name") or ""),
            str(artifact.get("mime_type") or ""),
            turn_id,
        ]
    )
    return short_hash(key)


def _role_for_artifact(kind: str, artifact: dict[str, Any]) -> str:
    metadata = _artifact_metadata(artifact)
    explicit = str(metadata.get("role") or metadata.get("output_role") or "").lower()
    name_text = " ".join(
        str(item or "")
        for item in (
            artifact.get("name"),
            artifact.get("path"),
            metadata.get("title"),
            metadata.get("caption"),
            metadata.get("method_family"),
            metadata.get("method"),
        )
    ).lower()
    text = f"{explicit} {name_text}"
    if kind == "model":
        return "model_summary"
    if kind == "figure":
        if re.search(
            r"diagnostic|residual|qq|calibration|assumption|missing|quality|诊断|残差|校准|缺失|质量", text
        ):
            return "diagnostic_figure"
        return "primary_figure"
    if kind == "text":
        if re.search(r"method|code|evidence|package|方法|代码|依据", text):
            return "method_note"
        if re.search(r"diagnostic|limitation|warning|assumption|repair|诊断|限制|警告", text):
            return "quality_note"
        return "paper_text"
    if re.search(r"diagnostic|assumption|missing|quality|dictionary|profile|诊断|假设|缺失|质量|变量", text):
        return "diagnostic_table"
    if re.search(r"baseline|table.?1|demographic|characteristic|描述|基线", text):
        return "primary_table"
    if re.search(r"supplement|appendix|detail|raw|补充|明细", text):
        return "supporting_table"
    return "primary_table"


def _priority_for_role(role: str, index: int) -> int:
    base = {
        "primary_table": 10,
        "primary_figure": 20,
        "paper_text": 30,
        "diagnostic_table": 50,
        "diagnostic_figure": 55,
        "quality_note": 60,
        "supporting_table": 70,
        "method_note": 90,
        "model_summary": 25,
    }.get(role, 80)
    return base + index


def _paper_group_for_role(role: str) -> str:
    if role == "primary_table":
        return "Primary analysis"
    if role == "primary_figure":
        return "Primary analysis"
    if role.startswith("diagnostic") or role == "quality_note":
        return "Diagnostics / Quality"
    if role == "supporting_table":
        return "Supplementary"
    if role == "model_summary":
        return "Primary analysis"
    return "Primary analysis"


def _paper_group_for_tier(tier: str, role: str) -> str:
    if tier == "diagnostic":
        return "Diagnostics / Quality"
    if tier == "sensitivity":
        return "Sensitivity analysis"
    if tier == "conditional":
        return "Supplementary"
    if tier == "recommended":
        return "Secondary analysis"
    return _paper_group_for_role(role)


def _export_formats(kind: str, artifact: dict[str, Any]) -> list[str]:
    path = str(artifact.get("path") or "").lower()
    suffix = Path(path).suffix
    if kind == "table":
        return ["csv", "xlsx", "docx"]
    if kind == "figure":
        formats = ["png", "pdf", "tiff"]
        if suffix == ".pdf":
            formats = ["pdf"]
        elif suffix in {".tif", ".tiff"}:
            formats = ["tiff"]
        return formats
    if kind == "text":
        return ["txt"]
    if kind == "model":
        return ["txt"]
    return []


def _title_for_artifact(kind: str, artifact: dict[str, Any], index: int) -> str:
    metadata = _artifact_metadata(artifact)
    title = metadata.get("title") or metadata.get("label") or metadata.get("caption") or artifact.get("name")
    if title:
        return _clean_text(title, 180)
    return {"table": "表格", "figure": "图形", "text": "文本", "model": "模型"}.get(kind, "产物") + f" {index + 1}"


def artifact_from_r(
    artifact: dict[str, Any],
    *,
    kind: str,
    index: int,
    turn_id: str,
    source: str = "r",
) -> dict[str, Any]:
    metadata = _artifact_metadata(artifact)
    role = _role_for_artifact(kind, artifact)
    portfolio_role = str(metadata.get("role") or metadata.get("output_role") or "").strip()
    portfolio_tier = str(metadata.get("portfolio_tier") or metadata.get("tier") or "core").strip()
    title = _title_for_artifact(kind, artifact, index)
    paper_eligible = bool(
        metadata.get("paper_ready") is True
        or str(metadata.get("paper_ready")).lower() == "true"
        or role
        in {"primary_table", "primary_figure", "paper_text", "supporting_table", "model_summary"}
    )
    return {
        "artifact_id": _artifact_id(kind, artifact, turn_id),
        "turn_id": turn_id,
        "kind": kind,
        "source": source,
        "source_tool": str(artifact.get("source_tool") or metadata.get("source_tool") or ""),
        "job_id": str(artifact.get("job_id") or metadata.get("job_id") or ""),
        "role": role,
        "portfolio_role": portfolio_role,
        "portfolio_tier": portfolio_tier,
        "portfolio_status": str(metadata.get("portfolio_status") or "").strip(),
        "portfolio_reason": str(metadata.get("portfolio_reason") or "").strip(),
        "plan_id": str(metadata.get("plan_id") or "").strip(),
        "title": title,
        "caption": _clean_text(metadata.get("caption") or metadata.get("note") or "", 1000),
        "footnote": _clean_text(metadata.get("footnote") or metadata.get("note") or "", 1000),
        "path": str(artifact.get("path") or ""),
        "name": str(artifact.get("name") or title),
        "mime_type": str(artifact.get("mime_type") or ""),
        "preview": "",
        "export_formats": _export_formats(kind, artifact),
        "priority": _priority_for_role(role, index),
        "core_selected": False,
        "paper_eligible": paper_eligible,
        "included_in_paper": False,
        "paper_group": _paper_group_for_tier(portfolio_tier, role),
        "paper_order": 0,
        "metadata": metadata,
        "created_at": now_iso(),
    }


def _figure_variant_key(artifact: dict[str, Any]) -> str:
    if artifact.get("kind") != "figure" or not artifact.get("path"):
        return ""
    path = Path(str(artifact.get("path")))
    if path.suffix.lower() not in FIGURE_VARIANT_SUFFIXES:
        return ""
    return str(path.with_suffix(""))


def _coalesce_figure_variants(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for artifact in artifacts:
        key = _figure_variant_key(artifact)
        if not key or key not in indexes:
            if key:
                indexes[key] = len(output)
            output.append(artifact)
            continue

        index = indexes[key]
        existing = output[index]
        existing_suffix = Path(str(existing.get("path") or "")).suffix.lower()
        incoming_suffix = Path(str(artifact.get("path") or "")).suffix.lower()
        variants = [existing, artifact]
        preferred = min(
            variants,
            key=lambda item: FIGURE_PREVIEW_RANK.get(
                Path(str(item.get("path") or "")).suffix.lower(),
                99,
            ),
        )
        other = artifact if preferred is existing else existing

        for field in ("title", "caption", "footnote", "name"):
            if not preferred.get(field) and other.get(field):
                preferred[field] = other[field]
        preferred["metadata"] = {
            **dict(other.get("metadata") or {}),
            **dict(preferred.get("metadata") or {}),
        }
        manually_set = [item for item in variants if item.get("core_manually_set")]
        preferred["core_manually_set"] = bool(manually_set)
        preferred["core_selected"] = (
            any(item.get("core_selected") for item in manually_set)
            if manually_set
            else any(item.get("core_selected") for item in variants)
        )
        preferred["included_in_paper"] = any(item.get("included_in_paper") for item in variants)
        preferred["paper_eligible"] = any(item.get("paper_eligible") for item in variants)
        formats = {
            str(value).lower() for item in variants for value in item.get("export_formats") or [] if value
        }
        for suffix in (existing_suffix, incoming_suffix):
            if suffix:
                formats.add(suffix.removeprefix("."))
        preferred["export_formats"] = [fmt for fmt in FIGURE_FORMAT_ORDER if fmt in formats]
        output[index] = preferred
    return output


def _artifact_revision_key(artifact: dict[str, Any]) -> str:
    if artifact.get("source") != "r" or not artifact.get("path"):
        return ""
    kind = str(artifact.get("kind") or "")
    name = str(artifact.get("name") or "").strip().casefold()
    if kind not in {"table", "figure", "text"} or not name:
        return ""
    return f"{kind}|{name}"


def _coalesce_artifact_revisions(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    indexes: dict[str, int] = {}
    for artifact in artifacts:
        key = _artifact_revision_key(artifact)
        if not key or key not in indexes:
            if key:
                indexes[key] = len(output)
            output.append(artifact)
            continue

        index = indexes[key]
        previous = output[index]
        manually_set = [item for item in (previous, artifact) if item.get("core_manually_set")]
        artifact["core_manually_set"] = bool(manually_set)
        artifact["core_selected"] = (
            any(item.get("core_selected") for item in manually_set)
            if manually_set
            else any(item.get("core_selected") for item in (previous, artifact))
        )
        artifact["included_in_paper"] = bool(
            previous.get("included_in_paper") or artifact.get("included_in_paper")
        )
        artifact["paper_eligible"] = bool(previous.get("paper_eligible") or artifact.get("paper_eligible"))
        if previous.get("included_in_paper") and not artifact.get("paper_order"):
            artifact["paper_group"] = previous.get("paper_group") or artifact.get("paper_group")
            artifact["paper_order"] = previous.get("paper_order") or 0
        if artifact.get("kind") == "figure":
            formats = {
                str(value).lower()
                for item in (previous, artifact)
                for value in item.get("export_formats") or []
                if value
            }
            artifact["export_formats"] = [fmt for fmt in FIGURE_FORMAT_ORDER if fmt in formats]
        output[index] = artifact
    return output


def virtual_text_artifact(
    *,
    turn_id: str,
    title: str,
    text: str,
    role: str = "paper_text",
    source: str = "ai",
) -> dict[str, Any]:
    payload = {
        "name": title,
        "path": "",
        "mime_type": "text/plain",
        "metadata": {"role": role, "title": title},
    }
    artifact = artifact_from_r(payload, kind="text", index=0, turn_id=turn_id, source=source)
    artifact["artifact_id"] = short_hash(f"{turn_id}|{role}|{title}|{text}", "va")
    artifact["role"] = role
    artifact["preview"] = text[:VIRTUAL_TEXT_PREVIEW_LIMIT]
    artifact["paper_eligible"] = role in {"paper_text", "quality_note"}
    artifact["export_formats"] = ["txt"]
    return artifact


def empty_state(session_id: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "workspace_generation": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "dataset": {},
        "overview": {
            "analysis_turn_count": 0,
            "table_count": 0,
            "figure_count": 0,
            "text_count": 0,
            "model_count": 0,
            "included_in_paper_count": 0,
            "quality_flag_count": 0,
        },
        "analysis_turns": [],
        "portfolio_manifests": {},
        "paper_collection": {
            "groups": list(PAPER_GROUPS),
            "items": [],
            "narrative_language": "zh",
        },
        "delivery_status": {
            "score": 0,
            "items": [],
            "exports": {},
        },
    }


class ResearchResultStore:
    def __init__(self, session_id: str, generation: str = ""):
        self.session_id = str(session_id)
        self.generation = str(generation or "")
        self.path = (
            generation_state_path(self.session_id, self.generation)
            if self.generation
            else result_state_path(self.session_id)
        )

    def for_generation(self, generation: str) -> "ResearchResultStore":
        return ResearchResultStore(self.session_id, generation)

    def prepare_generation(self, generation: str, dataset: dict[str, Any]) -> dict[str, Any]:
        target = self.for_generation(generation)
        if target.path.exists():
            raise FileExistsError("Research workspace generation already exists")
        state = empty_state(self.session_id)
        state["workspace_generation"] = generation
        state["dataset"] = {
            "dataset_id": str(dataset.get("dataset_id") or ""),
            "name": str(dataset.get("name") or ""),
            "format": str(dataset.get("format") or ""),
            "created_at": str(dataset.get("created_at") or ""),
            "summary": (dataset.get("profile") or {}).get("summary") or {},
        }
        return target.save(state)

    def discard_generation(self, generation: str) -> None:
        root = generation_root(self.session_id, generation)
        expected_parent = data_path("outputs", "research", self.session_id, "generations").resolve(
            strict=False
        )
        resolved = root.resolve(strict=False)
        if resolved.parent != expected_parent:
            raise ValueError("Invalid research workspace generation")
        if resolved.is_dir():
            shutil.rmtree(resolved)

    def load(self) -> tuple[dict[str, Any], bool, str]:
        if not self.path.is_file():
            return empty_state(self.session_id), False, ""
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty_state(self.session_id), False, ""
        state = record.get("state") if isinstance(record, dict) else None
        if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
            return empty_state(self.session_id), False, ""
        state_generation = str(state.get("workspace_generation") or "")
        if state_generation != self.generation:
            return empty_state(self.session_id), False, ""
        return (
            self._normalize_state(state),
            True,
            str(record.get("saved_at") or state.get("updated_at") or ""),
        )

    def save(self, state: dict[str, Any]) -> dict[str, Any]:
        supplied_generation = str(state.get("workspace_generation") or "")
        if supplied_generation and supplied_generation != self.generation:
            raise ValueError("Research result state does not match the workspace generation")
        normalized = self._normalize_state(state)
        # Browser autosave excludes private replay inputs. Preserve them only
        # for the same execution and byte-identical code in this generation.
        previous, _, _ = self.load()
        snapshots = {
            (ref.get("job_id"), ref.get("code")): ref.get("reproducibility", {}).get("execution_snapshot")
            for turn in previous.get("analysis_turns", []) for ref in turn.get("execution_refs", [])
            if ref.get("job_id")
        }
        for turn in normalized.get("analysis_turns", []):
            for ref in turn.get("execution_refs", []):
                snapshot = snapshots.get((ref.get("job_id"), ref.get("code")))
                if snapshot:
                    ref.setdefault("reproducibility", {})["execution_snapshot"] = deepcopy(snapshot)
        normalized["workspace_generation"] = self.generation
        normalized["updated_at"] = now_iso()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "saved_at": normalized["updated_at"],
            "state": normalized,
        }
        serialized = json.dumps(record, ensure_ascii=False, indent=2)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(serialized, encoding="utf-8")
        temp.replace(self.path)
        return normalized

    def reset(self) -> dict[str, Any]:
        state = empty_state(self.session_id)
        return self.save(state)

    def conversation_outcome(self, turn_id: str, *, plan_id: str = "", r_attempted: bool = False) -> dict[str, Any]:
        """Project the canonical portfolio into a terminal conversation receipt.

        Finishing a model stream says nothing about scientific delivery. Read
        the saved result/manifest used by the result page and continuation.
        """
        state, _, _ = self.load()
        turn = self._find_turn(state, turn_id) or {}
        refs = turn.get("execution_refs") or []
        latest = refs[-1] if refs else {}
        active_plan_id = str(turn.get("portfolio_plan_id") or latest.get("plan_id") or plan_id)
        manifest = (state.get("portfolio_manifests") or {}).get(active_plan_id) or {}
        analysis_requested = bool(active_plan_id or refs or r_attempted)
        portfolio_complete = manifest.get("complete") is True if analysis_requested else None
        failed = turn.get("status") == "failed" or latest.get("ok") is False
        completed = bool(portfolio_complete) and not failed if analysis_requested else True
        assessment = manifest.get("latest_assessment") or latest.get("result_assessment") or {}
        diagnostic_quality = str(assessment.get("diagnostic_quality") or turn.get("diagnostic_quality") or "not_assessed")
        return {
            "turn_completed": True,
            "completed": completed,
            "analysis_status": "conversation_only" if not analysis_requested else "failed" if failed else "complete" if completed else "incomplete",
            "portfolio_complete": portfolio_complete,
            "plan_id": active_plan_id,
            "diagnostic_quality": diagnostic_quality,
            "validation_scope": "artifact_structure",
            "scientific_review_status": "not_reviewed",
            "outstanding_roles": list(manifest.get("outstanding_roles") or assessment.get("outstanding_roles") or assessment.get("missing_roles") or []),
            "missing_diagnostics": list(manifest.get("missing_diagnostics") or assessment.get("missing_diagnostics") or []),
            "artifact_count": len(turn.get("artifacts") or []),
        }

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

    def set_dataset(self, dataset: dict[str, Any], summary: dict[str, Any] | None = None) -> dict[str, Any]:
        state, _exists, _saved_at = self.load()
        state["dataset"] = {
            "dataset_id": str(dataset.get("dataset_id") or ""),
            "name": str(dataset.get("name") or ""),
            "format": str(dataset.get("format") or ""),
            "created_at": str(dataset.get("created_at") or ""),
            "summary": summary or dataset.get("profile", {}).get("summary") or {},
        }
        return self.save(state)

    def append_r_payload(
        self,
        *,
        turn_id: str,
        question: str,
        template_context: dict[str, Any] | None,
        payload: dict[str, Any],
        conversation_job_id: str = "",
        dataset_id: str = "",
    ) -> dict[str, Any]:
        state, _exists, _saved_at = self.load()
        frozen_dataset_id = str(dataset_id or "")
        state_dataset_id = str((state.get("dataset") or {}).get("dataset_id") or "")
        if frozen_dataset_id and frozen_dataset_id != state_dataset_id:
            raise ValueError("R payload does not match the frozen dataset boundary")
        turn = self._ensure_turn(
            state,
            turn_id=turn_id,
            question=question,
            template_context=template_context or {},
        )
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        execution = payload.get("execution") if isinstance(payload.get("execution"), dict) else {}
        execution_dataset_id = str(execution.get("dataset_id") or frozen_dataset_id or state_dataset_id)
        if frozen_dataset_id and execution_dataset_id != frozen_dataset_id:
            raise ValueError("R execution does not match the frozen dataset boundary")
        execution_generation = str(execution.get("workspace_generation") or "")
        if self.generation and execution_generation != self.generation:
            raise ValueError("R execution does not match the frozen workspace generation")
        raw_workflow = execution.get("workflow")
        workflow_state = (
            {
                key: deepcopy(raw_workflow[key])
                for key in _WORKFLOW_STATE_FIELDS
                if key in raw_workflow
            }
            if isinstance(raw_workflow, dict) and raw_workflow.get("run_id")
            else {}
        )
        execution_status = str(
            execution.get("status") or ("succeeded" if result.get("ok") is not False else "failed")
        )
        execution_ref = {
            "job_id": str(payload.get("job_id") or ""),
            "conversation_job_id": conversation_job_id,
            "dataset_id": execution_dataset_id,
            "plan_id": str(execution.get("plan_id") or ""),
            "workspace_generation": execution_generation,
            "status": execution_status,
            "diagnostic_status": dict(execution.get("diagnostic_status") or {}),
            "result_assessment": dict(payload.get("result_assessment") or {}),
            "ok": (
                result.get("ok")
                if isinstance(result.get("ok"), bool)
                else execution_status == "succeeded"
            ),
            "error": str(result.get("error") or ""),
            "warnings": [str(item) for item in result.get("warnings") or []],
            "code": str(result.get("code") or ""),
            "analysis_plan": dict(execution.get("analysis_plan") or {}),
            "reproducibility": dict(result.get("reproducibility") or {}),
            "stdout": str(result.get("stdout") or "")[:8000],
            "stderr": str(result.get("stderr") or "")[:8000],
            "created_at": now_iso(),
        }
        if (
            execution_ref["job_id"]
            or execution_ref["plan_id"]
            or execution_ref["result_assessment"]
            or execution_ref["code"]
            or execution_ref["error"]
            or execution_ref["warnings"]
        ):
            existing_index = next(
                (
                    index
                    for index, item in enumerate(turn["execution_refs"])
                    if execution_ref["job_id"]
                    and item.get("job_id") == execution_ref["job_id"]
                ),
                None,
            )
            if existing_index is None:
                turn["execution_refs"].append(execution_ref)
            else:
                existing_ref = turn["execution_refs"][existing_index]
                existing_failed = existing_ref.get("ok") is False or str(
                    existing_ref.get("status") or ""
                ).casefold() in {"failed", "blocked", "canceled", "cancelled", "error"}
                if not existing_failed or execution_ref["ok"] is False:
                    turn["execution_refs"].pop(existing_index)
                    turn["execution_refs"].append(execution_ref)
        for kind, collection in (
            ("table", "tables"),
            ("figure", "figures"),
            ("text", "texts"),
            ("model", "models"),
        ):
            for index, item in enumerate(result.get(collection) or []):
                if not isinstance(item, dict):
                    continue
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                item = {**item, "metadata": {**metadata, "plan_id": execution_ref["plan_id"]}}
                artifact = artifact_from_r(item, kind=kind, index=index, turn_id=turn_id)
                self._merge_artifact(turn, artifact)
        for warning in result.get("warnings") or []:
            self._add_quality_flag(turn, str(warning), severity="warning")
        if result.get("ok") is False and result.get("error"):
            self._add_quality_flag(turn, str(result.get("error")), severity="error")
        elif result.get("ok") is not False and self._payload_has_artifacts(result):
            self._clear_recovered_execution_errors(turn)
        self._refresh_turn_summary(turn)
        self._refresh_core_defaults(turn)
        plan_id = execution_ref["plan_id"]
        portfolio = execution.get("output_portfolio")
        if plan_id and isinstance(portfolio, list) and portfolio:
            manifests = state.setdefault("portfolio_manifests", {})
            previous_manifest = manifests.get(plan_id) if isinstance(manifests.get(plan_id), dict) else {}
            all_artifacts = [
                artifact
                for analysis_turn in state.get("analysis_turns", [])
                for artifact in analysis_turn.get("artifacts", [])
                if isinstance(artifact, dict)
            ]
            previous_workflow = previous_manifest.get("workflow_run")
            effective_workflow = (
                workflow_state
                if workflow_state
                else deepcopy(previous_workflow)
                if isinstance(previous_workflow, dict) and previous_workflow.get("run_id")
                else {}
            )
            manifest = build_artifact_manifest(
                plan_id=plan_id,
                portfolio_version=str(execution.get("portfolio_version") or "1.0"),
                portfolio=[item for item in portfolio if isinstance(item, dict)],
                artifacts=all_artifacts,
                diagnostic_status=execution.get("diagnostic_status")
                if isinstance(execution.get("diagnostic_status"), dict)
                else {},
                planned_diagnostics=[
                    str(item) for item in execution.get("planned_diagnostics") or []
                ],
                assessment=payload.get("result_assessment")
                if isinstance(payload.get("result_assessment"), dict)
                else {},
                previous=previous_manifest,
                workflow_state=effective_workflow,
            )
            manifests[plan_id] = manifest
            turn["portfolio_plan_id"] = plan_id
            turn["portfolio_complete"] = manifest["complete"]
            self._refresh_turn_summary(turn)
        self._refresh_state_summary(state)
        return self.save(state)

    def attach_narrative(self, *, turn_id: str, text: str, title: str = "论文结果解释") -> dict[str, Any]:
        state, _exists, _saved_at = self.load()
        turn = self._find_turn(state, turn_id)
        if turn is None:
            turn = self._ensure_turn(state, turn_id=turn_id, question="", template_context={})
        clean = str(text or "").strip()
        if clean:
            turn["narrative"] = clean
            turn["conclusion_items"] = _extract_conclusions(clean)
            self._merge_artifact(
                turn, virtual_text_artifact(turn_id=turn_id, title=title, text=clean, role="paper_text")
            )
        self._refresh_turn_summary(turn)
        self._refresh_core_defaults(turn)
        self._refresh_state_summary(state)
        return self.save(state)

    def patch_artifact(self, artifact_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        state, _exists, _saved_at = self.load()
        artifact = self._find_artifact(state, artifact_id)
        if artifact is None:
            return None
        allowed = {
            "title",
            "caption",
            "footnote",
            "core_selected",
            "included_in_paper",
            "paper_group",
            "paper_order",
        }
        for key, value in updates.items():
            if key not in allowed:
                continue
            if key == "core_selected":
                artifact[key] = bool(value)
                artifact["core_manually_set"] = True
            elif key == "included_in_paper":
                artifact[key] = bool(value)
            elif key == "paper_order":
                try:
                    artifact[key] = max(0, int(value))
                except (TypeError, ValueError):
                    artifact[key] = 0
            else:
                artifact[key] = _clean_text(value, 1000)
        if artifact.get("included_in_paper"):
            artifact["paper_group"] = artifact.get("paper_group") or DEFAULT_PAPER_GROUP
        self._refresh_all_turns(state)
        self._refresh_state_summary(state)
        return self.save(state)

    def reorder_paper(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        state, _exists, _saved_at = self.load()
        by_id = {
            artifact.get("artifact_id"): artifact
            for turn in state.get("analysis_turns", [])
            for artifact in turn.get("artifacts", [])
        }
        for order, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            artifact = by_id.get(str(item.get("artifact_id") or ""))
            if artifact is None:
                continue
            if item.get("paper_group"):
                artifact["paper_group"] = _clean_text(item.get("paper_group"), 120)
            artifact["paper_order"] = order + 1
            artifact["included_in_paper"] = True
        self._refresh_state_summary(state)
        return self.save(state)

    def _normalize_state(self, state: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(empty_state(self.session_id))
        normalized.update({k: v for k, v in state.items() if k in normalized})
        normalized["schema_version"] = SCHEMA_VERSION
        normalized["session_id"] = self.session_id
        normalized["analysis_turns"] = [
            self._normalize_turn(turn) for turn in state.get("analysis_turns", []) if isinstance(turn, dict)
        ]
        normalized["portfolio_manifests"] = {
            str(plan_id): dict(manifest)
            for plan_id, manifest in dict(state.get("portfolio_manifests") or {}).items()
            if str(plan_id) and isinstance(manifest, dict)
        }
        paper = state.get("paper_collection") if isinstance(state.get("paper_collection"), dict) else {}
        normalized["paper_collection"] = {
            "groups": list(paper.get("groups") or PAPER_GROUPS),
            "items": [],
            "narrative_language": str(paper.get("narrative_language") or "zh"),
        }
        delivery = state.get("delivery_status") if isinstance(state.get("delivery_status"), dict) else {}
        normalized["delivery_status"] = {
            "score": int(delivery.get("score") or 0),
            "items": list(delivery.get("items") or []),
            "exports": dict(delivery.get("exports") or {}),
        }
        self._refresh_state_summary(normalized)
        return normalized

    def _normalize_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "turn_id": str(turn.get("turn_id") or short_hash(now_iso(), "turn")),
            "title": _clean_text(turn.get("title") or "分析轮次", 180),
            "question": str(turn.get("question") or ""),
            "source": str(turn.get("source") or "natural_language"),
            "template_id": str(turn.get("template_id") or ""),
            "template_title": str(turn.get("template_title") or ""),
            "created_at": str(turn.get("created_at") or now_iso()),
            "updated_at": str(turn.get("updated_at") or now_iso()),
            "status": str(turn.get("status") or "succeeded"),
            "conclusion_items": [str(item) for item in turn.get("conclusion_items") or []][:8],
            "artifacts": [],
            "narrative": str(turn.get("narrative") or ""),
            "quality_flags": [
                dict(item) for item in turn.get("quality_flags") or [] if isinstance(item, dict)
            ],
            "methods_note": str(turn.get("methods_note") or ""),
            "execution_refs": [
                dict(item) for item in turn.get("execution_refs") or [] if isinstance(item, dict)
            ],
            "summary": dict(turn.get("summary") or {}),
            "portfolio_plan_id": str(turn.get("portfolio_plan_id") or ""),
            "portfolio_complete": bool(turn.get("portfolio_complete")),
        }
        artifacts = [
            self._normalize_artifact(item, clean["turn_id"])
            for item in turn.get("artifacts") or []
            if isinstance(item, dict)
        ]
        clean["artifacts"] = _coalesce_artifact_revisions(_coalesce_figure_variants(artifacts))
        self._refresh_turn_summary(clean)
        return clean

    def _normalize_artifact(self, artifact: dict[str, Any], turn_id: str) -> dict[str, Any]:
        base = {
            "artifact_id": str(
                artifact.get("artifact_id") or short_hash(json.dumps(artifact, sort_keys=True, default=str))
            ),
            "turn_id": str(artifact.get("turn_id") or turn_id),
            "kind": str(artifact.get("kind") or "artifact"),
            "source": str(artifact.get("source") or "r"),
            "source_tool": str(
                artifact.get("source_tool")
                or (artifact.get("metadata") or {}).get("source_tool")
                or ""
            ),
            "job_id": str(
                artifact.get("job_id")
                or (artifact.get("metadata") or {}).get("job_id")
                or ""
            ),
            "role": str(artifact.get("role") or "supporting_table"),
            "portfolio_role": str(
                artifact.get("portfolio_role")
                or (artifact.get("metadata") or {}).get("role")
                or ""
            ),
            "portfolio_tier": str(artifact.get("portfolio_tier") or "core"),
            "portfolio_status": str(artifact.get("portfolio_status") or ""),
            "portfolio_reason": str(artifact.get("portfolio_reason") or ""),
            "plan_id": str(
                artifact.get("plan_id")
                or (artifact.get("metadata") or {}).get("plan_id")
                or ""
            ),
            "title": _clean_text(artifact.get("title") or artifact.get("name") or "科研产物", 180),
            "caption": _clean_text(artifact.get("caption") or "", 1000),
            "footnote": _clean_text(artifact.get("footnote") or "", 1000),
            "path": str(artifact.get("path") or ""),
            "name": str(artifact.get("name") or artifact.get("title") or ""),
            "mime_type": str(artifact.get("mime_type") or ""),
            "preview": str(artifact.get("preview") or "")[:VIRTUAL_TEXT_PREVIEW_LIMIT],
            "export_formats": [str(item) for item in artifact.get("export_formats") or []],
            "priority": int(artifact.get("priority") or 999),
            "core_selected": bool(artifact.get("core_selected")),
            "core_manually_set": bool(artifact.get("core_manually_set")),
            "paper_eligible": bool(artifact.get("paper_eligible")),
            "included_in_paper": bool(artifact.get("included_in_paper")),
            "paper_group": str(
                artifact.get("paper_group") or _paper_group_for_role(str(artifact.get("role") or ""))
            ),
            "paper_order": int(artifact.get("paper_order") or 0),
            "metadata": dict(artifact.get("metadata") or {}),
            "created_at": str(artifact.get("created_at") or now_iso()),
        }
        if not base["export_formats"]:
            base["export_formats"] = _export_formats(base["kind"], base)
        return base

    def _ensure_turn(
        self,
        state: dict[str, Any],
        *,
        turn_id: str,
        question: str,
        template_context: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self._find_turn(state, turn_id)
        if existing is not None:
            return existing
        template_id = str(template_context.get("id") or "")
        template_title = str(template_context.get("title") or "")
        # A conversational continuation also carries the frozen plan title.
        # Only a real template id makes the turn template-sourced.
        source = "template" if template_id else "natural_language"
        title = (
            template_title
            or _derive_title(question)
            or f"分析轮次 {len(state.get('analysis_turns', [])) + 1}"
        )
        turn = self._normalize_turn(
            {
                "turn_id": turn_id,
                "title": title,
                "question": question,
                "source": source,
                "template_id": template_id,
                "template_title": template_title,
                "created_at": now_iso(),
                "status": "running",
            }
        )
        state.setdefault("analysis_turns", []).append(turn)
        return turn

    def _find_turn(self, state: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
        for turn in state.get("analysis_turns", []):
            if turn.get("turn_id") == turn_id:
                return turn
        return None

    def _find_artifact(self, state: dict[str, Any], artifact_id: str) -> dict[str, Any] | None:
        for turn in state.get("analysis_turns", []):
            for artifact in turn.get("artifacts", []):
                if artifact.get("artifact_id") == artifact_id:
                    return artifact
        return None

    def _merge_artifact(self, turn: dict[str, Any], artifact: dict[str, Any]) -> None:
        for existing in turn.get("artifacts", []):
            if existing.get("artifact_id") == artifact.get("artifact_id") or (
                artifact.get("path") and existing.get("path") == artifact.get("path")
            ):
                keep = {
                    "core_selected": existing.get("core_selected", False),
                    "core_manually_set": existing.get("core_manually_set", False),
                    "included_in_paper": existing.get("included_in_paper", False),
                    "paper_group": existing.get("paper_group"),
                    "paper_order": existing.get("paper_order", 0),
                    "title": existing.get("title") or artifact.get("title"),
                    "caption": existing.get("caption") or artifact.get("caption"),
                    "footnote": existing.get("footnote") or artifact.get("footnote"),
                }
                existing.update(artifact)
                existing["core_selected"] = bool(keep["core_selected"])
                existing["core_manually_set"] = bool(keep["core_manually_set"])
                existing["included_in_paper"] = bool(keep["included_in_paper"])
                for key in ("paper_group", "paper_order", "title", "caption", "footnote"):
                    value = keep.get(key)
                    if value not in ("", None):
                        existing[key] = value
                return
        turn.setdefault("artifacts", []).append(artifact)

    def _add_quality_flag(self, turn: dict[str, Any], message: str, severity: str = "warning") -> None:
        text = _clean_text(message, 1000)
        if not text:
            return
        if any(item.get("message") == text for item in turn.get("quality_flags", [])):
            return
        turn.setdefault("quality_flags", []).append(
            {
                "severity": severity,
                "message": text,
                "created_at": now_iso(),
            }
        )

    @staticmethod
    def _payload_has_artifacts(result: dict[str, Any]) -> bool:
        return bool(
            result.get("tables")
            or result.get("figures")
            or result.get("texts")
            or result.get("models")
            or result.get("artifacts")
        )

    @staticmethod
    def _clear_recovered_execution_errors(turn: dict[str, Any]) -> None:
        recovered_patterns = (
            r"rscript exited with code",
            r"cannot open file",
            r"unused argument .*metadata",
            r"no such file or directory",
        )
        flags = []
        for flag in turn.get("quality_flags", []):
            message = str(flag.get("message") or "")
            if flag.get("severity") == "error" and any(
                re.search(pattern, message, flags=re.IGNORECASE) for pattern in recovered_patterns
            ):
                continue
            flags.append(flag)
        turn["quality_flags"] = flags

    def _refresh_turn_summary(self, turn: dict[str, Any]) -> None:
        artifacts = turn.get("artifacts", [])
        turn["methods_note"] = "\n\n".join(
            item.get("preview") or item.get("title") or item.get("name") or ""
            for item in artifacts
            if item.get("role") == "method_note"
        ).strip() or turn.get("methods_note", "")
        latest_assessment = next(
            (
                item.get("result_assessment")
                for item in reversed(turn.get("execution_refs", []))
                if isinstance(item.get("result_assessment"), dict)
                and item.get("result_assessment")
            ),
            {},
        )
        latest_execution = next(
            (
                item
                for item in reversed(turn.get("execution_refs", []))
                if isinstance(item, dict)
            ),
            {},
        )
        latest_failed = bool(latest_execution) and (
            latest_execution.get("ok") is False
            or str(latest_execution.get("status") or "").casefold()
            in {"failed", "blocked", "canceled", "cancelled", "error"}
        )
        # Completion describes delivery; diagnostic quality describes method
        # limitations and remains visible even when every file was produced.
        turn["diagnostic_quality"] = str(latest_assessment.get("diagnostic_quality") or "not_assessed")
        turn["validation_scope"] = "artifact_structure"
        turn["scientific_review_status"] = "not_reviewed"
        turn["quality_flags"] = [flag for flag in turn.get("quality_flags", []) if flag.get("source") != "method_diagnostic"]
        for name in latest_assessment.get("failed_diagnostics") or []:
            turn["quality_flags"].append({"severity": "error", "source": "method_diagnostic", "message": f"方法诊断未通过：{name}；需限制结论或调整分析方法。"})
        for name in latest_assessment.get("warning_diagnostics") or []:
            turn["quality_flags"].append({"severity": "warning", "source": "method_diagnostic", "message": f"方法诊断需注意：{name}"})
        if latest_failed:
            turn["status"] = "failed"
        elif any(item.get("severity") == "error" for item in turn.get("quality_flags", [])) and not artifacts:
            turn["status"] = "failed"
        elif turn.get("portfolio_complete") or latest_assessment.get("complete") is True:
            turn["status"] = "succeeded"
        elif latest_assessment or turn.get("portfolio_plan_id"):
            turn["status"] = "incomplete"
        else:
            turn["status"] = "succeeded"
        turn["updated_at"] = now_iso()
        turn["summary"] = {
            "table_count": sum(1 for item in artifacts if item.get("kind") == "table"),
            "figure_count": sum(1 for item in artifacts if item.get("kind") == "figure"),
            "text_count": sum(
                1 for item in artifacts if item.get("kind") == "text" and item.get("role") != "method_note"
            ),
            "model_count": sum(1 for item in artifacts if item.get("kind") == "model"),
            "method_note_count": sum(1 for item in artifacts if item.get("role") == "method_note"),
            "quality_flag_count": len(turn.get("quality_flags", [])),
            "core_count": sum(1 for item in artifacts if item.get("core_selected")),
            "paper_count": sum(1 for item in artifacts if item.get("included_in_paper")),
        }

    def _refresh_core_defaults(self, turn: dict[str, Any]) -> None:
        artifacts = [item for item in turn.get("artifacts", []) if item.get("role") != "method_note"]
        manual_selected = [
            item for item in artifacts if item.get("core_selected") and item.get("core_manually_set")
        ]
        if len(manual_selected) >= CORE_LIMIT:
            return

        # Auto-selected core items should remain balanced as new R artifacts
        # arrive. User-touched items are preserved through core_manually_set.
        for item in artifacts:
            if not item.get("core_manually_set"):
                item["core_selected"] = False

        selected = list(manual_selected)
        candidates = [
            item for item in artifacts if not item.get("core_selected") and not item.get("core_manually_set")
        ]
        sorted_candidates = sorted(
            candidates,
            key=lambda artifact: (int(artifact.get("priority") or 999), artifact.get("title") or ""),
        )

        def _take_first(predicate) -> None:
            if len(selected) >= CORE_LIMIT:
                return
            for candidate in sorted_candidates:
                if candidate in selected:
                    continue
                if predicate(candidate):
                    selected.append(candidate)
                    return

        _take_first(lambda item: item.get("kind") == "table")
        _take_first(lambda item: item.get("kind") == "figure")
        _take_first(lambda item: item.get("kind") == "text")
        _take_first(lambda item: item.get("kind") == "model")

        for item in sorted_candidates:
            if len(selected) >= CORE_LIMIT:
                break
            if item not in selected:
                selected.append(item)

        for item in selected[:CORE_LIMIT]:
            item["core_selected"] = True

    def _refresh_all_turns(self, state: dict[str, Any]) -> None:
        for turn in state.get("analysis_turns", []):
            self._refresh_turn_summary(turn)

    def _refresh_state_summary(self, state: dict[str, Any]) -> None:
        turns = state.get("analysis_turns", [])
        artifacts = [artifact for turn in turns for artifact in turn.get("artifacts", [])]
        paper_items = [
            {
                "artifact_id": artifact.get("artifact_id"),
                "turn_id": artifact.get("turn_id"),
                "kind": artifact.get("kind"),
                "title": artifact.get("title"),
                "group": artifact.get("paper_group") or DEFAULT_PAPER_GROUP,
                "order": artifact.get("paper_order") or 0,
            }
            for artifact in artifacts
            if artifact.get("included_in_paper")
        ]
        paper_items.sort(
            key=lambda item: (str(item.get("group")), int(item.get("order") or 0), str(item.get("title")))
        )
        state["overview"] = {
            "analysis_turn_count": len(turns),
            "table_count": sum(1 for item in artifacts if item.get("kind") == "table"),
            "figure_count": sum(1 for item in artifacts if item.get("kind") == "figure"),
            "text_count": sum(
                1 for item in artifacts if item.get("kind") == "text" and item.get("role") != "method_note"
            ),
            "model_count": sum(1 for item in artifacts if item.get("kind") == "model"),
            "included_in_paper_count": len(paper_items),
            "quality_flag_count": sum(len(turn.get("quality_flags", [])) for turn in turns),
        }
        state.setdefault("paper_collection", {})
        state["paper_collection"]["groups"] = list(state["paper_collection"].get("groups") or PAPER_GROUPS)
        state["paper_collection"]["items"] = paper_items
        state["delivery_status"] = _delivery_status(state)


def _derive_title(question: str) -> str:
    text = _clean_text(question, 80)
    if not text:
        return ""
    return text.rstrip("。！？!?")[:80]


def _extract_conclusions(text: str) -> list[str]:
    output: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        line = re.sub(r"^[-*•\d.、\s]+", "", line).strip()
        if not line or len(line) < 8:
            continue
        if re.search(r"结果|显示|提示|发现|差异|相关|风险|显著|模型|样本|缺失", line):
            output.append(line[:240])
        if len(output) >= 6:
            break
    if not output and text.strip():
        output.append(_clean_text(text, 240))
    return output


def _delivery_status(state: dict[str, Any]) -> dict[str, Any]:
    overview = state.get("overview") or {}
    dataset = state.get("dataset") or {}
    items = [
        {
            "key": "dataset",
            "label": "数据已上传",
            "ok": bool(dataset.get("dataset_id")),
            "detail": dataset.get("name") or "等待上传数据",
        },
        {
            "key": "turns",
            "label": "分析轮次",
            "ok": int(overview.get("analysis_turn_count") or 0) > 0,
            "detail": f"{overview.get('analysis_turn_count') or 0} 个轮次",
        },
        {
            "key": "artifacts",
            "label": "结构化产物",
            "ok": int(overview.get("table_count") or 0) + int(overview.get("figure_count") or 0) > 0,
            "detail": f"{overview.get('table_count') or 0} 表 · {overview.get('figure_count') or 0} 图",
        },
        {
            "key": "paper",
            "label": "论文整理",
            "ok": int(overview.get("included_in_paper_count") or 0) > 0,
            "detail": f"{overview.get('included_in_paper_count') or 0} 个纳入论文",
        },
        {
            "key": "quality",
            "label": "质量提醒",
            "ok": int(overview.get("quality_flag_count") or 0) == 0,
            "detail": f"{overview.get('quality_flag_count') or 0} 条提醒",
        },
    ]
    return {
        "score": sum(1 for item in items if item["ok"]),
        "items": items,
        "exports": dict((state.get("delivery_status") or {}).get("exports") or {}),
    }
