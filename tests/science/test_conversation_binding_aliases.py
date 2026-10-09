"""Regression fixtures from the real missing-package conversation request."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import ConversationPlanDraft, RequiredOutput, ResolvedInput
from agent.analysis.planner import AnalysisPlanner
from agent.analysis.workflows import default_workflow_registry, validate_workflow_run_spec
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.protocol import DatasetRef, RRunRequest
from tests.science.test_method_numerics import backend  # noqa: F401


# The typed IDs, dependency parents, CI and estimator below reproduce the
# set_analysis_plan dictionary in flow-final/provider-requests.json. Only the
# dataset identity/profile are independent synthetic fixtures.
REQUEST = {
    "intent": "Compare control vs intervention groups on score using Cohen's d standardized mean difference with 95% CI (effsize::cohen.d)",
    "category": "基础统计",
    "method_family": "standardized_mean_difference",
    "scenario": "Two-group between-subjects comparison of a continuous outcome across control and intervention; Cohen's d with 95% CI using effsize::cohen.d.",
    "variable_bindings": {"outcome": "score", "group": "group"},
    "analysis_inputs": [
        {
            "id": "outcome_score",
            "label": "Continuous outcome: score",
            "kind": "column",
            "value": "score",
            "provenance": "user",
            "confidence": 1,
        },
        {
            "id": "group_var",
            "label": "Grouping variable: group",
            "kind": "column",
            "value": "group",
            "provenance": "user",
            "confidence": 1,
        },
        {
            "id": "group_control",
            "label": "Reference group level: control",
            "kind": "level",
            "value": "control",
            "provenance": "user",
            "confidence": 1,
            "depends_on": "group_var",
        },
        {
            "id": "group_intervention",
            "label": "Comparison group level: intervention",
            "kind": "level",
            "value": "intervention",
            "provenance": "user",
            "confidence": 1,
            "depends_on": "group_var",
        },
        {
            "id": "ci_level",
            "label": "Confidence level for the effect-size interval",
            "kind": "parameter",
            "value": 0.95,
            "provenance": "user",
            "confidence": 1,
        },
        {
            "id": "effect_size_method",
            "label": "Effect-size estimator: Cohen's d via effsize::cohen.d",
            "kind": "specification",
            "value": "effsize::cohen.d",
            "provenance": "user",
            "confidence": 1,
        },
    ],
    "package_queries": ["effsize", "cohen.d", "effect size standardized mean difference"],
    "required_outputs": [
        {"role": "main_results", "kind": "table", "label": "Cohen's d with 95% CI and group descriptives"},
        {
            "role": "interpretation",
            "kind": "text",
            "label": "Interpretation of the standardized mean difference",
        },
    ],
}
PROFILE = {
    "dataset_id": "conversation-alias-fixture",
    "format": "csv",
    "input_shape": "tabular",
    "rows": 8,
    "columns": [
        {"name": "score", "semantic_type": "numeric", "unique_count": 8},
        {"name": "alternate_score", "semantic_type": "numeric", "unique_count": 8},
        {"name": "group", "semantic_type": "categorical", "unique_count": 2},
    ],
    "local_semantics": {"levels": {"group": ["control", "intervention"]}},
}


class DeclaredPackageResolver:
    def resolve(self, queries, method_family):
        assert tuple(queries) == tuple(REQUEST["package_queries"])
        assert method_family == REQUEST["method_family"]
        # This is a compiler fixture, not a claim that effsize is installed.
        return ("effsize",)


def compile_request(request=None):
    payload = copy.deepcopy(REQUEST if request is None else request)
    draft = ConversationPlanDraft(
        intent=payload["intent"],
        category=payload["category"],
        method_family=payload["method_family"],
        scenario=payload["scenario"],
        variable_bindings=payload["variable_bindings"],
        analysis_inputs=tuple(
            ResolvedInput(
                input_id=item["id"],
                label=item["label"],
                kind=item["kind"],
                value=item["value"],
                provenance=item["provenance"],
                confidence=item.get("confidence", 1),
                depends_on=item.get("depends_on", ""),
            )
            for item in payload["analysis_inputs"]
        ),
        package_queries=tuple(payload["package_queries"]),
        required_outputs=tuple(RequiredOutput(**item) for item in payload["required_outputs"]),
    )
    return AnalysisPlanner(AnalysisCatalog(), DeclaredPackageResolver()).from_conversation(draft, PROFILE)


def test_real_request_freezes_canonical_bindings_and_original_dependencies():
    plan = compile_request()
    for canonical, alias, column in (("outcome", "outcome_score", "score"), ("group", "group_var", "group")):
        assert plan.resolved_inputs[canonical].value == plan.resolved_inputs[alias].value == column
        assert plan.resolved_inputs[canonical].kind == "column"
        assert plan.resolved_inputs[canonical].provenance == "user"
        assert plan.resolved_inputs[canonical].confidence == 1
    assert plan.resolved_inputs["group_control"].depends_on == "group_var"
    assert plan.resolved_inputs["group_intervention"].depends_on == "group_var"
    assert plan.resolved_inputs["ci_level"].value == pytest.approx(0.95)
    assert plan.resolved_inputs["effect_size_method"].value == "effsize::cohen.d"
    assert plan.packages == ("effsize",)
    spec = default_workflow_registry().compile(plan, dataset_profile=PROFILE)
    assert set(spec.data_contract.critical_inputs) <= set(spec.resolved_inputs)
    report = validate_workflow_run_spec(spec, PROFILE)
    assert report.ready, report.issues


def test_alias_inherits_verified_evidence_without_promoting_confidence():
    request = copy.deepcopy(REQUEST)
    request["analysis_inputs"][0].update(provenance="inferred", confidence=0.72)
    plan = compile_request(request)
    assert plan.resolved_inputs["outcome"].provenance == "inferred"
    assert plan.resolved_inputs["outcome"].confidence == pytest.approx(0.72)


@pytest.mark.parametrize(
    "kind,value", [("column", "alternate_score"), ("parameter", 0.3), ("specification", "score")]
)
@pytest.mark.parametrize("label", ["Other typed input", "outcome"])
def test_conflicting_existing_canonical_input_fails_closed(kind, value, label):
    request = copy.deepcopy(REQUEST)
    request["analysis_inputs"].append(
        {"id": "outcome", "label": label, "kind": kind, "value": value, "provenance": "user"}
    )
    with pytest.raises(ValueError, match="Conflicting analysis role binding"):
        compile_request(request)


def test_exact_alias_label_cannot_hide_a_conflicting_canonical_id():
    request = copy.deepcopy(REQUEST)
    request["analysis_inputs"][0]["label"] = "outcome"
    request["analysis_inputs"].append(
        {
            "id": "outcome",
            "label": "Different endpoint",
            "kind": "column",
            "value": "alternate_score",
            "provenance": "user",
        }
    )
    with pytest.raises(ValueError, match="Conflicting analysis role binding"):
        compile_request(request)


def test_alias_does_not_guess_an_absent_legacy_role():
    request = copy.deepcopy(REQUEST)
    request["variable_bindings"] = {}
    plan = compile_request(request)
    assert "outcome" not in plan.resolved_inputs
    assert "group" not in plan.resolved_inputs
    report = validate_workflow_run_spec(
        default_workflow_registry().compile(plan, dataset_profile=PROFILE), PROFILE
    )
    assert report.ready, report.issues


def test_unrecognized_legacy_role_keeps_its_explicit_column():
    request = copy.deepcopy(REQUEST)
    request["variable_bindings"] = {"primary_measure": "score", "group": "group"}
    request["analysis_inputs"][0].update(id="primary_measure_score", label="primary_measure")
    plan = compile_request(request)
    spec = default_workflow_registry().compile(plan, dataset_profile=PROFILE)
    assert set(spec.data_contract.critical_inputs) <= set(spec.resolved_inputs)
    assert plan.resolved_inputs["primary_measure_score"].value == "score"


def test_strict_guard_still_requires_added_canonical_inputs():
    plan = compile_request()
    result = BindingHelpersMixin._frozen_binding_report(
        'baa_text("probe",paste(baa_plan_input("outcome_score"),baa_plan_input("group_var")))',
        plan,
        PROFILE,
    )
    assert result["status"] != "passed"
    assert {"outcome", "group"} <= set(result["missing_inputs"])


def test_aliases_drive_real_r_selection_and_confirmed_numeric_reference(backend, tmp_path):
    plan = compile_request()
    script = r"""
