"""Regression examples for scientifically different template branches."""

from dataclasses import replace

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.workflows import default_workflow_registry, validate_workflow_run_spec


def profile(*columns):
    return {
        "dataset_id": "applicability-data", "format": "csv", "rows": 100,
        "columns": [
            {"name": name, "semantic_type": kind, "type": kind, "unique_count": 2 if kind == "categorical" else 80}
            for name, kind in columns
        ],
    }


def prepare(tid, data, values=None):
    return AnalysisPreparer(AnalysisCatalog()).prepare(tid, data, values)


def roles(plan):
    return {role for output in plan.required_outputs for role in (output.role, *output.legacy_roles)}


def test_group_comparison_requires_an_outcome_and_workflow_cannot_bypass_it():
    data = profile(("arm", "categorical"))
    result = prepare("group_tests", data)
    assert result.status == "needs_input"
    assert {q.input_id for q in result.questions} == {"continuous_outcomes", "categorical_outcomes"}
    ready = prepare("group_tests", profile(("arm", "categorical"), ("score", "numeric")))
    assert ready.status == "ready"
    assert "正态性/方差齐性检查" in ready.plan.diagnostics
    assert "小样本或稀疏单元格检查" not in ready.plan.diagnostics
    broken = replace(ready.plan, resolved_inputs={"group": ready.plan.resolved_inputs["group"]})
    registry = default_workflow_registry()
    spec = registry.compile(broken, dataset_profile=data)
    assert not validate_workflow_run_spec(spec, dataset_profile=data).ready


def test_ungrouped_table_one_does_not_demand_comparison_columns():
    result = prepare("publication_table", profile(("age", "numeric"), ("bmi", "numeric")))
    assert result.status == "ready"
    table = next(o for o in result.plan.required_outputs if o.role == "baseline_table")
    assert "总体描述" in table.acceptance
    assert not {"P 值", "SMD"} & set(table.acceptance)
    assert "连续变量分布检查" in result.plan.diagnostics
    assert "分类变量稀疏水平检查" not in result.plan.diagnostics


@pytest.mark.parametrize("tid,values,forbidden", [
    ("efa_factor", {"categorical_variables": ["q1", "q2", "q3"]}, {"sample_id", "dimension", "reverse_item_indicator"}),
    ("unsupervised_ml", {"data_object": ["q1", "q2", "q3"]}, {"sample_id", "external_validation_variable"}),
    ("psych_network", {"categorical_variables": ["q1", "q2", "q3"]}, {"group", "time_point_variable"}),
])
def test_methods_that_discover_structure_work_without_external_labels(tid, values, forbidden):
    result = prepare(tid, profile(*[(name, "numeric") for name in ("q1", "q2", "q3")]), values)
    assert result.status == "ready", result
    assert not forbidden & {q.input_id for q in result.questions}


def test_dag_without_mediator_does_not_require_a_mediation_result():
    result = prepare("dag_causal", profile(("exposure", "categorical"), ("outcome", "numeric")),
                     {"dag_specification": "dag { exposure [exposure]; outcome [outcome]; exposure -> outcome }"})
    assert result.status == "ready"
    assert "table_2" not in roles(result.plan)


def test_correlation_branch_does_not_force_agreement_analysis():
    data = profile(("x", "numeric"), ("y", "numeric"))
    result = prepare("correlation_agreement", data, {"continuous_variables": ["x", "y"]})
    assert result.status == "ready"
    assert "table_1" in roles(result.plan)
    assert not {"table_2", "table_3", "figure_1"} & roles(result.plan)


def test_gee_branch_has_no_random_effect_deliverables():
    data = profile(("score", "numeric"), ("time", "numeric"), ("id", "categorical"))
    result = prepare("mixed_repeated", data, {"outcome": "score", "time": "time", "subject_id": "id", "analysis_method": "gee"})
    assert result.status == "ready"
    assert "随机效应方差检查" not in result.plan.diagnostics
    assert not {"table_2", "text_1"} & roles(result.plan)


