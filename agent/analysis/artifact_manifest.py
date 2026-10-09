"""Canonical scientific artifact manifest shared across analysis turns."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.analysis.result_validation import (
    artifact_has_trusted_receipt,
    artifact_satisfies_output_contract,
    merge_diagnostic_statuses,
)


def _terminal_item_status(status: str, tier: str) -> bool:
    return status == "produced" or (
        status in {"not_applicable", "limited", "declined"} and tier != "core"
    )


@dataclass(frozen=True)
class ManifestValidationEvidence:
    artifacts: tuple[dict[str, Any], ...]
    diagnostic_status: dict[str, str]
    r_executed: bool


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _portfolio_role(artifact: Mapping[str, Any]) -> str:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    return str(
        metadata.get("role")
        or metadata.get("output_role")
        or artifact.get("portfolio_role")
        or ""
    ).strip()


def _plan_id(artifact: Mapping[str, Any]) -> str:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    return str(metadata.get("plan_id") or artifact.get("plan_id") or "").strip()


_PERSISTED_METADATA_FIELDS = (
    "dataset_id",
    "workspace_generation",
    "job_id",
    "source_execution_status",
    "plan_id",
    "variable_bindings",
    "resolved_inputs",
    "diagnostic_status",
    "diagnostics",
    "role",
    "output_role",
    "semantic_key",
    "portfolio_tier",
    "portfolio_status",
    "portfolio_reason",
    "acceptance_evidence",
    "source_tool",
    "artifact_size_bytes",
    "artifact_sha256",
    "artifact_validation_receipt",
)


def _persisted_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    persisted_metadata = {
        key: deepcopy(metadata[key])
        for key in _PERSISTED_METADATA_FIELDS
        if key in metadata
    }
    actual_kind = str(artifact.get("type") or artifact.get("kind") or "").strip()
    return {
        "artifact_id": str(artifact.get("artifact_id") or ""),
        "turn_id": str(artifact.get("turn_id") or ""),
        "type": actual_kind,
        "kind": actual_kind,
        "portfolio_role": _portfolio_role(artifact),
        "plan_id": _plan_id(artifact),
        "job_id": str(artifact.get("job_id") or metadata.get("job_id") or ""),
        "title": str(artifact.get("title") or artifact.get("name") or ""),
        "name": str(artifact.get("name") or ""),
        "path": str(artifact.get("path") or ""),
        "mime_type": str(artifact.get("mime_type") or ""),
        "source_tool": str(artifact.get("source_tool") or metadata.get("source_tool") or ""),
        "metadata": persisted_metadata,
    }


def _workflow_latest_execution_succeeded(workflow_state: Mapping[str, Any] | None) -> bool:
    if not isinstance(workflow_state, Mapping) or not workflow_state.get("run_id"):
        return True
    phases = workflow_state.get("phase_status")
    if not isinstance(phases, Mapping) or phases.get("analyze") != "completed":
        return False
    attempts = workflow_state.get("runtime_attempts")
    if isinstance(attempts, (list, tuple)) and attempts:
        latest = attempts[-1]
        if not isinstance(latest, Mapping) or latest.get("ok") is not True:
            return False
    return workflow_state.get("r_succeeded") is True


def build_artifact_manifest(
    *,
    plan_id: str,
    portfolio_version: str,
    portfolio: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    diagnostic_status: Mapping[str, Any] | None = None,
    planned_diagnostics: Sequence[str] = (),
    assessment: Mapping[str, Any] | None = None,
    previous: Mapping[str, Any] | None = None,
    workflow_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical manifest from every artifact owned by a plan."""

    previous = previous or {}
    expected = list(portfolio) or [
        item for item in previous.get("items") or [] if isinstance(item, dict)
    ]
    owned = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, Mapping)
        and _plan_id(artifact) == plan_id
        and _portfolio_role(artifact)
    ]
    items: list[dict[str, Any]] = []
    assessment_invalid = {
        str(item) for item in dict(assessment or {}).get("invalid_roles") or []
    }
    for raw in expected:
        role = str(raw.get("role") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        tier = str(raw.get("tier") or "core")
        if not role or not kind:
            continue
        accepted_roles = {
            role,
            *(str(item).strip() for item in raw.get("legacy_roles") or [] if str(item).strip()),
        }
        candidates = [artifact for artifact in owned if _portfolio_role(artifact) in accepted_roles]
        status = "missing"
        reason = ""
        actual_kinds: list[str] = []
        artifact_ids: list[str] = []
        for artifact in candidates:
            metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
            actual_kind = str(artifact.get("type") or artifact.get("kind") or "").strip()
            if actual_kind and actual_kind not in actual_kinds:
                actual_kinds.append(actual_kind)
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            if artifact_id and artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            disposition = str(metadata.get("portfolio_status") or "").strip().lower()
            disposition_reason = str(metadata.get("portfolio_reason") or "").strip()
            trusted = artifact_has_trusted_receipt(artifact, expected_kind=actual_kind)
            if (
                trusted
                and disposition in {"not_applicable", "limited", "declined"}
                and disposition_reason
            ):
                status = disposition
                reason = disposition_reason
                break
            if trusted and artifact_satisfies_output_contract(
                artifact,
                kind=kind,
                semantic_key=str(raw.get("semantic_key") or role),
                acceptance=[str(item) for item in raw.get("acceptance") or []],
            ):
                status = "produced"
        if role in assessment_invalid:
            status = "invalid"
            reason = "artifact did not satisfy kind, semantic key, receipt, or acceptance evidence"
        if candidates and status == "missing":
            status = "invalid"
            reason = (
                f"expected a server-validated {kind}; "
                f"received {', '.join(actual_kinds) or 'unknown'}"
            )
        items.append(
            {
                "role": role,
                "kind": kind,
                "label": str(raw.get("label") or role),
                "tier": tier,
                "source": str(raw.get("source") or "template"),
                "required": bool(raw.get("required", True)),
                "rationale": str(raw.get("rationale") or ""),
                "conditions": [str(item) for item in raw.get("conditions") or []],
                "acceptance": [str(item) for item in raw.get("acceptance") or []],
                "semantic_key": str(raw.get("semantic_key") or role),
                "legacy_roles": [str(item) for item in raw.get("legacy_roles") or []],
                "status": status,
                "reason": reason,
                "actual_kinds": actual_kinds,
                "artifact_ids": artifact_ids,
            }
        )

    diagnostic_conflicts = list(previous.get("diagnostic_conflicts") or [])
    diagnostics = merge_diagnostic_statuses(
        previous.get("diagnostic_status") or {}, diagnostic_status or {},
        warnings=diagnostic_conflicts,
    )
    expected_diagnostics = tuple(
        dict.fromkeys(
            [str(item) for item in previous.get("planned_diagnostics") or []]
            + [str(item) for item in planned_diagnostics]
        )
    )
    missing_diagnostics = [
        name
        for name in expected_diagnostics
        if diagnostics.get(name) not in {"passed", "warning", "failed", "not_assessed"}
    ]
    outstanding_roles = [
        item["role"]
        for item in items
        if item["required"] and not _terminal_item_status(item["status"], item["tier"])
    ]
    relevant_artifacts = [_persisted_artifact(artifact) for artifact in owned]
    latest_assessment = dict(assessment or {})
    execution_accepted = (
        latest_assessment.get("r_executed") is True
        and latest_assessment.get("complete") is True
    )
    effective_workflow = (
        dict(workflow_state)
        if isinstance(workflow_state, Mapping) and workflow_state.get("run_id")
        else dict(previous.get("workflow_run") or {})
        if isinstance(previous.get("workflow_run"), Mapping)
        else {}
    )
    workflow_execution_accepted = _workflow_latest_execution_succeeded(effective_workflow)
    workflow_published = (
        not effective_workflow
        or isinstance(effective_workflow.get("phase_status"), Mapping)
        and effective_workflow["phase_status"].get("publish") == "completed"
    )
    complete = (
        execution_accepted
        and workflow_execution_accepted
        and workflow_published
        and not outstanding_roles
        and not missing_diagnostics
        and bool(items)
    )
    manifest = {
        "plan_id": plan_id,
        "portfolio_version": str(portfolio_version or previous.get("portfolio_version") or "1.0"),
        "complete": complete,
        "validation_scope": "artifact_structure",
        "scientific_review_status": "not_reviewed",
        "items": items,
        "outstanding_roles": outstanding_roles,
        "planned_diagnostics": list(expected_diagnostics),
        "diagnostic_status": diagnostics,
        "diagnostic_conflicts": diagnostic_conflicts,
        "missing_diagnostics": missing_diagnostics,
        "artifacts": relevant_artifacts,
        "latest_assessment": latest_assessment,
        "created_at": str(previous.get("created_at") or _now()),
        "updated_at": _now(),
    }
    if effective_workflow:
        manifest["workflow_run"] = deepcopy(effective_workflow)
    return manifest


def validation_evidence_from_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    dataset_id: str,
    plan_id: str,
) -> ManifestValidationEvidence:
    """Revalidate persisted artifact receipts for ResultValidator evidence."""

    manifest = manifest or {}
    if str(manifest.get("plan_id") or "") != plan_id:
        return ManifestValidationEvidence(artifacts=(), diagnostic_status={}, r_executed=False)
    artifacts: list[dict[str, Any]] = []
    for persisted in manifest.get("artifacts") or []:
        if not isinstance(persisted, Mapping):
            continue
        metadata = (
            deepcopy(dict(persisted.get("metadata") or {}))
            if isinstance(persisted.get("metadata"), Mapping)
            else {}
        )
        if (
            str(metadata.get("dataset_id") or "") != dataset_id
            or str(metadata.get("plan_id") or "") != plan_id
        ):
            continue
        actual_kind = str(persisted.get("type") or persisted.get("kind") or "").strip()
        artifact = {
            "artifact_id": str(persisted.get("artifact_id") or ""),
            "turn_id": str(persisted.get("turn_id") or ""),
            "type": actual_kind,
            "kind": actual_kind,
            "name": str(persisted.get("name") or ""),
            "path": str(persisted.get("path") or ""),
            "mime_type": str(persisted.get("mime_type") or ""),
            "source_tool": str(persisted.get("source_tool") or ""),
            "job_id": str(persisted.get("job_id") or ""),
            "metadata": metadata,
        }
        if artifact_has_trusted_receipt(
            artifact,
            expected_kind=actual_kind,
            verify_file=True,
        ):
            artifacts.append(artifact)
    backed_diagnostics = merge_diagnostic_statuses(*(
        item["metadata"].get("diagnostic_status") or item["metadata"].get("diagnostics") or {}
        for item in artifacts
    ))
    diagnostics = merge_diagnostic_statuses(backed_diagnostics, {
        key: value for key, value in dict(manifest.get("diagnostic_status") or {}).items()
        if key in backed_diagnostics
    })
    latest_assessment = manifest.get("latest_assessment")
    execution_accepted = (
        isinstance(latest_assessment, Mapping)
        and latest_assessment.get("r_executed") is True
        and _workflow_latest_execution_succeeded(
            manifest.get("workflow_run")
            if isinstance(manifest.get("workflow_run"), Mapping)
            else None
        )
    )
    return ManifestValidationEvidence(
        artifacts=tuple(artifacts),
        diagnostic_status=diagnostics,
        r_executed=bool(artifacts) and execution_accepted,
    )
