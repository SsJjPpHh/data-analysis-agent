"""Deterministic local preparation for catalog and conversational analyses."""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any, Sequence

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.format_contracts import (
    accepted_template_formats,
    dataset_asset_formats,
    has_scientific_matrix_evidence,
    scientific_matrix_modality_compatibility,
    scientific_matrix_semantic_label,
    scientific_template_formats,
)
from agent.analysis.memory import DatasetRoleMemory
from agent.analysis.input_validation import joint_input_issues, normalize_parameter_value, normalize_setting_value, observed_group_count
from agent.analysis.models import (
    AnalysisPlan,
    AnalysisTemplate,
    InputCandidate,
    InputControl,
    InputQuestion,
    InputSpec,
    PreparationResult,
    ResolvedInput,
)
from agent.analysis.semantics import infer_semantic_type


_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "outcome": (
        "outcome",
        "endpoint",
        "response",
        "result",
        "target",
        "label",
        "status",
        "score",
        "结局",
        "终点",
        "结果",
    ),
    "exposure": (
        "exposure",
        "predictor",
        "treatment",
        "intervention",
        "condition",
        "dose",
        "暴露",
        "治疗",
        "干预",
    ),
    "group": (
        "group",
        "arm",
        "treatment",
        "intervention",
        "cohort",
        "condition",
        "class",
        "site",
        "center",
        "region",
        "sex",
        "gender",
        "分组",
        "组别",
    ),
    "followup_time": (
        "followup",
        "follow_up",
        "survival_time",
        "duration",
        "days",
        "months",
        "time_to_event",
        "随访",
        "生存时间",
    ),
    "event": ("event", "event_status", "death", "dead", "censor", "status", "事件"),
    "event_type": ("event_type", "cause", "failure_type", "事件类型"),
    "time": (
        "time",
        "date",
        "datetime",
        "year",
        "month",
        "week",
        "visit",
        "wave",
        "period",
        "时间",
        "日期",
    ),
    "subject_id": ("subject_id", "patient_id", "participant_id", "person_id", "病例号"),
    "sample_id": ("sample_id", "specimen_id", "sample", "样本"),
    "sample_trait": (
        "trait",
        "sample_trait",
        "sample_traits",
        "traits",
        "phenotype",
        "sample_phenotype",
        "性状",
        "表型",
    ),
    "study_id": ("study_id", "trial_id", "paper_id", "研究"),
    "document_id": ("document_id", "doc_id", "document", "文档"),
    "region_id": ("region_id", "area_id", "district_id", "region", "area", "地区"),
    "cluster_id": ("cluster_id", "group_id", "site_id", "center_id", "cluster"),
    "cluster_variable": (
        "cluster_variable",
        "cluster_id",
        "cluster",
        "psu",
        "primary_sampling_unit",
        "聚类",
    ),
    "measure": ("measure", "metric", "value", "amount", "score", "index", "指标", "数值"),
    "weight": ("weight", "sampling_weight", "survey_weight", "wt", "权重"),
    "strata": ("strata", "stratum", "stratification", "layer", "分层"),
    "batch": ("batch", "platform", "plate", "批次", "平台"),
    "dose": ("dose", "dosage", "剂量"),
    "denominator": ("population", "denominator", "person_time", "total", "分母", "人口"),
    "text": ("text", "content", "note", "comment", "abstract", "文本", "内容"),
    "coordinates": ("longitude", "latitude", "lon", "lat", "coord", "x", "y", "坐标"),
    "effect_estimate": ("effect", "estimate", "logrr", "logor", "beta", "效应量"),
    "standard_error": ("se", "stderr", "standard_error", "ci", "标准误"),
    "treatment_event_count": (
        "treat_events",
        "treatment_events",
        "event_treat",
        "events_treatment",
    ),
    "treatment_sample_size": ("treat_n", "treatment_n", "n_treat", "n_treatment"),
    "control_event_count": (
        "control_events",
        "event_control",
        "events_control",
    ),
    "control_sample_size": ("control_n", "n_control"),
    "true_positive": ("tp", "true_positive", "truepositive"),
    "false_positive": ("fp", "false_positive", "falsepositive"),
    "false_negative": ("fn", "false_negative", "falsenegative"),
    "true_negative": ("tn", "true_negative", "truenegative"),
    "negative_control_variable": (
        "negative_control_variable",
        "negative_control",
        "negative_control_exposure",
        "negative_control_outcome",
        "负对照",
    ),
    "origin_longitude": ("origin_lon", "origin_longitude", "start_lon", "start_longitude"),
    "origin_latitude": ("origin_lat", "origin_latitude", "start_lat", "start_latitude"),
    "facility_longitude": (
        "facility_lon",
        "facility_longitude",
        "destination_lon",
        "destination_longitude",
    ),
    "facility_latitude": (
        "facility_lat",
        "facility_latitude",
        "destination_lat",
        "destination_latitude",
    ),
}

_GENERIC_SET_ROLES = {
    "continuous_variables",
    "categorical_variables",
    "features",
    "variables",
    "data_object",
}

_DATASET_INTRINSIC_MEMORY_ROLES = {
    "sample_id",
    "subject_id",
    "study_id",
    "document_id",
    "region_id",
    "cluster_id",
}

_LOW_CARDINALITY_CATEGORICAL_ROLES = {
    "group",
    "strata",
    "event",
    "event_type",
    "outcome",
    "exposure",
}

_MATRIX_ORIENTATION_INPUT_ID = "__matrix_orientation__"
_MATRIX_ORIENTATION_VALUES = frozenset(
    {"samples_by_features", "requires_restructure", "not_target_matrix"}
)


