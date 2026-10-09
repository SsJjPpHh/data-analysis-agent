from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.memory import DatasetRoleMemory
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.format_contracts import accepted_template_formats
from data.analysis_plan_store import AnalysisPlanStore
import pytest
from r_runtime.inspection import inspection_summary
from r_runtime.protocol import RArtifact, RRunResult


def _profile(*columns, object_kind="tabular", generation="generation-1"):
    return {
        "dataset_id": "dataset-1",
        "format": "csv",
        "object_kind": object_kind,
        "workspace_generation": generation,
        "profile_revision": "profile-v1",
        "rows": 120,
        "columns": list(columns),
    }


def _column(name, semantic_type, *, unique_count=8, missing_pct=0):
    return {
        "name": name,
        "type": semantic_type,
        "semantic_type": semantic_type,
        "unique_count": unique_count,
        "missing_pct": missing_pct,
    }


def test_v4_catalog_has_explicit_typed_inputs_for_every_user_template():
    snapshot = AnalysisCatalog().snapshot()
    templates = [item for item in snapshot.templates if item.visibility == "user"]

    assert snapshot.schema_version.startswith("4.")
    assert len(templates) == 213
    assert all(item.input_specs for item in templates)
    assert all(len({spec.id for spec in item.input_specs}) == len(item.input_specs) for item in templates)

    all_specs = [spec for item in templates for spec in item.input_specs]
    assert {spec.kind for spec in all_specs} <= {
        "dataset",
        "column",
        "columns",
        "level",
        "parameter",
        "object",
        "specification",
    }
    assert all(spec.requirement in {"critical", "conditional", "optional"} for spec in all_specs)
    assert not any(spec.label == "大表路径" and spec.kind in {"column", "columns"} for spec in all_specs)

    sample_size = AnalysisCatalog().get("sample_size_power")
    kinds = {spec.id: spec.kind for spec in sample_size.input_specs}
    assert kinds["effect_size"] == "parameter"
    assert kinds["alpha"] == "parameter"
    assert kinds["power"] == "parameter"
    assert kinds["allocation_ratio"] == "parameter"


def test_data_quality_is_ready_without_manual_mapping():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "data_quality",
        _profile(
            _column("patient_id", "identifier", unique_count=120),
            _column("age", "numeric", unique_count=54),
            _column("arm", "categorical", unique_count=2),
        ),
    )

    assert result.status == "ready"
    assert result.questions == ()
    assert result.plan is not None


def test_publication_table_auto_selects_eligible_variable_sets():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        _profile(
            _column("patient_id", "identifier", unique_count=120),
            _column("age", "numeric", unique_count=54),
            _column("bmi", "numeric", unique_count=73),
            _column("sex", "categorical", unique_count=2),
            _column("arm", "categorical", unique_count=2),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert set(result.plan.resolved_inputs["continuous_variables"].value) == {"age", "bmi"}
    assert set(result.plan.resolved_inputs["categorical_variables"].value) == {"sex"}
    assert result.plan.resolved_inputs["group"].value == "arm"


def test_regression_only_requests_unresolved_critical_scientific_roles():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "regression_glm",
        _profile(
            _column("measure_a", "numeric", unique_count=90),
            _column("measure_b", "numeric", unique_count=85),
            _column("site", "categorical", unique_count=4),
        ),
    )

    assert result.status == "needs_input"
    assert {question.input_id for question in result.questions} == {"outcome", "exposure"}
    assert all(question.kind == "column" for question in result.questions)


def test_group_tests_resolves_outcome_sets_without_requiring_every_outcome_type():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "group_tests",
        _profile(
            _column("arm", "categorical", unique_count=2),
            _column("score", "numeric", unique_count=78),
            _column("response", "categorical", unique_count=2),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["group"].value == "arm"
    assert result.plan.resolved_inputs["continuous_outcomes"].value == ["score"]
    assert result.plan.resolved_inputs["categorical_outcomes"].value == ["response"]
    assert "paired_marker" not in result.plan.resolved_inputs


def test_sample_size_uses_parameter_controls_instead_of_column_mapping():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "sample_size_power",
        _profile(_column("primary_outcome", "numeric", unique_count=91)),
    )

    assert result.status == "needs_input"
    assert {question.input_id for question in result.questions} == {"effect_size", "design_scope"}
    assert next(q for q in result.questions if q.input_id == "effect_size").kind == "parameter"
    assert result.plan is None
    defaults = {item.input_id: item.value for item in result.resolved_inputs if item.provenance == "default"}
    assert defaults == {"alpha": 0.05, "power": 0.8, "allocation_ratio": 1.0}


