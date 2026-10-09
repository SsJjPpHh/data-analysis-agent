from agent.analysis.catalog import AnalysisCatalog


def test_malformed_output_conditions_fail_instead_of_becoming_unconditional():
    import pytest
    from agent.analysis.catalog import _required_output
    with pytest.raises(ValueError, match="conditions"):
        _required_output({"role":"table_1", "kind":"table", "conditions":{"conditions":["input:group"]}})


EXPECTED_CATEGORIES = {
    "基础统计",
    "临床研究",
    "公共卫生",
    "临床试验与证据合成",
    "行为与社会科学",
    "机器学习",
    "时间空间与文本",
    "组学与生物信息",
}

EXPECTED_V2_ADDITIONS = {
    "paired_matched_tests",
    "ancova_adjusted_groups",
    "stratified_categorical_trend",
    "bootstrap_permutation_inference",
    "nonlinear_spline_gam",
    "multiple_imputation_pooling",
    "clinical_rmst_non_ph",
    "clinical_recurrent_events",
    "clinical_multistate_illness_death",
    "clinical_restricted_cubic_spline",
    "clinical_landmark_dynamic_prediction",
    "clinical_target_trial_emulation",
    "clinical_doubly_robust_effect",
    "clinical_joint_longitudinal_survival",
    "public_rt_nowcasting",
    "public_joinpoint_segmented_trend",
    "public_vaccine_test_negative",
    "public_screening_evaluation",
    "public_burden_decomposition",
    "public_cost_effectiveness_psa",
    "trial_estimand_ich_e9",
    "trial_missing_tipping_point",
    "trial_phase1_dose_escalation",
    "trial_simon_two_stage",
    "trial_sample_size_reestimation",
    "trial_adaptive_randomization",
    "trial_multiplicity_gatekeeping",
    "evidence_meta_regression",
    "evidence_proportion_rate_meta",
    "behavior_scale_development",
    "behavior_riclpm_cross_lagged",
    "behavior_apim_dyadic",
    "behavior_multilevel_sem",
    "behavior_common_method_bias",
    "behavior_latent_transition",
    "behavior_response_style_nonresponse",
    "ml_nested_cv_leakage",
    "ml_external_transportability",
    "ml_fairness_subgroup",
    "ml_conformal_prediction",
    "ml_multiclass_multilabel_ordinal",
    "ml_causal_uplift",
    "ml_clinical_score_simplification",
    "time_var_vecm_cointegration",
    "time_hierarchical_grouped_forecast",
    "time_seasonal_anomaly_changepoint",
    "spatial_point_pattern",
    "spatial_interpolation_validation",
    "text_sentiment_analysis",
    "text_supervised_classification",
    "text_entity_relation_extraction",
    "text_topic_stability",
    "omics_gwas_prs",
    "omics_variant_burden_annotation",
    "omics_scrna_trajectory",
    "omics_scrna_differential_abundance",
    "omics_spatial_transcriptomics",
    "omics_atac_chip_binding",
    "omics_alternative_splicing",
    "omics_eqtl",
    "omics_somatic_mutation_cnv",
    "omics_microbiome_compositional",
    "omics_metabolomics_pathway",
}


def test_catalog_has_unique_user_templates_without_generated_wrappers():
    snapshot = AnalysisCatalog().snapshot()
    items = tuple(item for item in snapshot.templates if item.visibility == "user")
    ids = [item.id for item in items]

    assert len(ids) == len(set(ids))
    assert 205 <= len(ids) <= 215
    assert len(EXPECTED_V2_ADDITIONS) == 63
    assert EXPECTED_V2_ADDITIONS <= set(ids)
    assert len(set(ids) - EXPECTED_V2_ADDITIONS) == 150
    assert not any(template_id.startswith("capability_") for template_id in ids)
    assert {item.category for item in items} == EXPECTED_CATEGORIES
    assert {"data_quality", "publication_table"} <= set(ids)
    assert snapshot.aliases["publication_graphics_export"] == "publication_table"
    assert AnalysisCatalog().get("publication_graphics_export").id == "publication_table"


def test_omics_and_bioinformatics_templates_use_extended_local_resources():
    templates = [
        item
        for item in AnalysisCatalog().snapshot().templates
        if item.visibility == "user" and item.category == "组学与生物信息"
    ]

    assert templates
    assert all(item.resource_class == "extended" for item in templates)


def test_data_quality_has_no_mapping_prerequisite():
    plan = AnalysisCatalog().compile(
        "data_quality",
        {},
        {
            "dataset_id": "dataset-1",
            "format": "csv",
            "columns": [{"name": "age"}, {"name": "group"}],
        },
    )

    assert plan.variable_bindings == {}
    assert plan.intent == "数据质量与变量字典"


def test_catalog_compile_uses_v4_preparation_instead_of_legacy_required_roles():
    plan = AnalysisCatalog().compile(
        "publication_table",
        {},
        {
            "dataset_id": "dataset-1",
            "format": "csv",
            "rows": 100,
            "columns": [
                {"name": "age", "semantic_type": "numeric", "unique_count": 60},
                {"name": "sex", "semantic_type": "categorical", "unique_count": 2},
                {"name": "arm", "semantic_type": "categorical", "unique_count": 2},
            ],
        },
    )

    assert plan.template_id == "publication_table"
    assert plan.resolved_inputs["continuous_variables"].value == ["age"]
    assert plan.resolved_inputs["group"].value == "arm"


def test_common_algorithm_selected_tuning_values_are_not_mandatory_user_inputs():
    catalog = AnalysisCatalog()
    expected_optional = {
        "diagnostic_roc": "阈值候选",
        "calibration_dca": "阈值范围",
        "regularized_models": "惩罚参数",
        "latent_class": "类别数候选",
    }
    for template_id, label in expected_optional.items():
        inputs = {item.label: item for item in catalog.get(template_id).input_specs}
        assert inputs[label].requirement == "optional"


def test_conditional_dag_and_mixed_model_roles_are_not_blanket_requirements():
    catalog = AnalysisCatalog()
    mixed = catalog.get("mixed_repeated")
    assert {item.label for item in mixed.input_specs if item.requirement == "critical"} == {
        "结局变量",
        "时间变量",
        "个体/中心ID",
    }
    assert {item.label: item.kind for item in mixed.input_specs}["固定效应"] == "columns"

    dag = catalog.get("dag_causal")
    assert {item.label for item in dag.input_specs if item.requirement == "critical"} == {
        "暴露", "结局", "已确认 DAG",
    }
