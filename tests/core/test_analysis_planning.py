from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ConversationPlanDraft, RequiredOutput, ResolvedInput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.analysis.planner import AnalysisPlanner
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.workflows import default_workflow_registry
import pytest


class FakeCapabilityResolver:
    def __init__(self, packages: tuple[str, ...]):
        self._packages = packages

    def resolve(self, queries: tuple[str, ...], method_family: str) -> tuple[str, ...]:
        return self._packages


def test_analysis_plan_freezes_dataset_and_method():
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abc123def456",
        intent="比较两组结局",
        category="基础统计",
        method_family="group_comparison",
        scenario="两独立组比较",
        variable_bindings={"outcome": "score", "group": "arm"},
        packages=("stats",),
        prerequisites=("分组至少两个水平",),
        assumptions=("独立观测",),
        diagnostics=("分布与方差检查",),
        required_outputs=(RequiredOutput("primary_table", "table"),),
        resource_class="standard",
    )

    assert plan.dataset_id == "abc123def456"
    assert plan.required_outputs[0].role == "primary_table"


def test_conversation_plan_uses_current_dataset_and_live_packages():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("survival",)))
    draft = ConversationPlanDraft(
        intent="比较两组生存结局",
        category="临床研究",
        method_family="survival_analysis",
        scenario="Kaplan-Meier 与 Cox",
        variable_bindings={"time": "days", "event": "status", "group": "arm"},
        package_queries=("survival",),
        prerequisites=("生存时间非负且结局编码明确",),
        assumptions=("删失机制可接受",),
        diagnostics=("比例风险检查",),
        required_outputs=(RequiredOutput("survival_table", "table"),),
    )
    profile = {
        "dataset_id": "abc123def456",
        "columns": [{"name": "days"}, {"name": "status"}, {"name": "arm"}],
    }

    plan = planner.from_conversation(draft, profile)

    assert plan.dataset_id == "abc123def456"
    assert plan.packages == ("survival",)


def test_conversation_plan_uses_same_typed_inputs_as_catalog_plans():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("pwr",)))
    draft = ConversationPlanDraft(
        intent="计算两组样本量",
        category="临床试验与证据合成",
        method_family="sample_size",
        scenario="两独立组均值比较",
        analysis_inputs=(
            ResolvedInput("outcome", "主要终点", "column", "score", "inferred", 0.9),
            ResolvedInput("effect_size", "效应大小", "parameter", 0.4, "user"),
            ResolvedInput("alpha", "检验水准", "parameter", 0.05, "default"),
        ),
        package_queries=("sample size",),
        diagnostics=("输入参数范围检查",),
        required_outputs=(RequiredOutput("power_table", "table"),),
    )
    profile = {
        "dataset_id": "abc123def456",
        "workspace_generation": "generation-1",
        "profile_revision": "profile-v1",
        "columns": [{"name": "score"}],
    }

    plan = planner.from_conversation(draft, profile)

    assert plan.resolved_inputs["effect_size"].value == 0.4
    assert plan.variable_bindings == {"主要终点": "score"}
    assert plan.workspace_generation == "generation-1"
    assert plan.profile_revision == "profile-v1"


def test_free_form_plan_requires_a_frozen_input_and_accepts_an_active_dataset_input():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    profile = {
        "dataset_id": "dataset-whole",
        "columns": [{"name": "score", "semantic_type": "numeric"}],
    }
    common = dict(
        intent="执行全数据质量检查",
        category="基础统计",
        method_family="data_import_cleaning_profiling",
        scenario="全数据集质量概览",
        diagnostics=("缺失与重复检查",),
        required_outputs=(RequiredOutput("quality_report", "table"),),
    )

    with pytest.raises(ValueError, match="frozen typed input"):
        planner.from_conversation(ConversationPlanDraft(**common), profile)

    plan = planner.from_conversation(
        ConversationPlanDraft(
            **common,
            analysis_inputs=(
                ResolvedInput(
                    "data_object",
                    "当前数据集",
                    "dataset",
                    "dataset-whole",
                    "inferred",
                ),
            ),
        ),
        profile,
    )
    run_spec = default_workflow_registry().compile(plan, dataset_profile=profile)

    assert plan.resolved_inputs["data_object"].value == "dataset-whole"
    assert run_spec.data_contract.critical_inputs == ("data_object",)

    unsupervised = planner.from_conversation(
        ConversationPlanDraft(
            intent="对整个数值矩阵执行主成分与无监督聚类探索",
            category="机器学习",
            method_family="multivariate_exploration_and_ordination",
            scenario="全矩阵无监督探索",
            analysis_inputs=(
                ResolvedInput(
                    "analysis_object",
                    "当前分析对象",
                    "object",
                    "dataset-whole",
                    "inferred",
                ),
            ),
            required_outputs=(RequiredOutput("ordination_plot", "figure"),),
        ),
        profile,
    )
    assert unsupervised.resolved_inputs["analysis_object"].value == "dataset-whole"


