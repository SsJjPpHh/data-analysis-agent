from dataclasses import replace

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.readiness import (
    RUNTIME_PREFLIGHT_ISSUE_CODES,
    build_analysis_readiness_plan,
    build_template_compatibility_guidance,
    has_format_specific_preparation_route,
    uncovered_runtime_issue_codes,
)
from agent.analysis.workflows import CatalogWorkflowRegistry, validate_workflow_run_spec
from agent.analysis.workflows.validation import WorkflowValidationIssue


def _resolved_spec(template_id, profile):
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        template_id,
        dataset_profile=profile,
    )
    resolved = {
        item.id: profile["dataset_id"] if item.kind in {"dataset", "object"} else "已确认变量"
        for item in spec.data_contract.inputs
        if item.requirement == "critical"
    }
    return replace(spec, resolved_inputs=resolved)


def _scientific_profile(
    data_format,
    *,
    assets,
    object_kind,
    representation,
    processing_stage="analysis_ready",
    axes=None,
    **scientific_overrides,
):
    scientific = {
        "object_kind": object_kind,
        "representation": representation,
        "processing_stage": processing_stage,
        "axes": axes if axes is not None else {},
        **scientific_overrides,
    }
    return {
        "dataset_id": "dataset-1",
        "format": data_format,
        "object_kind": object_kind,
        "representation": representation,
        "processing_stage": processing_stage,
        "scientific": scientific,
        "assets": assets,
    }


def _asset(name, data_format, role, support="loader_backed"):
    return {
        "name": name,
        "relative_mount_name": name,
        "format": data_format,
        "role": role,
        "support": support,
    }


def _codes(report):
    return {issue.code for issue in report.issues}


def test_plain_csv_data_quality_remains_ready_without_new_scientific_metadata():
    profiles = (
        {"dataset_id": "dataset-1", "format": "csv"},
        {"dataset_id": "dataset-1", "format": "csv", "assets": []},
    )

    reports = [
        validate_workflow_run_spec(_resolved_spec("data_quality", profile), profile) for profile in profiles
    ]

    assert all(report.ready for report in reports), [report.issues for report in reports]


def test_explicit_asset_manifest_requires_exactly_one_primary_asset():
    no_primary = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "assets": [_asset("samples.csv", "csv", "sample_metadata")],
    }
    duplicate_primary = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "assets": [
            _asset("one.csv", "csv", "primary"),
            _asset("two.csv", "csv", "primary"),
        ],
    }

    missing_report = validate_workflow_run_spec(_resolved_spec("data_quality", no_primary), no_primary)
    duplicate_report = validate_workflow_run_spec(
        _resolved_spec("data_quality", duplicate_primary), duplicate_primary
    )

    assert "missing_primary_asset" in _codes(missing_report)
    assert "ambiguous_primary_asset" in _codes(duplicate_report)
    assert all(
        any("主资产" in issue.message for issue in report.issues)
        for report in (missing_report, duplicate_report)
    )


def test_blank_critical_input_is_still_unresolved():
    profile = {"dataset_id": "dataset-1", "format": "csv"}
    spec = _resolved_spec("group_tests", profile)
    first_critical = spec.data_contract.critical_inputs[0]
    spec = replace(spec, resolved_inputs={**spec.resolved_inputs, first_critical: "  "})

    report = validate_workflow_run_spec(spec, profile)

    assert "unresolved_critical_inputs" in _codes(report)


def test_r_object_and_tabular_contracts_reject_known_shape_mismatches():
    h5ad_profile = _scientific_profile(
        "h5ad",
        assets=[_asset("cells.h5ad", "h5ad", "primary")],
        object_kind="data.frame",
        representation="data_frame",
        axes={"observations": 20, "variables": 100},
    )
    seurat_profile = _scientific_profile(
        "rds",
        assets=[_asset("cells.rds", "rds", "primary")],
        object_kind="Seurat",
        representation="Seurat",
        axes={"features": 100, "cells": 20},
    )

    object_report = validate_workflow_run_spec(_resolved_spec("single_cell", h5ad_profile), h5ad_profile)
    tabular_report = validate_workflow_run_spec(
        _resolved_spec("data_quality", seurat_profile), seurat_profile
    )

    assert "dataset_shape_mismatch" in _codes(object_report)
    assert "dataset_shape_mismatch" in _codes(tabular_report)


