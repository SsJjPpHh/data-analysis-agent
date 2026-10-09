"""Versioned analysis-template catalog and trusted plan compiler."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent.analysis.models import (
    AnalysisPlan,
    AnalysisTemplate,
    CatalogSnapshot,
    InputSpec,
    RequiredOutput,
)
from infrastructure.paths import resource_path


DEFAULT_CATALOG_PATH = resource_path("agent", "templates", "analysis_catalog.json")
_VALID_TEMPLATE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _required_output(value: object) -> RequiredOutput:
    if not isinstance(value, dict):
        raise ValueError("catalog output entries must be objects")
    role = str(value.get("role") or "").strip()
    kind = str(value.get("kind") or "").strip()
    if not _VALID_TEMPLATE_ID.fullmatch(role):
        raise ValueError(f"invalid output role: {role!r}")
    if kind not in {"table", "figure", "text", "model"}:
        raise ValueError(f"invalid output kind for {role}: {kind!r}")
    tier = str(value.get("tier") or "core").strip()
    if tier not in {"core", "recommended", "conditional", "diagnostic", "sensitivity"}:
        raise ValueError(f"invalid output tier for {role}: {tier!r}")
    conditions = value.get("conditions")
    if conditions is not None and (
        not isinstance(conditions, (list, tuple)) or
        not all(isinstance(condition, str) and condition.strip() for condition in conditions)
    ):
        raise ValueError(f"output conditions for {role} must be a list of explicit condition strings")
    return RequiredOutput(
        role=role,
        kind=kind,  # type: ignore[arg-type]
        required=bool(value.get("required", True)),
        label=str(value.get("label") or "").strip(),
        tier=tier,  # type: ignore[arg-type]
        source="template",
        rationale=str(value.get("rationale") or "").strip(),
        conditions=_string_tuple(value.get("conditions")),
        acceptance=_string_tuple(value.get("acceptance")),
        semantic_key=str(value.get("semantic_key") or role).strip(),
        legacy_roles=_string_tuple(value.get("legacy_roles")),
    )


def _input_spec(value: object) -> InputSpec:
    if not isinstance(value, dict):
        raise ValueError("catalog input entries must be objects")
    input_id = str(value.get("id") or "").strip()
    label = str(value.get("label") or "").strip()
    kind = str(value.get("kind") or "").strip()
    requirement = str(value.get("requirement") or "").strip()
    confirmation = str(value.get("confirmation") or "on_ambiguity").strip()
    auto_select = str(value.get("auto_select") or "none").strip()
    if not _VALID_TEMPLATE_ID.fullmatch(input_id):
        raise ValueError(f"invalid input id: {input_id!r}")
    if not label:
        raise ValueError(f"catalog input {input_id} must have a label")
    if kind not in {"dataset", "column", "columns", "level", "parameter", "object", "specification"}:
        raise ValueError(f"invalid input kind for {input_id}: {kind!r}")
    if requirement not in {"critical", "conditional", "optional"}:
        raise ValueError(f"invalid input requirement for {input_id}: {requirement!r}")
    if confirmation not in {"never", "on_ambiguity", "always"}:
        raise ValueError(f"invalid confirmation policy for {input_id}: {confirmation!r}")
    if auto_select not in {"none", "dataset", "best_match", "all_eligible"}:
        raise ValueError(f"invalid auto-select policy for {input_id}: {auto_select!r}")
    return InputSpec(
        id=input_id,
        label=label,
        kind=kind,  # type: ignore[arg-type]
        requirement=requirement,  # type: ignore[arg-type]
        semantic_role=str(value.get("semantic_role") or input_id).strip() or input_id,
        accepts=_string_tuple(value.get("accepts")),
        confirmation=confirmation,  # type: ignore[arg-type]
        auto_select=auto_select,  # type: ignore[arg-type]
        default=value.get("default"),
        has_default="default" in value,
        description=str(value.get("description") or "").strip(),
        depends_on=str(value.get("depends_on") or "").strip(),
        choices=_string_tuple(value.get("choices")),
        required_when=_string_tuple(value.get("required_when")),
        min_items=max(1, int(value.get("min_items", 1))),
        legacy_labels=_string_tuple(value.get("legacy_labels")),
    )


def _analysis_template(value: object) -> AnalysisTemplate:
    if not isinstance(value, dict):
        raise ValueError("catalog templates must be objects")
    template_id = str(value.get("id") or "").strip()
    if not _VALID_TEMPLATE_ID.fullmatch(template_id):
        raise ValueError(f"invalid template id: {template_id!r}")
    input_contract = value.get("input_contract")
    method_contract = value.get("method_contract")
    output_contract = value.get("output_contract")
    if not all(isinstance(item, dict) for item in (input_contract, method_contract, output_contract)):
        raise ValueError(f"template {template_id} is missing a contract object")
    input_contract = dict(input_contract)
    method_contract = dict(method_contract)
    output_contract = dict(output_contract)
    outputs = output_contract.get("required")
    if not isinstance(outputs, list) or not outputs:
        raise ValueError(f"template {template_id} must declare at least one required output")
    shape = str(input_contract.get("shape") or "")
    resource_class = str(value.get("resource_class") or "")
    visibility = str(value.get("visibility") or "")
    if shape not in {"tabular", "r_object"}:
        raise ValueError(f"template {template_id} has an invalid input shape")
    if resource_class not in {"standard", "extended"}:
        raise ValueError(f"template {template_id} has an invalid resource class")
    if visibility not in {"user", "internal"}:
        raise ValueError(f"template {template_id} has invalid visibility")
    inputs = input_contract.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise ValueError(f"template {template_id} must declare typed inputs")
    input_specs = tuple(_input_spec(item) for item in inputs)
    input_ids = [item.id for item in input_specs]
    if len(input_ids) != len(set(input_ids)):
        raise ValueError(f"template {template_id} input IDs must be unique")
    alternatives = tuple(_string_tuple(group) for group in input_contract.get("at_least_one", []))
    diagnostic_conditions = {
        str(name): _string_tuple(conditions)
        for name, conditions in method_contract.get("diagnostic_conditions", {}).items()
    }
    package_branches = {
        str(condition): _string_tuple(packages)
        for condition, packages in method_contract.get("package_branches", {}).items()
    }
    package_policy = str(method_contract.get("package_policy") or "candidate")
    if package_policy not in {"candidate", "required"}:
        raise ValueError(f"template {template_id} has an invalid package policy")
    branch_inputs: dict[str, set[str]] = {}
    for condition, packages in package_branches.items():
        key, separator, expected = condition.removeprefix("input:").partition("=")
        spec = next((item for item in input_specs if item.id == key), None)
        if (
            not condition.startswith("input:")
            or spec is None or (separator and expected not in spec.choices)
            or not packages or set(packages) - set(method_contract.get("packages", []))
        ):
            raise ValueError(f"template {template_id} has invalid package branch: {condition}")
        if separator:
            branch_inputs.setdefault(key, set()).add(expected)
    for key, choices in branch_inputs.items():
        if choices != set(next(item.choices for item in input_specs if item.id == key)):
            raise ValueError(f"template {template_id} has incomplete package branches for {key}")
    for group in alternatives:
        if not group or set(group) - set(input_ids):
            raise ValueError(f"template {template_id} has invalid alternative inputs")
    if set(diagnostic_conditions) - set(method_contract.get("diagnostics", [])):
        raise ValueError(f"template {template_id} has conditions for unknown diagnostics")
    condition_sets = [*diagnostic_conditions.values()]
    condition_sets.extend(spec.required_when for spec in input_specs)
    condition_sets.extend(_string_tuple(output.get("conditions")) for output in outputs)
    for conditions in condition_sets:
        for condition in conditions:
            if condition.startswith("input:"):
                key, separator, expected = condition[6:].partition("=")
                spec = next((item for item in input_specs if item.id == key), None)
                if spec is None or (separator and spec.choices and expected not in spec.choices):
                    raise ValueError(f"template {template_id} has invalid condition: {condition}")
            elif condition not in {"group", "time", "event", "numeric", "categorical", "missing"}:
                raise ValueError(f"template {template_id} has unknown condition: {condition}")
    for spec in input_specs:
        if spec.choices and spec.has_default and spec.default not in spec.choices:
            raise ValueError(f"template {template_id} has invalid default for {spec.id}")
    return AnalysisTemplate(
        id=template_id,
        category=str(value.get("category") or "").strip(),
        family=str(value.get("family") or "").strip(),
        scenario=str(value.get("scenario") or "").strip(),
        title=str(value.get("title") or "").strip(),
        aliases=_string_tuple(value.get("aliases")),
        search_terms=_string_tuple(value.get("search_terms")),
        input_shape=shape,  # type: ignore[arg-type]
        formats=_string_tuple(input_contract.get("formats")),
        required_roles=_string_tuple(input_contract.get("required_roles")),
        optional_roles=_string_tuple(input_contract.get("optional_roles")),
        packages=_string_tuple(method_contract.get("packages")),
        prerequisites=_string_tuple(method_contract.get("prerequisites")),
        assumptions=_string_tuple(method_contract.get("assumptions")),
        diagnostics=_string_tuple(method_contract.get("diagnostics")),
        required_outputs=tuple(_required_output(item) for item in outputs),
        resource_class=resource_class,  # type: ignore[arg-type]
        visibility=visibility,  # type: ignore[arg-type]
        prompt=str(value.get("prompt") or "").strip(),
        input_specs=input_specs,
        input_alternatives=alternatives,
        diagnostic_conditions=diagnostic_conditions,
        package_branches=package_branches,
        package_policy=package_policy,
    )


@lru_cache(maxsize=8)
def _load_catalog(path_text: str) -> CatalogSnapshot:
    path = Path(path_text)
    try:
        catalog_text = path.read_text(encoding="utf-8")
        payload = json.loads(catalog_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read analysis catalog: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("analysis catalog must be an object")
    schema_version = str(payload.get("schema_version") or "")
    if not schema_version.startswith("4."):
        raise ValueError("analysis catalog schema version must be V4")
    categories = _string_tuple(payload.get("categories"))
    templates_value = payload.get("templates")
    aliases_value = payload.get("aliases")
    if not isinstance(templates_value, list) or not isinstance(aliases_value, dict):
        raise ValueError("analysis catalog templates and aliases are required")
    templates = tuple(_analysis_template(item) for item in templates_value)
    ids = [item.id for item in templates]
    if len(ids) != len(set(ids)):
        raise ValueError("analysis catalog template IDs must be unique")
    if {item.category for item in templates if item.visibility == "user"} - set(categories):
        raise ValueError("analysis catalog contains an undeclared category")
    aliases = {str(key): str(value) for key, value in aliases_value.items()}
    id_set = set(ids)
    for alias, target in aliases.items():
        if not _VALID_TEMPLATE_ID.fullmatch(alias) or target not in id_set:
            raise ValueError(f"invalid analysis catalog alias: {alias!r} -> {target!r}")
    return CatalogSnapshot(
        schema_version=schema_version,
        categories=categories,
        templates=templates,
        aliases=aliases,
    )


def _dataset_columns(dataset_profile: dict) -> set[str]:
    columns = dataset_profile.get("columns")
    if not isinstance(columns, list):
        columns = dataset_profile.get("variables")
    result: set[str] = set()
    for item in columns if isinstance(columns, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("column") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            result.add(name)
    return result


class AnalysisCatalog:
    """Load, search and compile the single trusted template catalog."""

    def __init__(self, path: str | Path | None = None):
        self._path = Path(path or DEFAULT_CATALOG_PATH).expanduser().resolve(strict=False)

    def snapshot(self) -> CatalogSnapshot:
        snapshot = _load_catalog(str(self._path))
        return CatalogSnapshot(
            schema_version=snapshot.schema_version,
            categories=tuple(snapshot.categories),
            templates=tuple(snapshot.templates),
            aliases=dict(snapshot.aliases),
        )

    def search(
        self,
        query: str,
        dataset_profile: dict | None = None,
    ) -> tuple[AnalysisTemplate, ...]:
        terms = [term for term in re.split(r"\s+", str(query or "").casefold()) if term]
        dataset_format = str((dataset_profile or {}).get("format") or "").lower().lstrip(".")
        ranked: list[tuple[int, int, AnalysisTemplate]] = []
        for index, template in enumerate(self.snapshot().templates):
            if template.visibility != "user":
                continue
            if dataset_format and template.formats and dataset_format not in template.formats:
                continue
            searchable = " ".join(
                (
                    template.id,
                    template.title,
                    template.category,
                    template.family,
                    template.scenario,
                    *template.aliases,
                    *template.search_terms,
                    *template.packages,
                )
            ).casefold()
            score = sum(4 if term in template.title.casefold() else 1 for term in terms if term in searchable)
            if terms and score == 0:
                continue
            ranked.append((score, -index, template))
        ranked.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in ranked)

    def get(self, template_id: str) -> AnalysisTemplate:
        snapshot = self.snapshot()
        requested = str(template_id or "").strip()
        resolved = snapshot.aliases.get(requested, requested)
        for template in snapshot.templates:
            if template.id == resolved:
                return template
        raise KeyError(f"unknown analysis template: {requested}")

    def compile(
        self,
        template_id: str,
        bindings: dict[str, Any],
        dataset_profile: dict,
    ) -> AnalysisPlan:
        # Import lazily because the preparer depends on the catalog lookup API.
        from agent.analysis.preparation import AnalysisPreparer

        prepared = AnalysisPreparer(self).prepare(template_id, dataset_profile, bindings)
        if prepared.status == "not_applicable":
            raise ValueError(prepared.reason or "Template is not applicable to the active dataset")
        if prepared.status == "needs_input":
            labels = ", ".join(item.label for item in prepared.questions)
            raise ValueError(f"Missing critical analysis inputs: {labels}")
        if prepared.plan is None:
            raise ValueError("Unable to compile a frozen analysis plan")
        return prepared.plan
