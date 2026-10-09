# -*- coding: utf-8 -*-
"""Browser projection for the Analysis Catalog V4 API."""

from __future__ import annotations

import re
from typing import Any, Iterable

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.method_policy import template_method_status
from agent.analysis.models import AnalysisTemplate, InputSpec, RequiredOutput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.r_capability_catalog import search_capabilities
from agent.r_package_inventory import load_r_package_inventory
from infrastructure.paths import resource_path
from r_runtime.package_policy import package_setup_requirements


_ROOT = resource_path()
_PUBLICATION_EXPORTS = ("csv", "xlsx", "docx", "png", "pdf", "tiff")
_BASE_R_PACKAGES = {
    "base",
    "compiler",
    "datasets",
    "graphics",
    "grDevices",
    "grid",
    "methods",
    "parallel",
    "splines",
    "stats",
    "stats4",
    "tools",
    "utils",
}


def _normalized_package_set(packages: Iterable[str] | None) -> set[str]:
    return {str(package).strip() for package in (packages or []) if str(package).strip()}


def _workflow_label(template: AnalysisTemplate) -> str:
    text = " ".join((template.id, template.title, template.scenario))
    if template.id == "data_quality" or re.search(r"质量|导入|清洗|缺失|EDA|变量字典", text):
        return "数据准备"
    if re.search(r"样本量|设计|随机|SAP|终点", text, flags=re.IGNORECASE):
        return "研究设计"
    if re.search(r"诊断|校准|验证|敏感性|漂移|ROC", text, flags=re.IGNORECASE):
        return "验证诊断"
    if template.id == "publication_table" or re.search(r"论文|报告|交付|三线表", text):
        return "论文交付"
    if template.resource_class == "extended":
        return "高级专题"
    return "方法建模"


def _output_label(output: RequiredOutput) -> str:
    return output.label or output.role


def _input_spec_payload(spec: InputSpec) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": spec.id,
        "label": spec.label,
        "kind": spec.kind,
        "requirement": spec.requirement,
        "semantic_role": spec.semantic_role,
        "accepts": list(spec.accepts),
        "confirmation": spec.confirmation,
        "auto_select": spec.auto_select,
        "description": spec.description,
        "choices": list(spec.choices),
        "required_when": list(spec.required_when),
        "min_items": spec.min_items,
    }
    if spec.has_default:
        payload["default"] = spec.default
    if spec.depends_on:
        payload["depends_on"] = spec.depends_on
    return payload


def _capability_matches(template: AnalysisTemplate, *, limit: int = 3) -> list[dict[str, Any]]:
    query = " ".join((template.title, template.scenario, *template.packages))
    return [dict(item) for item in search_capabilities(query, limit=limit)]


def _fallback_packages(
    template: AnalysisTemplate,
    matches: list[dict[str, Any]],
    available: set[str],
) -> list[str]:
    selected: list[str] = []
    declared = set(template.packages)
    for match in matches:
        for value in match.get("packages") or []:
            package = str(value).strip()
            if (
                not package
                or package in declared
                or package in selected
                or (available and package not in available)
            ):
                continue
            selected.append(package)
            if len(selected) >= 8:
                return selected
    return selected


def _execution_contract(
    template: AnalysisTemplate,
    *,
    runtime_ok: bool,
    runtime_missing: list[str],
) -> dict[str, Any]:
    if runtime_ok and runtime_missing:
        status, label = "installable", "当前本机 R 缺少候选包，运行时可自动安装"
    elif template.id == "data_quality":
        status, label = "ready", "可直接用于数据理解"
    else:
        status, label = "preparable", "系统将按当前数据自动准备"
    return {
        "status": status,
        "label": label,
        "cost": "high" if template.resource_class == "extended" else "standard",
        "priority": "catalog_v4",
    }