def test_conversation_plan_rejects_continuous_high_cardinality_group_role():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="按组比较结局",
        category="基础统计",
        method_family="group_comparison",
        scenario="独立组比较",
        variable_bindings={"group": "age", "outcome": "score"},
        required_outputs=(RequiredOutput("comparison", "table"),),
    )
    profile = {
        "dataset_id": "dataset-semantic",
        "columns": [
            {"name": "age", "semantic_type": "numeric", "unique_count": 80},
            {"name": "score", "semantic_type": "numeric", "unique_count": 70},
        ],
    }

    with pytest.raises(ValueError, match="group|分组"):
        planner.from_conversation(draft, profile)


def test_conversation_plan_rejects_covariate_set_overlapping_frozen_outcome():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="回归分析",
        category="基础统计",
        method_family="regression",
        scenario="多变量回归",
        analysis_inputs=(
            ResolvedInput("outcome", "结局", "column", "score", "user"),
            ResolvedInput("covariates", "协变量", "columns", ["score", "age"], "user"),
        ),
        required_outputs=(RequiredOutput("model", "model"),),
    )
    profile = {
        "dataset_id": "dataset-overlap",
        "columns": [
            {"name": "score", "semantic_type": "numeric", "unique_count": 70},
            {"name": "age", "semantic_type": "numeric", "unique_count": 60},
        ],
    }

    with pytest.raises(ValueError, match="overlap|重叠"):
        planner.from_conversation(draft, profile)


def test_conversation_plan_rejects_distinct_scalar_roles_using_the_same_column():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="分析暴露与结局的回归关系",
        category="基础统计",
        method_family="regression",
        scenario="多变量回归",
        variable_bindings={"outcome": "score", "exposure": "score"},
        required_outputs=(RequiredOutput("model", "model"),),
    )
    profile = {
        "dataset_id": "dataset-scalar-overlap",
        "columns": [
            {"name": "score", "semantic_type": "numeric", "unique_count": 70},
        ],
    }

    with pytest.raises(ValueError, match="scalar role overlap"):
        planner.from_conversation(draft, profile)


def test_conversation_regression_cannot_hide_variable_roles_behind_dataset_binding():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="分析 x 对 y 的回归关系",
        category="基础统计",
        method_family="regression",
        scenario="x 与 y 的线性关系",
        analysis_inputs=(
            ResolvedInput(
                "data_object",
                "当前数据集",
                "dataset",
                "dataset-object-only",
                "inferred",
            ),
        ),
        required_outputs=(RequiredOutput("model", "model"),),
    )
    profile = {
        "dataset_id": "dataset-object-only",
        "columns": [
            {"name": "x", "semantic_type": "numeric", "unique_count": 50},
            {"name": "y", "semantic_type": "numeric", "unique_count": 50},
        ],
    }

    with pytest.raises(ValueError, match="dataset/object-only"):
        planner.from_conversation(draft, profile)


def test_conversation_role_requirement_uses_intent_even_if_family_claims_data_quality():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="建立线性模型分析两个变量之间的关系",
        category="基础统计",
        method_family="data_import_cleaning_profiling",
        scenario="拟合线性模型",
        analysis_inputs=(
            ResolvedInput(
                "data_object",
                "当前数据集",
                "dataset",
                "dataset-object-only",
                "inferred",
            ),
        ),
        required_outputs=(RequiredOutput("model", "model"),),
    )
    profile = {
        "dataset_id": "dataset-object-only",
        "columns": [
            {"name": "x", "semantic_type": "numeric", "unique_count": 50},
            {"name": "y", "semantic_type": "numeric", "unique_count": 50},
        ],
    }

    with pytest.raises(ValueError, match="dataset/object-only"):
        planner.from_conversation(draft, profile)


def test_conversation_plan_normalizes_a_chinese_role_to_an_accessor_safe_id():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="按心血管病状态比较基线",
        category="基础统计",
        method_family="group_comparison",
        scenario="两组描述性比较",
        variable_bindings={"分组变量": "cvd"},
        required_outputs=(RequiredOutput("comparison", "table"),),
    )
    profile = {
        "dataset_id": "dataset-chinese-role",
        "columns": [
            {"name": "cvd", "semantic_type": "categorical", "unique_count": 2}
        ],
    }

    plan = planner.from_conversation(draft, profile)
    run_spec = default_workflow_registry().compile(plan, dataset_profile=profile)

    assert "分组变量" not in plan.resolved_inputs
    assert plan.resolved_inputs["group"].value == "cvd"
    assert "group" in run_spec.data_contract.critical_inputs


