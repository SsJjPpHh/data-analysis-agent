"""Static capability checks for the catalog-backed workflow registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.format_contracts import (
    ANALYSIS_MATERIALIZABLE_FORMATS,
    SCIENTIFIC_FEATURE_AXIS_NAMES,
    SCIENTIFIC_SAMPLE_AXIS_NAMES,
    accepted_template_formats,
    dataset_asset_formats,
    has_scientific_matrix_evidence,
    scientific_matrix_modality_compatibility,
    scientific_matrix_semantic_label,
    scientific_template_formats,
)
from agent.analysis.workflows.contracts import WorkflowDefinition, WorkflowRunSpec


_BASE_TABULAR_FORMATS = frozenset({"csv", "tsv", "xlsx", "xls", "sav", "dta", "parquet"})
_RAW_PROCESSING_STAGES = frozenset(
    {
        "raw",
        "unprocessed",
        "upstream_required",
        "preprocessing_required",
        "not_ready",
    }
)
_UNKNOWN_PROCESSING_STAGES = frozenset({"", "unknown", "unspecified", "not_inspected", "na", "n/a"})
_TABULAR_SHAPE_MARKERS = (
    "data.frame",
    "data_frame",
    "dataframe",
    "data.table",
    "tbl_df",
    "lazy_duckdb",
    "lazy_table",
    "tabular",
)
_MATRIX_SHAPE_MARKERS = (
    "matrix",
    "dgcmatrix",
    "sparsematrix",
    "sparse_matrix",
)
_INDEX_SPECS: Mapping[str, tuple[str, frozenset[str], tuple[str, ...]]] = {
    "bam": ("alignment_index", frozenset({"bam_index", "bai"}), (".bai",)),
    "cram": ("alignment_index", frozenset({"cram_index", "crai"}), (".crai",)),
    "vcf": ("variant_index", frozenset({"vcf_index", "tbi", "csi"}), (".tbi", ".csi")),
    "bcf": ("variant_index", frozenset({"vcf_index", "csi"}), (".csi",)),
}
_INDEX_FORMATS = frozenset(
    index_format for _, index_formats, _ in _INDEX_SPECS.values() for index_format in index_formats
)
_INDEX_ROLES = frozenset({"index", "alignment_index", "variant_index"})
_FEATURE_AXIS_NAMES = frozenset(
    {
        *SCIENTIFIC_FEATURE_AXIS_NAMES,
        "variable",
        "variables",
        "taxa",
        "variant",
        "variants",
        "range",
        "ranges",
        "alignment",
        "alignments",
        "sequence",
        "sequences",
        "spectrum",
        "spectra",
        "peak",
        "peaks",
        "channel",
        "channels",
        "event",
        "events",
        "observation",
        "observations",
        "read",
        "reads",
    }
)
_SAMPLE_AXIS_NAMES = frozenset(
    {
        *SCIENTIFIC_SAMPLE_AXIS_NAMES,
        "subject",
        "subjects",
        "individual",
        "individuals",
        "spot",
        "spots",
    }
)
_EXTERNAL_SAMPLE_ROLES = frozenset({"sample_metadata", "covariates", "phenotype"})
_NON_MATRIX_AXIS_MODALITIES = frozenset(
    {
        "aligned_reads",
        "flow_cytometry",
        "genomic_ranges",
        "genomic_signal",
        "genotypes",
        "mass_spectrometry",
        "microbiome_artifact",
        "phylogenetic_tree",
        "sequences",
        "sequencing_reads",
        "somatic_variants",
        "variants",
    }
)


@dataclass(frozen=True)
class WorkflowValidationIssue:
    code: str
    message: str
    template_id: str = ""


@dataclass(frozen=True)
class WorkflowValidationReport:
    checked_templates: int
    checked_definitions: int
    issues: tuple[WorkflowValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> None:
        if self.valid:
            return
        details = "; ".join(
            f"{item.code}[{item.template_id or 'registry'}]: {item.message}" for item in self.issues
        )
        raise ValueError(f"workflow registry validation failed: {details}")


@dataclass(frozen=True)
class WorkflowPreflightReport:
    """Dataset-specific checks completed before any R process is started."""

    ready: bool
    issues: tuple[WorkflowValidationIssue, ...] = ()


def _normalized(value: object) -> str:
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


def _scientific_profile(dataset_profile: Mapping[str, Any]) -> Mapping[str, Any]:
    scientific = dataset_profile.get("scientific")
    return scientific if isinstance(scientific, Mapping) else {}


def _profile_assets(dataset_profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw_assets = dataset_profile.get("assets")
    if not isinstance(raw_assets, (list, tuple)):
        return ()
    return tuple(asset for asset in raw_assets if isinstance(asset, Mapping))


def _asset_role(asset: Mapping[str, Any]) -> str:
    return _normalized(asset.get("role"))


def _asset_format(asset: Mapping[str, Any]) -> str:
    return _normalized(asset.get("format")).lstrip(".")


def _asset_name(asset: Mapping[str, Any]) -> str:
    value = str(asset.get("name") or asset.get("relative_mount_name") or "").strip()
    if not value:
        return ""
    return PurePosixPath(value.replace("\\", "/")).name.casefold()


def _append_issue(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    code: str,
    message: str,
) -> None:
    issues.append(WorkflowValidationIssue(code, message, run_spec.template_id))


def _has_resolved_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return bool(value)
    return True


def _profile_shape(dataset_profile: Mapping[str, Any]) -> str:
    scientific = _scientific_profile(dataset_profile)
    evidence = tuple(
        _normalized(value)
        for value in (
            dataset_profile.get("object_kind"),
            dataset_profile.get("representation"),
            scientific.get("object_kind"),
            scientific.get("representation"),
        )
        if str(value or "").strip()
    )
    if not evidence:
        return "unknown"
    normalized = "|".join(evidence)
    if any(marker in normalized for marker in _TABULAR_SHAPE_MARKERS):
        return "tabular"
    if any(marker in normalized for marker in _MATRIX_SHAPE_MARKERS):
        return "matrix"
    if all(value in {"list", "unknown", "unspecified"} for value in evidence):
        return "unknown"
    return "r_object"


def _is_scientific_context(
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    actual_formats: Sequence[str],
    profile_shape: str,
) -> bool:
    scientific = _scientific_profile(dataset_profile)
    modality = _normalized(scientific.get("modality") or dataset_profile.get("modality"))
    if run_spec.data_contract.shape == "r_object":
        return True
    if set(actual_formats).intersection(scientific_template_formats(run_spec.template_id)):
        return True
    if run_spec.definition.category == "组学与生物信息" and profile_shape in {"matrix", "r_object"}:
        return True
    return bool(modality and modality not in {"tabular", "table"})


def _validate_profile_shape(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    actual_formats: Sequence[str],
) -> str:
    profile_shape = _profile_shape(dataset_profile)
    expected_shape = run_spec.data_contract.shape
    if expected_shape == "r_object":
        if profile_shape == "unknown":
            _append_issue(
                issues,
                run_spec,
                "unknown_dataset_shape",
                "无法确认科学数据的 object_kind/representation；请先完成数据检查，再运行该对象工作流。",
            )
        elif profile_shape == "tabular":
            # Declared table-to-matrix adapters remain subject to explicit
            # orientation/feature bindings and scientific modality checks.
            # Do not reject their actual inspected data.frame before that gate.
            if not (set(actual_formats).issubset(_BASE_TABULAR_FORMATS | {"rds", "rdata"})
                    and any(item.kind == "object" and "numeric_matrix" in item.accepts
                            for item in run_spec.data_contract.inputs)):
                _append_issue(
                    issues,
                    run_spec,
                    "dataset_shape_mismatch",
                    "当前模板需要 R 对象或矩阵，但检查结果是普通表格；请改用表格模板或上传正确对象。",
                )
        return profile_shape

    if profile_shape == "unknown":
        if actual_formats and set(actual_formats) <= _BASE_TABULAR_FORMATS:
            return profile_shape
        _append_issue(
            issues,
            run_spec,
            "unknown_dataset_shape",
            "无法确认数据的 object_kind/representation；请先完成数据检查，确认它能提供表格或矩阵输入。",
        )
        return profile_shape

    if profile_shape != "r_object":
        return profile_shape

    native_object_allowed = bool(
        set(actual_formats).intersection(scientific_template_formats(run_spec.template_id))
        or (run_spec.data_contract.object_inputs and run_spec.definition.category == "组学与生物信息")
    )
    if not native_object_allowed:
        _append_issue(
            issues,
            run_spec,
            "dataset_shape_mismatch",
            "当前模板需要表格或矩阵，但检查结果是专用 R 对象；请选择匹配该对象类型的科学模板。",
        )
    return profile_shape


def _flatten_requirements(value: object) -> tuple[str, ...]:
    if value in (None, False, ""):
        return ()
    if value is True:
        return ("数据声明需要先完成上游处理",)
    if isinstance(value, Mapping):
        values: list[str] = []
        for key, nested in value.items():
            if nested in (None, False, "", (), [], {}):
                continue
            if nested is True:
                values.append(str(key))
            elif isinstance(nested, (str, int, float)):
                values.append(f"{key}: {nested}")
            else:
                values.extend(_flatten_requirements(nested))
        return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))
    if isinstance(value, (list, tuple, set, frozenset)):
        values = [item for nested in value for item in _flatten_requirements(nested)]
        return tuple(dict.fromkeys(values))
    text = str(value).strip()
    return (text,) if text else ()


def _validate_processing_stage(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    primary_asset: Mapping[str, Any] | None,
    *,
    scientific_context: bool,
) -> bool:
    scientific = _scientific_profile(dataset_profile)
    stage = _normalized(dataset_profile.get("processing_stage") or scientific.get("processing_stage"))
    requirements = tuple(
        dict.fromkeys(
            (
                *_flatten_requirements(dataset_profile.get("upstream_required")),
                *_flatten_requirements(scientific.get("upstream_required")),
            )
        )
    )
    primary_format = _asset_format(primary_asset) if primary_asset is not None else ""
    materialized_at_analysis = primary_format in ANALYSIS_MATERIALIZABLE_FORMATS
    if materialized_at_analysis:
        requirements = tuple(
            item
            for item in requirements
            if not (
                "load_profile='analysis'" in item
                or 'load_profile="analysis"' in item
                or "reload with analysis" in item.casefold()
            )
        )
    support = _normalized(primary_asset.get("support")) if primary_asset is not None else ""
    upstream_blocked = bool(
        requirements
        or support == "upstream_required"
        or (stage in _RAW_PROCESSING_STAGES and not materialized_at_analysis)
    )
    if upstream_blocked:
        details = "；".join(requirements[:4])
        if not details and support == "upstream_required":
            details = "当前主资产仅支持导入检查，尚无可直接执行的下游加载能力"
        if not details:
            details = f"当前处理阶段为 {stage or 'raw'}"
        _append_issue(
            issues,
            run_spec,
            "upstream_processing_required",
            f"该数据尚未达到下游统计工作流要求，需先完成上游处理：{details}。",
        )
        return True
    if scientific_context and stage in _UNKNOWN_PROCESSING_STAGES:
        _append_issue(
            issues,
            run_spec,
            "unknown_processing_stage",
            "无法确认科学数据的 processing_stage；请先检查并标明为 analysis_ready、normalized 等可分析阶段。",
        )
    return False


def _validate_numeric_matrix_semantics(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
) -> None:
    matrix_inputs = tuple(
        item
        for item in run_spec.data_contract.inputs
        if item.kind == "object" and "numeric_matrix" in item.accepts
    )
    if not matrix_inputs:
        return
    scientific = _scientific_profile(dataset_profile)
    modality = _normalized(scientific.get("modality") or dataset_profile.get("modality"))
    if scientific_matrix_modality_compatibility(run_spec.template_id, modality) is False:
        _append_issue(
            issues,
            run_spec,
            "scientific_modality_mismatch",
            f"当前数据已识别为 {modality}，与模板要求的"
            f"{scientific_matrix_semantic_label(run_spec.template_id)}不一致；"
            "请选择匹配当前数据模态的模板，或导入目标实验数据。",
        )
        return
    if has_scientific_matrix_evidence(run_spec.template_id, dataset_profile):
        return
    orientation = str(run_spec.resolved_inputs.get("__matrix_orientation__") or "").strip()
    if orientation == "samples_by_features":
        return
    _append_issue(
        issues,
        run_spec,
        "unknown_omics_axes",
        "普通数值表不能仅凭数值列比例作为科学特征矩阵；请确认样本×特征方向，或先生成带 assay 与样本注释的分析对象。",
    )


def _embedded_spatial_coordinates(scientific: Mapping[str, Any]) -> bool:
    capabilities = scientific.get("capabilities")
    if isinstance(capabilities, (list, tuple, set, frozenset)) and any(
        _normalized(item) == "spatial_coordinates" for item in capabilities
    ):
        return True
    spatial = scientific.get("spatial")
    if not isinstance(spatial, Mapping):
        return False
    dimensions = spatial.get("coordinate_dimensions")
    return bool(isinstance(dimensions, (list, tuple)) and dimensions)


def _validate_required_asset_roles(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    assets: Sequence[Mapping[str, Any]],
    primary_asset: Mapping[str, Any] | None,
    scientific: Mapping[str, Any],
) -> None:
    if primary_asset is None:
        return
    primary_format = _asset_format(primary_asset)
    required: set[str] = set()
    if primary_format == "mtx":
        required.update({"barcodes", "features"})
    elif primary_format == "plink_bed":
        required.update({"feature_metadata", "sample_metadata"})
    elif primary_format == "cram":
        required.add("reference_sequence")
    if (
        run_spec.template_id == "omics_spatial_transcriptomics"
        and primary_format in {"mtx", "10x_h5", "h5ad"}
        and not _embedded_spatial_coordinates(scientific)
    ):
        required.add("spatial_coordinates")

    roles = {_asset_role(asset) for asset in assets}
    missing = sorted(required - roles)
    if not missing:
        return
    role_labels = {
        "barcodes": "barcodes（样本/细胞条码）",
        "features": "features（特征注释）",
        "feature_metadata": "feature_metadata（BIM 特征表）",
        "sample_metadata": "sample_metadata（FAM 样本表）",
        "reference_sequence": "reference_sequence（参考序列）",
        "spatial_coordinates": "spatial_coordinates（空间坐标）",
    }
    _append_issue(
        issues,
        run_spec,
        "missing_required_asset_roles",
        "当前复合数据缺少必要资产角色："
        + "、".join(role_labels.get(role, role) for role in missing)
        + "；请将匹配的侧车文件与主资产一起导入。",
    )


def _scientific_stem(name: str) -> str:
    value = name.casefold()
    suffixes = (
        ".gz",
        ".bgz",
        ".bam",
        ".cram",
        ".vcf",
        ".bcf",
        ".bai",
        ".crai",
        ".tbi",
        ".csi",
    )
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                changed = True
                break
    return value


def _index_name_matches(
    owner: Mapping[str, Any],
    index: Mapping[str, Any],
    suffixes: Sequence[str],
) -> bool | None:
    owner_name = _asset_name(owner)
    index_name = _asset_name(index)
    if not owner_name or not index_name:
        return None
    expected = {f"{owner_name}{suffix}" for suffix in suffixes}
    stem = _scientific_stem(owner_name)
    expected.update(f"{stem}{suffix}" for suffix in suffixes)
    return index_name in expected


def _validate_index_assets(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    assets: Sequence[Mapping[str, Any]],
    scientific: Mapping[str, Any],
) -> None:
    index_details = scientific.get("indexes")
    index_details = index_details if isinstance(index_details, Mapping) else {}
    raw_capabilities = scientific.get("capabilities")
    capabilities = {
        _normalized(value)
        for value in raw_capabilities
        if isinstance(value, str)
    } if isinstance(raw_capabilities, (list, tuple, set)) else set()
    content_validated = index_details.get("indexed") is True and bool(
        capabilities.intersection({"indexed_region_queries", "indexed_ranges"})
    )
    owners = [asset for asset in assets if _asset_format(asset) in _INDEX_SPECS]
    indexes = [
        asset
        for asset in assets
        if _asset_format(asset) in _INDEX_FORMATS or _asset_role(asset) in _INDEX_ROLES
    ]
    if indexes and not owners:
        _append_issue(
            issues,
            run_spec,
            "orphan_index_asset",
            "数据包包含索引文件，但没有可与之配对的 BAM/CRAM/VCF/BCF 主数据；请重新选择完整数据包。",
        )
        return

    matched_index_ids: set[int] = set()
    uncertain_index_ids: set[int] = set()
    for owner in owners:
        owner_format = _asset_format(owner)
        expected_role, expected_formats, suffixes = _INDEX_SPECS[owner_format]
        compatible = [
            index
            for index in indexes
            if (
                _asset_format(index) in expected_formats
                and _asset_role(index) in {"", "index", expected_role}
            )
            or (_asset_format(index) not in _INDEX_FORMATS and _asset_role(index) == expected_role)
        ]
        owner_name = _asset_name(owner) or f"{owner_format} 资产"
        if not indexes:
            _append_issue(
                issues,
                run_spec,
                "missing_index_asset",
                f"{owner_name} 缺少匹配的 {expected_role}；大文件区域查询前请一并导入索引。",
            )
            continue
        if not compatible:
            _append_issue(
                issues,
                run_spec,
                "index_asset_mismatch",
                f"{owner_name} 的索引类型不匹配；请提供与 {owner_format.upper()} 对应的索引文件。",
            )
            continue
        if owner_format == "vcf" and owner_name and not owner_name.endswith((".vcf.gz", ".vcf.bgz")):
            _append_issue(
                issues,
                run_spec,
                "invalid_indexed_vcf",
                f"{owner_name} 不是 bgzip 压缩的 VCF；请先生成 .vcf.gz/.vcf.bgz，再附加 TBI/CSI 索引。",
            )
        match_states = [(_index_name_matches(owner, index, suffixes), index) for index in compatible]
        if any(state is None for state, _ in match_states):
            uncertain_index_ids.update(id(index) for state, index in match_states if state is None)
            _append_issue(
                issues,
                run_spec,
                "unknown_index_pairing",
                f"无法从资产名称确认 {owner_name} 与索引的配对关系；请保留标准同名文件名后重新导入。",
            )
            continue
        matches = [index for state, index in match_states if state]
        matched_index_ids.update(id(index) for index in matches)
        if not matches:
            _append_issue(
                issues,
                run_spec,
                "index_asset_mismatch",
                f"索引文件名与 {owner_name} 不匹配；请导入同一数据文件生成的索引。",
            )
        elif len(matches) > 1:
            _append_issue(
                issues,
                run_spec,
                "ambiguous_index_asset",
                f"{owner_name} 对应多个索引文件；请仅保留一个确定匹配的索引。",
            )
        elif not content_validated:
            _append_issue(
                issues,
                run_spec,
                "unvalidated_index_content",
                f"{owner_name} 的索引尚未通过内容级区域查询校验；请先重新执行数据理解，"
                "确认索引与主文件实际对应后再运行分析。",
            )

    if matched_index_ids:
        unmatched = [
            index
            for index in indexes
            if id(index) not in matched_index_ids and id(index) not in uncertain_index_ids
        ]
        if unmatched:
            labels = "、".join(_asset_name(index) or _asset_format(index) for index in unmatched[:4])
            _append_issue(
                issues,
                run_spec,
                "orphan_index_asset",
                f"检测到未与任何数据资产配对的索引：{labels}；请移除无关索引或导入对应主文件。",
            )


def _axis_state(value: object) -> str:
    if value is None or isinstance(value, bool):
        return "unknown"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if numeric > 0:
        return "known"
    return "empty" if numeric == 0 else "unknown"


def _assay_axis_evidence(scientific: Mapping[str, Any]) -> tuple[bool, bool, bool]:
    assays = scientific.get("assays")
    if not isinstance(assays, (list, tuple)):
        return False, False, False
    feature_known = False
    sample_known = False
    has_empty = False
    for assay in assays:
        if not isinstance(assay, Mapping):
            continue
        dimensions = assay.get("dimensions")
        if not isinstance(dimensions, (list, tuple)) or len(dimensions) < 2:
            continue
        first, second = _axis_state(dimensions[0]), _axis_state(dimensions[1])
        feature_known = feature_known or first == "known"
        sample_known = sample_known or second == "known"
        has_empty = has_empty or first == "empty" or second == "empty"
    return feature_known, sample_known, has_empty


def _sample_alignment_state(
    dataset_profile: Mapping[str, Any],
    scientific: Mapping[str, Any],
) -> bool | str | None:
    capabilities = scientific.get("capabilities")
    normalized_capabilities = (
        {_normalized(item) for item in capabilities}
        if isinstance(capabilities, (list, tuple, set, frozenset))
        else set()
    )
    if "sample_alignment_failed" in normalized_capabilities:
        return False
    if normalized_capabilities.intersection({"sample_alignment_validated", "aligned_sample_map"}):
        return True

    value = dataset_profile.get("sample_alignment", scientific.get("sample_alignment"))
    if value is not None:
        if isinstance(value, bool):
            return value
        if isinstance(value, Mapping):
            for key in ("valid", "matched", "aligned"):
                if key in value and isinstance(value[key], bool):
                    return bool(value[key])
            value = value.get("status")
        normalized = _normalized(value)
        if normalized in {"valid", "matched", "aligned", "passed", "complete", "true"}:
            return True
        if normalized in {"invalid", "mismatch", "failed", "unaligned", "false"}:
            return False
        return "unknown"

    sample_map = scientific.get("sample_map")
    if isinstance(sample_map, Mapping) and sample_map:
        rows = sample_map.get("rows")
        if _axis_state(rows) == "known":
            return True
        if _axis_state(rows) == "empty":
            return False
        return "unknown"
    return None


def _requires_sample_axis(run_spec: WorkflowRunSpec) -> bool:
    for item in run_spec.data_contract.inputs:
        if item.requirement != "critical" or item.kind not in {"column", "columns"}:
            continue
        semantic = "|".join(
            _normalized(value) for value in (item.id, item.label, item.semantic_role) if value
        )
        if any(token in semantic for token in ("sample", "subject", "group", "样本", "分组", "表型")):
            return True
    return False


def _append_alignment_issue(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    alignment_state: bool | str | None,
    assets: Sequence[Mapping[str, Any]],
) -> None:
    if alignment_state is False:
        _append_issue(
            issues,
            run_spec,
            "sample_alignment_failed",
            "样本对齐检查失败；请统一表达矩阵、表型和样本元数据中的样本 ID 后重试。",
        )
    elif alignment_state == "unknown":
        _append_issue(
            issues,
            run_spec,
            "unknown_sample_alignment",
            "样本对齐状态无法确认；请检查 sample_map 或提供明确的样本 ID 对齐结果。",
        )
    elif alignment_state is None and {_asset_role(asset) for asset in assets}.intersection(
        _EXTERNAL_SAMPLE_ROLES
    ):
        _append_issue(
            issues,
            run_spec,
            "unknown_sample_alignment",
            "检测到外部样本元数据，但没有样本 ID 对齐证据；请先验证主对象与元数据一一对应。",
        )


def _validate_omics_axes_and_alignment(
    issues: list[WorkflowValidationIssue],
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]],
) -> None:
    if run_spec.definition.category != "组学与生物信息":
        return
    scientific = _scientific_profile(dataset_profile)
    modality = _normalized(scientific.get("modality") or dataset_profile.get("modality"))
    if modality in _NON_MATRIX_AXIS_MODALITIES:
        return
    axes_value = scientific.get("axes", dataset_profile.get("axes"))
    axes = axes_value if isinstance(axes_value, Mapping) else {}
    assay_features, assay_samples, assay_empty = _assay_axis_evidence(scientific)
    alignment_state = _sample_alignment_state(dataset_profile, scientific)
    # A confirmed ordinary table adapter has an inspected observation axis
    # and explicitly bound numeric features. Cell/spot observations may have
    # repeated biological donor IDs; their unique axis is checked in R.
    features = run_spec.resolved_inputs.get("feature_columns")
    columns_value = dataset_profile.get("columns")
    if not isinstance(columns_value, (list, tuple)):
        columns_value = dataset_profile.get("variables", ())
    columns = {
        str(item.get("name") or item.get("column") or ""): item
        for item in columns_value if isinstance(item, Mapping)
    } if isinstance(columns_value, (list, tuple)) else {}
    confirmed_table = (
        _profile_shape(dataset_profile) == "tabular"
        and set(dataset_asset_formats(dataset_profile)).issubset(_BASE_TABULAR_FORMATS | {"rds", "rdata"})
        and run_spec.resolved_inputs.get("__matrix_orientation__") == "samples_by_features"
        and any(item.kind == "object" and "numeric_matrix" in item.accepts
                for item in run_spec.data_contract.inputs)
        and isinstance(features, (list, tuple)) and len(features) >= 2
        and len(set(features)) == len(features)
        and all(name in columns and (
            _normalized(columns[name].get("type")) in {"numeric", "integer", "double", "float"}
            or _normalized(columns[name].get("semantic_type")) == "numeric"
        ) for name in features)
        and _axis_state(dataset_profile.get("rows")) == "known"
    )
    if confirmed_table:
        _append_alignment_issue(issues, run_spec, alignment_state, assets)
        return
    if not axes and not (assay_features or assay_samples or assay_empty):
        _append_issue(
            issues,
            run_spec,
            "unknown_omics_axes",
            "无法确认组学对象的 sample/feature 轴；请先完成数据检查并确认矩阵或对象维度。",
        )
        _append_alignment_issue(issues, run_spec, alignment_state, assets)
        return

    feature_states = [
        _axis_state(value) for key, value in axes.items() if _normalized(key) in _FEATURE_AXIS_NAMES
    ]
    sample_states = [
        _axis_state(value) for key, value in axes.items() if _normalized(key) in _SAMPLE_AXIS_NAMES
    ]
    feature_known = assay_features or "known" in feature_states
    sample_known = assay_samples or "known" in sample_states
    has_empty_axis = assay_empty or "empty" in feature_states or "empty" in sample_states
    if alignment_state is True:
        sample_known = True

    if has_empty_axis:
        _append_issue(
            issues,
            run_spec,
            "empty_omics_axis",
            "组学对象存在长度为 0 的 sample/feature 轴；请检查导入对象和过滤步骤。",
        )
    if not feature_known:
        _append_issue(
            issues,
            run_spec,
            "missing_feature_axis",
            "组学对象未提供可验证的 feature 轴；请检查特征矩阵、注释或对象 assay。",
        )
    if _requires_sample_axis(run_spec) and not sample_known:
        _append_issue(
            issues,
            run_spec,
            "missing_sample_axis",
            "当前分析需要样本/分组映射，但对象未提供可验证的 sample 轴；请补充样本信息并重新检查。",
        )

    _append_alignment_issue(issues, run_spec, alignment_state, assets)


def validate_workflow_run_spec(
    run_spec: WorkflowRunSpec,
    dataset_profile: Mapping[str, Any],
) -> WorkflowPreflightReport:
    """Validate a compiled run against the active path-free dataset profile."""

    issues: list[WorkflowValidationIssue] = []
    dataset_id = str(dataset_profile.get("dataset_id") or "").strip()
    if not dataset_id:
        _append_issue(
            issues,
            run_spec,
            "missing_dataset",
            "当前没有可分析的数据集；请先上传或选择数据。",
        )
    elif run_spec.dataset_id and run_spec.dataset_id != dataset_id:
        _append_issue(
            issues,
            run_spec,
            "dataset_mismatch",
            "分析计划属于另一份数据；请基于当前数据重新选择模板或生成计划。",
        )

    assets = _profile_assets(dataset_profile)
    primary_assets = tuple(asset for asset in assets if _asset_role(asset) == "primary")
    primary_asset = primary_assets[0] if len(primary_assets) == 1 else None
    if assets:
        if not primary_assets:
            _append_issue(
                issues,
                run_spec,
                "missing_primary_asset",
                "数据资产清单没有标记 primary 主资产；请重新导入并明确唯一主资产。",
            )
        elif len(primary_assets) > 1:
            _append_issue(
                issues,
                run_spec,
                "ambiguous_primary_asset",
                "数据资产清单包含多个 primary 主资产；请拆分为一次只分析一个主数据包。",
            )
    elif "assets" in dataset_profile and _normalized(dataset_profile.get("format")) not in (
        _BASE_TABULAR_FORMATS
    ):
        _append_issue(
            issues,
            run_spec,
            "missing_primary_asset",
            "科学数据没有可验证的 primary 主资产；请重新导入完整数据包。",
        )

    actual_formats = dataset_asset_formats(dataset_profile)
    accepted_formats = set(run_spec.data_contract.formats)
    if actual_formats and accepted_formats and not set(actual_formats).intersection(accepted_formats):
        _append_issue(
            issues,
            run_spec,
            "unsupported_dataset_format",
            "当前主资产格式与模板不匹配："
            + "、".join(actual_formats)
            + "；请选择支持该格式的模板或先转换数据。",
        )

    profile_shape = _validate_profile_shape(issues, run_spec, dataset_profile, actual_formats)
    scientific_context = _is_scientific_context(
        run_spec,
        dataset_profile,
        actual_formats,
        profile_shape,
    )
    processing_blocked = _validate_processing_stage(
        issues,
        run_spec,
        dataset_profile,
        primary_asset,
        scientific_context=scientific_context,
    )
    _validate_numeric_matrix_semantics(issues, run_spec, dataset_profile)
    _validate_required_asset_roles(
        issues,
        run_spec,
        assets,
        primary_asset,
        _scientific_profile(dataset_profile),
    )
    _validate_index_assets(issues, run_spec, assets, _scientific_profile(dataset_profile))
    if scientific_context and not processing_blocked:
        _validate_omics_axes_and_alignment(issues, run_spec, dataset_profile, assets)

    from agent.analysis.contract_conditions import missing_contract_inputs
    from agent.analysis.input_validation import joint_input_issues

    missing_inputs = missing_contract_inputs(
        run_spec.data_contract.inputs,
        run_spec.data_contract.input_alternatives,
        run_spec.resolved_inputs,
    )
    if missing_inputs:
        _append_issue(
            issues,
            run_spec,
            "unresolved_critical_inputs",
            "仍有关键输入未解析：" + "、".join(missing_inputs) + "；请确认变量或对象绑定。",
        )

    for message in joint_input_issues(
        run_spec.resolved_inputs, template_id=run_spec.template_id, dataset_profile=dataset_profile,
    ).values():
        _append_issue(issues, run_spec, "invalid_joint_inputs", message)

    for input_id in run_spec.data_contract.asset_inputs:
        if input_id not in run_spec.resolved_inputs:
            continue
        value = run_spec.resolved_inputs[input_id]
        if not isinstance(value, str) or value != dataset_id:
            _append_issue(
                issues,
                run_spec,
                "invalid_dataset_binding",
                f"对象输入 {input_id} 未绑定当前数据集；请重新生成本轮分析计划。",
            )

    return WorkflowPreflightReport(ready=not issues, issues=tuple(issues))


def validate_workflow_definitions(
    catalog: AnalysisCatalog,
    definitions: Sequence[WorkflowDefinition],
) -> WorkflowValidationReport:
    """Verify catalog coverage and scientific input/output invariants."""

    templates = tuple(item for item in catalog.snapshot().templates if item.visibility == "user")
    issues: list[WorkflowValidationIssue] = []
    by_template: dict[str, WorkflowDefinition] = {}
    seen_keys: set[tuple[str, str]] = set()
    for definition in definitions:
        if definition.template_id in by_template:
            issues.append(
                WorkflowValidationIssue(
                    "duplicate_template_definition",
                    "template has more than one workflow definition",
                    definition.template_id,
                )
            )
        else:
            by_template[definition.template_id] = definition
        key = (definition.workflow_id, definition.variant)
        if key in seen_keys:
            issues.append(
                WorkflowValidationIssue(
                    "duplicate_workflow_variant",
                    f"duplicate workflow key {definition.workflow_id}/{definition.variant}",
                    definition.template_id,
                )
            )
        seen_keys.add(key)

    template_ids = {item.id for item in templates}
    for template_id in sorted(set(by_template) - template_ids):
        issues.append(
            WorkflowValidationIssue(
                "unknown_template_definition",
                "definition is not backed by a user-visible catalog template",
                template_id,
            )
        )

    family_implementations: dict[str, tuple[str, str]] = {}
    expected_phases = ("preflight", "prepare", "analyze", "diagnose", "publish")
    for template in templates:
        definition = by_template.get(template.id)
        if definition is None:
            issues.append(
                WorkflowValidationIssue(
                    "missing_definition",
                    "user-visible template has no workflow definition",
                    template.id,
                )
            )
            continue
        if not definition.workflow_id or not definition.variant:
            issues.append(
                WorkflowValidationIssue(
                    "missing_workflow_identity",
                    "workflow_id and variant are required",
                    template.id,
                )
            )
        if definition.variant != template.id or definition.template_id != template.id:
            issues.append(
                WorkflowValidationIssue(
                    "unstable_template_variant",
                    "catalog template ID must remain the workflow variant",
                    template.id,
                )
            )
        if (definition.family, definition.scenario) != (template.family, template.scenario):
            issues.append(
                WorkflowValidationIssue(
                    "method_contract_drift",
                    "workflow family/scenario differs from the catalog",
                    template.id,
                )
            )
        if definition.resource_class != template.resource_class:
            issues.append(
                WorkflowValidationIssue(
                    "resource_class_drift",
                    "workflow resource class differs from the catalog",
                    template.id,
                )
            )
        if definition.data_contract.shape != template.input_shape:
            issues.append(
                WorkflowValidationIssue(
                    "input_shape_drift",
                    "workflow input shape differs from the catalog",
                    template.id,
                )
            )
        if definition.data_contract.formats != accepted_template_formats(template):
            issues.append(
                WorkflowValidationIssue(
                    "input_format_drift",
                    "workflow formats differ from the catalog",
                    template.id,
                )
            )
        if (
            definition.data_contract.required_roles != template.required_roles
            or definition.data_contract.optional_roles != template.optional_roles
        ):
            issues.append(
                WorkflowValidationIssue(
                    "input_role_drift",
                    "workflow required/optional roles differ from the catalog",
                    template.id,
                )
            )

        if definition.data_contract.inputs != template.input_specs:
            issues.append(
                WorkflowValidationIssue(
                    "typed_input_drift",
                    "workflow typed inputs differ from the catalog",
                    template.id,
                )
            )

        addressable_roles = {
            role for artifact in definition.artifacts for role in (artifact.role, *artifact.legacy_roles)
        }
        required_roles = {
            output.role for output in template.required_outputs if output.required
        }
        missing_required = sorted(required_roles - addressable_roles)
        if missing_required:
            issues.append(
                WorkflowValidationIssue(
                    "missing_core_baseline_output",
                    "missing output roles: " + ", ".join(missing_required),
                    template.id,
                )
            )
        if not definition.artifacts:
            issues.append(
                WorkflowValidationIssue(
                    "empty_output_portfolio",
                    "workflow must publish at least one artifact",
                    template.id,
                )
            )

        package_requirement = definition.package_requirement
        if package_requirement.candidates != template.packages:
            issues.append(
                WorkflowValidationIssue(
                    "package_candidate_drift",
                    "workflow package candidates differ from the catalog",
                    template.id,
                )
            )
        if package_requirement.candidates:
            if not package_requirement.primary:
                issues.append(
                    WorkflowValidationIssue(
                        "missing_primary_package",
                        "non-empty package candidates require a primary package",
                        template.id,
                    )
                )
            elif package_requirement.primary not in package_requirement.candidates:
                issues.append(
                    WorkflowValidationIssue(
                        "invalid_primary_package",
                        "primary package is not in the candidate list",
                        template.id,
                    )
                )
            elif package_requirement.primary != package_requirement.candidates[0]:
                issues.append(
                    WorkflowValidationIssue(
                        "unordered_primary_package",
                        "primary package must be the first catalog candidate",
                        template.id,
                    )
                )
            expected_alternatives = tuple(
                item for item in package_requirement.candidates if item != package_requirement.primary
            )
            if package_requirement.alternatives != expected_alternatives:
                issues.append(
                    WorkflowValidationIssue(
                        "invalid_package_fallbacks",
                        "package alternatives do not preserve candidate order",
                        template.id,
                    )
                )

        phases = tuple(step.phase for step in definition.steps)
        if phases != expected_phases:
            issues.append(
                WorkflowValidationIssue(
                    "incomplete_workflow_steps",
                    "workflow must define preflight/prepare/analyze/diagnose/publish in order",
                    template.id,
                )
            )
        step_ids = {step.step_id for step in definition.steps}
        dangling_dependencies = {
            dependency
            for step in definition.steps
            for dependency in step.depends_on
            if dependency not in step_ids
        }
        if dangling_dependencies:
            issues.append(
                WorkflowValidationIssue(
                    "dangling_step_dependency",
                    "unknown dependencies: " + ", ".join(sorted(dangling_dependencies)),
                    template.id,
                )
            )
        publish = next(
            (step for step in definition.steps if step.phase == "publish"),
            None,
        )
        published_roles = set(publish.produces) if publish is not None else set()
        missing_published = sorted({artifact.role for artifact in definition.artifacts} - published_roles)
        if missing_published:
            issues.append(
                WorkflowValidationIssue(
                    "unpublished_portfolio_artifact",
                    "publish step omits roles: " + ", ".join(missing_published),
                    template.id,
                )
            )
        if not definition.resource_hints:
            issues.append(
                WorkflowValidationIssue(
                    "missing_resource_hints",
                    "workflow must declare bounded execution resource hints",
                    template.id,
                )
            )

        object_ids = {item.id for item in template.input_specs if item.kind == "object"}
        compiled_object_ids = set(definition.data_contract.object_inputs)
        compiled_column_ids = set(definition.data_contract.column_inputs)
        if not object_ids <= compiled_object_ids or object_ids & compiled_column_ids:
            issues.append(
                WorkflowValidationIssue(
                    "object_input_downgraded",
                    "object inputs must remain object assets rather than column bindings",
                    template.id,
                )
            )
        if template.input_shape == "r_object":
            preflight = next(
                (step for step in definition.steps if step.phase == "preflight"),
                None,
            )
            if preflight is None or "r_object_class" not in preflight.checks:
                issues.append(
                    WorkflowValidationIssue(
                        "missing_r_object_preflight",
                        "R-object templates require an object-class preflight",
                        template.id,
                    )
                )
        if template.category == "组学与生物信息":
            if not object_ids <= set(definition.data_contract.asset_inputs):
                issues.append(
                    WorkflowValidationIssue(
                        "omics_asset_downgraded",
                        "omics object inputs must remain explicit assets",
                        template.id,
                    )
                )
            preflight = next(
                (step for step in definition.steps if step.phase == "preflight"),
                None,
            )
            if preflight is None or "omics_asset_semantics" not in preflight.checks:
                issues.append(
                    WorkflowValidationIssue(
                        "missing_omics_asset_preflight",
                        "omics workflows require object/asset semantic checks",
                        template.id,
                    )
                )

        analyze = next(
            (step.implementation for step in definition.steps if step.phase == "analyze"),
            "",
        )
        diagnose = next(
            (step.implementation for step in definition.steps if step.phase == "diagnose"),
            "",
        )
        implementations = (analyze, diagnose)
        prior = family_implementations.setdefault(template.family, implementations)
        if prior != implementations:
            issues.append(
                WorkflowValidationIssue(
                    "family_implementation_not_shared",
                    "templates in one method family must share analyze/diagnose implementations",
                    template.id,
                )
            )

    return WorkflowValidationReport(
        checked_templates=len(templates),
        checked_definitions=len(definitions),
        issues=tuple(issues),
    )