def test_scientific_object_with_unknown_shape_or_stage_fails_closed():
    profile = {
        "dataset_id": "dataset-1",
        "format": "h5ad",
        "assets": [_asset("cells.h5ad", "h5ad", "primary")],
        "scientific": {"axes": {"features": 100, "cells": 20}},
    }

    report = validate_workflow_run_spec(_resolved_spec("single_cell", profile), profile)

    assert {"unknown_dataset_shape", "unknown_processing_stage"} <= _codes(report)


@pytest.mark.parametrize(
    ("processing_stage", "support", "upstream_required"),
    [
        ("raw", "loader_backed", ["先完成质控和定量"]),
        ("analysis_ready", "upstream_required", []),
    ],
)
def test_raw_or_upstream_only_assets_never_report_downstream_workflow_ready(
    processing_stage,
    support,
    upstream_required,
):
    profile = _scientific_profile(
        "h5ad",
        assets=[_asset("cells.h5ad", "h5ad", "primary", support)],
        object_kind="SingleCellExperiment",
        representation="SingleCellExperiment",
        processing_stage=processing_stage,
        axes={"features": 100, "cells": 20},
        upstream_required=upstream_required,
    )

    report = validate_workflow_run_spec(_resolved_spec("single_cell", profile), profile)

    assert report.ready is False
    assert "upstream_processing_required" in _codes(report)
    assert any("上游处理" in issue.message for issue in report.issues)


def test_analysis_loader_materialization_is_not_mistaken_for_external_preprocessing():
    profile = _scientific_profile(
        "fcs",
        assets=[_asset("cells.fcs", "fcs", "primary")],
        object_kind="flowFrame",
        representation="flow_frame",
        processing_stage="raw",
        axes={"events": 1000, "channels": 12},
        modality="flow_cytometry",
        upstream_required="reload with load_profile='analysis' to materialize flowFrame objects",
    )

    report = validate_workflow_run_spec(_resolved_spec("flow_cytometry", profile), profile)

    assert report.ready, report.issues
    assert "upstream_processing_required" not in _codes(report)
    assert not _codes(report).intersection(
        {"unknown_omics_axes", "missing_feature_axis", "missing_sample_axis"}
    )


def test_every_runtime_preflight_blocker_has_an_actionable_preparation_route():
    profile = {"dataset_id": "dataset-1", "format": "fastq"}
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "bulk_rnaseq",
        dataset_profile=profile,
    )
    issues = tuple(
        WorkflowValidationIssue(code, f"blocked by {code}", spec.template_id)
        for code in sorted(RUNTIME_PREFLIGHT_ISSUE_CODES)
    )

    plan = build_analysis_readiness_plan(spec, profile, issues)
    covered = {
        code
        for action in plan["actions"]
        for code in action["reason_codes"]
    }

    assert uncovered_runtime_issue_codes() == ()
    assert covered == RUNTIME_PREFLIGHT_ISSUE_CODES
    assert plan["target"]["template_id"] == "bulk_rnaseq"
    assert "不得修改或覆盖原始数据" in plan["prompt"]

    fastq_plan = build_analysis_readiness_plan(
        spec,
        profile,
        (WorkflowValidationIssue("upstream_processing_required", "FASTQ 尚未定量"),),
    )
    assert fastq_plan["primary_route_id"] == "fastq_primary_processing"


def test_all_catalog_templates_can_be_targets_of_the_shared_preparation_layer():
    registry = CatalogWorkflowRegistry(AnalysisCatalog())
    issue = WorkflowValidationIssue(
        "unsupported_dataset_format",
        "当前数据格式需要转换",
    )

    plans = [
        build_analysis_readiness_plan(
            registry.compile_template(definition.template_id, dataset_profile={"format": "json"}),
            {"dataset_id": "dataset-1", "format": "json"},
            (issue,),
        )
        for definition in registry.definitions()
    ]

    assert len(plans) == 213
    assert all(plan["actions"] and plan["target"]["template_id"] for plan in plans)


