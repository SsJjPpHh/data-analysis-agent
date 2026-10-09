"""Read-only compatibility projections for a frozen analysis plan.

Execution code consumes the plan itself. These projections exist only for
browser/result compatibility and must never be parsed back into an execution
policy.
"""

from __future__ import annotations

from typing import Any

from agent.analysis.models import AnalysisPlan, RequiredOutput


def _output_payload(output: RequiredOutput) -> dict[str, Any]:
    return {
        "role": output.role,
        "kind": output.kind,
        "required": output.required,
        "label": output.label or output.role,
        "tier": output.tier,
        "source": output.source,
        "rationale": output.rationale,
        "conditions": list(output.conditions),
        "acceptance": list(output.acceptance),
        "semantic_key": output.semantic_key or output.role,
        "legacy_roles": list(output.legacy_roles),
    }


def plan_browser_projection(plan: AnalysisPlan, *, plan_id: str = "") -> dict[str, Any]:
    """Project one immutable plan into the legacy browser contract shape.

    ``required_outputs`` remains for older clients. ``output_portfolio`` is
    the canonical, lossless representation. The explicit floor marker keeps
    callers from treating historical template outputs as a maximum.
    """

    portfolio = [_output_payload(item) for item in plan.output_portfolio]
    required = [item for item in portfolio if item["required"]]
    min_tables = sum(item["kind"] == "table" for item in required)
    min_figures = sum(item["kind"] == "figure" for item in required)
    effective_plan_id = str(plan_id or plan.plan_id)
    # Imported lazily to keep the core model/projection dependency acyclic.
    from agent.analysis.workflows import default_workflow_registry, workflow_run_spec_projection

    workflow_spec = default_workflow_registry().compile(plan)
    return {
        "id": plan.template_id,
        "plan_id": effective_plan_id,
        "source": plan.source,
        "title": plan.intent,
        "category": plan.category,
        "workflow": plan.method_family,
        "package_names": list(plan.packages),
        "variable_bindings": dict(plan.variable_bindings),
        "portfolio_version": plan.portfolio_version,
        "capability_evidence": list(plan.capability_evidence),
        "output_portfolio": portfolio,
        "workflow_run_spec": workflow_run_spec_projection(workflow_spec),
        "template_contract": {
            "schema_version": "4.1.0",
            "applicability": {
                "prerequisites": list(plan.prerequisites),
                "variable_roles": [item.label for item in plan.resolved_inputs.values()],
            },
            "methodology": {
                "assumptions": list(plan.assumptions),
                "diagnostics": list(plan.diagnostics),
                "interpretation_requirements": ["报告效应量或不确定性", "说明数据和方法限制"],
            },
            "output_contract": {
                "baseline_is_floor": True,
                "portfolio_version": plan.portfolio_version,
                "min_tables": min_tables,
                "min_figures": min_figures,
                "required_outputs": [item["label"] for item in required],
                "required_output_roles": [item["role"] for item in required],
                "output_portfolio": portfolio,
                "requires_diagnostics": bool(plan.diagnostics),
                "requires_publication_interpretation": True,
            },
            "execution": {
                "authority": "scientific_workflow_run_spec",
                "cost": "high" if plan.resource_class == "extended" else "standard",
                "priority": "scientific_output_portfolio",
                "label": "后端冻结分析计划",
            },
            "fallback_packages": [],
        },
    }