def test_conversation_level_must_depend_on_a_column_and_match_observed_data():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    profile = {
        "dataset_id": "dataset-levels",
        "columns": [
            {"name": "arm", "semantic_type": "categorical", "unique_count": 2}
        ],
        "local_semantics": {"levels": {"arm": ["control", "treatment"]}},
    }

    def draft(level, depends_on="group"):
        return ConversationPlanDraft(
            intent="按组比较",
            category="基础统计",
            method_family="group_comparison",
            scenario="指定参照组",
            analysis_inputs=(
                ResolvedInput("group", "分组变量", "column", "arm", "user"),
                ResolvedInput(
                    "reference_level",
                    "参照水平",
                    "level",
                    level,
                    "user",
                    depends_on=depends_on,
                ),
            ),
            required_outputs=(RequiredOutput("comparison", "table"),),
        )

    plan = planner.from_conversation(draft("control"), profile)
    run_spec = default_workflow_registry().compile(plan, dataset_profile=profile)
    level_spec = next(
        item for item in run_spec.data_contract.inputs if item.id == "reference_level"
    )
    assert plan.resolved_inputs["reference_level"].depends_on == "group"
    assert level_spec.depends_on == "group"

    with pytest.raises(ValueError, match="Unknown level") as invalid_level:
        planner.from_conversation(draft("invented"), profile)
    assert "control" not in str(invalid_level.value)
    assert "treatment" not in str(invalid_level.value)
    with pytest.raises(ValueError, match="depends on an unresolved column"):
        planner.from_conversation(draft("control", depends_on="missing_parent"), profile)


def test_template_and_conversation_reject_invalid_core_power_parameters():
    profile = {"dataset_id": "power-data", "columns": []}
    preparer = AnalysisPreparer(AnalysisCatalog())

    with pytest.raises(ValueError, match="效应大小"):
        preparer.prepare("sample_size_power", profile, {"effect_size": "banana"})
    with pytest.raises(ValueError, match="把握度"):
        preparer.prepare(
            "sample_size_power",
            profile,
            {"effect_size": 0.4, "power": -99},
        )

    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("stats",)))
    draft = ConversationPlanDraft(
        intent="计算样本量",
        category="临床试验与证据合成",
        method_family="sample_size",
        scenario="两组设计",
        analysis_inputs=(
            ResolvedInput("effect_size", "效应大小", "parameter", "banana", "user"),
        ),
        required_outputs=(RequiredOutput("power_table", "table"),),
    )
    with pytest.raises(ValueError, match="effect_size|numeric"):
        planner.from_conversation(draft, profile)


def test_template_plan_consolidates_duplicate_baselines_and_adds_professional_outputs():
    catalog = AnalysisCatalog()
    profile = {
        "dataset_id": "abc123def456",
        "format": "csv",
        "rows": 180,
        "columns": [
            {
                "name": "age",
                "semantic_type": "numeric",
                "unique_count": 90,
                "missing_pct": 3.0,
            },
            {
                "name": "bmi",
                "semantic_type": "numeric",
                "unique_count": 120,
                "missing_pct": 4.0,
            },
            {
                "name": "sex",
                "semantic_type": "categorical",
                "unique_count": 2,
                "missing_pct": 0.0,
            },
            {
                "name": "arm",
                "semantic_type": "categorical",
                "unique_count": 2,
                "missing_pct": 0.0,
            },
        ],
    }

    plan = catalog.compile("publication_table", {}, profile)

    planned_roles = {item.role for item in plan.output_portfolio}
    assert "baseline_table" in planned_roles
    assert not {"table_2", "balance_table", "table_4", "table_5", "baseline_table_2"} & planned_roles
    baseline = next(item for item in plan.output_portfolio if item.role == "baseline_table")
    assert "P 值" in baseline.acceptance
    assert "SMD" in baseline.acceptance
    assert {
        "table_2",
        "balance_table",
        "table_4",
        "table_5",
        "baseline_table_2",
    } <= set(baseline.legacy_roles)
    assert {"baseline_balance_plot", "baseline_distribution_plot", "missingness_by_group_plot"} <= planned_roles
    assert any(item.tier == "recommended" for item in plan.output_portfolio)
    assert any(item.source == "capability" for item in plan.output_portfolio)