outcome_alias <- baa_plan_input("outcome_score")
group_alias <- baa_plan_input("group_var")
outcome_col <- baa_plan_input("outcome")
group_col <- baa_plan_input("group")
if (!identical(outcome_col,outcome_alias) || !identical(group_col,group_alias)) stop("Alias identity changed")
ref_level <- baa_plan_input("group_control")
comparison_level <- baa_plan_input("group_intervention")
ci <- baa_plan_input("ci_level")
method <- baa_plan_input("effect_size_method")
control <- dataset[[outcome_col]][dataset[[group_col]]==ref_level]
intervention <- dataset[[outcome_alias]][dataset[[group_alias]]==comparison_level]
baa_text("alias_probe",jsonlite::toJSON(list(control_mean=mean(control),intervention_mean=mean(intervention),difference=mean(intervention)-mean(control),ci=ci,method=method),auto_unbox=TRUE))
"""
    validation = BindingHelpersMixin._frozen_binding_report(script, plan, PROFILE)
    assert validation["status"] == "passed", validation
    path = tmp_path / "alias.csv"
    path.write_text(
        "score,group\n1,control\n2,control\n3,control\n4,control\n4,intervention\n5,intervention\n6,intervention\n7,intervention\n",
        encoding="utf-8",
    )
    code = "\n".join(
        (
            BindingHelpersMixin._frozen_input_prelude(plan),
            BindingHelpersMixin._frozen_user_code_block(script, plan),
            BindingHelpersMixin._frozen_input_epilogue(plan),
        )
    )
    result = backend.run_code(
        RRunRequest(
            job_id="conversation-alias-reference",
            dataset=DatasetRef(plan.dataset_id, str(path), "csv", path.name),
            code=code,
            artifacts_dir=str(tmp_path / "artifacts"),
            timeout_seconds=30,
        )
    )
    assert result.ok, result.error + "\n" + result.stderr
    probe = json.loads(
        Path(next(item.path for item in result.texts if item.name == "alias_probe")).read_text(
            encoding="utf-8"
        )
    )
    assert probe["control_mean"] == pytest.approx(2.5)
    assert probe["intervention_mean"] == pytest.approx(5.5)
    assert probe["difference"] == pytest.approx(3)
    assert probe["ci"] == pytest.approx(0.95)
    assert probe["method"] == "effsize::cohen.d"
    # This probe validates binding/numeric propagation, not an effsize run or
    # model acceptance. The parent owns the actual missing-package E2E.
