"""Compile template and conversational requests into one execution contract."""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.input_validation import normalize_parameter_value
from agent.analysis.models import AnalysisPlan, ConversationPlanDraft, ResolvedInput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler


class CapabilityResolver(Protocol):
    """Resolve analysis intent to packages available in the active R runtime."""

    def resolve(self, queries: tuple[str, ...], method_family: str) -> tuple[str, ...]: ...


def _column_names(dataset_profile: dict[str, Any]) -> set[str]:
    values = dataset_profile.get("columns")
    if not isinstance(values, list):
        values = dataset_profile.get("variables")
    names: set[str] = set()
    for item in values if isinstance(values, list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("column") or "").strip()
        else:
            name = str(item or "").strip()
        if name:
            names.add(name)
    return names


def _column_profiles(dataset_profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = dataset_profile.get("columns")
    if not isinstance(values, list):
        values = dataset_profile.get("variables")
    return {
        str(item.get("name") or item.get("column") or "").strip(): item
        for item in (values if isinstance(values, list) else [])
        if isinstance(item, dict) and str(item.get("name") or item.get("column") or "").strip()
    }


def _role_family(value: str) -> str:
    text = str(value or "")
    patterns = (
        ("group", r"group|arm|分组|组别"),
        ("strata", r"strata|stratum|分层"),
        ("time", r"time|duration|followup|随访|生存时间|时间"),
        ("event", r"event|status|事件|状态"),
        ("outcome", r"outcome|response|endpoint|结局|终点|因变量"),
        ("exposure", r"exposure|predictor|treatment|暴露|自变量|处理"),
        ("identifier", r"identifier|(?:^|_)id(?:$|_)|编号|标识"),
    )
    return next((family for family, pattern in patterns if re.search(pattern, text, re.I)), "")


def _safe_input_id(value: str, fallback: str = "column") -> str:
    """Return an accessor-safe ID while retaining recognizable ASCII roles."""

    text = str(value or "").strip().casefold()
    slug = re.sub(r"[^a-z0-9_]+", "_", text).strip("_")
    if slug and not slug[0].isalpha():
        slug = f"{fallback}_{slug}"
    candidate = slug or _role_family(text) or fallback
    candidate = re.sub(r"[^a-z0-9_]+", "_", candidate).strip("_")
    if not candidate or not candidate[0].isalpha():
        candidate = fallback
    return candidate[:80].rstrip("_") or fallback


def _observed_levels(dataset_profile: dict[str, Any], column: str) -> tuple[str, ...]:
    local_semantics = dataset_profile.get("local_semantics")
    local_semantics = local_semantics if isinstance(local_semantics, dict) else {}
    levels = local_semantics.get("levels")
    levels = levels if isinstance(levels, dict) else {}
    values = levels.get(column)
    if not isinstance(values, list):
        return ()
    return tuple(dict.fromkeys(str(value) for value in values if str(value) != ""))


def _input_role_family(item: ResolvedInput) -> str:
    return _role_family(" ".join((item.input_id, item.label)))


_WHOLE_DATASET_FAMILY_RE = re.compile(
    r"data_(?:import|clean|quality|profil)|exploratory_data|"
    r"unsupervised|clustering|ordination|dimensionality|principal_components?",
    re.IGNORECASE,
)
_WHOLE_DATASET_INTENT_RE = re.compile(
    r"whole.?dataset|entire.?dataset|all.?variables|data.?quality|data.?clean|"
    r"data.?profil|dataset.?overview|exploratory.?data|principal.?component|"
    r"clustering|ordination|dimension.?reduction|matrix|analysis.?object|corpus|"
    r"topic.?model|network.?analysis|image.?set|sequence.?data|"
    r"全数据|整个数据|整份数据|全部变量|数据质量|数据清洗|数据体检|数据概览|"
    r"探索性数据|主成分|聚类|排序|降维|矩阵|分析对象|语料|主题模型|网络分析|"
    r"影像数据|图像数据|序列数据",
    re.IGNORECASE,
)
_VARIABLE_RELATION_INTENT_RE = re.compile(
    r"regress|association|compare|comparison|effect|outcome|endpoint|exposure|"
    r"predictor|response|differential|survival|hazard|diagnostic|classification|"
    r"treatment|group|covariate|mediat|moderator|"
    r"回归|线性模型|广义线性模型|逻辑模型|关联|比较|效应|结局|终点|暴露|自变量|因变量|差异|生存|诊断|分类|"
    r"治疗|分组|协变量|中介|调节",
    re.IGNORECASE,
)


def _is_whole_dataset_task(draft: ConversationPlanDraft) -> bool:
    """Return whether object-only binding is scientifically sufficient.

    Dataset/object binding is an identity contract, not a substitute for
    freezing outcome, exposure, group, time, event, feature or parameter roles.
    Only genuine whole-dataset profiling/exploration may omit those roles.
    """

    family = str(draft.method_family or "")
    text = " ".join((draft.intent, draft.scenario, family))
    if _VARIABLE_RELATION_INTENT_RE.search(text):
        return False
    return bool(
        _WHOLE_DATASET_FAMILY_RE.search(family)
        or _WHOLE_DATASET_INTENT_RE.search(text)
    )


def _same_scientific_role(role: str, item: ResolvedInput) -> bool:
    normalized = str(role or "").strip().casefold()
    if normalized in {item.input_id.casefold(), item.label.casefold()}:
        return True
    family = _role_family(role)
    return bool(family and family == _input_role_family(item))


def _validate_role_semantics(role: str, column: str, profile: dict[str, Any]) -> None:
    family = _role_family(role)
    if not family or not profile:
        return
    semantic_type = str(profile.get("semantic_type") or "").casefold()
    try:
        unique_count = int(float(profile.get("unique_count")))
    except (TypeError, ValueError):
        unique_count = None
    if family in {"group", "strata", "event"}:
        compatible = semantic_type in {"", "categorical"} or (
            semantic_type == "numeric"
            and unique_count is not None
            and 2 <= unique_count <= 20
        )
        if not compatible:
            raise ValueError(
                f"{role} cannot use {column}: the selected role requires a categorical "
                "or low-cardinality coded column"
            )
    if family == "time" and semantic_type not in {"", "numeric", "datetime"}:
        raise ValueError(f"{role} cannot use {column}: time requires a numeric or datetime column")


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    start = text.find("{")
    if start < 0:
        return {}
    try:
        parsed = json.loads(text[start:])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class RuntimeCapabilityResolver:
    """Resolve packages through the same live inventory/help paths exposed to AI.

    The callbacks are deliberately injected: production supplies the existing
    ``get_r_package_inventory`` and ``get_r_package_help`` implementations,
    while tests can use a deterministic adapter without starting R.
    """

    def __init__(
        self,
        inventory_lookup: Callable[..., object],
        help_lookup: Callable[..., object] | None = None,
        *,
        max_packages: int = 16,
    ) -> None:
        self._inventory_lookup = inventory_lookup
        self._help_lookup = help_lookup
        self._max_packages = max(1, min(32, int(max_packages)))

    @staticmethod
    def _append(packages: list[str], value: object) -> None:
        name = str(value or "").strip()
        if name and name not in packages:
            packages.append(name)

    def _inventory_candidates(self, query: str) -> list[str]:
        result = _payload(self._inventory_lookup(query=query, include_all=False, limit=40))
        candidates: list[str] = []
        for item in result.get("recommended_candidate_packages") or []:
            self._append(candidates, item)
        for item in result.get("matched_installed_packages") or []:
            if isinstance(item, dict):
                self._append(candidates, item.get("package") or item.get("Package"))
        for capability in result.get("matched_capabilities") or []:
            if not isinstance(capability, dict):
                continue
            for item in capability.get("packages") or []:
                self._append(candidates, item)
        return candidates

    def _help_candidates(self, query: str) -> list[str]:
        if self._help_lookup is None:
            return []
        result = _payload(
            self._help_lookup(
                package="",
                packages=[],
                query=query,
                limit=20,
                include_docs=False,
                doc_limit=0,
            )
        )
        search = result.get("cross_package_search")
        search = search if isinstance(search, dict) else result
        candidates: list[str] = []
        for item in search.get("recommended_packages") or []:
            if isinstance(item, dict):
                self._append(candidates, item.get("package") or item.get("Package"))
            else:
                self._append(candidates, item)
        for item in search.get("search_results") or []:
            if isinstance(item, dict):
                self._append(candidates, item.get("package") or item.get("Package"))
        return candidates

    def resolve(self, queries: tuple[str, ...], method_family: str) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(text for value in (*queries, method_family) if (text := str(value or "").strip()))
        )
        packages: list[str] = []
        for query in normalized:
            for package in self._inventory_candidates(query):
                self._append(packages, package)
                if len(packages) >= self._max_packages:
                    return tuple(packages)
        if not packages and normalized:
            for package in self._help_candidates(normalized[0]):
                self._append(packages, package)
                if len(packages) >= self._max_packages:
                    break
        return tuple(packages or ["stats"])