def test_every_catalog_baseline_role_remains_addressable_after_normalization():
    """Consolidation may merge artifacts, but it must never erase old capability."""

    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()

    for template in catalog.snapshot().templates:
        normalized = compiler.normalize_baseline(
            source="template",
            method_family=template.family,
            outputs=template.required_outputs,
        )
        addressable_roles = {
            role
            for output in normalized
            for role in (output.role, *output.legacy_roles)
        }
        original_roles = {output.role for output in template.required_outputs}

        assert original_roles <= addressable_roles, template.id


def test_every_catalog_template_keeps_table_figure_and_scientific_interpretation_outputs():
    """Every template is a complete research workflow, not only an R method call."""

    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()
    profile = {
        "dataset_id": "abc123def456",
        "format": "csv",
        "rows": 120,
        "columns": [
            {"name": "outcome", "semantic_type": "numeric", "missing_pct": 2.0},
            {"name": "group", "semantic_type": "categorical", "missing_pct": 0.0},
        ],
    }

    for template in catalog.snapshot().templates:
        portfolio = compiler.compile(
            source="template",
            template_id=template.id,
            intent=template.title,
            method_family=template.family,
            scenario=template.scenario,
            baseline_outputs=template.required_outputs,
            dataset_profile=profile,
            packages=template.packages,
        )
        kinds = {item.kind for item in portfolio.outputs}
        assert {"table", "figure", "text"} <= kinds, template.id


def test_trial_design_trial_analysis_protocol_and_meta_have_distinct_portfolios():
    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()
    profile = {
        "dataset_id": "abc123def456",
        "format": "csv",
        "rows": 120,
        "columns": [
            {"name": "outcome", "semantic_type": "numeric", "missing_pct": 0.0},
            {"name": "group", "semantic_type": "categorical", "missing_pct": 0.0},
        ],
    }

    expected_profiles = {
        "randomization_allocation": "clinical_trial_design",
        "rct_primary_analysis": "clinical_trial_analysis",
        "noninferiority_equivalence": "clinical_trial_analysis",
        "sap_protocol_stats": "clinical_trial_protocol",
        "pairwise_meta": "evidence_synthesis",
    }
    forbidden = {
        "randomization_allocation": {"evidence_forest_plot", "evidence_bias_diagnostic_plot"},
        "rct_primary_analysis": {"trial_design_summary", "evidence_forest_plot"},
        "noninferiority_equivalence": {"trial_design_summary", "evidence_forest_plot"},
        "sap_protocol_stats": {"trial_effect_estimates", "evidence_forest_plot"},
        "pairwise_meta": {"trial_design_summary", "trial_effect_estimates"},
    }

    for template_id, expected_profile in expected_profiles.items():
        template = catalog.get(template_id)
        compiled = compiler.compile(
            source="template",
            template_id=template.id,
            intent=template.title,
            method_family=template.family,
            scenario=template.scenario,
            baseline_outputs=template.required_outputs,
            dataset_profile=profile,
            packages=template.packages,
        )
        roles = {item.role for item in compiled.outputs}
        assert compiled.capability_evidence[0] == expected_profile, template_id
        assert {item.kind for item in compiled.outputs} >= {"table", "figure", "text"}
        assert not forbidden[template_id] & roles, template_id


@pytest.mark.parametrize(
    ("intent", "expected_role", "forbidden_role"),
    [
        ("对多个研究的效应量执行 Meta 分析并绘制森林图", "evidence_effect_table", "trial_design_summary"),
        ("计算两组研究的样本量和统计把握度", "trial_design_summary", "evidence_effect_table"),
        ("随机对照临床试验的样本量和把握度", "trial_design_summary", "trial_effect_estimates"),
        ("设计一项临床试验", "trial_design_summary", "trial_effect_estimates"),
        ("Design a clinical trial", "trial_design_summary", "trial_effect_estimates"),
        ("分析随机对照试验主要终点并报告治疗效应", "trial_effect_estimates", "trial_design_summary"),
        ("分析非劣效 RCT 主要终点和 CI", "trial_effect_estimates", "trial_design_summary"),
        ("analyze bioequivalence PK trial results", "trial_effect_estimates", "trial_design_summary"),
        ("撰写临床试验方案", "sap_analysis_framework", "trial_effect_estimates"),
    ],
)
def test_free_ai_disambiguates_the_legacy_trial_and_meta_family_from_intent(
    intent,
    expected_role,
    forbidden_role,
):
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="conversation",
        intent=intent,
        method_family="clinical_trials_power_meta_analysis",
        scenario=intent,
        baseline_outputs=(RequiredOutput("requested_result", "table"),),
        dataset_profile={
            "dataset_id": "abc123def456",
            "rows": 120,
            "columns": [
                {"name": "outcome", "semantic_type": "numeric", "missing_pct": 0.0},
                {"name": "group", "semantic_type": "categorical", "missing_pct": 0.0},
            ],
        },
    )

    roles = {item.role for item in compiled.outputs}
    assert expected_role in roles
    assert forbidden_role not in roles


