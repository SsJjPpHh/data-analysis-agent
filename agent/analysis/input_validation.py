"""Shared validation for frozen non-column scientific inputs."""

from __future__ import annotations

import math
import re
import json
from typing import Any, Mapping

from agent.analysis.contract_conditions import has_value, input_value


def _parameter_family(input_id: str, label: str, semantic_role: str) -> str:
    text = " ".join((input_id, label, semantic_role)).casefold()
    if re.search(r"soft.?threshold|软阈值", text):
        return "positive"
    patterns = (
        ("probability", r"event_probability|null_response_rate|target_response_rate"),
        ("alpha", r"(?:^|\W|_)alpha(?:$|\W|_)|检验水准|显著性水平"),
        ("power", r"(?:^|\W|_)power(?:$|\W|_)|把握度|检验效能"),
        ("allocation_ratio", r"allocation.?ratio|分组比例|分配比例"),
        ("effect_size", r"effect.?size|效应大小|效应量"),
        ("positive_integer", r"sample.?size|block.?size|class.?count|样本量|区组大小|类别数"),
        ("nonnegative", r"false.?positive.?cost|false.?negative.?cost|假阳性成本|假阴性成本"),
        ("continuous_summary", r"continuous.?summary|连续变量汇总规则"),
    )
    return next(
        (family for family, pattern in patterns if re.search(pattern, text, re.IGNORECASE)),
        "",
    )


def _finite_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric analysis parameter")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("analysis parameter must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError("analysis parameter must be finite")
    return number