class AnalysisPlanner:
    """Trusted compiler used by both catalog templates and free conversation."""

    def __init__(self, catalog: AnalysisCatalog, capability_resolver: CapabilityResolver):
        self._catalog = catalog
        self._capabilities = capability_resolver

    def from_template(
        self,
        template_id: str,
        bindings: dict[str, str],
        dataset_profile: dict[str, Any],
    ) -> AnalysisPlan:
        return self._catalog.compile(template_id, bindings, dataset_profile)

    def from_conversation(
        self,
        draft: ConversationPlanDraft,
        dataset_profile: dict[str, Any],
    ) -> AnalysisPlan:
        dataset_id = str(dataset_profile.get("dataset_id") or "").strip()
        if not dataset_id:
            raise ValueError("dataset profile must include dataset_id")
        if not all(
            (
                draft.intent.strip(),
                draft.category.strip(),
                draft.method_family.strip(),
                draft.scenario.strip(),
            )
        ):
            raise ValueError("analysis intent, category, method family and scenario are required")
        if draft.category not in self._catalog.snapshot().categories:
            raise ValueError(f"Unknown analysis category: {draft.category}")
        columns = _column_names(dataset_profile)
        profiles = _column_profiles(dataset_profile)
        unknown = sorted(set(draft.variable_bindings.values()) - columns)
        if unknown:
            raise ValueError(f"Unknown dataset columns: {', '.join(unknown)}")
        if not draft.required_outputs:
            raise ValueError("at least one required analysis output is required")
        for role, column in draft.variable_bindings.items():
            _validate_role_semantics(role, column, profiles.get(column, {}))
        resolved_inputs: dict[str, ResolvedInput] = {}
        for item in draft.analysis_inputs:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", item.input_id):
                raise ValueError(f"Invalid analysis input ID: {item.input_id!r}")
            if item.input_id in resolved_inputs:
                raise ValueError(f"Duplicate analysis input: {item.input_id}")
            if item.depends_on and item.kind != "level":
                raise ValueError(
                    f"Only level inputs may declare depends_on: {item.input_id}"
                )
            if item.kind == "column" and str(item.value) not in columns:
                raise ValueError(f"Unknown dataset columns: {item.value}")
            if item.kind == "column":
                _validate_role_semantics(
                    " ".join((item.input_id, item.label)),
                    str(item.value),
                    profiles.get(str(item.value), {}),
                )
            if item.kind == "columns":
                values = item.value if isinstance(item.value, (list, tuple)) else []
                unknown_values = sorted(str(value) for value in values if str(value) not in columns)
                if not values or unknown_values:
                    raise ValueError(
                        "Unknown dataset columns: " + ", ".join(unknown_values or [str(item.value)])
                    )
            if item.kind in {"dataset", "object"} and str(item.value) != dataset_id:
                raise ValueError(
                    f"Analysis input {item.input_id} must bind the active dataset"
                )
            if item.kind == "parameter":
                try:
                    normalized_parameter = normalize_parameter_value(
                        input_id=item.input_id,
                        label=item.label,
                        semantic_role=item.input_id,
                        value=item.value,
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid analysis parameter {item.input_id}: {exc}"
                    ) from exc
                item = replace(item, value=normalized_parameter)
            if item.kind == "specification":
                limit = 4_000
                if isinstance(item.value, bool):
                    pass
                elif isinstance(item.value, (int, float)):
                    if not math.isfinite(float(item.value)):
                        raise ValueError(
                            f"Analysis {item.kind} must be finite: {item.input_id}"
                        )
                elif not isinstance(item.value, str) or not item.value.strip() or len(item.value) > limit:
                    raise ValueError(f"Invalid analysis {item.kind}: {item.input_id}")
            if item.kind == "level":
                if not item.depends_on:
                    raise ValueError(
                        f"Level input {item.input_id} must declare depends_on"
                    )
                if not isinstance(item.value, (str, int, float, bool)) or str(item.value) == "":
                    raise ValueError(f"Invalid analysis level: {item.input_id}")
                if isinstance(item.value, float) and not math.isfinite(item.value):
                    raise ValueError(f"Invalid analysis level: {item.input_id}")
                item = replace(item, value=str(item.value))
            resolved_inputs[item.input_id] = item
        for item in tuple(resolved_inputs.values()):
            if item.kind != "level":
                continue
            parent = resolved_inputs.get(item.depends_on)
            if parent is None or parent.kind != "column":
                raise ValueError(
                    f"Level input {item.input_id} depends on an unresolved column input: "
                    f"{item.depends_on}"
                )
            parent_column = str(parent.value)
            observed = _observed_levels(dataset_profile, parent_column)
            if not observed:
                raise ValueError(
                    f"Observed levels are unavailable for {parent_column}; "
                    f"cannot verify {item.input_id}"
                )
            if str(item.value) not in observed:
                raise ValueError(
                    f"Unknown level for {parent_column}: the supplied value is not present "
                    "in local dataset metadata; choose a value locally in the desktop selector"
                )
        scalar_input_owner: dict[str, ResolvedInput] = {}
        for item in resolved_inputs.values():
            if item.kind != "column":
                continue
            value = str(item.value)
            prior = scalar_input_owner.get(value)
            if prior is not None and prior.input_id != item.input_id:
                raise ValueError(
                    "Analysis scalar role overlap: "
                    f"{prior.input_id} and {item.input_id} both bind {value}"
                )
            scalar_input_owner[value] = item
        binding_owner: dict[str, str] = {}
        for role, raw_column in draft.variable_bindings.items():
            column = str(raw_column)
            prior_role = binding_owner.get(column)
            if prior_role is not None:
                prior_family = _role_family(prior_role)
                current_family = _role_family(role)
                if not prior_family or prior_family != current_family:
                    raise ValueError(
                        "Analysis scalar role overlap: "
                        f"{prior_role} and {role} both bind {column}"
                    )
            binding_owner[column] = role
            existing = scalar_input_owner.get(column)
            if existing is not None and not _same_scientific_role(role, existing):
                raise ValueError(
                    "Analysis scalar role overlap: "
                    f"{existing.input_id} and {role} both bind {column}"
                )
        scalar_columns = {
            str(value) for value in draft.variable_bindings.values() if str(value)
        } | {
            str(item.value) for item in draft.analysis_inputs if item.kind == "column"
        }
        for item in draft.analysis_inputs:
            if item.kind != "columns":
                continue
            values = {str(value) for value in item.value}
            overlap = sorted(values & scalar_columns)
            if overlap:
                raise ValueError(
                    f"Analysis input overlap: {item.input_id} repeats protected scalar role columns "
                    + ", ".join(overlap)
                )
        variable_bindings = dict(draft.variable_bindings)
        for item in draft.analysis_inputs:
            if item.kind == "column":
                variable_bindings[item.label] = str(item.value)
        for role, column in draft.variable_bindings.items():
            input_id = _safe_input_id(role)
            canonical = resolved_inputs.get(input_id)
            if canonical is not None and (
                canonical.kind != "column" or str(canonical.value) != column
            ):
                # An exact label on an earlier alias must not conceal a
                # conflicting typed canonical ID later in the request.
                raise ValueError(f"Conflicting analysis role binding: {role}")
            exact = next(
                (
                    item
                    for item in resolved_inputs.values()
                    if role.casefold() in {item.input_id.casefold(), item.label.casefold()}
                ),
                None,
            )
            if exact is not None:
                if exact.kind != "column" or str(exact.value) != column:
                    raise ValueError(f"Conflicting analysis role binding: {role}")
            family = _role_family(role)
            family_matches = [
                item
                for item in resolved_inputs.values()
                if item.kind == "column" and family and _input_role_family(item) == family
            ]
            if family_matches:
                if any(str(item.value) != column for item in family_matches):
                    raise ValueError(f"Conflicting analysis role binding: {role}")
            if canonical is not None:
                continue
            evidence = exact or next(iter(family_matches), None)
            # Workflow contracts include the explicit legacy role as a typed
            # input. Keep original IDs (including level dependency parents),
            # and freeze the same confirmed column under the canonical ID.
            # This copies evidence; it never infers a missing scientific role
            # or upgrades the confidence of a supplied typed binding.
            resolved_inputs[input_id] = (
                replace(evidence, input_id=input_id, label=role)
                if evidence is not None
                else ResolvedInput(
                    input_id=input_id,
                    label=role,
                    kind="column",
                    value=column,
                    provenance="inferred",
                    confidence=1.0,
                )
            )
        if not resolved_inputs:
            raise ValueError(
                "free-form analysis requires at least one frozen typed input; "
                "bind dataset/object for a whole-dataset task"
            )
        if not any(
            item.kind not in {"dataset", "object"}
            for item in resolved_inputs.values()
        ) and not _is_whole_dataset_task(draft):
            raise ValueError(
                "dataset/object-only binding is allowed only for a genuine whole-dataset "
                "profiling or unsupervised exploration task; freeze the requested variable, "
                "level, parameter, or specification roles"
            )
        packages = self._capabilities.resolve(draft.package_queries, draft.method_family)
        compiled_portfolio = ScientificOutputPortfolioCompiler().compile(
            source="conversation",
            intent=draft.intent,
            method_family=draft.method_family,
            scenario=draft.scenario,
            baseline_outputs=draft.required_outputs,
            dataset_profile=dataset_profile,
            resolved_inputs=resolved_inputs,
            packages=packages,
            diagnostics=draft.diagnostics,
        )
        return AnalysisPlan(
            plan_id=uuid.uuid4().hex[:16],
            source="conversation",
            dataset_id=dataset_id,
            intent=draft.intent,
            category=draft.category,
            method_family=draft.method_family,
            scenario=draft.scenario,
            variable_bindings=variable_bindings,
            packages=packages,
            prerequisites=draft.prerequisites,
            assumptions=draft.assumptions,
            diagnostics=draft.diagnostics,
            required_outputs=compiled_portfolio.outputs,
            resource_class=draft.resource_class,
            resolved_inputs=resolved_inputs,
            workspace_generation=str(dataset_profile.get("workspace_generation") or ""),
            profile_revision=str(dataset_profile.get("profile_revision") or ""),
            portfolio_version=compiled_portfolio.version,
            capability_evidence=compiled_portfolio.capability_evidence,
        )
