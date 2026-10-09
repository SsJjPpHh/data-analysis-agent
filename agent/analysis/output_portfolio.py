"""Compile template baselines and scientific capabilities into one portfolio."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from agent.analysis.models import OutputKind, OutputTier, RequiredOutput, ResolvedInput
from agent.analysis.input_validation import observed_group_count


@dataclass(frozen=True)
class PortfolioOutputSpec:
    role: str
    kind: OutputKind
    label: str
    tier: OutputTier
    rationale: str
    conditions: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    semantic_key: str = ""
    match_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledPortfolio:
    outputs: tuple[RequiredOutput, ...]
    capability_evidence: tuple[str, ...] = ()
    version: str = "1.0"


@dataclass(frozen=True)
class PortfolioProfile:
    id: str
    families: frozenset[str]
    outputs: tuple[PortfolioOutputSpec, ...]


_PUBLICATION_OUTPUTS = (
    PortfolioOutputSpec(
        role="baseline_balance_plot",
        kind="figure",
        label="组间基线平衡图（SMD）",
        tier="recommended",
        rationale="图形化展示组间标准化差异，便于识别不平衡变量。",
        conditions=("group",),
        acceptance=("图中可识别变量与标准化差异", "标题和图注说明分组与平衡阈值"),
        semantic_key="baseline_balance",
    ),
    PortfolioOutputSpec(
        role="baseline_distribution_plot",
        kind="figure",
        label="连续基线变量分布图",
        tier="recommended",
        rationale="补充 Table 1 无法展示的分布形态、偏态和离群信息。",
        conditions=("numeric",),
        acceptance=("展示主要连续变量分布", "分组存在时按组比较"),
        semantic_key="baseline_distribution",
    ),
    PortfolioOutputSpec(
        role="missingness_by_group_plot",
        kind="figure",
        label="分组缺失情况图",
        tier="diagnostic",
        rationale="检查缺失是否在研究组间不均衡并影响 Table 1 解释。",
        conditions=("group", "missing"),
        acceptance=("按变量和研究组展示缺失比例", "图注明确分母"),
        semantic_key="missingness_by_group",
    ),
)


def _output(
    role: str,
    kind: OutputKind,
    label: str,
    tier: OutputTier = "recommended",
    conditions: tuple[str, ...] = (),
    rationale: str = "",
    match_terms: tuple[str, ...] = (),
) -> PortfolioOutputSpec:
    return PortfolioOutputSpec(
        role=role,
        kind=kind,
        label=label,
        tier=tier,
        rationale=rationale or f"补充{label}，形成可审阅的完整科研证据链。",
        conditions=conditions,
        semantic_key=role,
        match_terms=match_terms,
    )


_PROFILES: tuple[PortfolioProfile, ...] = (
    PortfolioProfile(
        "data_import_cleaning_profiling",
        frozenset({"data_import_cleaning_profiling"}),
        (
            _output("variable_type_overview_plot", "figure", "变量类型与可分析性概览图"),
            _output("missingness_pattern_plot", "figure", "缺失模式图", "diagnostic", ("missing",)),
            _output("duplicate_and_outlier_diagnostics", "table", "重复与异常记录诊断表", "diagnostic"),
            _output("analysis_readiness_report", "text", "数据分析就绪性报告", "core"),
        ),
    ),
    PortfolioProfile(
        "publication_tables_and_reporting",
        frozenset({"publication_tables_and_reporting"}),
        _PUBLICATION_OUTPUTS,
    ),
    PortfolioProfile(
        "classical_statistics_and_mixed_models",
        frozenset(
            {
                "adjusted_group_comparison",
                "categorical_association",
                "classical_statistics",
                "classical_statistics_and_mixed_models",
                "clinical_regression",
                "multiple_testing",
                "nonlinear_regression",
                "paired_comparison",
                "resampling_inference",
            }
        ),
        (
            _output(
                "effect_estimate_table",
                "table",
                "效应量与不确定性表",
                "core",
                match_terms=(r"回归", r"模型表", r"模型系数", r"边际效应", r"效应量", r"估计表"),
            ),
            _output(
                "effect_estimate_plot",
                "figure",
                "效应量图",
                "recommended",
                match_terms=(r"效应.*图", r"森林图"),
            ),
            _output(
                "model_assumption_diagnostics",
                "table",
                "模型假设诊断表",
                "diagnostic",
                match_terms=(r"诊断", r"模型假设", r"假设检验"),
            ),
            _output(
                "model_diagnostic_plot",
                "figure",
                "模型诊断图",
                "diagnostic",
                match_terms=(r"诊断", r"残差", r"\bqq\b"),
            ),
            _output("fitted_model_summary", "model", "拟合模型摘要", "core"),
            _output(
                "robustness_comparison",
                "table",
                "稳健性比较表",
                "sensitivity",
                match_terms=(r"稳健", r"敏感性", r"多重校正"),
            ),
            _output(
                "manuscript_results",
                "text",
                "论文结果段",
                "core",
                match_terms=(r"论文结果", r"结果段", r"结果解释"),
            ),
        ),
    ),
    PortfolioProfile(
        "survival_competing_risks_prognosis",
        frozenset(
            {
                "dynamic_prediction",
                "joint_longitudinal_survival",
                "multistate_survival",
                "survival_analysis",
                "survival_competing_risks_prognosis",
            }
        ),
        (
            _output(
                "survival_effect_estimates",
                "table",
                "生存效应估计表",
                "core",
                match_terms=(r"\bcox\b", r"fine.?gray", r"风险模型", r"效应", r"\bhr\b"),
            ),
            _output(
                "survival_curve",
                "figure",
                "生存或累积发生曲线",
                "core",
                match_terms=(
                    r"\bkm\b",
                    r"\bcif\b",
                    r"生存.{0,8}(?:曲线|概率)",
                    r"累积发生",
                ),
            ),
            _output(
                "survival_risk_table",
                "table",
                "风险集与事件数表",
                "recommended",
                match_terms=(r"风险表", r"风险集", r"事件数"),
            ),
            _output(
                "survival_model_diagnostics",
                "table",
                "生存模型诊断表",
                "diagnostic",
                match_terms=(r"\bph\b", r"比例风险", r"诊断", r"假设检验"),
            ),
            _output("survival_diagnostic_plot", "figure", "生存模型诊断图", "diagnostic"),
            _output("survival_model_summary", "model", "生存模型摘要", "core"),
            _output("survival_sensitivity_analysis", "table", "生存分析敏感性结果", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "clinical_prediction_and_diagnostic_performance",
        frozenset(
            {
                "clinical_prediction",
                "clinical_prediction_and_diagnostic_performance",
                "model_validation",
                "prediction_uncertainty",
            }
        ),
        (
            _output(
                "prediction_performance_table",
                "table",
                "预测性能指标表",
                "core",
                match_terms=(
                    r"\bauc\b",
                    r"\bc.index\b",
                    r"性能",
                    r"阈值",
                    r"混淆矩阵",
                    r"敏感度",
                    r"特异度",
                    r"brier",
                ),
            ),
            _output(
                "prediction_discrimination_plot",
                "figure",
                "区分度曲线图",
                "core",
                match_terms=(r"\broc\b", r"\bpr\b", r"区分度"),
            ),
            _output(
                "prediction_calibration_plot",
                "figure",
                "校准图",
                "diagnostic",
                match_terms=(r"校准",),
            ),
            _output(
                "prediction_clinical_utility_plot",
                "figure",
                "临床效用图",
                "recommended",
                match_terms=(r"\bdca\b", r"决策曲线", r"临床效用"),
            ),
            _output(
                "prediction_validation_diagnostics",
                "table",
                "验证与过拟合诊断表",
                "diagnostic",
                match_terms=(r"验证诊断", r"过拟合", r"模型诊断"),
            ),
            _output("prediction_model_summary", "model", "预测模型摘要", "core"),
            _output(
                "prediction_subgroup_sensitivity",
                "table",
                "亚组性能敏感性表",
                "sensitivity",
                match_terms=(r"亚组", r"敏感性", r"稳健性"),
            ),
        ),
    ),
    PortfolioProfile(
        "missing_data_and_imputation",
        frozenset({"missing_data", "missing_data_and_imputation", "trial_missing_data"}),
        (
            _output("missingness_pattern_table", "table", "缺失模式表", "core"),
            _output("missingness_pattern_plot", "figure", "缺失模式图", "diagnostic"),
            _output("imputation_diagnostics", "table", "插补诊断表", "diagnostic"),
            _output("pooled_estimates", "table", "多重插补合并估计表", "core"),
            _output("complete_case_sensitivity", "table", "完整病例敏感性比较", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "epidemiology_and_public_health",
        frozenset(
            {
                "burden_decomposition",
                "epidemiology_and_public_health",
                "health_economics",
                "infectious_disease_surveillance",
                "population_trend_analysis",
                "screening_evaluation",
                "vaccine_effectiveness",
            }
        ),
        (
            _output("epidemiologic_measure_table", "table", "流行病学效应指标表", "core"),
            _output("epidemiologic_uncertainty_plot", "figure", "效应与不确定性图", "core"),
            _output("population_stratified_results", "table", "人群分层结果表", "recommended"),
            _output("population_trend_plot", "figure", "人群趋势图", "recommended", ("time",)),
            _output("public_health_sensitivity", "table", "公共卫生敏感性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "causal_inference_and_real_world_evidence",
        frozenset(
            {
                "causal_inference",
                "causal_inference_and_real_world_evidence",
                "clinical_trial_estimands",
                "heterogeneous_treatment_effects",
            }
        ),
        (
            _output("causal_balance_table", "table", "协变量平衡表", "core"),
            _output("causal_love_plot", "figure", "协变量平衡 Love plot", "diagnostic"),
            _output("causal_overlap_plot", "figure", "倾向评分重叠图", "diagnostic"),
            _output("causal_effect_table", "table", "因果效应估计表", "core"),
            _output("causal_robustness_analysis", "table", "因果稳健性分析", "sensitivity"),
            _output("causal_model_summary", "model", "因果效应模型摘要", "core"),
            _output("causal_identification_note", "text", "因果识别假设说明", "core"),
        ),
    ),
    PortfolioProfile(
        "survey_weighted_analysis",
        frozenset({"survey_weighted_analysis"}),
        (
            _output("survey_weighted_estimates", "table", "加权估计与置信区间表", "core"),
            _output("survey_weighted_distribution_plot", "figure", "加权分布图", "recommended"),
            _output("survey_design_diagnostics", "table", "复杂抽样设计诊断表", "diagnostic"),
            _output("survey_design_effects", "table", "设计效应表", "diagnostic"),
            _output("survey_model_summary", "model", "复杂抽样模型摘要", "core"),
            _output("survey_unweighted_sensitivity", "table", "加权与未加权敏感性比较", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "clinical_trial_design",
        frozenset(
            {
                "adaptive_randomization",
                "adaptive_trial_design",
                "clinical_trial_design",
                "clinical_trials_power_meta_analysis",
                "dose_finding",
                "phase2_design",
            }
        ),
        (
            _output("trial_design_summary", "table", "试验设计参数与决策表", "core"),
            _output("trial_design_plot", "figure", "样本量、效能或分配方案图", "recommended"),
            _output(
                "trial_operating_characteristics",
                "table",
                "设计运行特征表",
                "recommended",
            ),
            _output("trial_design_diagnostics", "table", "试验设计诊断表", "diagnostic"),
            _output("trial_design_sensitivity", "table", "设计参数敏感性分析", "sensitivity"),
            _output("trial_design_rationale", "text", "试验设计依据与实施说明", "core"),
        ),
    ),
    PortfolioProfile(
        "clinical_trial_analysis",
        frozenset({"clinical_trial_analysis"}),
        (
            _output("trial_effect_estimates", "table", "临床试验效应估计表", "core"),
            _output("trial_effect_plot", "figure", "临床试验效应与不确定性图", "recommended"),
            _output(
                "trial_analysis_population_diagnostics",
                "table",
                "分析集、依从性与缺失诊断表",
                "diagnostic",
            ),
            _output("trial_model_diagnostics", "table", "临床试验模型诊断表", "diagnostic"),
            _output("trial_model_summary", "model", "临床试验模型摘要", "core"),
            _output("trial_sensitivity_analysis", "table", "临床试验敏感性分析", "sensitivity"),
            _output("trial_manuscript_results", "text", "临床试验论文结果段", "core"),
        ),
    ),
    PortfolioProfile(
        "clinical_trial_protocol",
        frozenset({"clinical_trial_protocol"}),
        (
            _output("sap_analysis_framework", "table", "统计分析计划框架", "core"),
            _output("sap_estimand_and_population", "table", "估计目标与分析集定义表", "core"),
            _output("sap_missingness_and_sensitivity", "table", "缺失与敏感性分析计划", "sensitivity"),
            _output("sap_output_shells", "table", "统计表图壳清单", "recommended"),
            _output("sap_analysis_flow", "figure", "分析流程与决策图", "recommended"),
            _output("sap_quality_checks", "table", "SAP 一致性与完整性检查", "diagnostic"),
            _output("sap_methodology", "text", "方案统计方法段", "core"),
        ),
    ),
    PortfolioProfile(
        "evidence_synthesis",
        frozenset({"evidence_synthesis"}),
        (
            _output("evidence_effect_table", "table", "证据合成效应主表", "core"),
            _output("evidence_forest_plot", "figure", "森林图或网络证据图", "core"),
            _output("evidence_heterogeneity_diagnostics", "table", "异质性与一致性诊断表", "diagnostic"),
            _output("evidence_bias_diagnostic_plot", "figure", "证据偏倚诊断图", "diagnostic"),
            _output("evidence_model_summary", "model", "证据合成模型摘要", "core"),
            _output("evidence_sensitivity_analysis", "table", "证据合成敏感性分析", "sensitivity"),
            _output("evidence_interpretation", "text", "证据确定性与解释边界", "core"),
        ),
    ),
    PortfolioProfile(
        "psychology_questionnaires_social_science",
        frozenset(
            {
                "dyadic_analysis",
                "latent_state_models",
                "longitudinal_sem",
                "measurement_bias",
                "multilevel_sem",
                "psychology_questionnaires_social_science",
                "psychometrics",
                "survey_response_quality",
            }
        ),
        (
            _output("psychometric_quality_table", "table", "信度与测量质量表", "core"),
            _output("psychometric_model_fit", "table", "测量或结构模型拟合表", "core"),
            _output("psychometric_structure_plot", "figure", "量表或结构模型图", "recommended"),
            _output("psychometric_diagnostics", "table", "测量模型诊断表", "diagnostic"),
            _output("psychometric_model_summary", "model", "心理测量模型摘要", "core"),
            _output("measurement_invariance_sensitivity", "table", "测量不变性或稳健性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "multivariate_exploration_and_ordination",
        frozenset({"multivariate_exploration_and_ordination"}),
        (
            _output("ordination_scores_table", "table", "降维坐标与样本得分表", "core"),
            _output("ordination_loadings_table", "table", "载荷与变量贡献表", "core"),
            _output("ordination_primary_plot", "figure", "多变量排序或降维图", "core"),
            _output("ordination_group_plot", "figure", "分组结构与样本关系图", "recommended"),
            _output("ordination_diagnostics", "table", "尺度、维度与稳定性诊断表", "diagnostic"),
            _output("ordination_sensitivity", "table", "预处理与维度选择敏感性", "sensitivity"),
            _output("ordination_interpretation", "text", "多变量结构解释", "core"),
        ),
    ),
    PortfolioProfile(
        "machine_learning_and_explainable_prediction",
        frozenset(
            {
                "algorithmic_fairness",
                "machine_learning_and_explainable_prediction",
                "model_transportability",
                "structured_classification",
                "tabular_machine_learning",
            }
        ),
        (
            _output(
                "ml_resampling_metrics",
                "table",
                "重采样性能指标表",
                "core",
                match_terms=(
                    r"模型比较",
                    r"测试集性能",
                    r"交叉验证性能",
                    r"benchmark",
                    r"排行榜",
                    r"重采样性能",
                    r"最佳模型指标",
                    r"\broc\b",
                ),
            ),
            _output(
                "ml_performance_curves",
                "figure",
                "模型性能曲线",
                "core",
                match_terms=(r"\broc\b", r"\bpr\b", r"性能图"),
            ),
            _output("ml_calibration_plot", "figure", "模型校准图", "diagnostic"),
            _output("ml_feature_importance", "figure", "特征重要性图", "recommended"),
            _output("ml_explainability_plot", "figure", "模型解释图", "recommended"),
            _output("ml_fitted_model_summary", "model", "最终预测模型摘要", "core"),
            _output(
                "ml_overfitting_and_subgroup_diagnostics",
                "table",
                "过拟合与亚组性能诊断表",
                "diagnostic",
                match_terms=(r"过拟合", r"亚组性能", r"泄漏诊断"),
            ),
        ),
    ),
    PortfolioProfile(
        "time_series_longitudinal_trends_signal_processing",
        frozenset(
            {
                "hierarchical_forecasting",
                "multivariate_time_series",
                "time_series_anomaly_detection",
                "time_series_longitudinal_trends_signal_processing",
            }
        ),
        (
            _output("time_series_decomposition_plot", "figure", "趋势与季节分解图", "core"),
            _output("time_series_forecast_table", "table", "预测值与区间表", "core"),
            _output("time_series_forecast_plot", "figure", "预测图", "core"),
            _output("time_series_residual_diagnostics", "figure", "时序残差诊断图", "diagnostic"),
            _output("time_series_model_summary", "model", "时间序列模型摘要", "core"),
            _output("time_series_backtest", "table", "回测与稳健性结果", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "spatial_environmental_geographic_analysis",
        frozenset(
            {
                "spatial_environmental_geographic_analysis",
                "spatial_interpolation",
                "spatial_point_process",
            }
        ),
        (
            _output("spatial_primary_map", "figure", "主要空间分布图", "core"),
            _output("spatial_effect_table", "table", "空间效应估计表", "core"),
            _output("spatial_autocorrelation_diagnostics", "table", "空间自相关诊断表", "diagnostic"),
            _output("spatial_residual_map", "figure", "空间残差图", "diagnostic"),
            _output("spatial_model_summary", "model", "空间模型摘要", "core"),
            _output("spatial_specification_sensitivity", "table", "空间设定敏感性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "text_mining_and_network_analysis",
        frozenset(
            {
                "information_extraction",
                "sentiment_analysis",
                "text_classification",
                "text_mining_and_network_analysis",
                "topic_modeling",
            }
        ),
        (
            _output("text_or_network_summary", "table", "文本或网络核心结果表", "core"),
            _output("text_or_network_primary_plot", "figure", "文本或网络主要图形", "core"),
            _output("text_or_network_structure_plot", "figure", "主题或网络结构图", "recommended"),
            _output("text_or_network_diagnostics", "table", "稳定性与质量诊断表", "diagnostic"),
            _output("text_or_network_model_summary", "model", "文本或网络模型摘要", "core"),
            _output("text_or_network_sensitivity", "table", "设定敏感性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "bulk_omics_and_differential_expression",
        frozenset({"bulk_omics_and_differential_expression"}),
        (
            _output("omics_differential_results", "table", "差异分析结果表", "core"),
            _output("omics_dimension_reduction_plot", "figure", "降维与样本关系图", "diagnostic"),
            _output("omics_volcano_plot", "figure", "火山图", "core"),
            _output("omics_heatmap", "figure", "表达或特征热图", "recommended"),
            _output("omics_qc_diagnostics", "table", "组学质量控制诊断表", "diagnostic"),
            _output("omics_model_summary", "model", "组学统计模型摘要", "core"),
            _output("omics_batch_sensitivity", "table", "批次与稳健性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "pathway_enrichment_annotation",
        frozenset({"pathway_enrichment_annotation"}),
        (
            _output("enrichment_results", "table", "富集分析结果表", "core"),
            _output("enrichment_dotplot", "figure", "富集气泡图", "core"),
            _output("enrichment_curve_plot", "figure", "富集曲线图", "recommended"),
            _output("pathway_network_plot", "figure", "通路关系图", "recommended"),
            _output("enrichment_sensitivity", "table", "背景集与阈值敏感性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "single_cell_and_cell_annotation",
        frozenset(
            {"single_cell_and_cell_annotation", "single_cell_composition", "single_cell_trajectory"}
        ),
        (
            _output("single_cell_qc", "table", "单细胞质量控制表", "diagnostic"),
            _output("single_cell_embedding", "figure", "单细胞降维聚类图", "core"),
            _output("single_cell_markers", "table", "细胞标志基因表", "core"),
            _output("single_cell_composition_plot", "figure", "细胞组成图", "recommended"),
            _output("single_cell_batch_diagnostics", "figure", "批次与整合诊断图", "diagnostic"),
            _output("single_cell_model_summary", "model", "单细胞统计模型摘要", "recommended"),
            _output("single_cell_trajectory_plot", "figure", "细胞轨迹图", "conditional"),
        ),
    ),
    PortfolioProfile(
        "genomics_variants_epigenomics",
        frozenset(
            {
                "alternative_splicing",
                "cancer_genomics",
                "epigenomic_binding",
                "genetic_association",
                "molecular_qtl",
                "variant_analysis",
            }
        ),
        (
            _output("genomic_primary_results", "table", "基因组主要结果表", "core"),
            _output("genomic_primary_plot", "figure", "基因组主要结果图", "core"),
            _output("genomic_annotation_summary", "table", "变异或区域注释表", "recommended"),
            _output("genomic_qc_diagnostics", "table", "基因组质量控制诊断表", "diagnostic"),
            _output("genomic_model_summary", "model", "基因组统计模型摘要", "core"),
            _output("genomic_threshold_sensitivity", "table", "阈值与模型敏感性分析", "sensitivity"),
        ),
    ),
    PortfolioProfile(
        "microbiome_proteomics_multi_omics_medical_imaging",
        frozenset(
            {
                "metabolomics",
                "microbiome_compositional",
                "microbiome_proteomics_multi_omics_medical_imaging",
                "spatial_omics",
            }
        ),
        (
            _output("multi_omics_primary_results", "table", "多组学或丰度主要结果表", "core"),
            _output("multi_omics_ordination_plot", "figure", "排序或降维图", "core"),
            _output("multi_omics_feature_plot", "figure", "关键特征图", "recommended"),
            _output("multi_omics_integration_plot", "figure", "跨组学整合图", "recommended"),
            _output("multi_omics_qc_diagnostics", "table", "多组学质量控制诊断表", "diagnostic"),
            _output("multi_omics_model_summary", "model", "多组学统计模型摘要", "core"),
            _output("multi_omics_sensitivity", "table", "预处理与稳健性分析", "sensitivity"),
        ),
    ),
)


_PROFILE_BY_FAMILY: dict[str, PortfolioProfile] = {
    family: profile for profile in _PROFILES for family in profile.families
}
_PROFILE_BY_ID: dict[str, PortfolioProfile] = {profile.id: profile for profile in _PROFILES}

# A catalog template already carries a method-specific output contract.  Broad
# family profiles are useful for canonicalizing equivalent output names, but
# their unmatched members must not become compulsory work for every method in
# that family (for example, calibration for clustering or a volcano plot for
# RNA-seq QC).  Table 1 is the one deliberately additive template: its bounded,
# data-gated companion plots are part of that workflow. Other reporting
# templates retain their own method-specific artifact contract.
_ADDITIVE_TEMPLATE_IDS = frozenset({"publication_table"})

# This legacy catalog family intentionally covered both trial design and
# evidence synthesis.  Template IDs now disambiguate it, while free-form AI
# plans must use the stated intent instead of inheriting one arbitrary side.
_INTENT_ROUTED_FAMILIES = frozenset({"clinical_trials_power_meta_analysis"})


@dataclass(frozen=True)
class PackageOutputExtension:
    """Outputs unlocked by a concrete installed R package capability."""

    profile_id: str
    packages: frozenset[str]
    context_pattern: str
    outputs: tuple[PortfolioOutputSpec, ...]


_PACKAGE_OUTPUT_EXTENSIONS: tuple[PackageOutputExtension, ...] = (
    PackageOutputExtension(
        profile_id="survival_competing_risks_prognosis",
        packages=frozenset({"timeroc", "survivalroc", "survauc", "pec", "riskregression"}),
        context_pattern=(
            r"prediction|prognos|risk.?model|external.?valid|time.?dependent|"
            r"\bauc\b|c.?index|brier|预测|预后|风险模型|外部验证|时间依赖|区分度"
        ),
        outputs=(
            _output(
                "survival_time_dependent_performance",
                "table",
                "时间依赖预测性能表",
                "recommended",
                rationale="当前 R 运行时具备时间依赖 AUC/C-index/Brier 评估能力。",
                match_terms=(r"预测性能", r"\bauc\b", r"c.?index", r"brier", r"验证指标"),
            ),
            _output(
                "survival_time_dependent_roc_plot",
                "figure",
                "时间依赖 ROC 曲线",
                "recommended",
                rationale="当前 R 运行时可绘制随时间变化的区分度曲线。",
                match_terms=(r"\broc\b", r"区分度", r"\bauc\b"),
            ),
        ),
    ),
    PackageOutputExtension(
        profile_id="survival_competing_risks_prognosis",
        packages=frozenset({"rmda", "dcurves"}),
        context_pattern=(
            r"decision.?curve|net.?benefit|clinical.?utility|决策曲线|净获益|临床效用"
        ),
        outputs=(
            _output(
                "survival_clinical_utility_plot",
                "figure",
                "生存预测临床决策曲线",
                "recommended",
                rationale="当前 R 运行时具备生存预测净获益与临床效用评估能力。",
                match_terms=(r"\bdca\b", r"决策曲线", r"净获益", r"临床效用"),
            ),
        ),
    ),
    PackageOutputExtension(
        profile_id="survival_competing_risks_prognosis",
        packages=frozenset({"rms", "pec", "riskregression"}),
        context_pattern=(
            r"calibrat|校准"
        ),
        outputs=(
            _output(
                "survival_calibration_plot",
                "figure",
                "生存预测校准图",
                "diagnostic",
                rationale="当前 R 运行时具备生存预测校准评估能力。",
                match_terms=(r"calibrat", r"校准"),
            ),
        ),
    ),
    PackageOutputExtension(
        profile_id="classical_statistics_and_mixed_models",
        packages=frozenset({"marginaleffects", "emmeans", "effects"}),
        context_pattern=(
            r"marginal.?effect|adjusted.?prediction|estimated.?marginal.?mean|"
            r"least.?squares.?mean|\bemmeans?\b|边际效应|调整后预测|边际均值|最小二乘均值"
        ),
        outputs=(
            _output(
                "adjusted_predictions_table",
                "table",
                "调整后预测或边际均值表",
                "recommended",
                rationale="当前 R 运行时具备边际效应、调整后预测与对比估计能力。",
                match_terms=(r"边际效应", r"边际均值", r"调整后预测", r"事后比较"),
            ),
            _output(
                "adjusted_predictions_plot",
                "figure",
                "调整后预测或边际效应图",
                "recommended",
                rationale="当前 R 运行时可将模型调整后的效应转为直观图形。",
                match_terms=(
                    r"边际效应",
                    r"边际均值",
                    r"调整后预测",
                    r"预测概率",
                    r"predicted.?probabil",
                ),
            ),
        ),
    ),
    PackageOutputExtension(
        profile_id="evidence_synthesis",
        packages=frozenset({"meta", "metafor", "metasens"}),
        context_pattern=(
            r"funnel|publication.?bias|small.?stud|漏斗|发表偏倚|小样本效应"
        ),
        outputs=(
            _output(
                "evidence_funnel_plot",
                "figure",
                "漏斗图与小样本效应检查",
                "diagnostic",
                rationale="当前 R 运行时具备证据合成偏倚与小样本效应诊断能力。",
                match_terms=(r"funnel", r"漏斗", r"发表偏倚", r"小样本效应"),
            ),
        ),
    ),
    PackageOutputExtension(
        profile_id="machine_learning_and_explainable_prediction",
        packages=frozenset({"iml", "dalex", "shapviz", "fastshap", "pdp"}),
        context_pattern=(
            r"local.?explan|individual.?prediction|\bshap\b|partial.?depend|"
            r"局部解释|个体预测|特征贡献|部分依赖"
        ),
        outputs=(
            _output(
                "ml_local_explanation_plot",
                "figure",
                "个体预测解释图",
                "recommended",
                rationale="当前 R 运行时具备局部 SHAP/特征贡献解释能力。",
                match_terms=(r"\bshap\b", r"局部解释", r"个体预测", r"特征贡献"),
            ),
        ),
    ),
)

# The V4 catalog deliberately kept several familiar browser families after the
# 278-to-~200 consolidation. These overrides route their execution capability
# without changing the user-facing classification.
_TEMPLATE_PROFILE_OVERRIDES: dict[str, str] = {
    "regression_glm": "classical_statistics_and_mixed_models",
    "survival_km_cox": "survival_competing_risks_prognosis",
    "environment_mixture": "epidemiology_and_public_health",
    "sample_size_power": "clinical_trial_design",
    "noninferiority_equivalence": "clinical_trial_analysis",
    "group_sequential": "clinical_trial_design",
    "rct_primary_analysis": "clinical_trial_analysis",
    "pairwise_meta": "evidence_synthesis",
    "network_meta": "evidence_synthesis",
    "diagnostic_meta": "evidence_synthesis",
    "meta_bias_sensitivity": "evidence_synthesis",
    "bayesian_meta": "evidence_synthesis",
    "gam_splines_time": "time_series_longitudinal_trends_signal_processing",
    "assumption_diagnostics": "classical_statistics_and_mixed_models",
    "effect_size_precision": "classical_statistics_and_mixed_models",
    "multiple_testing_endpoints": "classical_statistics_and_mixed_models",
    "subgroup_sensitivity_basic": "classical_statistics_and_mixed_models",
    "clinical_subgroup_interaction": "classical_statistics_and_mixed_models",
    "matched_case_control": "causal_inference_and_real_world_evidence",
    "safety_adverse_events": "clinical_trial_analysis",
    "nested_case_control": "causal_inference_and_real_world_evidence",
    "composite_endpoint": "clinical_trial_analysis",
    "sap_protocol_stats": "clinical_trial_protocol",
    "randomization_allocation": "clinical_trial_design",
    "crossover_trial_analysis": "clinical_trial_analysis",
    "cluster_rct_analysis": "clinical_trial_analysis",
    "bioequivalence_pk": "clinical_trial_analysis",
    "survival_trial_design": "clinical_trial_design",
    "safety_meta_analysis": "evidence_synthesis",
    "living_review_update": "evidence_synthesis",
    "grade_evidence_summary": "evidence_synthesis",
    "multilevel_behavior_model": "classical_statistics_and_mixed_models",
    "mediation_moderation": "classical_statistics_and_mixed_models",
    "conjoint_choice": "classical_statistics_and_mixed_models",
    "discrete_choice_model": "classical_statistics_and_mixed_models",
    "correlation_agreement": "classical_statistics_and_mixed_models",
    "multivariate_ordination": "multivariate_exploration_and_ordination",
    "unsupervised_ml": "multivariate_exploration_and_ordination",
    "multiomics_integration": "microbiome_proteomics_multi_omics_medical_imaging",
    "scale_reliability": "psychology_questionnaires_social_science",
    "dose_response_trial": "clinical_trial_analysis",
    "multilevel_public_health": "epidemiology_and_public_health",
    "environment_time_series": "time_series_longitudinal_trends_signal_processing",
    "ipd_meta_mixed": "evidence_synthesis",
    "functional_data_analysis": "time_series_longitudinal_trends_signal_processing",
    "bayesian_robust_stats": "classical_statistics_and_mixed_models",
    "trial_estimand_ich_e9": "clinical_trial_analysis",
    "trial_missing_tipping_point": "clinical_trial_analysis",
    "trial_phase1_dose_escalation": "clinical_trial_design",
    "trial_simon_two_stage": "clinical_trial_design",
    "trial_sample_size_reestimation": "clinical_trial_design",
    "trial_adaptive_randomization": "clinical_trial_design",
    "trial_multiplicity_gatekeeping": "clinical_trial_analysis",
    "evidence_meta_regression": "evidence_synthesis",
    "evidence_proportion_rate_meta": "evidence_synthesis",
    "flow_cytometry": "microbiome_proteomics_multi_omics_medical_imaging",
    "immune_repertoire_summary": "microbiome_proteomics_multi_omics_medical_imaging",
    "multiassay_clinical_bridge": "microbiome_proteomics_multi_omics_medical_imaging",
    "dimensionality_reduction_ml": "multivariate_exploration_and_ordination",
    "mixed_methods_open_text": "text_mining_and_network_analysis",
    "wgcna_network": "text_mining_and_network_analysis",
    "genomic_ranges": "genomics_variants_epigenomics",
    "rnaseq_batch_qc": "data_import_cleaning_profiling",
    "methylation_expression_proxy": "genomics_variants_epigenomics",
    "proteomics_ms": "microbiome_proteomics_multi_omics_medical_imaging",
}

_INTENT_PROFILE_RULES: tuple[tuple[str, str], ...] = (
    (
        "missing_data_and_imputation",
        r"\b(?:imputation|imputations|impute|imputing)\b|插补|"
        r"\b(?:use|run|perform|apply|execute)\b.{0,12}\bmice\b|(?:使用|采用|执行|运行)\s*mice(?![a-z])",
    ),
    (
        "publication_tables_and_reporting",
        r"table\s*1|table\s*one|三线表|基线特征表|基线表",
    ),
    # Evidence synthesis wins when its effect measure happens to be a
    # survival or causal estimate.  Those words describe the pooled input,
    # not a request to run a new Cox or causal-identification workflow.
    (
        "evidence_synthesis",
        r"meta.?analysis|meta\s*分析|systematic.?review|pooled.?effect|evidence.?synthesis|元分析|系统综述|证据合成|合并效应",
    ),
    (
        "microbiome_proteomics_multi_omics_medical_imaging",
        r"multi.?omics|cross.?omics|多组学|跨组学|联合组学",
    ),
    (
        "data_import_cleaning_profiling",
        r"(?:(?:rna.?seq|transcriptom|gene.?expression).{0,30}(?:batch|quality.?control|\bqc\b|批次|质控)|"
        r"(?:batch|quality.?control|\bqc\b|批次|质控).{0,30}(?:rna.?seq|transcriptom|gene.?expression)|"
        r"RNA.?测序.{0,20}(?:批次|质控)|(?:批次|质控).{0,20}RNA.?测序)",
    ),
    (
        "text_mining_and_network_analysis",
        r"\bwgcna\b|co.?expression.?network|共表达(?:模块|网络)|模块.?性状|hub.?gene",
    ),
    (
        "multivariate_exploration_and_ordination",
        r"unsupervised|cluster(?:ing)?|ordination|principal.?component|"
        r"dimension(?:ality)?.?reduction|无监督|聚类|主成分|多变量排序|降维",
    ),
    (
        "survival_competing_risks_prognosis",
        r"survival|kaplan|\bcox\b|fine.?gray|competing.?risk|生存|竞争风险|累积发生",
    ),
    (
        "clinical_prediction_and_diagnostic_performance",
        r"\broc\b|\bauc\b|calibrat|decision.?curve|diagnostic.?performance|预测模型|诊断效能|校准图|决策曲线",
    ),
    (
        "causal_inference_and_real_world_evidence",
        r"causal|propensity|inverse.?probability|difference.in.difference|\bdid\b|instrumental.?variable|因果|倾向评分|工具变量|双重差分|中介效应",
    ),
    (
        "clinical_trial_protocol",
        r"statistical.?analysis.?plan|\bsap\b|analysis.?shell|clinical.?trial.?protocol|统计分析计划|临床试验方案|试验方案|表壳",
    ),
    (
        "clinical_trial_design",
        r"sample.?size|statistical.?power|(?:design|plan).{0,18}clinical.?trial|clinical.?trial.{0,18}(?:design|plan)|randomi[sz](?:ation)?.{0,12}(?:allocation|scheme|list|sequence)|group.?sequential|dose.?escalation|(?:non.?inferior|\bequivalence\b).{0,18}(?:design|margin|sample.?size|power)|(?:design|margin|sample.?size|power).{0,18}(?:non.?inferior|\bequivalence\b)|样本量|把握度|(?:设计|规划).{0,10}临床试验|临床试验.{0,10}(?:设计|规划)|随机分配|随机化方案|随机序列|(?:非劣|等效).{0,10}(?:设计|界值|样本量|把握度)|(?:设计|界值|样本量|把握度).{0,10}(?:非劣|等效)|序贯|剂量递增",
    ),
    (
        "clinical_trial_analysis",
        r"randomi[sz]ed.?controlled.?trial|\brct\b|clinical.?trial|crossover.?trial|cluster.?trial|bioequivalence|tipping.?point|gatekeeping|(?:analy[sz]|result|effect|confidence.?interval|\bci\b).{0,18}(?:non.?inferior|\bequivalence\b)|(?:non.?inferior|\bequivalence\b).{0,18}(?:analy[sz]|result|effect|confidence.?interval|\bci\b)|临床试验|随机对照试验|交叉试验|整群试验|生物等效|门控检验|(?:分析|结果|效应|置信区间).{0,10}(?:非劣|等效)|(?:非劣|等效).{0,10}(?:分析|结果|效应|置信区间)",
    ),
    (
        "survey_weighted_analysis",
        r"survey|sampling.?weight|complex.?sample|抽样权重|复杂抽样|加权调查",
    ),
    (
        "psychology_questionnaires_social_science",
        r"psychometr|questionnaire|reliability|factor.?analysis|structural.?equation|\bsem\b|rasch|\birt\b|量表|问卷|信度|效度|因子分析|结构方程",
    ),
    (
        "machine_learning_and_explainable_prediction",
        r"machine.?learning|random.?forest|boost|xgboost|lightgbm|neural|cross.?validation|shap|feature.?importance|机器学习|交叉验证|特征重要性|聚类",
    ),
    (
        "time_series_longitudinal_trends_signal_processing",
        r"time.?series|forecast|longitudinal|repeated.?measure|trend|seasonal|时间序列|预测|纵向|趋势|季节",
    ),
    (
        "spatial_environmental_geographic_analysis",
        r"spatial|geographic|\bgis\b|geostat|moran|longitude|latitude|空间分析|地理|地图|经纬度",
    ),
    (
        "single_cell_and_cell_annotation",
        r"single.?cell|scrna|seurat|单细胞|细胞注释",
    ),
    (
        "pathway_enrichment_annotation",
        r"pathway|enrichment|\bgsea\b|\bkegg\b|gene.?ontology|通路|富集",
    ),
    (
        "genomics_variants_epigenomics",
        r"variant|\bgwas\b|genom|methyl|atac|chip.?seq|eqtl|变异|基因组|甲基化|表观组",
    ),
    (
        "bulk_omics_and_differential_expression",
        r"rna.?seq|differential.?expression|transcriptom|microarray|差异表达|转录组|基因表达",
    ),
    (
        "microbiome_proteomics_multi_omics_medical_imaging",
        r"microbiom|proteom|metabolom|multi.?omics|medical.?imag|微生物组|蛋白组|代谢组|多组学|医学影像",
    ),
    (
        "text_mining_and_network_analysis",
        r"text.?min|natural.?language|topic.?model|sentiment|social.?network|文本挖掘|主题模型|情感分析|社会网络",
    ),
    (
        "epidemiology_and_public_health",
        r"epidemiolog|incidence|prevalence|public.?health|disease.?burden|流行病|发病率|患病率|公共卫生|疾病负担",
    ),
    (
        "data_import_cleaning_profiling",
        r"data.?quality|data.?clean|data.?profil|variable.?dictionary|exploratory.?data|数据质量|数据清洗|变量字典|数据体检",
    ),
)


_IMPUTATION_REQUEST_RE = re.compile(
    next(pattern for profile, pattern in _INTENT_PROFILE_RULES if profile == "missing_data_and_imputation"),
    flags=re.IGNORECASE,
)
_IMPUTATION_TERM = r"(?:\b(?:imputation|imputations|impute|imputing|imputed)\b|(?<![a-z])mice(?![a-z])|(?:多重|单次|多次)?插补)"
_NEGATED_IMPUTATION_RE = re.compile(
    # Remove only a negated operation, so "不要单次插补，改用MICE多重插补"
    # still requests MI. Reporting missingness/complete cases is not MI.
    r"(?:\b(?:no|not|without|avoid|skip|exclude|omit|never)\b|\bdo\s+not\b|\bdon['’]t\b)"
    r"[^,;，；。\n]{0,60}?" + _IMPUTATION_TERM
    + r"|(?:不|未|无需|无须|不用|不要|禁止|拒绝|避免)[^,;，；。\n]{0,20}?" + _IMPUTATION_TERM
    + r"|" + _IMPUTATION_TERM
    + r"[^,;，；。\n]{0,20}?(?:已(?:禁用|关闭)|(?:不|未)(?:进行|执行|采用|使用|启用)|"
    r"不是(?:本次|当前|此次)|\b(?:disabled|excluded|unnecessary)\b|"
    r"\b(?:not|isn['’]t)\s+(?:be\s+)?(?:required|needed|necessary|performed|used)\b)",
    flags=re.IGNORECASE,
)


def _requests_imputation(intent: str, scenario: str) -> bool:
    request = " ".join((intent, scenario))
    request = _NEGATED_IMPUTATION_RE.sub("", request)
    # A package name, an already-imputed dataset or a family hint does not
    # establish a new imputation workflow. MICE alone needs an action request.
    return bool(_IMPUTATION_REQUEST_RE.search(request))


def _select_profile(
    *,
    template_id: str,
    method_family: str,
    intent: str,
    scenario: str,
) -> PortfolioProfile | None:
    override = _TEMPLATE_PROFILE_OVERRIDES.get(template_id, "")
    if override:
        return _PROFILE_BY_ID.get(override)
    exact = _PROFILE_BY_FAMILY.get(method_family)
    intent_parts = (intent, scenario)
    if method_family not in _INTENT_ROUTED_FAMILIES:
        intent_parts = (method_family, *intent_parts)
    text = " ".join(intent_parts).casefold()
    # Free-form drafts originate in model output, so method_family is a hint,
    # not stronger evidence than the user's explicit scientific request.
    # Catalog templates retain exact family routing (or an explicit override)
    # to preserve their stable public contract.
    if template_id and exact is not None and method_family not in _INTENT_ROUTED_FAMILIES:
        return exact
    requests_imputation = _requests_imputation(intent, scenario)
    for profile_id, pattern in _INTENT_PROFILE_RULES:
        if profile_id == "missing_data_and_imputation":
            # Missingness is a diagnostic, not permission to change Welch or
            # complete-case analysis into MI. A free-form family is a model
            # hint; an explicit, non-negated request must establish imputation.
            if requests_imputation:
                return _PROFILE_BY_ID.get(profile_id)
            continue
        if re.search(pattern, text, flags=re.IGNORECASE):
            return _PROFILE_BY_ID.get(profile_id)
    if exact is not None and exact.id != "missing_data_and_imputation":
        return exact
    return _PROFILE_BY_ID.get("classical_statistics_and_mixed_models")


def _spec_matches_baseline(output: RequiredOutput, spec: PortfolioOutputSpec) -> bool:
    if output.kind != spec.kind:
        return False
    if output.role == spec.role or (output.semantic_key and output.semantic_key == spec.semantic_key):
        return True
    baseline_label = _normalized_output_label(output.label or output.role)
    spec_label = _normalized_output_label(spec.label)
    if baseline_label and baseline_label == spec_label:
        return True
    searchable = f"{output.role} {output.label}".casefold()
    return any(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in spec.match_terms)


def _canonical_output_from_matches(
    spec: PortfolioOutputSpec,
    matches: Sequence[RequiredOutput],
) -> RequiredOutput:
    inherited_acceptance = tuple(
        dict.fromkeys(item for match in matches for item in match.acceptance)
    )
    inherited_labels = "、".join(
        dict.fromkeys((item.label or item.role) for item in matches)
    )
    rationale = spec.rationale
    if inherited_labels:
        rationale = f"合并原模板基线（{inherited_labels}）。{rationale}"
    legacy_roles = tuple(
        dict.fromkeys(
            role
            for match in matches
            for role in (match.role, *match.legacy_roles)
            if role and role != spec.role
        )
    )
    return RequiredOutput(
        role=spec.role,
        kind=spec.kind,
        required=any(item.required for item in matches),
        label=spec.label,
        tier=spec.tier,
        source="template",
        rationale=rationale,
        conditions=spec.conditions,
        acceptance=tuple(dict.fromkeys((*inherited_acceptance, *spec.acceptance))),
        semantic_key=spec.semantic_key or spec.role,
        legacy_roles=legacy_roles,
    )


def _profile_columns(dataset_profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = dataset_profile.get("columns")
    if not isinstance(raw, list):
        raw = dataset_profile.get("variables")
    return tuple(item for item in raw if isinstance(item, dict)) if isinstance(raw, list) else ()


def _features(
    dataset_profile: Mapping[str, Any],
    resolved_inputs: Mapping[str, ResolvedInput],
) -> frozenset[str]:
    features: set[str] = set()
    columns = _profile_columns(dataset_profile)
    semantic_types = {
        str(item.get("semantic_type") or item.get("type") or "").strip().casefold()
        for item in columns
    }
    if "numeric" in semantic_types or "integer" in semantic_types or "double" in semantic_types:
        features.add("numeric")
    if "categorical" in semantic_types or "factor" in semantic_types or "logical" in semantic_types:
        features.add("categorical")
    for item in columns:
        try:
            missing_pct = float(item.get("missing_pct") or 0)
        except (TypeError, ValueError):
            missing_pct = 0.0
        try:
            missing_count = int(item.get("missing_count") or 0)
        except (TypeError, ValueError):
            missing_count = 0
        if missing_pct > 0 or missing_count > 0:
            features.add("missing")
            break
    role_text = " ".join(
        f"{input_id} {item.label}" for input_id, item in resolved_inputs.items()
    ).casefold()
    group_count = observed_group_count(resolved_inputs, dataset_profile)
    if (group_count is None or group_count >= 2) and any(
        token in role_text for token in ("group", "arm", "分组", "组别", "treatment")
    ):
        features.add("group")
    if any(token in role_text for token in ("time", "date", "随访", "时间", "日期")):
        features.add("time")
    if any(token in role_text for token in ("event", "status", "事件", "结局")):
        features.add("event")
    return frozenset(features)


_REPEATED_SEMANTIC_ROLE_BASES = {
    "analysis_recommendation",
    "baseline_table",
    "calibration_output",
    "diagnostic_output",
    "forest_plot",
    "manuscript_results",
    "method_note",
    "outlier_summary",
    "roc_output",
    "sensitivity_output",
    "survival_plot",
}


def _normalized_output_label(value: str) -> str:
    text = str(value or "").casefold().replace("table one", "table1").replace("table 1", "table1")
    for before, after in (
        ("三线表", ""),
        ("论文式", ""),
        ("科研", ""),
        ("输出", ""),
        ("汇总", ""),
        ("表格", "表"),
        ("曲线", "图"),
    ):
        text = text.replace(before, after)
    text = re.sub(r"[\s/（）()_\-·]+", "", text)
    return re.sub(r"(表|图|说明|解释|段落)$", "", text)


def _baseline_identity(output: RequiredOutput) -> str:
    role_base = re.sub(r"_\d+$", "", output.role)
    if role_base in _REPEATED_SEMANTIC_ROLE_BASES:
        return f"{output.kind}:role:{role_base}"
    label = _normalized_output_label(output.label or output.role)
    return f"{output.kind}:label:{label}"


def _publication_baseline(outputs: Sequence[RequiredOutput]) -> list[RequiredOutput]:
    """Fold legacy Table 1 fragments into one publication-ready table.

    P values, SMD/effect size and exportability are columns/properties of the
    same Table 1, not five additional scientific artifacts.
    """

    tables = [item for item in outputs if item.kind == "table"]
    remaining = [item for item in outputs if item.kind != "table"]
    if not tables:
        return list(outputs)
    primary = next((item for item in tables if item.role == "baseline_table"), tables[0])
    acceptance = tuple(
        dict.fromkeys(
            (
                *primary.acceptance,
                "总体与分组描述",
                "P 值",
                "SMD",
                "效应量或不确定性",
                "可由结果页导出为 CSV/XLSX/DOCX",
            )
        )
    )
    consolidated = replace(
        primary,
        label="Table 1（含分组描述、P 值、SMD/效应量）",
        rationale="合并原模板中重复的 Table 1、组间比较、SMD、效应量和导出表角色。",
        acceptance=acceptance,
        semantic_key="baseline_table",
        legacy_roles=tuple(
            dict.fromkeys(
                role
                for item in tables
                for role in (item.role, *item.legacy_roles)
                if role and role != primary.role
            )
        ),
    )
    return [consolidated, *remaining]


def _normalized_baseline(
    outputs: Sequence[RequiredOutput],
    source: str,
    method_family: str,
) -> list[RequiredOutput]:
    has_table_one_baseline = any(
        item.role == "baseline_table" and "table1" in _normalized_output_label(item.label)
        for item in outputs
    )
    candidates = (
        _publication_baseline(outputs)
        if source == "template" and has_table_one_baseline
        else list(outputs)
    )
    normalized: list[RequiredOutput] = []
    positions: dict[str, int] = {}
    for item in candidates:
        identity = _baseline_identity(item)
        if identity in positions:
            index = positions[identity]
            existing = normalized[index]
            normalized[index] = replace(
                existing,
                required=existing.required or item.required,
                acceptance=tuple(dict.fromkeys((*existing.acceptance, *item.acceptance))),
                legacy_roles=tuple(
                    dict.fromkeys(
                        (
                            *existing.legacy_roles,
                            item.role,
                            *item.legacy_roles,
                        )
                    )
                ),
            )
            continue
        positions[identity] = len(normalized)
        normalized.append(
            replace(
                item,
                tier=item.tier or "core",
                source="template" if source == "template" else "conversation",
                semantic_key=(
                    item.semantic_key
                    if item.semantic_key and item.semantic_key != item.role
                    else identity
                ),
                legacy_roles=tuple(dict.fromkeys(item.legacy_roles)),
            )
        )
    return normalized


class ScientificOutputPortfolioCompiler:
    """Build a data-applicable portfolio without replacing legacy baselines."""

    VERSION = "1.2"

    def normalize_baseline(
        self,
        *,
        source: str,
        method_family: str,
        outputs: Sequence[RequiredOutput],
    ) -> tuple[RequiredOutput, ...]:
        """Return the non-redundant compatibility baseline for UI and plans."""

        return tuple(_normalized_baseline(outputs, source, method_family))

    def unmapped_method_families(self, families: Sequence[str] | set[str]) -> tuple[str, ...]:
        """Return catalog method families that lack a structured profile."""

        return tuple(sorted(set(families) - set(_PROFILE_BY_FAMILY)))

    def compile(
        self,
        *,
        source: str,
        template_id: str = "",
        intent: str,
        method_family: str,
        scenario: str,
        baseline_outputs: Sequence[RequiredOutput],
        dataset_profile: Mapping[str, Any],
        resolved_inputs: Mapping[str, ResolvedInput] | None = None,
        packages: Sequence[str] = (),
        packages_verified: bool = True,
        diagnostics: Sequence[str] = (),
    ) -> CompiledPortfolio:
        del diagnostics
        from agent.analysis.contract_conditions import conditions_match

        features = _features(dataset_profile, resolved_inputs or {})
        if resolved_inputs is not None:
            baseline_outputs = tuple(
                output for output in baseline_outputs
                if conditions_match(output.conditions, resolved_inputs, features)
            )
        baseline = list(
            self.normalize_baseline(
                source=source,
                method_family=method_family,
                outputs=baseline_outputs,
            )
        )
        if template_id == "publication_table" and resolved_inputs is not None and "group" not in features:
            baseline = [
                replace(output, label="Table 1（总体描述）", acceptance=("总体描述", "可由结果页导出为 CSV/XLSX/DOCX"))
                if output.role == "baseline_table" else output
                for output in baseline
            ]
        original_baseline = tuple(baseline)
        evidence: list[str] = []

        profile = _select_profile(
            template_id=template_id,
            method_family=method_family,
            intent=intent,
            scenario=scenario,
        )
        specs: tuple[PortfolioOutputSpec, ...] = profile.outputs if profile else ()
        if profile:
            evidence.append(profile.id)

        normalized_packages = tuple(
            dict.fromkeys(str(package).strip() for package in packages if str(package).strip())
        )
        package_keys = {package.casefold() for package in normalized_packages}
        if normalized_packages:
            evidence.append(
                ("installed-r:" if packages_verified else "catalog-r-candidates:")
                + ",".join(normalized_packages)
            )
        extension_specs: list[PortfolioOutputSpec] = []
        if profile is not None:
            # The profile already establishes the broad method family. Package
            # availability may add an output only when the actual request or
            # template scenario names that scientific task; a family name such
            # as "prognosis" is not evidence that calibration was requested.
            extension_context = " ".join((intent, scenario))
            for extension in _PACKAGE_OUTPUT_EXTENSIONS:
                if (
                    extension.profile_id == profile.id
                    and package_keys & extension.packages
                    and re.search(
                        extension.context_pattern,
                        extension_context,
                        flags=re.IGNORECASE,
                    )
                ):
                    extension_specs.extend(extension.outputs)
        extension_roles = {item.role for item in extension_specs}
        if extension_specs:
            existing_roles = {item.role for item in specs}
            specs = tuple((*specs, *(item for item in extension_specs if item.role not in existing_roles)))

        compiled_outputs: list[RequiredOutput] = []
        for spec in specs:
            conditions_met = all(condition in features for condition in spec.conditions)
            if spec.conditions and not conditions_met:
                # A condition is an applicability gate, not a request for the
                # model to manufacture or manually decline an impossible
                # output. Baseline template outputs remain untouched below.
                continue
            matches = [item for item in baseline if _spec_matches_baseline(item, spec)]
            if matches:
                if source == "template":
                    # Equivalent legacy fragments are consolidated under one
                    # canonical role and retain every old role as an alias.
                    # Match terms above are deliberately method-specific so a
                    # calibration or trend-test artifact is not relabelled by
                    # a generic word such as "survival" or "test".
                    matched_ids = {id(item) for item in matches}
                    baseline = [item for item in baseline if id(item) not in matched_ids]
                    compiled_outputs.append(_canonical_output_from_matches(spec, matches))
                continue
            if not (
                source == "conversation"
                or template_id in _ADDITIVE_TEMPLATE_IDS
                or spec.role in extension_roles
            ):
                # The method/template baseline is authoritative.  A broad
                # family profile may rename an equivalent baseline artifact,
                # but it cannot manufacture unrelated mandatory outputs.
                continue
            if spec.role in extension_roles and any(
                _spec_matches_baseline(item, spec) for item in original_baseline
            ):
                # Another canonical profile item may already have consumed a
                # combined baseline artifact (for example one calibration/AUC
                # figure). Do not split that artifact into duplicate package-
                # driven work merely because the installed package could do so.
                continue
            output_tier: OutputTier = spec.tier
            compiled_outputs.append(
                RequiredOutput(
                    role=spec.role,
                    kind=spec.kind,
                    required=output_tier == "core" and conditions_met,
                    label=spec.label,
                    tier=output_tier,
                    source="capability",
                    rationale=spec.rationale,
                    conditions=spec.conditions,
                    acceptance=spec.acceptance,
                    semantic_key=spec.semantic_key or spec.role,
                    legacy_roles=(),
                )
            )

        all_outputs = [*baseline, *compiled_outputs]
        if source == "template" and not any(output.kind == "table" for output in all_outputs):
            all_outputs.append(
                RequiredOutput(
                    role="scientific_result_table",
                    kind="table",
                    required=True,
                    label="主要数值或结构结果表",
                    tier="core",
                    source="system",
                    rationale="模板必须保留可导出、可复核的结构化主要结果。",
                    acceptance=("内容来自本次实际计算", "字段、分母和不确定性说明清楚"),
                    semantic_key="scientific_result_table",
                )
            )
        if not any(output.kind == "figure" for output in all_outputs):
            all_outputs.append(
                RequiredOutput(
                    role="scientific_result_visualization",
                    kind="figure",
                    required=False,
                    label="与分析方法相符的主要结果图（适用时）",
                    tier="recommended",
                    source="system",
                    rationale="仅在该方法存在有信息量的图形表达时补充，不强制制造无意义图形。",
                    acceptance=("图形类型与实际方法相符", "标题、坐标、图例和图注明确"),
                    semantic_key="scientific_result_visualization",
                )
            )
        if not any(output.kind == "text" for output in all_outputs):
            all_outputs.append(
                RequiredOutput(
                    role="scientific_result_interpretation",
                    kind="text",
                    required=True,
                    label="科研结果解释与局限性说明",
                    tier="core",
                    source="system",
                    rationale="确保图表和模型结果同时形成可独立审阅的科研解释。",
                    acceptance=(
                        "解释主要结果、效应方向与不确定性",
                        "说明适用范围、数据限制与必要的下一步分析",
                    ),
                    semantic_key="scientific_result_interpretation",
                )
            )

        return CompiledPortfolio(
            outputs=tuple(all_outputs),
            capability_evidence=tuple(evidence),
            version=self.VERSION,
        )
