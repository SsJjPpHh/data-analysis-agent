"""Trusted projections for workflow execution and model instructions."""

from __future__ import annotations

from typing import Any

from agent.analysis.workflows.contracts import WorkflowRunSpec


def workflow_run_spec_projection(spec: WorkflowRunSpec) -> dict[str, Any]:
    """Return a path-free, JSON-safe execution contract."""
    return {
        "run_id": spec.run_id,
        "source": spec.source,
        "workflow_id": spec.workflow_id,
        "variant": spec.variant,
        "template_id": spec.template_id,
        "dataset_id": spec.dataset_id,
        "source_plan_id": spec.source_plan_id,
        "dataset_profile": dict(spec.dataset_profile),
        "contract_signature": spec.definition.contract_signature,
        "data_contract": {
            "shape": spec.data_contract.shape,
            "formats": list(spec.data_contract.formats),
            "critical_inputs": list(spec.data_contract.critical_inputs),
            "object_inputs": list(spec.data_contract.object_inputs),
            "asset_inputs": list(spec.data_contract.asset_inputs),
            "column_inputs": list(spec.data_contract.column_inputs),
        },
        "package_requirement": {
            "primary": spec.package_requirement.primary,
            "alternatives": list(spec.package_requirement.alternatives),
            "candidates": list(spec.package_requirement.candidates),
            "queries": list(spec.package_requirement.queries),
        },
        "steps": [
            {
                "step_id": step.step_id,
                "phase": step.phase,
                "implementation": step.implementation,
                "depends_on": list(step.depends_on),
                "consumes": list(step.consumes),
                "produces": list(step.produces),
                "checks": list(step.checks),
            }
            for step in spec.steps
        ],
        "artifact_roles": [item.role for item in spec.artifacts],
        "resource_hints": list(spec.resource_hints),
    }


def workflow_system_section(spec: WorkflowRunSpec) -> str:
    """Render the deterministic workflow stages into the model contract."""
    step_lines = "\n".join(
        f"- {step.phase}: {step.implementation}; checks={', '.join(step.checks) or 'none'}; "
        f"produces={', '.join(step.produces) or 'none'}"
        for step in spec.steps
    )
    contract = spec.data_contract
    packages = spec.package_requirement
    profile = spec.dataset_profile
    scientific = profile.get("scientific") if isinstance(profile.get("scientific"), dict) else {}
    modality = str(profile.get("modality") or scientific.get("modality") or "unknown")
    representation = str(
        profile.get("representation")
        or scientific.get("representation")
        or profile.get("object_kind")
        or "unknown"
    )
    stage = str(profile.get("processing_stage") or scientific.get("processing_stage") or "unknown")
    asset_formats = profile.get("asset_formats") if isinstance(profile.get("asset_formats"), list) else []
    return (
        "\n\n[SCIENTIFIC WORKFLOW RUN SPEC]\n"
        f"Run ID: {spec.run_id}; workflow={spec.workflow_id}; variant={spec.variant}; "
        f"source={spec.source}\n"
        f"Data contract: shape={contract.shape}; formats={', '.join(contract.formats)}; "
        f"critical inputs={', '.join(contract.critical_inputs) or 'none'}; "
        f"object assets={', '.join(contract.asset_inputs) or 'none'}\n"
        f"Active dataset: modality={modality}; representation={representation}; processing_stage={stage}; "
        f"asset formats={', '.join(str(item) for item in asset_formats) or 'unknown'}\n"
        f"Package order: primary={packages.primary or 'stats'}; alternatives="
        f"{', '.join(packages.alternatives) or 'none'}\n"
        "Execute the shared stages in order. Do not skip preflight or silently flatten an R/scientific "
        "object into a metadata table. A failed stage must be repaired or reported as incomplete; later "
        "stages cannot erase that failure.\n"
        f"{step_lines}\n"
        "The publish stage is complete only when the server-side portfolio validator accepts every core "
        "artifact and all planned diagnostics have an explicit status.\n"
        "[END SCIENTIFIC WORKFLOW RUN SPEC]"
    )