def test_large_data_engine_uses_active_dataset_not_a_fake_path_column():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "large_data_engine",
        _profile(
            _column("event_date", "datetime", unique_count=100),
            _column("region", "categorical", unique_count=8),
            _column("amount", "numeric", unique_count=115),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["dataset"].value == "dataset-1"
    assert all(item.value != "大表路径" for item in result.resolved_inputs)


def test_object_template_reports_not_applicable_for_plain_table():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "single_cell",
        _profile(
            _column("sample_id", "identifier", unique_count=120),
            _column("value", "numeric", unique_count=95),
            object_kind="tabular",
        ),
    )

    assert result.status == "not_applicable"
    assert result.plan is None
    assert "对象" in result.reason


def test_scientific_object_profile_uses_assays_instead_of_metadata_numeric_ratio():
    profile = _profile(
        _column("sample_id", "identifier", unique_count=120),
        _column("group", "categorical", unique_count=2),
        object_kind="SingleCellExperiment",
    )
    profile.update(
        {
            "format": "rds",
            "modality": "single_cell",
            "scientific": {
                "modality": "single_cell",
                "representation": "SingleCellExperiment",
                "assays": [{"name": "counts", "dimensions": [2000, 120]}],
                "axes": {"features": 2000, "samples": 120},
            },
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare("single_cell", profile)

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["data_object"].value == "dataset-1"


def test_generic_summarized_experiment_is_not_misclassified_as_atac():
    profile = _profile(
        _column("sample_id", "identifier", unique_count=20),
        _column("group", "categorical", unique_count=2),
        object_kind="SummarizedExperiment",
    )
    profile.update(
        {
            "format": "rds",
            "modality": "omics_matrix",
            "scientific": {
                "modality": "omics_matrix",
                "representation": "SummarizedExperiment",
                "assays": [{"name": "counts", "dimensions": [100, 20]}],
            },
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare("omics_atac_chip_binding", profile)

    assert result.status == "not_applicable"


@pytest.mark.parametrize(
    ("template_id", "data_format", "object_kind"),
    [
        ("single_cell", "h5ad", "matrix"),
        ("single_cell", "mtx", "matrix"),
        ("flow_cytometry", "fcs", "flowFrame"),
        ("genomic_ranges", "bed", "GRanges"),
        ("microbiome_amplicon", "biom", "biom"),
        ("proteomics_ms", "mzml", "MSnExp"),
        ("omics_variant_burden_annotation", "vcf", "VCF"),
    ],
)
def test_native_scientific_formats_reach_their_matching_template_workflow(
    template_id, data_format, object_kind
):
    profile = _profile(object_kind=object_kind)
    profile.update(
        {
            "format": data_format,
            "assets": [{"format": data_format, "role": "primary"}],
            "modality": "matrix",
            "rows": 100,
            "columns": [
                _column("sample_id", "identifier", unique_count=100),
                _column("group", "categorical", unique_count=2),
                _column("feature_1", "numeric", unique_count=80),
                _column("feature_2", "numeric", unique_count=81),
                _column("feature_3", "numeric", unique_count=82),
            ],
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare(template_id, profile)

    assert result.status != "not_applicable", result.reason


def test_known_single_cell_modality_cannot_enter_bulk_rnaseq_matrix_workflow():
    profile = _profile(
        _column("sample_id", "identifier", unique_count=20),
        _column("group", "categorical", unique_count=2),
        object_kind="SingleCellExperiment",
    )
    profile.update(
        {
            "format": "h5ad",
            "assets": [{"format": "h5ad", "role": "primary"}],
            "modality": "single_cell",
            "scientific": {
                "modality": "single_cell",
                "representation": "SingleCellExperiment",
                "assays": [{"name": "counts", "dimensions": [2000, 20]}],
                "axes": {"features": 2000, "cells": 20},
            },
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare("bulk_rnaseq", profile)

    assert result.status == "not_applicable"
    assert result.plan is None
    assert "single_cell" in result.reason
    assert "跨实验模态" in result.reason


def test_generic_rds_object_does_not_turn_phyloseq_into_a_trained_ml_model():
    profile = _profile(
        _column("sample_id", "identifier", unique_count=20),
        _column("outcome", "categorical", unique_count=2),
        _column("feature", "numeric", unique_count=18),
        object_kind="phyloseq",
    )
    profile.update(
        {
            "format": "rds",
            "assets": [{"format": "rds", "role": "primary"}],
            "modality": "microbiome",
            "scientific": {"modality": "microbiome", "representation": "phyloseq"},
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare("ml_explainability", profile)

    assert result.status == "not_applicable"
    assert "结构化 R 分析对象" in result.reason


def test_assay_name_without_feature_and_sample_axes_is_not_matrix_evidence():
    profile = _profile(
        _column("measure_a", "numeric", unique_count=80),
        _column("measure_b", "numeric", unique_count=82),
        _column("measure_c", "numeric", unique_count=81),
        _column("group", "categorical", unique_count=2),
        object_kind="data.frame",
    )
    profile["scientific"] = {"assays": [{"name": "mystery"}]}

    result = AnalysisPreparer(AnalysisCatalog()).prepare("bulk_rnaseq", profile)

    assert result.status == "needs_input"
    assert result.plan is None
    assert {item.input_id for item in result.questions} == {"__matrix_orientation__"}


def test_scientific_format_contract_preserves_every_catalog_baseline():
    for template in AnalysisCatalog().snapshot().templates:
        if template.visibility != "user":
            continue
        accepted = accepted_template_formats(template)
        assert accepted[: len(template.formats)] == template.formats
        assert "rdata" not in accepted


def test_bundle_format_matching_uses_all_assets_not_only_the_primary_member():
    profile = _profile(
        _column("chromosome", "categorical", unique_count=24),
        _column("start", "numeric", unique_count=100),
        _column("end", "numeric", unique_count=100),
        object_kind="GRanges",
    )
    profile.update(
        {
            "format": "tsv",
            "assets": [
                {"format": "tsv", "role": "sample_metadata"},
                {"format": "bed", "role": "primary"},
            ],
        }
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare("genomic_ranges", profile)

    assert result.status != "not_applicable", result.reason
    assert result.plan is None


def test_inspection_summary_preserves_object_level_scientific_structure(tmp_path):
    profile_path = tmp_path / "dataset_profile.csv"
    profile_path.write_text(
        "column,type,missing,missing_pct,non_missing,unique_count,example\n"
        "sample_id,character,0,0,5,5,\n"
        "group,factor,0,0,5,2,\n",
        encoding="utf-8",
    )
    levels_path = tmp_path / "dataset_levels.csv"
    levels_path.write_text("column,level\ngroup,A\ngroup,B\n", encoding="utf-8")
    overview_path = tmp_path / "dataset_overview.txt"
    overview_path.write_text(
        "Rows: 5\nColumns: 2\nObject kind: SummarizedExperiment\nModality: omics_matrix",
        encoding="utf-8",
    )
    structure_path = tmp_path / "dataset_structure.txt"
    structure_path.write_text(
        '{"schema_version":"2.0","object_kind":"SummarizedExperiment",'
        '"object_classes":["SummarizedExperiment"],"modality":"omics_matrix",'
        '"representation":"SummarizedExperiment","processing_stage":"analysis_ready",'
        '"axes":{"features":4,"samples":5},'
        '"assays":[{"name":"counts","dimensions":[4,5]}],'
        '"sample_metadata":["sample_id","group"]}',
        encoding="utf-8",
    )
    result = RRunResult(
        ok=True,
        tables=[
            RArtifact("dataset_profile", str(profile_path), "table", "text/csv"),
            RArtifact("dataset_levels", str(levels_path), "table", "text/csv"),
        ],
        texts=[
            RArtifact("dataset_overview", str(overview_path), "text", "text/plain"),
            RArtifact("dataset_structure", str(structure_path), "text", "application/json"),
        ],
    )

    summary = inspection_summary(result)

    assert summary["modality"] == "omics_matrix"
    assert summary["scientific"]["axes"] == {"features": 4, "samples": 5}
    assert summary["scientific"]["assays"][0]["name"] == "counts"


def test_event_level_questions_use_local_levels_not_column_names():
    profile = _profile(
        _column("followup_time", "numeric", unique_count=94),
        _column("event_type", "categorical", unique_count=3),
    )
    profile["local_semantics"] = {"levels": {"event_type": ["0", "1", "2"]}}

    result = AnalysisPreparer(AnalysisCatalog()).prepare("competing_risk", profile)

    assert result.status == "needs_input"
    questions = {item.input_id: item for item in result.questions}
    assert [item.value for item in questions["target_event_level"].candidates] == ["0", "1", "2"]
    assert set(questions) == {"target_event_level"}
    assert all(item.kind == "level" for item in questions.values())


def test_missing_required_numeric_shape_is_not_applicable_instead_of_empty_dropdown():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "nonlinear_spline_gam",
        _profile(
            _column("group", "categorical", unique_count=3),
            _column("site", "categorical", unique_count=4),
        ),
    )

    assert result.status == "not_applicable"
    assert "数值" in result.reason


def test_survival_time_remains_scientifically_required():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "survival_km_cox",
        _profile(
            _column("event_status", "categorical", unique_count=2),
            _column("treatment_group", "categorical", unique_count=2),
        ),
    )

    assert result.status == "not_applicable"
    assert "随访时间" in result.reason


def test_diagnostic_meta_does_not_silently_drop_tn():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "diagnostic_meta",
        _profile(
            _column("tp", "numeric", unique_count=20),
            _column("fp", "numeric", unique_count=20),
            _column("fn", "numeric", unique_count=20),
            _column("study_id", "identifier", unique_count=120),
        ),
        {"TP": "tp", "FP": "fp", "FN": "fn", "研究ID": "study_id"},
    )

    assert result.status == "not_applicable"
    assert "TN" in result.reason


def test_plain_clinical_table_is_not_accepted_as_an_omics_matrix():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "bulk_rnaseq",
        _profile(
            _column("patient_id", "identifier", unique_count=120),
            _column("age", "numeric", unique_count=54),
            _column("score", "numeric", unique_count=85),
            _column("arm", "categorical", unique_count=2),
        ),
    )

    assert result.status == "not_applicable"
    assert "矩阵" in result.reason


@pytest.mark.parametrize(
    "template_id",
    [
        "bulk_rnaseq",
        "microarray_expression",
        "wgcna_network",
        "microbiome_amplicon",
        "proteomics_ms",
        "multiomics_integration",
        "rnaseq_batch_qc",
        "omics_metabolomics_pathway",
    ],
)
def test_numeric_clinical_table_never_auto_freezes_dataset_as_scientific_matrix(template_id):
    profile = _profile(
        _column("age", "numeric", unique_count=54),
        _column("systobp", "numeric", unique_count=100),
        _column("bmi", "numeric", unique_count=90),
        _column("hdl", "numeric", unique_count=35),
        _column("gender", "categorical", unique_count=2),
        object_kind="data.frame",
    )
    profile.update({"modality": "tabular", "representation": "data_frame"})

    result = AnalysisPreparer(AnalysisCatalog()).prepare(template_id, profile)

    assert result.status == "needs_input"
    assert result.plan is None
    assert "data_object" not in {item.input_id for item in result.resolved_inputs}
    question = next(item for item in result.questions if item.input_id == "__matrix_orientation__")
    assert {item.value for item in question.candidates} == {
        "samples_by_features",
        "requires_restructure",
        "not_target_matrix",
    }


def test_confirmed_sample_by_feature_matrix_records_semantics_before_object_binding():
    profile = _profile(
        _column("gene_a", "numeric", unique_count=80),
        _column("gene_b", "numeric", unique_count=82),
        _column("gene_c", "numeric", unique_count=81),
        _column("group", "categorical", unique_count=2),
        object_kind="data.frame",
    )
    profile.update({"modality": "tabular", "representation": "data_frame"})

    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "bulk_rnaseq",
        profile,
        {"__matrix_orientation__": "samples_by_features", "group": "group",
         "feature_columns": ["gene_a", "gene_b", "gene_c"]},
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["__matrix_orientation__"].value == "samples_by_features"
    assert result.plan.resolved_inputs["__matrix_orientation__"].provenance == "user"
    assert result.plan.resolved_inputs["data_object"].value == "dataset-1"


def test_wgcna_sample_trait_never_auto_binds_the_sample_identifier_column():
    profile = _profile(
        _column("sample", "identifier", unique_count=120),
        _column("trait", "numeric", unique_count=75),
        _column("gene_a", "numeric", unique_count=82),
        _column("gene_b", "numeric", unique_count=81),
        _column("gene_c", "numeric", unique_count=79),
        _column("group", "categorical", unique_count=2),
        object_kind="data.frame",
    )
    profile.update({"modality": "tabular", "representation": "data_frame"})

    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "wgcna_network",
        profile,
        {"__matrix_orientation__": "samples_by_features", "group": "group", "input_scale":"log2",
         "feature_columns":["gene_a", "gene_b", "gene_c"]},
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["sample_trait"].value == "trait"
    assert result.plan.resolved_inputs["sample_trait"].value != "sample"


@pytest.mark.parametrize("answer", ["requires_restructure", "not_target_matrix"])
def test_matrix_semantics_that_are_not_directly_runnable_return_guidance_state(answer):
    profile = _profile(
        _column("feature_a", "numeric", unique_count=80),
        _column("feature_b", "numeric", unique_count=82),
        _column("feature_c", "numeric", unique_count=81),
        _column("group", "categorical", unique_count=2),
        object_kind="data.frame",
    )

    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "bulk_rnaseq",
        profile,
        {"__matrix_orientation__": answer},
    )

    assert result.status == "not_applicable"
    assert result.plan is None
    assert "矩阵" in result.reason


def test_curated_template_contract_removes_cloned_irrelevant_roles():
    matched = AnalysisCatalog().get("matched_case_control")
    assert {item.label for item in matched.input_specs if item.requirement == "critical"} == {
        "匹配组ID",
        "病例状态",
        "主要暴露变量",
    }
    assert "经纬度/区域ID" not in {item.label for item in matched.input_specs}

    randomization = AnalysisCatalog().get("randomization_allocation")
    assert {item.label: item.kind for item in randomization.input_specs}["样本量"] == "parameter"


def test_catalog_contracts_do_not_mix_survival_meta_prediction_and_omics_semantics():
    catalog = AnalysisCatalog()

    table_one = catalog.get("publication_table")
    assert all("数据泄漏" not in item for item in table_one.assumptions)

    sample_size = catalog.get("sample_size_power")
    assert all("随访时间和事件状态" not in item for item in sample_size.prerequisites)
    assert all("研究层面的效应量" not in item for item in sample_size.prerequisites)

    randomization = catalog.get("randomization_allocation")
    assert all("发表偏倚" not in item and item != "异质性" for item in randomization.diagnostics)

    rnaseq_qc = catalog.get("rnaseq_batch_qc")
    assert all("临床净获益" not in item for item in rnaseq_qc.diagnostics)
    assert any("设计矩阵" in item for item in rnaseq_qc.diagnostics)

    imbalance = catalog.get("imbalanced_classification")
    threshold = next(item for item in imbalance.input_specs if item.id == "threshold")
    assert threshold.kind == "parameter"
    assert threshold.requirement == "optional"


def test_catalog_contracts_do_not_reuse_a_semantic_memory_role_within_one_template():
    for template in AnalysisCatalog().snapshot().templates:
        roles = [item.semantic_role for item in template.input_specs]
        assert len(roles) == len(set(roles)), template.id


def test_ml_workflow_and_safety_meta_remove_duplicate_or_mistyped_inputs():
    machine_learning = AnalysisCatalog().get("tabular_ml_workflow")
    assert {item.label for item in machine_learning.input_specs if item.requirement == "critical"} == {
        "预测结局",
        "特征变量",
    }

    safety_meta = AnalysisCatalog().get("safety_meta_analysis")
    kinds = {item.label: item.kind for item in safety_meta.input_specs}
    assert kinds["治疗组样本量"] == "column"
    assert kinds["对照组样本量"] == "column"


def test_generic_feature_sets_are_inferred_after_reserving_scalar_roles():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "tabular_ml_workflow",
        _profile(
            _column("patient_id", "identifier", unique_count=120),
            _column("outcome", "numeric", unique_count=80),
            _column("age", "numeric", unique_count=54),
            _column("biomarker", "numeric", unique_count=100),
            _column("arm", "categorical", unique_count=2),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["outcome"].value == "outcome"
    assert result.plan.resolved_inputs["features"].value == ["age", "arm", "biomarker"]


def test_set_inference_reserves_a_later_unambiguous_group_column():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "multiple_testing_endpoints",
        _profile(
            _column("endpoint_a", "numeric", unique_count=80),
            _column("endpoint_b", "numeric", unique_count=70),
            _column("arm", "categorical", unique_count=2),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["outcome"].value == ["endpoint_a", "endpoint_b"]
    assert result.plan.resolved_inputs["group"].value == "arm"


def test_standard_safety_meta_column_names_prepare_without_five_manual_mappings():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "safety_meta_analysis",
        _profile(
            _column("study_id", "identifier", unique_count=20),
            _column("treat_events", "numeric", unique_count=12),
            _column("treat_n", "numeric", unique_count=16),
            _column("control_events", "numeric", unique_count=10),
            _column("control_n", "numeric", unique_count=17),
        ),
    )

    assert result.status == "ready"
    assert result.questions == ()


def test_standard_origin_and_facility_coordinates_are_distinguished_automatically():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "distance_accessibility",
        _profile(
            _column("origin_lon", "numeric", unique_count=110),
            _column("origin_lat", "numeric", unique_count=112),
            _column("facility_lon", "numeric", unique_count=42),
            _column("facility_lat", "numeric", unique_count=43),
        ),
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert {item.label: item.value for item in result.plan.resolved_inputs.values()} == {
        "起点经度": "origin_lon",
        "起点纬度": "origin_lat",
        "设施经度": "facility_lon",
        "设施纬度": "facility_lat",
        "直线覆盖阈值米": 5000,
    }


def test_user_confirmed_column_must_satisfy_the_typed_contract():
    with pytest.raises(ValueError, match="Invalid value"):
        AnalysisPreparer(AnalysisCatalog()).prepare(
            "survival_km_cox",
            _profile(
                _column("followup_time", "numeric", unique_count=80),
                _column("event", "categorical", unique_count=2),
                _column("site", "categorical", unique_count=4),
            ),
            {"followup_time": "site"},
        )


def test_user_confirmed_event_level_must_exist_in_the_parent_column():
    profile = _profile(
        _column("followup_time", "numeric", unique_count=94),
        _column("event_type", "categorical", unique_count=3),
    )
    profile["local_semantics"] = {"levels": {"event_type": ["0", "1", "2"]}}

    with pytest.raises(ValueError, match="Invalid value"):
        AnalysisPreparer(AnalysisCatalog()).prepare(
            "competing_risk",
            profile,
            {"target_event_level": "9"},
        )


def test_role_memory_is_scoped_to_dataset_generation_and_profile_revision():
    memory = DatasetRoleMemory()
    memory.remember(
        dataset_id="dataset-1",
        workspace_generation="generation-1",
        profile_revision="profile-v1",
        semantic_role="group",
        value="arm",
    )

    assert (
        memory.recall(
            dataset_id="dataset-1",
            workspace_generation="generation-1",
            profile_revision="profile-v1",
            semantic_role="group",
        )
        == "arm"
    )
    assert (
        memory.recall(
            dataset_id="dataset-1",
            workspace_generation="generation-2",
            profile_revision="profile-v1",
            semantic_role="group",
        )
        is None
    )
    assert (
        memory.recall(
            dataset_id="dataset-1",
            workspace_generation="generation-1",
            profile_revision="profile-v2",
            semantic_role="group",
        )
        is None
    )


def test_inspection_summary_builds_local_semantic_profile_without_raw_rows(tmp_path):
    profile_path = tmp_path / "dataset_profile.csv"
    profile_path.write_text(
        "column,type,missing,missing_pct,non_missing,unique_count,example\n"
        "patient_id,character,0,0,3,3,\n"
        "arm,factor,0,0,3,2,\n"
        "age,numeric,0,0,3,3,\n",
        encoding="utf-8",
    )
    levels_path = tmp_path / "dataset_levels.csv"
    levels_path.write_text("column,level\narm,A\narm,B\n", encoding="utf-8")
    overview_path = tmp_path / "dataset_overview.txt"
    overview_path.write_text("Rows: 3\nColumns: 3\nObject kind: data.frame", encoding="utf-8")
    result = RRunResult(
        ok=True,
        tables=(
            RArtifact("dataset_profile", str(profile_path), "table", "text/csv"),
            RArtifact("dataset_levels", str(levels_path), "table", "text/csv"),
        ),
        texts=(RArtifact("dataset_overview", str(overview_path), "text", "text/plain"),),
    )

    summary = inspection_summary(result)

    assert summary["object_kind"] == "data.frame"
    assert summary["profile_revision"]
    assert summary["variables"][0]["semantic_type"] == "identifier"
    assert summary["variables"][1]["semantic_type"] == "categorical"
    assert summary["variables"][2]["semantic_type"] == "numeric"
    assert summary["variables"][1]["unique_count"] == 2
    assert summary["local_semantics"]["levels"] == {"arm": ["A", "B"]}


def test_inspection_treats_named_binary_numeric_group_as_categorical(tmp_path):
    profile_path = tmp_path / "dataset_profile.csv"
    profile_path.write_text(
        "column,type,missing,missing_pct,non_missing,unique_count,example\n"
        "treatment_group,numeric,0,0,100,2,\n",
        encoding="utf-8",
    )
    overview_path = tmp_path / "dataset_overview.txt"
    overview_path.write_text("Rows: 100\nColumns: 1\nObject kind: data.frame", encoding="utf-8")
    result = RRunResult(
        ok=True,
        tables=(RArtifact("dataset_profile", str(profile_path), "table", "text/csv"),),
        texts=(RArtifact("dataset_overview", str(overview_path), "text", "text/plain"),),
    )

    assert inspection_summary(result)["variables"][0]["semantic_type"] == "categorical"


def test_inspection_recognizes_common_numeric_category_codes_without_retyping_scores(tmp_path):
    profile_path = tmp_path / "dataset_profile.csv"
    profile_path.write_text(
        "column,type,missing,missing_pct,non_missing,unique_count,example\n"
        "hypertension,numeric,0,0,100,2,\n"
        "edu,numeric,0,0,100,3,\n"
        "medication,numeric,0,0,100,3,\n"
        "age,numeric,0,0,100,2,\n"
        "depression_score,numeric,0,0,100,3,\n",
        encoding="utf-8",
    )
    overview_path = tmp_path / "dataset_overview.txt"
    overview_path.write_text("Rows: 100\nColumns: 5\nObject kind: data.frame", encoding="utf-8")
    result = RRunResult(
        ok=True,
        tables=(RArtifact("dataset_profile", str(profile_path), "table", "text/csv"),),
        texts=(RArtifact("dataset_overview", str(overview_path), "text", "text/plain"),),
    )

    variables = {item["name"]: item for item in inspection_summary(result)["variables"]}

    assert variables["hypertension"]["semantic_type"] == "categorical"
    assert variables["edu"]["semantic_type"] == "categorical"
    assert variables["medication"]["semantic_type"] == "categorical"
    assert variables["hypertension"]["numeric_coding"] == "labels_unconfirmed"
    assert variables["age"]["semantic_type"] == "numeric"
    assert variables["depression_score"]["semantic_type"] == "numeric"


@pytest.mark.parametrize(
    "name", ["age_group", "bmi_category", "score_group", "income_group"]
)
def test_structural_group_suffix_overrides_continuous_numeric_stem(name):
    from agent.analysis.semantics import infer_semantic_type

    assert infer_semantic_type(name, "numeric", 4, 100) == "categorical"


def test_unknown_two_value_numeric_sample_is_not_automatically_relabeled():
    from agent.analysis.semantics import infer_semantic_type

    assert infer_semantic_type("measurement_x", "numeric", 2, 2) == "numeric"


def test_frozen_plan_store_rejects_a_different_dataset_generation(tmp_path):
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "data_quality",
        _profile(_column("age", "numeric", unique_count=80)),
    )
    assert prepared.plan is not None
    store = AnalysisPlanStore(tmp_path / "plans")

    store.save("session-one", prepared.plan)

    restored = store.load(
        "session-one",
        prepared.plan.plan_id,
        dataset_id="dataset-1",
        workspace_generation="generation-1",
        profile_revision="profile-v1",
    )
    assert restored == prepared.plan
    assert (
        store.load(
            "session-one",
            prepared.plan.plan_id,
            dataset_id="dataset-1",
            workspace_generation="generation-2",
            profile_revision="profile-v1",
        )
        is None
    )


def test_frozen_plan_store_keeps_plans_for_long_lived_result_continuations(tmp_path, monkeypatch):
    import data.analysis_plan_store as plan_store_module

    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "data_quality",
        _profile(_column("age", "numeric", unique_count=80)),
    )
    assert prepared.plan is not None
    now = 1_700_000_000.0
    monkeypatch.setattr(plan_store_module.time, "time", lambda: now)
    store = AnalysisPlanStore(tmp_path / "plans")
    store.save("session-one", prepared.plan)

    now += 30 * 24 * 60 * 60
    restored = store.load(
        "session-one",
        prepared.plan.plan_id,
        dataset_id="dataset-1",
        workspace_generation="generation-1",
        profile_revision="profile-v1",
    )

    assert restored == prepared.plan


def test_frozen_plan_store_ignores_a_non_object_record(tmp_path):
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "data_quality",
        _profile(_column("age", "numeric", unique_count=80)),
    )
    assert prepared.plan is not None
    store = AnalysisPlanStore(tmp_path / "plans")
    store.save("session-one", prepared.plan)
    record_path = next((tmp_path / "plans").rglob("*.json"))
    record_path.write_text("[]", encoding="utf-8")

    assert (
        store.load(
            "session-one",
            prepared.plan.plan_id,
            dataset_id="dataset-1",
            workspace_generation="generation-1",
            profile_revision="profile-v1",
        )
        is None
    )


def test_noninferiority_requires_design_choices_and_freezes_selected_levels():
    profile = _profile(
        _column("y", "numeric", unique_count=110),
        _column("arm", "categorical", unique_count=2),
        _column("population", "categorical", unique_count=2),
    )
    profile["local_semantics"] = {"levels": {"arm": ["A", "B"], "population": ["ITT", "PP"]}}
    inputs = {"outcome": "y", "group": "arm", "analysis_population": "population", "noninferiority_margin": 1}
    preparer = AnalysisPreparer(AnalysisCatalog())
    pending = preparer.prepare("noninferiority_equivalence", profile, inputs)
    assert pending.status == "needs_input"
    assert {"reference_group", "analysis_population_level", "outcome_direction"} <= {
        item.input_id for item in pending.questions
    }
    choices = {"reference_group": "B", "analysis_population_level": "PP", "outcome_direction": "lower_better"}
    prepared = preparer.prepare("noninferiority_equivalence", profile, {**inputs, **choices})
    assert prepared.status == "ready"
    for name, value in choices.items():
        assert prepared.plan.resolved_inputs[name].value == value
    with pytest.raises(ValueError, match="Invalid value"):
        preparer.prepare("noninferiority_equivalence", profile, {**inputs, **choices, "reference_group": "C"})
