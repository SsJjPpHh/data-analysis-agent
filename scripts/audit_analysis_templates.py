#!/usr/bin/env python3
"""Audit the public contracts in analysis catalog V4."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.analysis.catalog import AnalysisCatalog


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

EVIDENCE_SYNTHESIS_TEMPLATES = {
    "pairwise_meta",
    "network_meta",
    "diagnostic_meta",
    "meta_bias_sensitivity",
    "bayesian_meta",
    "safety_meta_analysis",
    "living_review_update",
    "grade_evidence_summary",
    "evidence_meta_regression",
    "evidence_proportion_rate_meta",
}

PREDICTION_DIAGNOSTIC_TEMPLATES = {
    "clinical_cutpoint_maxstat",
    "external_validation_model",
    "nri_idi_reclassification",
    "probability_calibration_ml",
}

LEAKAGE_RELEVANT_TERMS = re.compile(
    r"机器学习|预测|预后模型|ROC|校准|状态空间|地统计|空间插值|forecast|machine.?learning",
    flags=re.IGNORECASE,
)

GENERIC_INPUT_ROLES = {"sample", "variable"}


def main() -> int:
    snapshot = AnalysisCatalog().snapshot()
    user_templates = tuple(item for item in snapshot.templates if item.visibility == "user")
    ids = [item.id for item in user_templates]
    contract_issues: list[str] = []

    if len(ids) != 213:
        contract_issues.append(f"expected exactly 213 user templates, found {len(ids)}")
    if len(ids) != len(set(ids)):
        contract_issues.append("template IDs are not unique")
    if any(template_id.startswith("capability_") for template_id in ids):
        contract_issues.append("generated capability wrappers remain user-visible")
    if {item.category for item in user_templates} != EXPECTED_CATEGORIES:
        contract_issues.append("the eight approved categories are not represented exactly")
    if snapshot.schema_version != "4.0.0":
        contract_issues.append(f"expected schema 4.0.0, found {snapshot.schema_version}")

    required_fields = (
        "family",
        "scenario",
        "title",
        "formats",
        "packages",
        "prerequisites",
        "assumptions",
        "diagnostics",
        "required_outputs",
        "prompt",
    )
    for item in user_templates:
        missing = [name for name in required_fields if not getattr(item, name)]
        if missing:
            contract_issues.append(f"{item.id}: missing {', '.join(missing)}")
        if any(not output.label for output in item.required_outputs):
            contract_issues.append(f"{item.id}: an output label is empty")
        input_ids = [spec.id for spec in item.input_specs]
        input_labels = [spec.label for spec in item.input_specs]
        semantic_roles = [spec.semantic_role for spec in item.input_specs]
        if not input_ids:
            contract_issues.append(f"{item.id}: typed input contract is empty")
        if len(input_ids) != len(set(input_ids)):
            contract_issues.append(f"{item.id}: typed input IDs are not unique")
        if any(
            re.fullmatch(r"input_[0-9]+|role_[0-9a-f]{8,}", input_id)
            for input_id in input_ids
        ):
            contract_issues.append(f"{item.id}: opaque/positional typed input ID remains")
        if generic_ids := sorted(set(input_ids) & GENERIC_INPUT_ROLES):
            contract_issues.append(
                f"{item.id}: scientifically ambiguous typed input ID remains: "
                + ", ".join(generic_ids)
            )
        if any(
            re.fullmatch(r"input_[0-9]+|role_[0-9a-f]{8,}", role)
            for role in semantic_roles
        ):
            contract_issues.append(f"{item.id}: opaque/positional semantic role remains")
        if generic_roles := sorted(set(semantic_roles) & GENERIC_INPUT_ROLES):
            contract_issues.append(
                f"{item.id}: scientifically ambiguous semantic role remains: "
                + ", ".join(generic_roles)
            )
        if len(input_labels) != len(set(input_labels)):
            contract_issues.append(f"{item.id}: typed input labels are not unique")
        if len(semantic_roles) != len(set(semantic_roles)):
            contract_issues.append(f"{item.id}: semantic memory roles are not unique")
        legacy_labels = set(item.required_roles + item.optional_roles)
        declared_labels = set(input_labels) | {label for spec in item.input_specs for label in spec.legacy_labels}
        if missing_labels := sorted(legacy_labels - declared_labels):
            contract_issues.append(f"{item.id}: legacy roles lost: {', '.join(missing_labels)}")
        if item.input_shape == "r_object" and not any(spec.kind == "object" for spec in item.input_specs):
            contract_issues.append(f"{item.id}: R-object template has no object input")
        input_id_set = set(input_ids)
        for spec in item.input_specs:
            if spec.depends_on and spec.depends_on not in input_id_set:
                contract_issues.append(f"{item.id}: {spec.id} depends on missing input {spec.depends_on}")
            if spec.label == "大表路径" and spec.kind != "dataset":
                contract_issues.append(f"{item.id}: dataset path is still modeled as {spec.kind}")
            if re.search(r"效应大小|检验水准|把握度|分组比例", spec.label) and spec.kind != "parameter":
                contract_issues.append(f"{item.id}: parameter {spec.label} is modeled as {spec.kind}")
            if re.search(r"矩阵|对象$", spec.label) and spec.kind == "column":
                contract_issues.append(f"{item.id}: object/set {spec.label} is modeled as one column")
            if (
                item.input_shape == "tabular"
                and spec.kind == "object"
                and "numeric_matrix" not in spec.accepts
            ):
                contract_issues.append(
                    f"{item.id}: tabular object {spec.label} lacks a matrix structure constraint"
                )

        input_labels = " ".join(input_labels)
        method_text = " ".join(
            (
                item.title,
                item.category,
                item.family,
                item.scenario,
                item.prompt,
                *item.prerequisites,
                *item.assumptions,
                *item.diagnostics,
            )
        )
        if "存在随访时间和事件状态字段" in item.prerequisites:
            if not (re.search(r"时间|随访", input_labels) and re.search(r"事件|结局状态", input_labels)):
                contract_issues.append(
                    f"{item.id}: survival prerequisite exists without time and event inputs"
                )
        if "存在研究层面的效应量、标准误或可换算指标" in item.prerequisites:
            if item.id not in EVIDENCE_SYNTHESIS_TEMPLATES:
                contract_issues.append(
                    f"{item.id}: study-level evidence prerequisite leaked into a non-synthesis template"
                )
        if any("发表偏倚" in value for value in item.diagnostics):
            if item.id not in EVIDENCE_SYNTHESIS_TEMPLATES:
                contract_issues.append(
                    f"{item.id}: publication-bias diagnostics leaked into a non-synthesis template"
                )
        if "训练、验证和预处理过程避免数据泄漏" in item.assumptions:
            if not LEAKAGE_RELEVANT_TERMS.search(method_text):
                contract_issues.append(
                    f"{item.id}: predictive leakage assumption leaked into a non-predictive template"
                )
        if any(value in {"区分度", "校准"} for value in item.diagnostics):
            if item.id not in PREDICTION_DIAGNOSTIC_TEMPLATES:
                contract_issues.append(
                    f"{item.id}: prediction diagnostics leaked into an unrelated template"
                )
        if any("临床净获益或阈值分析" in value for value in item.diagnostics):
            if item.id not in PREDICTION_DIAGNOSTIC_TEMPLATES:
                contract_issues.append(
                    f"{item.id}: clinical-utility diagnostics leaked into an unrelated template"
                )
        if "存在抽样权重，必要时包含分层和聚类字段" in item.prerequisites:
            if item.family != "survey_weighted_analysis":
                contract_issues.append(
                    f"{item.id}: survey-design prerequisite leaked into a non-survey template"
                )

    scientific_invariants = {
        ("survival_km_cox", "随访时间"): "critical",
        ("diagnostic_meta", "TN"): "critical",
    }
    templates_by_id = {item.id: item for item in user_templates}
    for (template_id, input_label), requirement in scientific_invariants.items():
        inputs_by_label = {spec.label: spec for spec in templates_by_id[template_id].input_specs}
        if (
            inputs_by_label.get(input_label) is None
            or inputs_by_label[input_label].requirement != requirement
        ):
            contract_issues.append(f"{template_id}: {input_label} must remain scientifically {requirement}")
    matched_labels = {spec.label for spec in templates_by_id["matched_case_control"].input_specs}
    if "经纬度/区域ID" in matched_labels:
        contract_issues.append("matched_case_control: copied geography role remains")
    ml_critical = {
        spec.label
        for spec in templates_by_id["tabular_ml_workflow"].input_specs
        if spec.requirement == "critical"
    }
    if ml_critical != {"预测结局", "特征变量"}:
        contract_issues.append("tabular_ml_workflow: duplicate outcome/feature requirements remain")
    expected_critical_roles = {
        "mixed_repeated": {"结局变量", "时间变量", "个体/中心ID"},
        "dag_causal": {"暴露", "结局", "已确认 DAG"},
    }
    for template_id, expected in expected_critical_roles.items():
        actual = {
            spec.label for spec in templates_by_id[template_id].input_specs if spec.requirement == "critical"
        }
        if actual != expected:
            contract_issues.append(f"{template_id}: conditional roles became blanket requirements")
    safety_inputs = {spec.label: spec for spec in templates_by_id["safety_meta_analysis"].input_specs}
    for label in ("治疗组样本量", "对照组样本量"):
        if safety_inputs.get(label) is None or safety_inputs[label].kind != "column":
            contract_issues.append(f"safety_meta_analysis: {label} must be a numeric column")
    tuning_inputs = {
        "diagnostic_roc": "阈值候选",
        "calibration_dca": "阈值范围",
        "regularized_models": "惩罚参数",
        "latent_class": "类别数候选",
    }
    for template_id, label in tuning_inputs.items():
        values = {spec.label: spec for spec in templates_by_id[template_id].input_specs}
        if values.get(label) is None or values[label].requirement == "critical":
            contract_issues.append(f"{template_id}: algorithm-selected {label} must not be mandatory")

    noninferiority = templates_by_id["noninferiority_equivalence"]
    noninferiority_text = " ".join(
        (noninferiority.title, noninferiority.scenario, noninferiority.prompt)
    )
    if "进行分析或样本量估计" in noninferiority_text or "设计或结果" in noninferiority_text:
        contract_issues.append(
            "noninferiority_equivalence: result-analysis contract is mixed with trial design"
        )
    survival_ml_outputs = {
        output.label for output in templates_by_id["survival_ml"].required_outputs
    }
    if not any("校准" in label for label in survival_ml_outputs):
        contract_issues.append(
            "survival_ml: prompt/diagnostics require calibration but output contract omits it"
        )

    category_counts = {
        category: sum(item.category == category for item in user_templates)
        for category in snapshot.categories
    }
    report = {
        "schema_version": snapshot.schema_version,
        "user_templates": len(user_templates),
        "internal_templates": len(snapshot.templates) - len(user_templates),
        "category_counts": category_counts,
        "aliases": len(snapshot.aliases),
        "method_families": len({item.family for item in user_templates}),
        "input_shapes": {
            shape: sum(item.input_shape == shape for item in user_templates)
            for shape in ("tabular", "r_object")
        },
        "resource_classes": {
            resource_class: sum(item.resource_class == resource_class for item in user_templates)
            for resource_class in ("standard", "extended")
        },
        "typed_inputs": sum(len(item.input_specs) for item in user_templates),
        "critical_inputs": sum(
            spec.requirement == "critical" for item in user_templates for spec in item.input_specs
        ),
        "contract_issues": contract_issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if contract_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