def _canonical_input_role(spec: InputSpec) -> str:
    role = str(spec.semantic_role or "").strip()
    if role in _ROLE_ALIASES or role in _DATASET_INTRINSIC_MEMORY_ROLES:
        return role
    text = " ".join((str(spec.id or ""), str(spec.label or ""), role)).casefold()
    patterns = (
        ("time", r"follow.?up|duration|survival.?time|event.?time|时间|日期|随访"),
        ("event_type", r"event.?type|failure.?type|cause|事件类型|结局类型"),
        ("group", r"group|arm|condition|cohort|分组|组别|地区变量|样本组"),
        ("strata", r"strata|stratum|分层"),
        ("outcome", r"outcome|response|endpoint|dependent|结局|终点|因变量"),
        ("exposure", r"exposure|predictor|independent|treatment|暴露|自变量|处理变量"),
        ("event", r"event|status|事件|状态"),
        ("subject_id", r"subject.?id|patient.?id|个体.?id|病例编号"),
        ("sample_id", r"sample.?id|样本编号"),
    )
    return next(
        (candidate for candidate, pattern in patterns if re.search(pattern, text, re.IGNORECASE)),
        role,
    )


def _legacy_hashed_input_id(label: str) -> str:
    """Return the deterministic opaque ID used by pre-readable catalogs."""

    digest = hashlib.sha256(str(label or "").encode("utf-8")).hexdigest()[:12]
    return f"role_{digest}"


@dataclass(frozen=True)
class _Column:
    name: str
    semantic_type: str
    unique_count: int | None
    missing_pct: float
    position: int
    numeric_coding: str = ""
    numeric_storage: bool = False


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", str(value or "").casefold()).strip("_")


def _as_number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _semantic_type(item: dict[str, Any], rows: int | None) -> str:
    explicit = str(item.get("semantic_type") or "").strip().casefold()
    if explicit in {"numeric", "categorical", "datetime", "text", "identifier", "spatial"}:
        return explicit
    unique = _as_number(item.get("unique_count"))
    return infer_semantic_type(
        str(item.get("name") or item.get("column") or ""),
        str(item.get("type") or item.get("class") or ""),
        int(unique) if unique is not None else None,
        rows,
    )


def _columns(dataset_profile: dict[str, Any]) -> tuple[_Column, ...]:
    values = dataset_profile.get("columns")
    if not isinstance(values, list):
        values = dataset_profile.get("variables")
    rows_value = _as_number(dataset_profile.get("rows"))
    rows = int(rows_value) if rows_value is not None else None
    result: list[_Column] = []
    for position, item in enumerate(values if isinstance(values, list) else []):
        if not isinstance(item, dict):
            item = {"name": str(item or "")}
        name = str(item.get("name") or item.get("column") or "").strip()
        if not name:
            continue
        unique_value = _as_number(item.get("unique_count"))
        missing_pct = _as_number(item.get("missing_pct")) or 0.0
        result.append(
            _Column(
                name=name,
                semantic_type=_semantic_type(item, rows),
                unique_count=int(unique_value) if unique_value is not None else None,
                missing_pct=missing_pct,
                position=position,
                numeric_coding=str(item.get("numeric_coding") or "").strip(),
                numeric_storage=str(item.get("type") or item.get("class") or "").strip().casefold()
                in {"numeric", "integer", "double", "float", "int"},
            )
        )
    return tuple(result)


def _type_matches(
    column: _Column,
    spec: InputSpec,
    *,
    allow_user_override: bool = False,
) -> bool:
    if not spec.accepts or "any" in spec.accepts or column.semantic_type in spec.accepts:
        return True
    # Bounded integer counts may be profiled as coded categories. An explicit
    # user binding can retain their inspected numeric storage; factor/text
    # columns cannot pass this override and the R method still checks domains.
    if allow_user_override and column.numeric_storage and "numeric" in spec.accepts:
        return True
    numeric_category_candidate = (
        column.semantic_type == "numeric"
        and column.unique_count is not None
        and 2 <= column.unique_count <= 20
        and "categorical" in spec.accepts
    )
    if not numeric_category_candidate:
        return False
    # A numeric storage column with a small observed domain is a legitimate
    # user-selected factor.  Automatic broad sets stay conservative; scalar
    # grouping/event roles may still offer it as a candidate.
    return allow_user_override or (
        spec.kind == "column" and _canonical_input_role(spec) in _LOW_CARDINALITY_CATEGORICAL_ROLES
    )


def _aliases(spec: InputSpec) -> tuple[str, ...]:
    aliases = list(_ROLE_ALIASES.get(_canonical_input_role(spec), ()))
    # Catalog IDs are stable English semantic names even when the user-facing
    # label is Chinese.  Include both IDs in local name matching so inputs such
    # as true_positive/validation_split no longer depend on an opaque hash.
    aliases.extend((spec.id, spec.semantic_role))
    normalized_label = _normalize_name(spec.label)
    if re.search(r"时间|日期", spec.label):
        aliases.extend(_ROLE_ALIASES["time"])
    if re.search(r"指标|观测值|数值", spec.label):
        aliases.extend(_ROLE_ALIASES["measure"])
    if re.search(r"分组|治疗组|地区|中心", spec.label):
        aliases.extend(_ROLE_ALIASES["group"])
    if re.search(r"ID|主键", spec.label):
        aliases.extend(("id", "identifier", "key"))
    if normalized_label:
        aliases.append(normalized_label)
    return tuple(dict.fromkeys(_normalize_name(value) for value in aliases if value))


def _score(
    column: _Column,
    spec: InputSpec,
    *,
    rows: int | None,
    allow_user_override: bool = False,
) -> tuple[float, str]:
    if not _type_matches(column, spec, allow_user_override=allow_user_override):
        return 0.0, "类型不匹配"
    accepts_any = "any" in spec.accepts or not spec.accepts
    score = 0.34 if accepts_any and spec.kind == "columns" else 0.12 if accepts_any else 0.38
    reasons = [f"类型为{column.semantic_type}"]
    normalized = _normalize_name(column.name)
    normalized_label = _normalize_name(spec.label)
    name_tokens = set(normalized.split("_"))
    for alias_index, alias in enumerate(_aliases(spec)):
        if not alias:
            continue
        if normalized == alias:
            score += 0.68 if alias == normalized_label else max(0.25, 0.65 - alias_index * 0.04)
            reasons.append("名称精确匹配")
            break
        alias_min_chars = 2 if re.search(r"[\u4e00-\u9fff]", alias) else 4
        if alias in name_tokens or (len(alias) >= alias_min_chars and alias in normalized):
            score += max(0.28, 0.48 - alias_index * 0.02)
            reasons.append("名称语义匹配")
            break
    if _canonical_input_role(spec) in {"group", "strata"} and (
        column.semantic_type == "categorical"
        or (
            column.semantic_type == "numeric"
            and column.unique_count is not None
            and 2 <= column.unique_count <= 20
        )
    ):
        if column.unique_count is not None and 2 <= column.unique_count <= 20:
            score += 0.12
            reasons.append("低基数分类变量")
    if spec.semantic_role.endswith("_id") and rows and column.unique_count is not None:
        if column.unique_count >= rows * 0.9:
            score += 0.22
            reasons.append("近似唯一标识")
    if column.missing_pct >= 80:
        score -= 0.22
        reasons.append("高缺失降权")
    return max(0.0, min(score, 1.0)), "、".join(reasons)


