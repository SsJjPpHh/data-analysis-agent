"""Missing-value diagnostics must not silently change the scientific method."""

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import ConversationPlanDraft, RequiredOutput, ResolvedInput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.analysis.planner import AnalysisPlanner


PROFILE = {
    "dataset_id": "synthetic-welch", "rows": 120,
    "columns": [
        {"name": "y", "semantic_type": "numeric", "missing_count": 0, "missing_pct": 0.0},
        {"name": "arm", "semantic_type": "categorical", "levels": ["A", "B"], "unique_count": 2, "missing_count": 0},
    ],
}
BASELINE = (RequiredOutput("welch_results", "table", label="Welch均值差及置信区间"),)
IMPUTATION_ROLES = {"pooled_estimates", "imputation_diagnostics", "complete_case_sensitivity"}


def _compile(intent, *, family="classical_statistics", scenario="两独立组Welch t检验", profile=PROFILE):
    return ScientificOutputPortfolioCompiler().compile(
        source="conversation", intent=intent, method_family=family, scenario=scenario,
        baseline_outputs=BASELINE, dataset_profile=profile, packages=("stats", "mice"),
    )


@pytest.mark.parametrize("intent", [
    "Welch t检验；缺失值/有限值诊断说明",
    "Welch t检验并报告缺失样本数及排除原因",
    "Welch t检验；检查缺失数据，采用完整案例",
    "Welch t检验，不插补；报告有效样本",
    "Welch t检验，不进行任何多重插补",
    "Welch t检验，无需插补，完整病例分析",
    "Welch t检验；MICE已禁用",
    "Welch t检验，不使用MICE，仅报告缺失样本",
    "Welch t检验；插补不是本次任务",
    "Welch t-test with missing-data and finite-value diagnostics",
    "Welch t-test; report missing samples and use complete cases",
    "Welch t-test without imputation",
    "Welch t-test; do not impute missing data",
    "Welch t-test; do not use MICE",
    "Welch t-test; imputation is not required",
    "Welch t-test; imputation will not be performed",
    "Welch t-test; imputation isn't necessary",
    "Welch t-test using an already imputed dataset",
    "Welch t-test in mice; report missing samples",
    "Welch t-test; check whether the mice package is installed",
])
@pytest.mark.parametrize("family", ["classical_statistics", "welch_t_test", "missing_data_and_imputation"])
def test_missing_checks_and_negated_imputation_never_require_pooled_estimates(intent, family):
    compiled = _compile(intent, family=family)
    assert compiled.capability_evidence[0] == "classical_statistics_and_mixed_models"
    assert not IMPUTATION_ROLES & {output.role for output in compiled.outputs}
    assert "welch_results" in {output.role for output in compiled.outputs}


@pytest.mark.parametrize("intent", [
    "使用MICE执行多重插补，并按Rubin规则合并估计",
    "执行多重插补，并比较完整病例敏感性结果",
    "不要单次插补，改用MICE多重插补",
    "Use MICE multiple imputation and pool the model estimates",
    "Perform multiple imputation with complete-case sensitivity analysis",
    "Do not use single imputation; perform multiple imputation",
    "Run MICE and pool the fitted model estimates",
    "执行MICE并按Rubin规则合并估计",
])
def test_positive_imputation_requests_keep_the_original_missing_data_portfolio(intent):
    compiled = _compile(intent)
    assert compiled.capability_evidence[0] == "missing_data_and_imputation"
    assert IMPUTATION_ROLES <= {output.role for output in compiled.outputs}


@pytest.mark.parametrize("template_id", ["missing_imputation", "multiple_imputation_pooling"])
def test_actual_imputation_templates_keep_catalog_portfolio(template_id):
    template = AnalysisCatalog().get(template_id)
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template", template_id=template.id, intent=template.title,
        method_family=template.family, scenario=template.scenario,
        baseline_outputs=template.required_outputs, dataset_profile=PROFILE,
    )
    assert compiled.capability_evidence[0] == "missing_data_and_imputation"
    covered = {output.role for output in compiled.outputs} | {
        alias for output in compiled.outputs for alias in output.legacy_roles
    }
    assert {output.role for output in template.required_outputs} <= covered
    assert len(compiled.outputs) <= len(template.required_outputs)


def test_existing_clinical_missing_data_override_is_preserved():
    template = AnalysisCatalog().get("trial_missing_tipping_point")
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template", template_id=template.id, intent=template.title,
        method_family=template.family, scenario=template.scenario,
        baseline_outputs=template.required_outputs, dataset_profile=PROFILE,
    )
    assert compiled.capability_evidence[0] == "clinical_trial_analysis"


class _LocalCapabilities:
    def resolve(self, queries, method_family):
        # Availability of mice is not evidence that the user requested MI.
        return ("stats", "mice")


def test_actual_conversation_planner_freezes_welch_without_imputation_outputs():
    draft = ConversationPlanDraft(
        intent="Welch t检验；缺失值/有限值诊断说明", category="基础统计",
        method_family="welch_t_test", scenario="A与B两个独立组的均值比较",
        analysis_inputs=(
            ResolvedInput("outcome", "结局变量", "column", "y", "user"),
            ResolvedInput("group", "分组变量", "column", "arm", "user"),
            ResolvedInput("analysis_method", "分析方法", "specification", "welch", "user"),
        ),
        package_queries=("stats",), diagnostics=("缺失值/有限值检查",), required_outputs=BASELINE,
    )
    plan = AnalysisPlanner(AnalysisCatalog(), _LocalCapabilities()).from_conversation(draft, PROFILE)
    assert plan.resolved_inputs["analysis_method"].value == "welch"
    assert plan.variable_bindings == {"结局变量": "y", "分组变量": "arm"}
    assert "classical_statistics_and_mixed_models" in plan.capability_evidence
    assert not IMPUTATION_ROLES & {output.role for output in plan.output_portfolio}
    assert "welch_results" in {output.role for output in plan.output_portfolio}


def test_observed_missing_values_alone_do_not_request_imputation():
    profile = {**PROFILE, "columns": [
        {**PROFILE["columns"][0], "missing_count": 12, "missing_pct": 10.0}, PROFILE["columns"][1],
    ]}
    compiled = _compile("Welch t检验；报告缺失值，完整案例分析", profile=profile)
    assert not IMPUTATION_ROLES & {output.role for output in compiled.outputs}