def test_only_known_source_formats_are_promoted_from_not_applicable_to_preparation():
    assert has_format_specific_preparation_route({"format": "fastq"}) is True
    assert has_format_specific_preparation_route({"format": "generic_hdf5"}) is True
    assert has_format_specific_preparation_route({"format": "csv"}) is False


def test_stale_tabular_matrix_plan_without_semantic_confirmation_fails_preflight():
    profile = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "object_kind": "data.frame",
        "representation": "data_frame",
        "modality": "tabular",
        "processing_stage": "analysis_ready",
        "assets": [_asset("clinical.csv", "csv", "primary")],
    }
    spec = _resolved_spec("bulk_rnaseq", profile)

    report = validate_workflow_run_spec(spec, profile)

    assert report.ready is False
    assert "unknown_omics_axes" in _codes(report)


def test_assay_name_without_axes_does_not_bypass_matrix_preflight():
    profile = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "object_kind": "data.frame",
        "representation": "data_frame",
        "modality": "tabular",
        "processing_stage": "analysis_ready",
        "scientific": {"assays": [{"name": "mystery"}]},
        "assets": [_asset("unknown.csv", "csv", "primary")],
    }
    spec = _resolved_spec("bulk_rnaseq", profile)

    report = validate_workflow_run_spec(spec, profile)

    assert report.ready is False
    assert "unknown_omics_axes" in _codes(report)


def test_stale_matrix_plan_with_known_wrong_scientific_modality_fails_preflight():
    profile = _scientific_profile(
        "h5ad",
        assets=[_asset("cells.h5ad", "h5ad", "primary")],
        object_kind="SingleCellExperiment",
        representation="SingleCellExperiment",
        axes={"features": 2000, "cells": 20},
        modality="single_cell",
        assays=[{"name": "counts", "dimensions": [2000, 20]}],
    )
    spec = _resolved_spec("bulk_rnaseq", profile)

    report = validate_workflow_run_spec(spec, profile)

    assert report.ready is False
    assert "scientific_modality_mismatch" in _codes(report)


def test_object_mismatch_guidance_separates_target_preparation_from_current_data_alternatives():
    profile = {"dataset_id": "dataset-1", "format": "csv", "modality": "tabular"}
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "single_cell",
        dataset_profile=profile,
    )

    guidance = build_template_compatibility_guidance(
        spec,
        profile,
        "当前数据是普通表格对象。",
        alternative_templates=(
            {"id": "data_quality", "title": "数据质量与变量字典", "reason": "先检查表格"},
        ),
    )

    assert guidance["status"] == "compatibility_guidance"
    assert guidance["primary_route_id"] == "prepare_required_r_object"
    assert guidance["alternative_templates"][0]["id"] == "data_quality"
    assert "不能被转换成另一种实验数据" in guidance["prompt"]


def test_matrix_market_workflow_requires_feature_and_barcode_sidecars():
    incomplete = _scientific_profile(
        "mtx",
        assets=[_asset("matrix.mtx.gz", "mtx", "primary")],
        object_kind="dgCMatrix",
        representation="sparse_matrix",
        axes={"features": 100, "cells": 20},
    )
    complete = {
        **incomplete,
        "assets": [
            _asset("matrix.mtx.gz", "mtx", "primary"),
            _asset("features.tsv.gz", "tsv", "features"),
            _asset("barcodes.tsv.gz", "tsv", "barcodes"),
        ],
    }

    incomplete_report = validate_workflow_run_spec(_resolved_spec("single_cell", incomplete), incomplete)
    complete_report = validate_workflow_run_spec(_resolved_spec("single_cell", complete), complete)

    assert "missing_required_asset_roles" in _codes(incomplete_report)
    assert complete_report.ready, complete_report.issues