def _candidates(
    columns: tuple[_Column, ...],
    spec: InputSpec,
    *,
    rows: int | None,
    excluded: set[str] | None = None,
    allow_user_override: bool = False,
) -> tuple[InputCandidate, ...]:
    if spec.choices:
        return tuple(InputCandidate(value, value, 1.0, "模板方法选项") for value in spec.choices)
    if spec.kind not in {"column", "columns"}:
        return ()
    excluded = excluded or set()
    ranked: list[InputCandidate] = []
    for column in columns:
        if column.name in excluded:
            continue
        score, reason = _score(
            column,
            spec,
            rows=rows,
            allow_user_override=allow_user_override,
        )
        if score <= 0:
            continue
        ranked.append(InputCandidate(column.name, column.name, round(score, 3), reason))
    return tuple(sorted(ranked, key=lambda item: (-item.confidence, str(item.value))))


def _level_candidates(
    dataset_profile: dict[str, Any],
    spec: InputSpec,
    resolved: dict[str, ResolvedInput],
) -> tuple[InputCandidate, ...]:
    if not spec.depends_on or spec.depends_on not in resolved:
        return ()
    parent_value = resolved[spec.depends_on].value
    if not isinstance(parent_value, str):
        return ()
    local_semantics = dataset_profile.get("local_semantics")
    local_semantics = local_semantics if isinstance(local_semantics, dict) else {}
    levels = local_semantics.get("levels")
    levels = levels if isinstance(levels, dict) else {}
    values = levels.get(parent_value)
    if not isinstance(values, list):
        return ()
    return tuple(
        InputCandidate(value=str(value), label=str(value), confidence=1.0, reason="本地低基数水平")
        for value in values[:32]
        if str(value) != ""
    )


def _valid_value(
    spec: InputSpec,
    value: Any,
    columns: tuple[_Column, ...],
    dataset_id: str,
    *,
    allow_user_override: bool = False,
) -> bool:
    if spec.choices and value not in spec.choices:
        return False
    by_name = {item.name: item for item in columns}
    if spec.kind == "column":
        return (
            isinstance(value, str)
            and value in by_name
            and _type_matches(
                by_name[value],
                spec,
                allow_user_override=allow_user_override,
            )
        )
    if spec.kind == "columns":
        if isinstance(value, (list, tuple)) and not value:
            return spec.requirement != "critical"
        return (
            isinstance(value, (list, tuple))
            and len(value) >= spec.min_items
            and len(value) == len(set(value))
            and all(
                isinstance(item, str)
                and item in by_name
                and _type_matches(
                    by_name[item],
                    spec,
                    allow_user_override=allow_user_override,
                )
                for item in value
            )
        )
    if spec.kind in {"dataset", "object"}:
        return isinstance(value, str) and value == dataset_id
    if spec.kind == "parameter":
        try:
            normalize_parameter_value(
                input_id=spec.id,
                label=spec.label,
                semantic_role=spec.semantic_role,
                value=value,
            )
        except ValueError:
            return False
        return True
    if spec.kind == "level":
        return isinstance(value, (str, int, float, bool)) and str(value) != ""
    if spec.kind == "specification":
        try:
            normalize_setting_value(value, json_document="json" in spec.accepts, json_or_text="json_or_text" in spec.accepts)
        except ValueError:
            return False
        return True
    return False


def _valid_level_value(
    dataset_profile: dict[str, Any],
    spec: InputSpec,
    value: Any,
    resolved: dict[str, ResolvedInput],
) -> bool:
    if not _valid_value(spec, value, (), ""):
        return False
    candidates = _level_candidates(dataset_profile, spec, resolved)
    return bool(candidates) and str(value) in {str(item.value) for item in candidates}


_OBJECT_KIND_HINTS: dict[str, tuple[str, ...]] = {
    "ml_explainability": (
        "lm",
        "glm",
        "coxph",
        "survreg",
        "train",
        "workflow",
        "model_fit",
        "ranger",
        "randomforest",
        "xgb.booster",
        "lgb.booster",
        "gbm",
        "keras",
        "torch",
    ),
    "flow_cytometry": ("flowset", "flowframe"),
    "single_cell": ("seurat", "singlecellexperiment", "summarizedexperiment"),
    "scrna_marker_enrichment": ("seurat", "singlecellexperiment", "summarizedexperiment"),
    "scrna_sample_integration": ("seurat", "singlecellexperiment", "summarizedexperiment"),
    "omics_scrna_trajectory": ("seurat", "singlecellexperiment", "summarizedexperiment"),
    "omics_scrna_differential_abundance": (
        "seurat",
        "singlecellexperiment",
        "summarizedexperiment",
    ),
    "omics_spatial_transcriptomics": (
        "seurat",
        "spatialexperiment",
        "summarizedexperiment",
    ),
    "omics_atac_chip_binding": ("dba", "diffbind", "csaw", "rangedse"),
}

_OBJECT_MODALITY_HINTS: dict[str, tuple[str, ...]] = {
    "flow_cytometry": ("flow_cytometry",),
    "single_cell": ("single_cell",),
    "scrna_marker_enrichment": ("single_cell",),
    "scrna_sample_integration": ("single_cell",),
    "omics_scrna_trajectory": ("single_cell",),
    "omics_scrna_differential_abundance": ("single_cell",),
    "omics_spatial_transcriptomics": ("spatial",),
    "omics_atac_chip_binding": ("chromatin", "epigenomic_binding"),
}