@pytest.mark.parametrize(
    ("method_family", "expected_profile", "expected_role"),
    [
        ("clinical_trial_design", "clinical_trial_design", "trial_design_summary"),
        ("clinical_trial_analysis", "clinical_trial_analysis", "trial_effect_estimates"),
        ("clinical_trial_protocol", "clinical_trial_protocol", "sap_analysis_framework"),
    ],
)
def test_free_ai_explicit_trial_family_routes_without_intent_guessing(
    method_family, expected_profile, expected_role
):
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="conversation",
        intent="执行用户明确指定的方法",
        method_family=method_family,
        scenario="用户已明确统计阶段",
        baseline_outputs=(RequiredOutput("requested_result", "table"),),
        dataset_profile={"dataset_id": "abc123def456", "columns": []},
    )

    assert compiled.capability_evidence[0] == expected_profile
    assert expected_role in {item.role for item in compiled.outputs}


def test_cross_domain_templates_do_not_inherit_unrelated_survival_or_ml_outputs():
    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()
    profile = {
        "dataset_id": "abc123def456",
        "rows": 120,
        "columns": [
            {"name": "outcome", "semantic_type": "numeric", "missing_pct": 0.0},
            {"name": "group", "semantic_type": "categorical", "missing_pct": 0.0},
        ],
    }

    subgroup = catalog.get("clinical_subgroup_interaction")
    subgroup_outputs = compiler.compile(
        source="template",
        template_id=subgroup.id,
        intent=subgroup.title,
        method_family=subgroup.family,
        scenario=subgroup.scenario,
        baseline_outputs=subgroup.required_outputs,
        dataset_profile=profile,
    ).outputs
    subgroup_roles = {item.role for item in subgroup_outputs}
    assert any(item.kind == "table" for item in subgroup_outputs)
    assert "survival_curve" not in subgroup_roles

    ordination = catalog.get("multivariate_ordination")
    ordination_outputs = compiler.compile(
        source="template",
        template_id=ordination.id,
        intent=ordination.title,
        method_family=ordination.family,
        scenario=ordination.scenario,
        baseline_outputs=ordination.required_outputs,
        dataset_profile=profile,
    ).outputs
    ordination_roles = {item.role for item in ordination_outputs}
    assert any(item.label == "载荷表" for item in ordination_outputs)
    assert "ml_calibration_plot" not in ordination_roles


@pytest.mark.parametrize(
    ("template_id", "forbidden_roles"),
    [
        ("unsupervised_ml", {"ml_calibration_plot", "ml_performance_curves"}),
        ("rnaseq_batch_qc", {"omics_differential_results", "omics_volcano_plot"}),
        ("interrupted_time_series", {"time_series_forecast_table", "time_series_forecast_plot"}),
        (
            "multilevel_behavior_model",
            {"psychometric_quality_table", "measurement_invariance_sensitivity"},
        ),
        (
            "clinical_rmst_non_ph",
            {
                "survival_time_dependent_performance",
                "survival_time_dependent_roc_plot",
                "survival_calibration_plot",
            },
        ),
        (
            "clinical_cutpoint_maxstat",
            {
                "survival_time_dependent_performance",
                "survival_time_dependent_roc_plot",
                "survival_calibration_plot",
            },
        ),
        (
            "multiple_testing_endpoints",
            {
                "adjusted_predictions_table",
                "adjusted_predictions_plot",
                "baseline_balance_plot",
                "baseline_distribution_plot",
            },
        ),
    ],
)
def test_template_specific_output_contract_is_not_polluted_by_broad_family_profile(
    template_id, forbidden_roles
):
    template = AnalysisCatalog().get(template_id)
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template",
        template_id=template.id,
        intent=template.title,
        method_family=template.family,
        scenario=template.scenario,
        baseline_outputs=template.required_outputs,
        dataset_profile={"dataset_id": "abc123def456", "columns": []},
        packages=template.packages,
    )

    assert not forbidden_roles & {item.role for item in compiled.outputs}


