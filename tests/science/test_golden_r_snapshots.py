"""Golden R snapshots for the four high-frequency research templates.

These tests freeze a catalog plan against fixture data and assert that
artifact roles stay stable. They do not introduce hardcoded production
handlers; group-omics and free-form analysis still go through LLM-written R.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.tools.research import RToolsMixin
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.protocol import DatasetRef, RRunRequest


try:
    host_environment = HostREnvironment.locate()
    host_verified = host_environment.prepare()
    rscript_bin = str(host_verified.rscript)
    runtime_env = host_environment.process_environment(verified=host_verified)
except REnvironmentError:
    rscript_bin = None
    runtime_env = {}

local_rscript_required = pytest.mark.skipif(
    rscript_bin is None,
    reason="Local R is unavailable",
)

GOLDEN_TEMPLATE_IDS = (
    "data_quality",
    "publication_table",
    "regression_glm",
    "survival_km_cox",
)


def _fixture_csv(path: Path) -> Path:
    path.write_text(
        "\n".join(
            [
                "id,age,sex,arm,outcome,time,event",
                "s1,42,F,A,0,12,0",
                "s2,55,M,B,1,8,1",
                "s3,61,F,A,1,20,1",
                "s4,37,M,B,0,15,0",
                "s5,48,F,A,0,18,0",
                "s6,70,M,B,1,6,1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _resolved(input_id: str, kind: str, value, label: str = "") -> ResolvedInput:
    return ResolvedInput(
        input_id=input_id,
        label=label or input_id,
        kind=kind,  # type: ignore[arg-type]
        value=value,
        provenance="dataset",
    )


def _plan_for(template_id: str, dataset_id: str) -> AnalysisPlan:
    template = AnalysisCatalog().get(template_id)
    resolved: dict[str, ResolvedInput] = {}
    if template_id == "publication_table":
        resolved = {
            "group": _resolved("group", "column", "arm", "分组变量"),
            "continuous_variables": _resolved(
                "continuous_variables", "columns", ["age"], "连续基线变量"
            ),
            "categorical_variables": _resolved(
                "categorical_variables", "columns", ["sex"], "分类基线变量"
            ),
        }
    elif template_id == "regression_glm":
        resolved = {
            "outcome": _resolved("outcome", "column", "outcome", "结局变量"),
            "exposure": _resolved("exposure", "column", "arm", "主要暴露变量"),
            "covariates": _resolved("covariates", "columns", ["age", "sex"], "协变量"),
        }
    elif template_id == "survival_km_cox":
        resolved = {
            "followup_time": _resolved("followup_time", "column", "time", "随访时间"),
            "event": _resolved("event", "column", "event", "事件状态"),
            "exposure": _resolved("exposure", "column", "arm", "分组/暴露变量"),
        }
    elif template_id == "data_quality":
        resolved = {
            "outcome": _resolved("outcome", "column", "outcome", "结局候选变量"),
            "exposure": _resolved("exposure", "column", "arm", "分组/暴露候选变量"),
        }
    return AnalysisPlan(
        plan_id=f"golden-{template_id}",
        source="template",
        dataset_id=dataset_id,
        intent=template.title,
        category=template.category,
        method_family=template.family,
        scenario=template.scenario,
        variable_bindings={},
        packages=template.packages,
        prerequisites=template.prerequisites,
        assumptions=template.assumptions,
        diagnostics=template.diagnostics,
        required_outputs=template.required_outputs,
        resource_class=template.resource_class,
        resolved_inputs=resolved,
        template_id=template.id,
        workspace_generation="golden-generation",
        profile_revision="golden-profile",
    )


def _snapshot_code(plan: AnalysisPlan) -> str:
    lines = [
        f'invisible(baa_plan_input("{input_id}"))'
        for input_id in plan.resolved_inputs
    ]
    figure_prelude = False
    for output in plan.required_outputs:
        role = output.role
        if output.kind == "table":
            lines.append(
                f'baa_table("{role}", data.frame(role = {role!r}, n = nrow(dataset)), '
                f"metadata = list(role = {role!r}))"
            )
        elif output.kind == "figure":
            if not figure_prelude:
                lines.append(
                    "golden_plot <- ggplot(dataset, aes(x = age, y = as.numeric(outcome))) + "
                    "geom_point() + labs(x = \"Age\", y = \"Outcome\")"
                )
                figure_prelude = True
            lines.append(
                f'baa_plot("{role}", golden_plot, metadata = list(role = {role!r}))'
            )
        elif output.kind == "text":
            lines.append(
                f'baa_text("{role}", {output.label or role!r}, metadata = list(role = {role!r}))'
            )
        elif output.kind == "model":
            lines.append(
                "golden_fit <- stats::glm(outcome ~ age, data = dataset, family = binomial())"
            )
            lines.append(
                f'baa_model_summary("{role}", golden_fit, metadata = list(role = {role!r}))'
            )
    return "\n".join(lines)


class _StampAgent(RToolsMixin):
    def __init__(self, plan: AnalysisPlan):
        self._analysis_plan = plan
        self._research_workspace_generation = plan.workspace_generation


@local_rscript_required
@pytest.mark.parametrize("template_id", GOLDEN_TEMPLATE_IDS)
def test_golden_r_snapshot_keeps_catalog_roles(tmp_path, template_id):
    data_path = _fixture_csv(tmp_path / f"{template_id}.csv")
    plan = _plan_for(template_id, dataset_id=template_id)
    expected_roles = {(item.kind, item.role) for item in plan.required_outputs}
    assert expected_roles, f"{template_id} has no required outputs"

    executed = "\n".join(
        (
            RToolsMixin._worker_compatibility_prelude(),
            RToolsMixin._frozen_input_prelude(plan),
            RToolsMixin._frozen_user_code_block(_snapshot_code(plan), plan),
            RToolsMixin._frozen_input_epilogue(plan),
        )
    )
    artifacts_dir = tmp_path / template_id
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(
        RRunRequest(
            job_id=f"golden-{template_id}",
            dataset=DatasetRef(
                dataset_id=template_id,
                path=str(data_path),
                format="csv",
                name=data_path.name,
            ),
            code=executed,
            artifacts_dir=str(artifacts_dir),
            timeout_seconds=90,
        )
    )
    assert result.ok is True, f"{template_id}: {result.error}\n{result.stderr}"

    agent = _StampAgent(plan)
    agent._attach_analysis_plan_metadata(
        result,
        template_id,
        job_id=f"golden-{template_id}",
        artifacts_root=str(artifacts_dir),
    )
    observed = {
        (artifact.kind, str((artifact.metadata or {}).get("role") or ""))
        for collection in (result.tables, result.figures, result.texts, result.models)
        for artifact in collection
    }
    missing = expected_roles - observed
    assert not missing, f"{template_id} missing roles: {sorted(missing)}\nobserved={sorted(observed)}"


def test_golden_templates_are_user_visible_catalog_entries():
    catalog = AnalysisCatalog()
    for template_id in GOLDEN_TEMPLATE_IDS:
        template = catalog.get(template_id)
        assert template.visibility == "user"
        assert template.required_outputs
        assert template.id == template_id