def _object_compatible(template: AnalysisTemplate, dataset_profile: dict[str, Any]) -> bool:
    if template.input_shape != "r_object":
        return True
    object_kind = str(dataset_profile.get("object_kind") or "").strip().casefold()
    scientific = dataset_profile.get("scientific")
    scientific = scientific if isinstance(scientific, dict) else {}
    if template.id == "ml_explainability":
        # Column bindings describe bundle$data; the object accessor still
        # returns the complete uploaded bundle, including its fitted model.
        return scientific.get("representation") == "model_bundle" and {
            "fitted_model", "observation_data", "training_background"
        }.issubset(set(scientific.get("capabilities") or ()))
    modality = str(dataset_profile.get("modality") or scientific.get("modality") or "").casefold()
    modality_hints = _OBJECT_MODALITY_HINTS.get(template.id)
    if modality_hints and modality not in {"", "unknown", "object", "r_object", "matrix", "tabular", "table"}:
        # A positively inspected scientific modality is stronger than a generic
        # RDS suffix. Cross-modality objects must fail closed.
        return modality in modality_hints
    # Native scientific files may enter an object workflow before conversion.
    # The loader and preflight establish the resulting R object compatibility.
    formats = set(dataset_asset_formats(dataset_profile))
    inherent_formats = formats - {"rds", "rdata", "h5", "generic_hdf5"}
    if inherent_formats.intersection(scientific_template_formats(template.id)):
        return True
    object_classes = set(re.split(r"[/,\s]+", object_kind))
    generic_matrix_object = (
        object_kind == ""
        or bool(object_classes.intersection({"tabular", "data.frame", "dataframe", "tbl_df", "matrix", "array"}))
        or scientific.get("representation") == "data_frame"
    )
    if generic_matrix_object:
        rows_value = _as_number(dataset_profile.get("rows"))
        rows = int(rows_value) if rows_value is not None else None
        # An explicitly declared matrix adapter follows the same orientation
        # confirmation gate as other scientific matrices. A CSV is never
        # silently promoted to a Seurat/SCE/spatial object by its suffix.
        return any(
            spec.kind == "object" and "numeric_matrix" in spec.accepts
            for spec in template.input_specs
        ) and _tabular_matrix_compatibility(template, dataset_profile, _columns(dataset_profile), rows) in {
            "compatible", "needs_confirmation"
        }
    hints = _OBJECT_KIND_HINTS.get(template.id)
    return not hints or any(hint in object_kind for hint in hints)


def _tabular_matrix_compatibility(
    template: AnalysisTemplate,
    dataset_profile: dict[str, Any],
    columns: tuple[_Column, ...],
    rows: int | None,
) -> str:
    """Classify matrix readiness without guessing from numeric columns alone.

    A numeric-heavy clinical table and a sample-by-feature assay table can
    have the same bounded column profile.  Native scientific formats and
    inspected assay axes are safe evidence; an ordinary CSV/Excel table needs
    an explicit orientation confirmation before it can become a matrix input.
    """

    matrix_inputs = [
        spec for spec in template.input_specs if spec.kind == "object" and "numeric_matrix" in spec.accepts
    ]
    if not matrix_inputs:
        return "compatible"
    scientific = dataset_profile.get("scientific")
    scientific = scientific if isinstance(scientific, dict) else {}
    modality = str(dataset_profile.get("modality") or scientific.get("modality") or "").casefold()
    if scientific_matrix_modality_compatibility(template.id, modality) is False:
        return "modality_mismatch"
    if has_scientific_matrix_evidence(template.id, dataset_profile):
        return "compatible"
    analyzable = [column for column in columns if column.semantic_type != "identifier"]
    numeric_count = sum(column.semantic_type == "numeric" or column.numeric_storage for column in analyzable)
    numeric_ratio = numeric_count / len(analyzable) if analyzable else 0.0
    # Lazy datasets have rows=None (bounded sample profile): the numeric
    # evidence alone must keep the template reachable as needs_confirmation —
    # a big expression matrix should ask the user to confirm orientation,
    # not be permanently rejected as incompatible.
    rows_known = rows is not None
    size_ok = (rows >= 3) if rows_known else True
    if not (size_ok and numeric_count >= 3 and numeric_ratio >= 0.6):
        return "incompatible"
    return "needs_confirmation"