@pytest.mark.parametrize(
    ("data_format", "primary_name", "index_format", "index_role", "index_name", "axes"),
    [
        ("bam", "sample.bam", "bam_index", "alignment_index", "sample.bam.bai", {"alignments": 50}),
        (
            "vcf",
            "cohort.vcf.gz",
            "vcf_index",
            "variant_index",
            "cohort.vcf.gz.tbi",
            {"variants": 50},
        ),
    ],
)
def test_indexed_scientific_assets_require_a_matching_index(
    data_format,
    primary_name,
    index_format,
    index_role,
    index_name,
    axes,
):
    base = _scientific_profile(
        data_format,
        assets=[_asset(primary_name, data_format, "primary")],
        object_kind="baa_scientific_handle",
        representation=f"{data_format}_handle",
        processing_stage="aligned" if data_format == "bam" else "variant_calls",
        axes=axes,
    )
    matching = {
        **base,
        "scientific": {
            **base["scientific"],
            "indexes": {"indexed": True},
            "capabilities": ["bounded_header", "indexed_region_queries"],
        },
        "assets": [
            *_scientific_profile(
                data_format,
                assets=[_asset(primary_name, data_format, "primary")],
                object_kind="ignored",
                representation="ignored",
            )["assets"],
            _asset(index_name, index_format, index_role, "upstream_required"),
        ],
    }
    mismatched = {
        **base,
        "assets": [
            _asset(primary_name, data_format, "primary"),
            _asset(f"other.{index_name.rsplit('.', 1)[-1]}", index_format, index_role),
        ],
    }
    unvalidated = {
        **matching,
        "scientific": {
            **matching["scientific"],
            "indexes": {"indexed": False},
            "capabilities": ["bounded_header"],
        },
    }

    missing_report = validate_workflow_run_spec(_resolved_spec("genomic_ranges", base), base)
    matching_report = validate_workflow_run_spec(_resolved_spec("genomic_ranges", matching), matching)
    unvalidated_report = validate_workflow_run_spec(
        _resolved_spec("genomic_ranges", unvalidated), unvalidated
    )
    mismatched_report = validate_workflow_run_spec(_resolved_spec("genomic_ranges", mismatched), mismatched)

    assert "missing_index_asset" in _codes(missing_report)
    assert matching_report.ready, matching_report.issues
    assert "unvalidated_index_content" in _codes(unvalidated_report)
    assert "index_asset_mismatch" in _codes(mismatched_report)


def test_omics_axes_and_sample_alignment_fail_closed_when_missing_or_false():
    base = _scientific_profile(
        "h5ad",
        assets=[_asset("cells.h5ad", "h5ad", "primary")],
        object_kind="SingleCellExperiment",
        representation="SingleCellExperiment",
        axes={"features": 100, "cells": 20},
    )
    no_axes = {**base, "scientific": {**base["scientific"], "axes": {}}}
    no_features = {**base, "scientific": {**base["scientific"], "axes": {"cells": 20}}}
    failed_alignment = {
        **base,
        "scientific": {**base["scientific"], "sample_alignment": {"status": "mismatch"}},
    }

    no_axes_report = validate_workflow_run_spec(_resolved_spec("single_cell", no_axes), no_axes)
    no_features_report = validate_workflow_run_spec(_resolved_spec("single_cell", no_features), no_features)
    failed_alignment_report = validate_workflow_run_spec(
        _resolved_spec("single_cell", failed_alignment), failed_alignment
    )

    assert "unknown_omics_axes" in _codes(no_axes_report)
    assert "missing_feature_axis" in _codes(no_features_report)
    assert "sample_alignment_failed" in _codes(failed_alignment_report)


def test_external_sample_metadata_requires_explicit_alignment_evidence():
    profile = _scientific_profile(
        "h5ad",
        assets=[
            _asset("cells.h5ad", "h5ad", "primary"),
            _asset("subjects.csv", "csv", "sample_metadata"),
        ],
        object_kind="SingleCellExperiment",
        representation="SingleCellExperiment",
        axes={"features": 100, "cells": 20},
    )

    report = validate_workflow_run_spec(_resolved_spec("single_cell", profile), profile)

    assert "unknown_sample_alignment" in _codes(report)