def _compatibility_template(
    template: AnalysisTemplate,
    *,
    live_packages: set[str],
    runtime_ok: bool,
    installable: set[str],
) -> dict[str, Any]:
    requires_numeric_matrix = any(
        spec.kind == "object" and "numeric_matrix" in spec.accepts
        for spec in template.input_specs
    )
    baseline_outputs = ScientificOutputPortfolioCompiler().normalize_baseline(
        source="template",
        method_family=template.family,
        outputs=template.required_outputs,
    )
    available_lookup = (live_packages | _BASE_R_PACKAGES) if runtime_ok else (installable | _BASE_R_PACKAGES)
    runtime_installed = [package for package in template.packages if package in available_lookup]
    runtime_missing = [
        package
        for package in template.packages
        if runtime_ok and package not in available_lookup
    ]
    matches = _capability_matches(template)
    external_setup_requirements = list(package_setup_requirements(template.packages))
    fallback_packages = _fallback_packages(template, matches, available_lookup)
    outputs = [_output_label(output) for output in baseline_outputs if output.required]
    min_tables = sum(output.required and output.kind == "table" for output in baseline_outputs)
    min_figures = sum(output.required and output.kind == "figure" for output in baseline_outputs)
    execution = _execution_contract(
        template,
        runtime_ok=runtime_ok,
        runtime_missing=runtime_missing,
    )
    availability = {
        "status": execution["status"],
        "runtime_inventory_checked": runtime_ok,
        "installed_packages": runtime_installed,
        "missing_packages": runtime_missing,
    }
    package_summary = (
        f"已安装 {len(runtime_installed)} · 缺少 {len(runtime_missing)}（可按需安装）"
        if runtime_ok
        else f"候选 R 包 {len(template.packages)} · 启动运行时后确认"
    )
    workflow = _workflow_label(template)
    return {
        "id": template.id,
        "method_validation": template_method_status(template.id, assumptions=template.assumptions, input_shape=template.input_shape),
        "category": template.category,
        "family": template.family,
        "scenario": template.scenario,
        "title": template.title,
        "subtitle": template.scenario,
        "aliases": list(template.aliases),
        "search_terms": list(template.search_terms),
        "workflow": workflow,
        "input_contract": {
            "shape": template.input_shape,
            "formats": list(template.formats),
            "required_roles": list(template.required_roles),
            "optional_roles": list(template.optional_roles),
            "inputs": [_input_spec_payload(spec) for spec in template.input_specs],
            "at_least_one": [list(group) for group in template.input_alternatives],
        },
        "method_contract": {
            "packages": list(template.packages),
            "prerequisites": list(template.prerequisites),
            "assumptions": list(template.assumptions),
            "diagnostics": list(template.diagnostics),
            "diagnostic_conditions": {name: list(conditions) for name, conditions in template.diagnostic_conditions.items()},
            "package_branches": {condition: list(packages) for condition, packages in template.package_branches.items()},
        },
        "output_contract": {
            "baseline_is_floor": True,
            "portfolio_mode": "template_baseline_plus_r_capability",
            "required": [
                {
                    "role": output.role,
                    "kind": output.kind,
                    "required": output.required,
                    "label": output.label,
                    "tier": output.tier,
                    "source": output.source,
                    "rationale": output.rationale,
                    "conditions": list(output.conditions),
                    "acceptance": list(output.acceptance),
                    "semantic_key": output.semantic_key or output.role,
                    "legacy_roles": list(output.legacy_roles),
                }
                for output in baseline_outputs
            ]
        },
        "resource_class": template.resource_class,
        "visibility": template.visibility,
        "prompt": template.prompt,
        "packages": ", ".join(template.packages),
        "package_names": list(template.packages),
        "runtime_installed_packages": runtime_installed,
        "runtime_missing_packages": runtime_missing,
        "runtime_inventory_ok": runtime_ok,
        "external_setup_requirements": external_setup_requirements,
        "package_summary": package_summary,
        "availability": availability,
        "capability_matches": matches,
        "template_contract": {
            "schema_version": "4.0.0",
            "applicability": {
                "data_types": (
                    ["结构化 R/原生科学对象"]
                    if template.input_shape == "r_object"
                    else ["科学特征矩阵（普通表格需确认样本×特征方向）"]
                    if requires_numeric_matrix
                    else ["单个表格数据"]
                ),
                "study_designs": [],
                "outcome_types": [],
                "variable_roles": [*template.required_roles, *template.optional_roles],
                "required_roles": list(template.required_roles),
                "optional_roles": list(template.optional_roles),
                "prerequisites": list(template.prerequisites),
            },
            "preparation": {
                "mode": "automatic",
                "at_least_one": [list(group) for group in template.input_alternatives],
                "critical_inputs": [
                    spec.label for spec in template.input_specs if spec.requirement == "critical"
                ],
                "conditional_inputs": [
                    spec.label for spec in template.input_specs if spec.requirement == "conditional"
                ],
                "optional_inputs": [
                    spec.label for spec in template.input_specs if spec.requirement == "optional"
                ],
            },
            "methodology": {
                "assumptions": list(template.assumptions),
                "diagnostics": list(template.diagnostics),
                "diagnostic_conditions": {name: list(conditions) for name, conditions in template.diagnostic_conditions.items()},
                "interpretation_requirements": ["报告效应量或不确定性", "说明数据和方法限制"],
            },
            "output_contract": {
                "baseline_is_floor": True,
                "portfolio_mode": "template_baseline_plus_r_capability",
                "min_tables": min_tables,
                "min_figures": min_figures,
                "required_outputs": outputs,
                "required_output_roles": [output.role for output in baseline_outputs],
                "requires_code": True,
                "requires_diagnostics": bool(template.diagnostics),
                "requires_publication_interpretation": True,
                "exports": list(_PUBLICATION_EXPORTS),
            },
            "execution": execution,
            "fallback_packages": fallback_packages,
            "external_setup_requirements": external_setup_requirements,
        },
        "curated_profile": {
            "schema_version": "4.0.0",
            "profile_set": "analysis_catalog_v4",
            "priority": "catalog_v4",
            "label": "目录 V4",
            "scenario": template.scenario,
            "questions": list(template.search_terms[:4]),
        },
        "template_prompt_appendix": (
            f"模板合同：输入将按 V4 类型化合同自动准备；仅在科学含义有歧义时确认；"
            f"最低产物基线={'、'.join(outputs)}；实际执行将按数据、方法与 R 包能力补全科研产物。"
        ),
        "analysis_prompt": template.prompt,
        "source": "analysis-catalog-v4-live-inventory" if runtime_ok else "analysis-catalog-v4",
    }