class AnalysisPreparer:
    """Resolve what is safe locally and ask only for unresolved critical inputs."""

    def __init__(
        self,
        catalog: AnalysisCatalog,
        memory: DatasetRoleMemory | None = None,
        runtime_packages: Sequence[str] | None = None,
    ) -> None:
        self._catalog = catalog
        self._memory = memory or DatasetRoleMemory()
        self._runtime_packages = (
            tuple(str(item).strip() for item in runtime_packages if str(item).strip())
            if runtime_packages is not None
            else None
        )

    def _resolved_template_packages(
        self, template: AnalysisTemplate, resolved: dict[str, ResolvedInput],
    ) -> tuple[str, ...]:
        """Select the frozen method branch before consulting installed packages.

        Branch candidates retain missing implementations for the dependency
        gate. Templates without branches keep their existing inventory policy.
        """

        from agent.analysis.contract_conditions import conditions_match

        branches = [
            packages for condition, packages in template.package_branches.items()
            if conditions_match((condition,), resolved)
        ]
        if template.package_branches:
            selectors = {condition[6:].partition("=")[0] for condition in template.package_branches if "=" in condition}
            for key in selectors:
                selected = [condition for condition in template.package_branches
                            if condition.startswith(f"input:{key}=") and conditions_match((condition,), resolved)]
                if len(selected) != 1:
                    raise ValueError("请明确选择一个分析分支后再检查依赖。")
            # Keep missing method implementations visible to the dependency
            # gate; an unrelated installed package cannot satisfy this branch.
            if selectors:
                return tuple(dict.fromkeys(package for branch in branches for package in branch))
            optional = {package for branch in template.package_branches.values() for package in branch}
            common = [package for package in template.packages if package not in optional]
            return tuple(dict.fromkeys(common + [package for branch in branches for package in branch]))
        if template.package_policy == "required" or self._runtime_packages is None:
            return template.packages
        installed = {item.casefold(): item for item in self._runtime_packages}
        selected: list[str] = []
        for candidate in template.packages:
            package = installed.get(str(candidate).strip().casefold())
            if package and package not in selected:
                selected.append(package)
        if not selected and "stats" in installed:
            selected.append(installed["stats"])
        return tuple(selected)

    def prepare(
        self,
        template_id: str,
        dataset_profile: dict[str, Any],
        provided_inputs: dict[str, Any] | None = None,
    ) -> PreparationResult:
        template = self._catalog.get(template_id)
        # Scientific inputs come only from the versioned catalog contract.
        # Optional controls must not be synthesized from label/name heuristics.
        input_specs = template.input_specs
        dataset_id = str(dataset_profile.get("dataset_id") or "").strip()
        if not dataset_id:
            raise ValueError("dataset profile must include dataset_id")
        if not _object_compatible(template, dataset_profile):
            return PreparationResult(
                status="not_applicable",
                template_id=template.id,
                reason="该模板需要结构化 R 分析对象；当前上传内容是普通表格对象。",
            )
        dataset_formats = dataset_asset_formats(dataset_profile)
        accepted_formats = accepted_template_formats(template)
        if dataset_formats and accepted_formats and not set(dataset_formats).intersection(accepted_formats):
            return PreparationResult(
                status="not_applicable",
                template_id=template.id,
                reason=(
                    "该模板不支持当前数据资产格式："
                    + "、".join(item.upper() for item in dataset_formats)
                    + "。"
                ),
            )

        columns = _columns(dataset_profile)
        rows_value = _as_number(dataset_profile.get("rows"))
        rows = int(rows_value) if rows_value is not None else None
        supplied = dict(provided_inputs or {})
        raw_matrix_orientation = supplied.pop(_MATRIX_ORIENTATION_INPUT_ID, None)
        matrix_orientation = str(raw_matrix_orientation or "").strip()
        has_numeric_matrix_input = any(
            spec.kind == "object" and "numeric_matrix" in spec.accepts
            for spec in input_specs
        )
        if matrix_orientation and not has_numeric_matrix_input:
            raise ValueError("Unknown template inputs: __matrix_orientation__")
        if matrix_orientation and matrix_orientation not in _MATRIX_ORIENTATION_VALUES:
            raise ValueError("Invalid matrix orientation confirmation")
        matrix_compatibility = _tabular_matrix_compatibility(
            template,
            dataset_profile,
            columns,
            rows,
        )
        if matrix_compatibility in {"incompatible", "modality_mismatch"}:
            modality = str(
                dataset_profile.get("modality")
                or (
                    dataset_profile.get("scientific", {}).get("modality")
                    if isinstance(dataset_profile.get("scientific"), dict)
                    else ""
                )
                or "unknown"
            )
            reason = (
                f"当前数据已识别为 {modality}，与“{template.title}”要求的"
                f"{scientific_matrix_semantic_label(template.id)}不一致；不能跨实验模态强行运行。"
                if matrix_compatibility == "modality_mismatch"
                else (
                    "当前数据没有可验证的特征×样本 assay 结构，也不像该模板所需的数值特征矩阵；"
                    "请上传预处理矩阵、完整数据包或相应 R 对象。"
                )
            )
            return PreparationResult(
                status="not_applicable",
                template_id=template.id,
                reason=reason,
            )
        if matrix_compatibility == "needs_confirmation" and matrix_orientation in {
            "requires_restructure",
            "not_target_matrix",
        }:
            reason = (
                "当前矩阵是特征为行、样本为列或分组信息位于另一文件，需先整理为带样本注释的"
                "分析对象/完整数据包后再使用此模板。"
                if matrix_orientation == "requires_restructure"
                else "当前上传数据不是该模板要求的科学特征矩阵；不能把普通数值表直接作为该方法的矩阵输入。"
            )
            return PreparationResult(
                status="not_applicable",
                template_id=template.id,
                reason=reason,
            )
        generation = str(dataset_profile.get("workspace_generation") or "").strip()
        revision = str(dataset_profile.get("profile_revision") or "").strip()
        specs_by_id = {item.id: item for item in input_specs}
        specs_by_label = {label: item for item in input_specs for label in (item.label, *item.legacy_labels)}
        specs_by_legacy_id = {
            _legacy_hashed_input_id(label): item
            for item in input_specs for label in (item.label, *item.legacy_labels)
        }
        normalized_supplied: dict[str, Any] = {}
        unknown: list[str] = []
        for raw_key, value in supplied.items():
            key = str(raw_key or "").strip()
            spec = (
                specs_by_id.get(key)
                or specs_by_label.get(key)
                or specs_by_legacy_id.get(key)
            )
            if spec is None:
                unknown.append(key)
                continue
            if spec.id in normalized_supplied and normalized_supplied[spec.id] != value:
                raise ValueError(
                    f"Conflicting values for template input {spec.label}: "
                    f"both current and legacy input IDs were supplied"
                )
            normalized_supplied[spec.id] = value
        if unknown:
            raise ValueError(f"Unknown template inputs: {', '.join(sorted(unknown))}")

        resolved: dict[str, ResolvedInput] = {}
        questions: list[InputQuestion] = []
        incompatibilities: list[str] = []
        role_rebalance_warnings: list[str] = []
        used_scalar_columns: set[str] = set()
        matrix_confirmation_pending = (
            matrix_compatibility == "needs_confirmation"
            and matrix_orientation != "samples_by_features"
        )
        if matrix_compatibility == "needs_confirmation" and not matrix_confirmation_pending:
            resolved[_MATRIX_ORIENTATION_INPUT_ID] = ResolvedInput(
                _MATRIX_ORIENTATION_INPUT_ID,
                "矩阵方向与科学语义",
                "specification",
                matrix_orientation,
                "user",
            )
        if matrix_confirmation_pending:
            feature_semantics = scientific_matrix_semantic_label(template.id)
            questions.append(
                InputQuestion(
                    input_id=_MATRIX_ORIENTATION_INPUT_ID,
                    label="矩阵方向与科学语义",
                    kind="specification",
                    required=True,
                    candidates=(
                        InputCandidate(
                            "not_target_matrix",
                            "不是或不确定（不了解矩阵含义时选此项）：这是普通研究表，或我无法确认",
                            1.0,
                            "不运行该科学模板并提供判断指引与替代路径",
                        ),
                        InputCandidate(
                            "requires_restructure",
                            "需要整理：特征为行、样本为列，或分组/注释位于另一文件",
                            1.0,
                            "先生成带样本注释的 R 对象或完整数据包",
                        ),
                        InputCandidate(
                            "samples_by_features",
                            f"是：每行是一个观测（样本、细胞或spot），数值列是{feature_semantics}，分组/注释逐行对应",
                            1.0,
                            "用户声明后冻结方向；运行时仍先由 R 检查尺度、特征和样本对齐",
                        ),
                    ),
                    description=(
                        f"此模板要求{feature_semantics}。仅有多列数值不足以证明数据符合该科学语义；"
                        "请选择真实的数据方向；"
                        "系统不会把普通临床表自动当作表达矩阵。"
                    ),
                )
            )

        def remember(spec: InputSpec, value: Any) -> None:
            if (
                spec.kind not in {"column", "columns"}
                or _canonical_input_role(spec) not in _DATASET_INTRINSIC_MEMORY_ROLES
            ):
                return
            self._memory.remember(
                dataset_id=dataset_id,
                workspace_generation=generation,
                profile_revision=revision,
                semantic_role=_canonical_input_role(spec),
                value=value,
            )

        for spec in input_specs:
            if spec.id not in normalized_supplied:
                continue
            if spec.kind == "level":
                # Validate dependent levels only after their parent column has
                # been supplied or inferred in the ordered resolution pass.
                continue
            value = normalized_supplied[spec.id]
            if not _valid_value(
                spec,
                value,
                columns,
                dataset_id,
                allow_user_override=True,
            ):
                raise ValueError(f"Invalid value for template input {spec.label}")
            normalized_value = (
                list(value)
                if spec.kind == "columns"
                else normalize_parameter_value(
                    input_id=spec.id,
                    label=spec.label,
                    semantic_role=spec.semantic_role,
                    value=value,
                )
                if spec.kind == "parameter"
                else normalize_setting_value(value, json_document="json" in spec.accepts, json_or_text="json_or_text" in spec.accepts)
                if spec.kind == "specification"
                else value
            )
            if spec.kind == "column" and str(normalized_value) in used_scalar_columns:
                raise ValueError(f"Column is already used by another template input: {normalized_value}")
            resolved[spec.id] = ResolvedInput(
                spec.id,
                spec.label,
                spec.kind,
                normalized_value,
                "user",
            )
            if spec.kind == "column":
                used_scalar_columns.add(str(normalized_value))
            remember(spec, normalized_value)

        def rebalance_column_sets(excluded: set[str]) -> None:
            """Remove columns promoted to scalar roles from broad variable sets.

            This is a shared role-editing rule, not a Table 1 special case.  A
            changed outcome/group/event/split role takes precedence over a
            stale multi-select value; an emptied critical set is re-inferred
            from the remaining eligible columns below.
            """

            for input_id, item in tuple(resolved.items()):
                if item.kind != "columns":
                    continue
                values = [str(value) for value in item.value]
                overlap = [value for value in values if value in excluded]
                if not overlap:
                    continue
                remaining = [value for value in values if value not in excluded]
                spec = specs_by_id[input_id]
                message = (
                    f"已从“{item.label}”移除转作单一研究角色的列："
                    + "、".join(overlap)
                    + "。"
                )
                if message not in role_rebalance_warnings:
                    role_rebalance_warnings.append(message)
                if remaining:
                    resolved[input_id] = replace(item, value=remaining)
                    if input_id in normalized_supplied:
                        normalized_supplied[input_id] = remaining
                elif spec.auto_select == "all_eligible":
                    # The old set contained only a column that has now become
                    # group/outcome/etc.  Remove the stale resolution so the
                    # normal inference pass can select newly eligible columns.
                    resolved.pop(input_id, None)
                    normalized_supplied.pop(input_id, None)
                elif spec.requirement != "critical":
                    resolved[input_id] = replace(item, value=[])
                    if input_id in normalized_supplied:
                        normalized_supplied[input_id] = []
                else:
                    resolved.pop(input_id, None)
                    normalized_supplied.pop(input_id, None)

        rebalance_column_sets(used_scalar_columns)

        # Reserve unambiguous scalar roles before resolving broad column sets.
        # This prevents an outcome/group/split column from being swallowed by
        # an earlier "all eligible features" input solely because of ordering.
        reserved_scalar_columns: set[str] = set()
        for spec in input_specs:
            if spec.id in resolved or spec.kind != "column":
                continue
            candidates = _candidates(
                columns,
                spec,
                rows=rows,
                excluded=used_scalar_columns | reserved_scalar_columns,
            )
            if not candidates:
                continue
            best = candidates[0]
            margin = best.confidence - (candidates[1].confidence if len(candidates) > 1 else 0.0)
            if best.confidence >= 0.72 and (len(candidates) == 1 or margin >= 0.12):
                reserved_scalar_columns.add(str(best.value))

        rebalance_column_sets(used_scalar_columns | reserved_scalar_columns)

        for spec in input_specs:
            if spec.id in resolved:
                continue

            if (
                matrix_confirmation_pending
                and spec.kind == "object"
                and "numeric_matrix" in spec.accepts
            ):
                continue

            if spec.id in normalized_supplied:
                value = normalized_supplied[spec.id]
                valid = (
                    _valid_level_value(dataset_profile, spec, value, resolved)
                    if spec.kind == "level"
                    else _valid_value(
                        spec,
                        value,
                        columns,
                        dataset_id,
                        allow_user_override=True,
                    )
                )
                if not valid:
                    raise ValueError(f"Invalid value for template input {spec.label}")
                normalized_value = str(value) if spec.kind == "level" else value
                resolved[spec.id] = ResolvedInput(
                    spec.id,
                    spec.label,
                    spec.kind,
                    normalized_value,
                    "user",
                )
                remember(spec, normalized_value)
                continue

            recalled = (
                self._memory.recall(
                    dataset_id=dataset_id,
                    workspace_generation=generation,
                    profile_revision=revision,
                    semantic_role=_canonical_input_role(spec),
                )
                if _canonical_input_role(spec) in _DATASET_INTRINSIC_MEMORY_ROLES
                else None
            )
            recalled_conflicts = (spec.kind == "column" and str(recalled) in used_scalar_columns) or (
                spec.kind == "columns"
                and bool(
                    set(map(str, recalled if isinstance(recalled, (list, tuple)) else ()))
                    & (used_scalar_columns | reserved_scalar_columns)
                )
            )
            recalled_valid = (
                _valid_level_value(dataset_profile, spec, recalled, resolved)
                if spec.kind == "level"
                else _valid_value(spec, recalled, columns, dataset_id)
            )
            if recalled is not None and not recalled_conflicts and recalled_valid:
                normalized_value = list(recalled) if spec.kind == "columns" else recalled
                resolved[spec.id] = ResolvedInput(
                    spec.id,
                    spec.label,
                    spec.kind,
                    normalized_value,
                    "memory",
                )
                if spec.kind == "column":
                    used_scalar_columns.add(str(normalized_value))
                continue

            if spec.kind in {"dataset", "object"} and spec.auto_select == "dataset":
                resolved[spec.id] = ResolvedInput(
                    spec.id,
                    spec.label,
                    spec.kind,
                    dataset_id,
                    "dataset",
                )
                continue

            if spec.has_default:
                default_value = (
                    normalize_parameter_value(
                        input_id=spec.id,
                        label=spec.label,
                        semantic_role=spec.semantic_role,
                        value=spec.default,
                    )
                    if spec.kind == "parameter"
                    else normalize_setting_value(spec.default, json_document="json" in spec.accepts, json_or_text="json_or_text" in spec.accepts)
                    if spec.kind == "specification"
                    else spec.default
                )
                resolved[spec.id] = ResolvedInput(
                    spec.id,
                    spec.label,
                    spec.kind,
                    default_value,
                    "default",
                )
                continue

            candidates = (
                _level_candidates(dataset_profile, spec, resolved)
                if spec.kind == "level"
                else _candidates(
                    columns,
                    spec,
                    rows=rows,
                    excluded=(
                        used_scalar_columns | (reserved_scalar_columns if spec.kind == "columns" else set())
                        if spec.kind in {"column", "columns"}
                        else set()
                    ),
                )
            )
            if spec.kind == "level" and spec.depends_on not in resolved:
                # Ask for the parent variable first. A second deterministic
                # preparation pass can then offer only its observed levels.
                continue
            if spec.kind == "level" and spec.depends_on in resolved and not candidates:
                if spec.requirement == "critical":
                    incompatibilities.append(f"无法读取“{spec.label}”对应变量的可用水平")
                continue
            if spec.kind == "columns" and spec.auto_select == "all_eligible":
                should_resolve_set = (
                    spec.requirement != "optional" or spec.semantic_role in _GENERIC_SET_ROLES
                )
                eligible = [
                    str(item.value)
                    for item in candidates
                    if item.confidence >= 0.3
                    and not (
                        next(column for column in columns if column.name == item.value).semantic_type
                        == "identifier"
                        and "identifier" not in spec.accepts
                    )
                ]
                if should_resolve_set and eligible:
                    resolved[spec.id] = ResolvedInput(
                        spec.id,
                        spec.label,
                        spec.kind,
                        eligible,
                        "inferred",
                        min(item.confidence for item in candidates if str(item.value) in eligible),
                    )
                    continue

            if spec.kind == "column" and spec.auto_select == "best_match" and candidates:
                best = candidates[0]
                margin = best.confidence - (candidates[1].confidence if len(candidates) > 1 else 0.0)
                unambiguous = best.confidence >= 0.72 and (len(candidates) == 1 or margin >= 0.12)
                if unambiguous and spec.confirmation != "always":
                    resolved[spec.id] = ResolvedInput(
                        spec.id,
                        spec.label,
                        spec.kind,
                        best.value,
                        "inferred",
                        best.confidence,
                    )
                    used_scalar_columns.add(str(best.value))
                    continue

            if spec.requirement == "critical":
                if spec.kind in {"column", "columns"} and not candidates:
                    type_label = {
                        "numeric": "数值",
                        "categorical": "分类",
                        "datetime": "日期/时间",
                        "identifier": "标识符",
                        "text": "文本",
                    }.get(spec.accepts[0] if len(spec.accepts) == 1 else "", "可用")
                    incompatibilities.append(f"缺少可用于“{spec.label}”的{type_label}列")
                    continue
                questions.append(
                    InputQuestion(
                        input_id=spec.id,
                        label=spec.label,
                        kind=spec.kind,
                        required=True,
                        candidates=candidates[:12],
                        default=spec.default if spec.has_default else None,
                        description=spec.description,
                    )
                )

        from agent.analysis.contract_conditions import conditions_match, missing_contract_inputs

        missing = missing_contract_inputs(input_specs, template.input_alternatives, resolved)
        existing_questions = {item.input_id for item in questions}
        for input_id in missing:
            if input_id in existing_questions:
                continue
            spec = specs_by_id[input_id]
            # Existing critical-input handling may deliberately defer an object
            # until matrix-orientation confirmation. Do not ask twice.
            if spec.requirement == "critical" and input_id not in resolved:
                continue
            candidates = _candidates(columns, spec, rows=rows, allow_user_override=True)
            alternatives = next((group for group in template.input_alternatives if input_id in group), ())
            description = spec.description
            if alternatives:
                description += " 至少选择一类输入：" + "、".join(specs_by_id[key].label for key in alternatives) + "。"
            questions.append(InputQuestion(
                input_id=spec.id, label=spec.label, kind=spec.kind, required=True,
                candidates=candidates[:256], description=description,
            ))

        for input_id, message in joint_input_issues(
            resolved, template_id=template.id, dataset_profile=dataset_profile,
        ).items():
            spec = specs_by_id[input_id]
            questions = [q for q in questions if q.input_id != input_id]
            questions.append(InputQuestion(
                input_id=input_id, label=spec.label, kind=spec.kind, required=True,
                candidates=_candidates(columns, spec, rows=rows)[:256], description=message,
            ))

        def input_controls() -> tuple[InputControl, ...]:
            controls: list[InputControl] = []
            if matrix_confirmation_pending:
                matrix_question = next(
                    (
                        item
                        for item in questions
                        if item.input_id == _MATRIX_ORIENTATION_INPUT_ID
                    ),
                    None,
                )
                if matrix_question is not None:
                    controls.append(
                        InputControl(
                            input_id=matrix_question.input_id,
                            label=matrix_question.label,
                            kind=matrix_question.kind,
                            required=True,
                            candidates=matrix_question.candidates,
                            description=matrix_question.description,
                        )
                    )
            for spec in input_specs:
                current = resolved.get(spec.id)
                if spec.kind == "level":
                    candidates = _level_candidates(dataset_profile, spec, resolved)
                elif spec.kind in {"column", "columns"}:
                    candidates = _candidates(
                        columns,
                        spec,
                        rows=rows,
                        allow_user_override=True,
                    )
                else:
                    candidates = _candidates(columns, spec, rows=rows) if spec.choices else ()
                controls.append(
                    InputControl(
                        input_id=spec.id,
                        label=spec.label,
                        kind=spec.kind,
                        required=spec.requirement == "critical" or (
                            bool(spec.required_when) and conditions_match(spec.required_when, resolved)
                        ),
                        value=current.value if current is not None else None,
                        provenance=current.provenance if current is not None else "",
                        candidates=candidates[:256],
                        default=spec.default if spec.has_default else None,
                        description=next((q.description for q in questions if q.input_id == spec.id), spec.description),
                        editable=spec.kind not in {"dataset", "object"},
                    )
                )
            return tuple(controls)

        # Preserve parent-child semantics (for example reference/event levels)
        # in the frozen plan so the shared workflow compiler and R accessor can
        # enforce the same contract after preparation.
        specs_by_id = {spec.id: spec for spec in input_specs}
        for input_id, item in tuple(resolved.items()):
            dependency = specs_by_id.get(input_id).depends_on if input_id in specs_by_id else ""
            if dependency and item.depends_on != dependency:
                resolved[input_id] = replace(item, depends_on=dependency)

        resolved_values = tuple(resolved.values())
        controls = input_controls()
        selected_columns = {
            str(value)
            for item in resolved_values
            if item.kind in {"column", "columns"}
            for value in (
                item.value
                if isinstance(item.value, (list, tuple))
                else (item.value,)
            )
        }
        unconfirmed_labels = [
            column.name
            for column in columns
            if column.name in selected_columns
            and column.numeric_coding == "labels_unconfirmed"
        ]
        warnings = tuple(
            (
                *role_rebalance_warnings,
                "以下数值编码分类变量没有已验证的标签字典："
                + "、".join(unconfirmed_labels)
                + "；结果将保留原始水平并明确标注标签未确认，不推断编码含义。"
            )
            if unconfirmed_labels
            else role_rebalance_warnings
        )
        group_count = observed_group_count(resolved, dataset_profile)
        if template.id == "publication_table" and group_count is not None and group_count < 2:
            warnings += ("分组变量不足两个有效水平，本轮仅做描述汇总，不要求 P 值、SMD 或组间比较结果。",)
        if incompatibilities:
            return PreparationResult(
                status="not_applicable",
                template_id=template.id,
                resolved_inputs=resolved_values,
                input_controls=controls,
                reason="；".join(incompatibilities) + "。",
                warnings=warnings,
            )
        if questions:
            return PreparationResult(
                status="needs_input",
                template_id=template.id,
                resolved_inputs=resolved_values,
                questions=tuple(questions),
                input_controls=controls,
                reason=f"还需确认 {len(questions)} 个会改变分析含义的关键输入。",
                warnings=warnings,
            )

        variable_bindings = {
            item.label: str(resolved[item.id].value)
            for item in input_specs
            if item.kind == "column" and item.id in resolved
        }
        from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler

        plan_packages = self._resolved_template_packages(template, resolved)
        plan_diagnostics = tuple(
            name for name in template.diagnostics
            if conditions_match(template.diagnostic_conditions.get(name, ()), resolved)
        )
        compiled_portfolio = ScientificOutputPortfolioCompiler().compile(
            source="template",
            template_id=template.id,
            intent=template.title,
            method_family=template.family,
            scenario=template.scenario,
            baseline_outputs=template.required_outputs,
            dataset_profile=dataset_profile,
            resolved_inputs=resolved,
            packages=plan_packages,
            packages_verified=self._runtime_packages is not None and all(
                package.casefold() in {item.casefold() for item in self._runtime_packages}
                for package in plan_packages
            ),
            diagnostics=plan_diagnostics,
        )
        plan = AnalysisPlan(
            plan_id=uuid.uuid4().hex[:16],
            source="template",
            dataset_id=dataset_id,
            intent=template.title,
            category=template.category,
            method_family=template.family,
            scenario=template.scenario,
            variable_bindings=variable_bindings,
            packages=plan_packages,
            prerequisites=template.prerequisites,
            assumptions=template.assumptions,
            diagnostics=plan_diagnostics,
            required_outputs=compiled_portfolio.outputs,
            resource_class=template.resource_class,
            resolved_inputs=dict(resolved),
            template_id=template.id,
            workspace_generation=generation,
            profile_revision=revision,
            portfolio_version=compiled_portfolio.version,
            capability_evidence=compiled_portfolio.capability_evidence,
        )
        return PreparationResult(
            status="ready",
            template_id=template.id,
            resolved_inputs=resolved_values,
            input_controls=controls,
            plan=plan,
            reason="已根据当前数据画像形成冻结分析计划。",
            warnings=warnings,
        )