def test_free_form_request_uses_the_same_professional_portfolio_compiler():
    planner = AnalysisPlanner(
        AnalysisCatalog(),
        FakeCapabilityResolver(("gtsummary", "tableone", "ggplot2")),
    )
    draft = ConversationPlanDraft(
        intent="生成按治疗组比较的论文 Table 1",
        category="基础统计",
        method_family="publication_tables_and_reporting",
        scenario="论文基线特征与组间平衡",
        variable_bindings={"group": "arm"},
        package_queries=("Table 1", "baseline balance"),
        diagnostics=("缺失比例检查",),
        required_outputs=(RequiredOutput("requested_table_one", "table", label="Table 1"),),
    )
    profile = {
        "dataset_id": "abc123def456",
        "rows": 180,
        "columns": [
            {"name": "age", "semantic_type": "numeric", "missing_pct": 3.0},
            {"name": "arm", "semantic_type": "categorical", "missing_pct": 0.0},
        ],
    }

    plan = planner.from_conversation(draft, profile)

    planned_roles = {item.role for item in plan.output_portfolio}
    assert "requested_table_one" in planned_roles
    assert {"baseline_balance_plot", "baseline_distribution_plot", "missingness_by_group_plot"} <= planned_roles
    assert plan.packages == ("gtsummary", "tableone", "ggplot2")
    assert "publication_tables_and_reporting" in plan.capability_evidence
    assert any(item.startswith("installed-r:") for item in plan.capability_evidence)


def test_installed_r_packages_add_package_specific_scientific_outputs():
    compiler = ScientificOutputPortfolioCompiler()
    common = dict(
        source="conversation",
        intent="Cox 生存模型并评估预测性能、时间依赖 ROC 和临床决策曲线净获益",
        method_family="survival_analysis",
        scenario="生存预测与验证",
        baseline_outputs=(RequiredOutput("survival_table", "table"),),
        dataset_profile={
            "dataset_id": "abc123def456",
            "columns": [
                {"name": "days", "semantic_type": "numeric"},
                {"name": "status", "semantic_type": "categorical"},
            ],
        },
    )

    base = compiler.compile(**common, packages=("survival",))
    extended = compiler.compile(**common, packages=("survival", "timeROC", "rmda"))

    base_roles = {item.role for item in base.outputs}
    extended_roles = {item.role for item in extended.outputs}
    assert "survival_time_dependent_performance" not in base_roles
    assert "survival_clinical_utility_plot" not in base_roles
    assert {
        "survival_time_dependent_performance",
        "survival_clinical_utility_plot",
    } <= extended_roles
    assert "installed-r:survival,timeROC,rmda" in extended.capability_evidence


def test_free_form_profile_core_outputs_remain_required():
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="conversation",
        intent="用 Cox 模型分析生存结局",
        method_family="survival_analysis",
        scenario="生存效应估计",
        baseline_outputs=(RequiredOutput("requested_result", "table"),),
        dataset_profile={
            "dataset_id": "abc123def456",
            "columns": [
                {"name": "days", "semantic_type": "numeric"},
                {"name": "status", "semantic_type": "categorical"},
            ],
        },
        packages=("survival",),
    )

    core = [item for item in compiled.outputs if item.tier == "core"]
    assert core
    assert all(item.required for item in core)


@pytest.mark.parametrize(
    "intent",
    [
        "对多个研究的生存结局进行 Meta 分析并绘制森林图",
        "perform a meta-analysis of causal effect estimates",
    ],
)
def test_free_ai_meta_analysis_wins_over_embedded_survival_or_causal_terms(intent):
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="conversation",
        intent=intent,
        method_family="clinical_trials_power_meta_analysis",
        scenario=intent,
        baseline_outputs=(RequiredOutput("requested_result", "table"),),
        dataset_profile={"dataset_id": "abc123def456", "columns": []},
    )

    roles = {item.role for item in compiled.outputs}
    assert "evidence_effect_table" in roles
    assert "evidence_forest_plot" in roles
    assert "survival_curve" not in roles
    assert "causal_primary_effects" not in roles


@pytest.mark.parametrize(
    ("intent", "claimed_family", "expected_role", "forbidden_role"),
    [
        (
            "对无标签样本进行聚类、PCA 和降维解释",
            "psychology_questionnaires_social_science",
            "ordination_primary_plot",
            "psychometric_quality_table",
        ),
        (
            "整合多组学矩阵并进行联合降维和跨组学解释",
            "psychology_questionnaires_social_science",
            "multi_omics_primary_results",
            "psychometric_quality_table",
        ),
        (
            "检查 RNA-seq 批次效应、质控、PCA 和离群样本",
            "bulk_omics_and_differential_expression",
            "analysis_readiness_report",
            "omics_differential_results",
        ),
        (
            "执行 WGCNA 共表达网络、模块性状关联和 hub gene 分析",
            "bulk_omics_and_differential_expression",
            "text_or_network_summary",
            "omics_differential_results",
        ),
    ],
)
def test_free_ai_explicit_intent_overrides_a_misclassified_family(
    intent,
    claimed_family,
    expected_role,
    forbidden_role,
):
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="conversation",
        intent=intent,
        method_family=claimed_family,
        scenario=intent,
        baseline_outputs=(RequiredOutput("requested_result", "table"),),
        dataset_profile={"dataset_id": "abc123def456", "columns": []},
    )

    roles = {item.role for item in compiled.outputs}
    assert expected_role in roles
    assert forbidden_role not in roles