def normalize_setting_value(value: Any, *, text_limit: int = 4_000, json_document: bool = False, json_or_text: bool = False) -> Any:
    """Normalize a scalar or a bounded flat vector; never interpret R code."""
    if json_document or (json_or_text and (
        isinstance(value, dict)
        or isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value)
        or isinstance(value, str) and value.lstrip().startswith(("{", "["))
    )):
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
            if not isinstance(parsed, (dict, list)):
                raise ValueError("analysis JSON document must be an object or array")
            document = json.dumps(parsed, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("analysis JSON document must contain finite JSON data") from exc
        if not document or len(document) > 16_000:
            raise ValueError("analysis JSON document exceeds the settings budget; use native object metadata")
        return document
    if isinstance(value, str):
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            if len(value) > 16_000:
                raise ValueError("analysis vector is too large")
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("analysis vector must be a valid JSON array") from exc
    if isinstance(value, (list, tuple)):
        if not 1 <= len(value) <= 256:
            raise ValueError("analysis vector must contain 1 to 256 scalar values")
        result = []
        for item in value:
            if isinstance(item, (list, tuple, dict)) or item is None:
                raise ValueError("analysis vector cannot contain nested objects or null")
            if isinstance(item, str):
                item = item.strip()
                if not item or len(item) > text_limit:
                    raise ValueError("analysis vector text is empty or too long")
            elif isinstance(item, (int, float, bool)):
                if not math.isfinite(float(item)):
                    raise ValueError("analysis vector must contain finite values")
            else:
                raise ValueError("analysis vector contains an unsupported value")
            result.append(item)
        if len(json.dumps(result, ensure_ascii=False)) > 16_000:
            raise ValueError("analysis vector is too large")
        return result
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return value
    if isinstance(value, str) and value and len(value) <= text_limit:
        return value
    raise ValueError("analysis setting must be a bounded scalar or flat vector")


def normalize_parameter_value(
    *,
    input_id: str,
    label: str,
    semantic_role: str = "",
    value: Any,
) -> Any:
    """Validate known scientific parameter domains and normalize numeric text.

    The catalog contains both numeric parameters and bounded textual design
    settings. Unknown parameter families therefore retain the conservative V4
    scalar contract, while universally recognized statistical roles receive
    stronger server-side domain checks.
    """

    family = _parameter_family(input_id, label, semantic_role)
    if family == "continuous_summary":
        normalized = str(value or "").strip().casefold()
        if normalized not in {"auto", "mean_sd", "median_iqr"}:
            raise ValueError("continuous summary must be auto, mean_sd, or median_iqr")
        return normalized
    if family:
        number = _finite_number(value)
        if family in {"alpha", "power", "probability"} and not 0 < number < 1:
            raise ValueError(f"{family} must be strictly between 0 and 1")
        if family == "allocation_ratio" and number <= 0:
            raise ValueError("allocation ratio must be greater than 0")
        if family == "positive" and number <= 0:
            raise ValueError("analysis parameter must be greater than 0")
        if family == "positive_integer":
            if number <= 0 or not number.is_integer():
                raise ValueError("count parameter must be a positive integer")
            return int(number)
        if family == "nonnegative" and number < 0:
            raise ValueError("cost parameter must be non-negative")
        return number

    return normalize_setting_value(value, text_limit=1_000)


def observed_group_count(inputs: Mapping[str, Any], profile: Mapping[str, Any]) -> int | None:
    """Use observed non-missing levels; an unknown count stays unknown."""
    group = input_value(inputs.get("group"))
    columns = profile.get("columns")
    if not isinstance(columns, list):
        columns = profile.get("variables")
    for column in columns if isinstance(columns, list) else ():
        if not isinstance(column, Mapping) or column.get("name") != group:
            continue
        count = column.get("unique_count")
        try:
            number = _finite_number(count)
        except ValueError:
            return None
        return int(number) if number >= 0 and number.is_integer() else None
    return None


def _profile_column(profile: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    columns = profile.get("columns")
    if not isinstance(columns, list):
        columns = profile.get("variables")
    for column in columns if isinstance(columns, list) else ():
        if isinstance(column, Mapping) and column.get("name") == name:
            return column
    return None


def _column_levels(profile: Mapping[str, Any], name: str) -> list[str]:
    """Observed levels for one column, from wherever the pipeline stored them.

    The inspection summary keeps observed levels in
    ``local_semantics.levels[column]`` (per-column, bounded); some callers
    attach them directly on the column record.  Without the local_semantics
    fallback these guards never fire on production profiles.
    """
    column = _profile_column(profile, name)
    levels: list[str] = []
    if column:
        raw = column.get("levels")
        if isinstance(raw, list):
            levels = [str(level) for level in raw]
    if not levels:
        local_semantics = profile.get("local_semantics")
        local_semantics = local_semantics if isinstance(local_semantics, dict) else {}
        level_map = local_semantics.get("levels")
        if isinstance(level_map, dict):
            raw = level_map.get(name)
            if isinstance(raw, list):
                levels = [str(level) for level in raw]
    return levels


def _numeric_levels(levels: list[str]) -> list[float] | None:
    numbers: list[float] = []
    for level in levels:
        try:
            numbers.append(_finite_number(level))
        except ValueError:
            return None
    return numbers


def joint_input_issues(
    inputs: Mapping[str, Any],
    *,
    template_id: str = "",
    dataset_profile: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """A few method constraints shared by preparation and execution preflight."""
    values = {key: input_value(value) for key, value in inputs.items()}
    issues: dict[str, str] = {}
    from .method_policy import method_scope_issues
    issues.update(method_scope_issues(template_id, values))
    if has_value(values.get("__matrix_orientation__")):
        from .catalog import AnalysisCatalog
        matrix_template = AnalysisCatalog().get(template_id)
        declared = {s.id for s in matrix_template.input_specs}
        if "feature_columns" in declared and not has_value(values.get("feature_columns")):
            issues["feature_columns"] = "已确认样本为行的表达表还需明确特征列，避免将样本ID、分组或性状作为表达特征。"
        if ("matrix_orientation" in declared and has_value(values.get("matrix_orientation")) and
                values["matrix_orientation"] != values["__matrix_orientation__"]):
            issues["matrix_orientation"] = "矩阵方向与已确认的表格科学语义不一致。"
    # Ask for a direction when inspected labels carry no 0/1 convention. This
    # is an execution input, not a new review of the chosen scientific method.
    treatment_roles = {
        "propensity_clinical": "exposure", "clinical_doubly_robust_effect": "exposure",
        "attributable_fraction": "exposure", "public_vaccine_test_negative": "exposure",
    }
    binary_outcomes = {
        "public_vaccine_test_negative", "attributable_fraction", "calibration_dca",
        "external_validation_model", "nri_idi_reclassification", "matched_case_control",
        "decision_curve_threshold", "public_screening_evaluation",
    }
    direction_checks = []
    if template_id in treatment_roles and not any(has_value(values.get(k)) for k in ("treatment_level", "reference_group")):
        direction_checks.append((treatment_roles[template_id], "treatment_level", "治疗/暴露类别"))
    if template_id in binary_outcomes and not has_value(values.get("positive_class")):
        direction_checks.append(("outcome", "positive_class", "事件/阳性类别"))
    if direction_checks:
        from .catalog import AnalysisCatalog
        template = AnalysisCatalog().get(template_id)
        declared = {s.id for s in template.input_specs}
        for role, setting, label in direction_checks:
            levels = _column_levels(dataset_profile or {}, str(values.get(role) or ""))
            convention = set(level.casefold() for level in levels)
            if (setting in declared and len(levels) == 2 and
                    convention not in ({"0", "1"}, {"false", "true"})):
                issues[setting] = f"{role} 的取值为 {'、'.join(levels)}；请指定{label}，以冻结效应方向。"
    information_time = values.get("information_time")
    if has_value(information_time):
        if isinstance(information_time, str):
            text = information_time.strip()
            if text.startswith("c(") and text.endswith(")"):
                text = text[2:-1]
            elif text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            parts = re.split(r"[,，\s]+", text.strip())
        else:
            parts = information_time if isinstance(information_time, (list, tuple)) else [information_time]
        try:
            times = [_finite_number(part) for part in parts]
            valid = (
                bool(times) and all(0 < value <= 1 for value in times)
                and all(left < right for left, right in zip(times, times[1:]))
                and math.isclose(times[-1], 1, rel_tol=0, abs_tol=1e-9)
                and (template_id != "group_sequential" or 2 <= len(times) <= 10)
            )
        except ValueError:
            valid = False
        if not valid:
            issues["information_time"] = "累计信息比例须在 (0, 1] 内严格递增，末次为 1；例如 0.5, 1。"

    p0, p1 = values.get("null_response_rate"), values.get("target_response_rate")
    if has_value(p0) and has_value(p1):
        try:
            valid = 0 < _finite_number(p0) < _finite_number(p1) < 1
        except ValueError:
            valid = False
        if not valid:
            issues["target_response_rate"] = "Simon 两阶段设计须满足 0 < 无效响应率 p0 < 目标响应率 p1 < 1。"

    count = observed_group_count(inputs, dataset_profile or {})
    if template_id == "group_tests" and count is not None and count < 2:
        issues["group"] = "组间比较至少需要两个有效分组；请改选分组变量，或使用总体描述分析。"

    # Survival event coding: SPSS/Chinese exports commonly use 1=删失/2=事件,
    # while the engines require 0=删失/1=事件.  A wrong coding flips every
    # KM curve and HR silently, so an explicit level check at freeze time is
    # the cheapest place to catch it.
    if template_id in {"survival_km_cox", "extended_survival", "time_dependent_roc"}:
        event_value = values.get("event")
        if has_value(event_value):
            levels = _column_levels(dataset_profile or {}, str(event_value))
            numbers = _numeric_levels(levels)
            if numbers is not None and numbers:
                allowed = {0.0, 1.0}
                if not set(numbers) <= allowed:
                    issues["event"] = (
                        "事件变量的取值为 "
                        + "、".join(str(level) for level in levels[:6])
                        + "；本分析要求 0=删失、1=事件。SPSS 等软件导出常用 1=删失、2=事件，"
                        "直接运行会把事件定义弄反——请先将该列转换为 0/1 编码（明确哪个取值代表事件发生）后重新准备分析。"
                    )

    # Binary-outcome engines reject multi-class outcomes only at R runtime;
    # catch it at freeze time and point at the multi-class template instead.
    if template_id in {"regression_glm", "diagnostic_roc"}:
        outcome_value = values.get("outcome")
        if has_value(outcome_value):
            levels = _column_levels(dataset_profile or {}, str(outcome_value))
            column = _profile_column(dataset_profile or {}, str(outcome_value)) or {}
            numeric_outcome = str(column.get("semantic_type") or column.get("type") or "").lower() in {"numeric", "continuous", "integer", "double", "float"}
            if len(levels) > 2 and (template_id == "diagnostic_roc" or not numeric_outcome):
                issues["outcome"] = (
                    f"结局变量有 {len(levels)} 个取值（{('、'.join(levels[:5]))}"
                    f"{'…' if len(levels) > 5 else ''}）；本模板只支持二分类或连续结局。"
                    "多分类结局请改用「分类与有序回归」模板；若确为连续结局，请核对该列的数据类型。"
                )

    return issues