def test_cutpoint_survival_branch_requires_followup_and_event():
    data = profile(("marker", "numeric"), ("disease", "categorical"))
    inputs = {"measure": "marker", "outcome": "disease"}
    binary = prepare("clinical_cutpoint_maxstat", data, inputs)
    assert binary.status == "ready"
    assert "比例风险检验" not in binary.plan.diagnostics
    survival = prepare("clinical_cutpoint_maxstat", data, {**inputs, "analysis_method": "survival"})
    assert survival.status == "needs_input"
    assert {"followup_time", "event"} <= {q.input_id for q in survival.questions}


def test_survival_design_can_report_events_without_inventing_sample_size():
    result = prepare("survival_trial_design", profile(("age", "numeric")), {"effect_size": 0.75})
    assert result.status == "ready"
    assert "table_2" not in roles(result.plan)
    assert not any("Cox" in name or "比例风险检验" == name for name in result.plan.diagnostics)


def test_text_only_sentiment_does_not_claim_manual_validation():
    result = prepare("text_sentiment_analysis", profile(("text", "text")), {"text": "text"})
    assert result.status == "ready"
    assert "词典与人工标签对照" not in result.plan.diagnostics


def test_ora_requires_background_but_not_ranked_statistics():
    result = prepare("pathway_enrichment", profile(("gene", "text")), {
        "gene_feature_list": "gene", "species_id_type": "human ENTREZID", "background_gene_set": "assayed genes in study",
        "term_gene_mapping": ["pathway_a\tgene_a", "pathway_a\tgene_b"],
    })
    assert result.status == "ready", result
    assert "figure_3" not in roles(result.plan)
    assert "ranking_statistic" not in result.plan.resolved_inputs


@pytest.mark.parametrize("information_time", ["0.8, 0.4, 1", "0, 0.5, 1", "0.5, 0.9", "0.5, NaN, 1"])
def test_sequential_design_rejects_invalid_information_schedule(information_time):
    result = prepare("group_sequential", profile(("outcome", "numeric")), {
        "outcome": "outcome", "effect_size": 0.5, "information_time": information_time,
    })
    assert result.status == "needs_input"
    assert result.plan is None
    question = next(q for q in result.questions if q.input_id == "information_time")
    assert "严格递增" in question.description


def test_execution_preflight_rechecks_joint_inputs_from_a_frozen_plan():
    data = profile(("outcome", "numeric"))
    ready = prepare("group_sequential", data, {
        "outcome": "outcome", "effect_size": 0.5, "information_time": "c(0.5, 1)",
        "design_scope": "one_sided_normal_equal",
    })
    assert ready.status == "ready"
    inputs = dict(ready.plan.resolved_inputs)
    inputs["information_time"] = replace(inputs["information_time"], value="0.8, 0.4, 1")
    spec = default_workflow_registry().compile(replace(ready.plan, resolved_inputs=inputs), dataset_profile=data)
    report = validate_workflow_run_spec(spec, dataset_profile=data)
    assert not report.ready
    assert any(issue.code == "invalid_joint_inputs" for issue in report.issues)


def test_simon_design_requires_target_response_above_null_response():
    data = profile(("response", "categorical"))
    values = {"outcome": "response", "null_response_rate": 0.8, "target_response_rate": 0.2, "design_criterion": "optimal"}
    invalid = prepare("trial_simon_two_stage", data, values)
    assert invalid.status == "needs_input"
    assert {q.input_id for q in invalid.questions} == {"target_response_rate"}
    assert prepare("trial_simon_two_stage", data, {**values, "null_response_rate": 0.1}).status == "ready"


def test_one_observed_group_supports_description_but_not_comparisons():
    data = profile(("arm", "categorical"), ("age", "numeric"))
    data["columns"][0]["unique_count"] = 1
    descriptive = prepare("publication_table", data, {"group": "arm", "continuous_variables": ["age"]})
    assert descriptive.status == "ready"
    table = next(o for o in descriptive.plan.required_outputs if o.role == "baseline_table")
    assert "总体描述" in table.acceptance
    assert not {"P 值", "SMD"} & set(table.acceptance)
    assert not {"balance_table", "table_2", "table_4", "baseline_balance_plot"} & roles(descriptive.plan)
    assert descriptive.warnings
    comparison = prepare("group_tests", data, {"group": "arm", "continuous_outcomes": ["age"]})
    assert comparison.status == "needs_input"
    assert any(q.input_id == "group" and "两个有效分组" in q.description for q in comparison.questions)