@pytest.mark.parametrize(
    ("template_id", "preserved_role", "forbidden_canonical_role"),
    [
        ("survival_ml", "calibration_output", "survival_curve"),
        ("stratified_categorical_trend", "primary_table", "model_assumption_diagnostics"),
        ("clinical_restricted_cubic_spline", "primary_table", "model_assumption_diagnostics"),
    ],
)
def test_template_baseline_outputs_are_not_relabelled_by_broad_family_terms(
    template_id,
    preserved_role,
    forbidden_canonical_role,
):
    template = AnalysisCatalog().get(template_id)
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template",
        template_id=template.id,
        intent=template.title,
        method_family=template.family,
        scenario=template.scenario,
        baseline_outputs=template.required_outputs,
        dataset_profile={"dataset_id": "abc123def456", "columns": []},
        packages=template.packages,
    )

    roles = {item.role for item in compiled.outputs}
    assert preserved_role in roles
    canonical = next(
        (item for item in compiled.outputs if item.role == forbidden_canonical_role),
        None,
    )
    assert canonical is None or preserved_role not in canonical.legacy_roles


def test_template_package_inventory_stays_within_the_template_method_contract():
    prepared = AnalysisPreparer(
        AnalysisCatalog(),
        runtime_packages=("stats", "survival", "timeROC", "rmda"),
    ).prepare(
        "survival_km_cox",
        {
            "dataset_id": "abc123def456",
            "format": "csv",
            "rows": 100,
            "columns": [
                {"name": "days", "semantic_type": "numeric", "unique_count": 80},
                {"name": "status", "semantic_type": "categorical", "unique_count": 2},
                {"name": "arm", "semantic_type": "categorical", "unique_count": 2},
                {"name": "age", "semantic_type": "numeric", "unique_count": 60},
            ],
        },
        {"followup_time": "days", "event": "status", "exposure": "arm"},
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    # Fixed scripts retain required namespaces even when not yet installed;
    # the exact dependency gate handles installation before R execution.
    assert prepared.plan.packages == ("ggplot2", "readr", "stats", "survival")
    assert not {"timeROC", "rmda"} & set(prepared.plan.packages)
    assert not {
        "survival_time_dependent_performance",
        "survival_time_dependent_roc_plot",
        "survival_clinical_utility_plot",
        "survival_calibration_plot",
    } & {item.role for item in prepared.plan.output_portfolio}


def test_publication_table_runtime_packages_do_not_gain_cross_domain_candidates():
    prepared = AnalysisPreparer(
        AnalysisCatalog(),
        runtime_packages=(
            "stats",
            "gtsummary",
            "tableone",
            "GenomicRanges",
            "Rsamtools",
            "survival",
            "timeROC",
        ),
    ).prepare(
        "publication_table",
        {
            "dataset_id": "abc123def456",
            "format": "xlsx",
            "rows": 100,
            "columns": [
                {"name": "age", "semantic_type": "numeric", "unique_count": 80},
                {"name": "gender", "semantic_type": "categorical", "unique_count": 2},
                {"name": "cvd", "semantic_type": "categorical", "unique_count": 2},
            ],
        },
        {
            "group": "cvd",
            "categorical_variables": ["gender"],
            "continuous_variables": ["age"],
        },
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert prepared.plan.packages == ("dplyr", "readr", "stats")
    assert not {
        "GenomicRanges",
        "Rsamtools",
        "survival",
        "timeROC",
        "gtsummary",
        "tableone",
    } & set(prepared.plan.packages)


def test_every_catalog_method_family_has_a_structured_output_profile():
    families = {
        item.family
        for item in AnalysisCatalog().snapshot().templates
        if item.visibility == "user"
    }

    unmapped = ScientificOutputPortfolioCompiler().unmapped_method_families(families)

    assert unmapped == ()


def test_legacy_broad_family_uses_the_specific_template_capability_profile():
    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()
    profile = {
        "dataset_id": "abc123def456",
        "columns": [
            {"name": "days", "semantic_type": "numeric"},
            {"name": "status", "semantic_type": "categorical"},
        ],
    }

    survival = catalog.get("survival_km_cox")
    compiled = compiler.compile(
        source="template",
        template_id=survival.id,
        intent=survival.title,
        method_family=survival.family,
        scenario=survival.scenario,
        baseline_outputs=survival.required_outputs,
        dataset_profile=profile,
    )

    assert compiled.capability_evidence == ("survival_competing_risks_prognosis",)
    roles = {item.role for item in compiled.outputs}
    assert {
        "survival_effect_estimates",
        "survival_curve",
        "survival_risk_table",
        "survival_model_diagnostics",
    } <= roles
    assert not {"survival_plot", "table_1", "table_2", "table_3", "table_4", "diagnostic_output"} & roles
    assert len(compiled.outputs) <= 7
    assert all("Table 1（" not in item.label for item in compiled.outputs)


def test_capability_compiler_merges_legacy_model_output_fragments_by_scientific_role():
    catalog = AnalysisCatalog()
    compiler = ScientificOutputPortfolioCompiler()
    profile = {
        "dataset_id": "abc123def456",
        "columns": [{"name": "outcome", "semantic_type": "numeric"}],
    }

    regression = catalog.get("regression_glm")
    compiled = compiler.compile(
        source="template",
        template_id=regression.id,
        intent=regression.title,
        method_family=regression.family,
        scenario=regression.scenario,
        baseline_outputs=regression.required_outputs,
        dataset_profile=profile,
    )

    roles = {item.role for item in compiled.outputs}
    assert {
        "effect_estimate_table",
        "model_assumption_diagnostics",
        "model_diagnostic_plot",
        "robustness_comparison",
        "manuscript_results",
    } <= roles
    assert not {"table_1", "table_2", "table_3", "table_4", "table_5", "diagnostic_output_2"} & roles
    assert len(compiled.outputs) == 5


def test_ml_portfolio_keeps_unique_outputs_but_removes_duplicate_leaderboards_and_curves():
    catalog = AnalysisCatalog()
    template = catalog.get("tabular_ml_workflow")
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template",
        template_id=template.id,
        intent=template.title,
        method_family=template.family,
        scenario=template.scenario,
        baseline_outputs=template.required_outputs,
        dataset_profile={
            "dataset_id": "abc123def456",
            "columns": [{"name": "outcome", "semantic_type": "categorical"}],
        },
    )

    labels = {item.label for item in compiled.outputs}
    roles = {item.role for item in compiled.outputs}
    assert "ml_resampling_metrics" in roles
    assert "ml_performance_curves" in roles
    assert not {"Benchmark 表", "排行榜", "模型排行榜", "重采样性能表", "性能图"} & labels
    assert "预测文件" in labels
    assert len(compiled.outputs) <= 15


def test_prediction_portfolio_consolidates_roc_threshold_and_performance_fragments():
    catalog = AnalysisCatalog()
    template = catalog.get("diagnostic_roc")
    compiled = ScientificOutputPortfolioCompiler().compile(
        source="template",
        template_id=template.id,
        intent=template.title,
        method_family=template.family,
        scenario=template.scenario,
        baseline_outputs=template.required_outputs,
        dataset_profile={
            "dataset_id": "abc123def456",
            "columns": [{"name": "outcome", "semantic_type": "categorical"}],
        },
    )

    roles = {item.role for item in compiled.outputs}
    assert {"prediction_performance_table", "prediction_discrimination_plot"} <= roles
    assert not {"roc_output", "table_1", "table_2", "table_3", "table_4"} & roles
    assert len(compiled.outputs) <= 9


def test_freeform_method_name_is_routed_by_scientific_intent_when_not_in_catalog_families():
    planner = AnalysisPlanner(AnalysisCatalog(), FakeCapabilityResolver(("survival",)))
    draft = ConversationPlanDraft(
        intent="用 Cox 比例风险模型分析随访时间和事件",
        category="临床研究",
        method_family="cox_proportional_hazards",
        scenario="生存结局多因素分析",
        variable_bindings={"time": "days", "event": "status"},
        package_queries=("cox survival",),
        diagnostics=("比例风险假设",),
        required_outputs=(RequiredOutput("cox_table", "table", label="Cox HR 表"),),
    )
    profile = {
        "dataset_id": "abc123def456",
        "columns": [{"name": "days"}, {"name": "status"}],
    }

    plan = planner.from_conversation(draft, profile)

    assert "survival_competing_risks_prognosis" in plan.capability_evidence
    assert "installed-r:survival" in plan.capability_evidence
    assert "survival_model_summary" in {item.role for item in plan.output_portfolio}