def enriched_analysis_templates(
    *,
    runtime_packages: Iterable[str] | None = None,
    runtime_ok: bool = False,
    runtime_error: str = "",
    runtime_r_version: str = "",
) -> dict[str, Any]:
    """Project catalog V4 into the browser response shape."""

    snapshot = AnalysisCatalog().snapshot()
    inventory = load_r_package_inventory()
    live_packages = _normalized_package_set(runtime_packages)
    has_live_inventory = bool(runtime_ok and live_packages)
    installable = set(inventory.installable)
    templates = [
        _compatibility_template(
            template,
            live_packages=live_packages,
            runtime_ok=has_live_inventory,
            installable=installable,
        )
        for template in snapshot.templates
        if template.visibility == "user"
    ]
    families = list(
        dict.fromkeys(template.family for template in snapshot.templates if template.visibility == "user")
    )
    workflows = list(dict.fromkeys(str(template["workflow"]) for template in templates))
    return {
        "ok": True,
        "schema_version": snapshot.schema_version,
        "templates": templates,
        "categories": list(snapshot.categories),
        "families": families,
        "workflows": workflows,
        "aliases": dict(snapshot.aliases),
        "inventory": {
            "declared_research_packages": len(inventory.installable),
            "github_fallback_packages": len(inventory.github_fallbacks),
            "live_inventory_ok": has_live_inventory,
            "live_installed_packages": len(live_packages),
            "live_r_version": runtime_r_version,
            "live_error": "" if has_live_inventory else runtime_error,
            "source": str(_ROOT / "agent" / "templates" / "analysis_catalog.json"),
        },
        "capability_groups": len(search_capabilities("", limit=1000)),
        "catalog": {
            "user_templates": len(templates),
            "aliases": len(snapshot.aliases),
            "generated_wrappers": 0,
        },
    }
