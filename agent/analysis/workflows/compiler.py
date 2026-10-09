"""Compile catalog templates and conversational plans into workflow run specs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, Mapping, Sequence

from agent.analysis.models import (
    AnalysisPlan,
    AnalysisTemplate,
    ConversationPlanDraft,
    InputSpec,
    RequiredOutput,
    ResolvedInput,
)
from agent.analysis.format_contracts import accepted_template_formats, dataset_asset_formats
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.analysis.workflows.contracts import (
    ArtifactRequirement,
    DataContract,
    PackageRequirement,
    WorkflowDefinition,
    WorkflowRunSpec,
    WorkflowStep,
)


_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_DEFAULT_TABULAR_FORMATS = ("csv", "tsv", "xlsx", "xls", "sav", "dta", "rds", "parquet")
_ARTIFACT_FORMATS: dict[str, tuple[str, ...]] = {
    "table": ("csv", "parquet", "json"),
    "figure": ("png", "svg", "pdf"),
    "text": ("md", "txt", "json"),
    "model": ("rds", "json"),
}


def _slug(value: str, fallback: str = "analysis") -> str:
    slug = _SLUG_PATTERN.sub("_", str(value or "").casefold()).strip("_")
    return slug or fallback


def _ordered_strings(values: Sequence[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(text for value in values if (text := str(value or "").strip())))


def _artifact(output: RequiredOutput) -> ArtifactRequirement:
    return ArtifactRequirement(
        role=output.role,
        kind=output.kind,
        required=output.required,
        label=output.label,
        tier=output.tier,
        source=output.source,
        rationale=output.rationale,
        conditions=tuple(output.conditions),
        acceptance=tuple(output.acceptance),
        semantic_key=output.semantic_key or output.role,
        legacy_roles=tuple(output.legacy_roles),
        formats=_ARTIFACT_FORMATS[output.kind],
    )


def _artifacts(outputs: Sequence[RequiredOutput]) -> tuple[ArtifactRequirement, ...]:
    return tuple(_artifact(output) for output in outputs)


def _contract_signature(
    family: str,
    scenario: str,
    data_contract: DataContract,
    artifacts: Sequence[ArtifactRequirement],
) -> str:
    payload = {
        "family": family,
        "scenario": scenario,
        "shape": data_contract.shape,
        "formats": data_contract.formats,
        "inputs": [
            {
                "id": item.id,
                "kind": item.kind,
                "requirement": item.requirement,
                "semantic_role": item.semantic_role,
                "accepts": item.accepts,
            }
            for item in data_contract.inputs
        ],
        "outputs": [
            {
                "role": item.role,
                "kind": item.kind,
                "tier": item.tier,
                "required": item.required,
                "legacy_roles": item.legacy_roles,
            }
            for item in artifacts
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _resource_hints(
    resource_class: str,
    data_contract: DataContract,
    category: str,
) -> tuple[str, ...]:
    if resource_class == "extended":
        hints = [
            "execution_class=extended",
            "resource_policy=adaptive_local",
            "cpu_cores=adaptive",
            "memory=adaptive",
            "workspace_disk=adaptive",
            "timeout_seconds=0",
            "checkpoint_intermediate=true",
        ]
    else:
        hints = [
            "execution_class=standard",
            "resource_policy=adaptive_local",
            "cpu_cores=adaptive",
            "memory=adaptive",
            "workspace_disk=adaptive",
            "timeout_seconds=0",
        ]
    if data_contract.shape == "r_object" or data_contract.object_inputs:
        hints.append("preserve_r_objects=true")
    if category == "组学与生物信息":
        hints.extend(("sample_alignment_check=true", "preserve_object_asset_semantics=true"))
    return tuple(hints)


def _workflow_steps(
    *,
    workflow_id: str,
    category: str,
    data_contract: DataContract,
    artifacts: tuple[ArtifactRequirement, ...],
    prerequisites: Sequence[str],
    assumptions: Sequence[str],
    diagnostics: Sequence[str],
    resource_hints: tuple[str, ...],
) -> tuple[WorkflowStep, ...]:
    input_roles = tuple(item.id for item in data_contract.inputs)
    preflight_checks = ["input_shape", "input_format", "critical_inputs"]
    if data_contract.shape == "r_object" or data_contract.object_inputs:
        preflight_checks.extend(("r_object_class", "object_asset_identity"))
    if category == "组学与生物信息":
        preflight_checks.extend(("omics_sample_alignment", "omics_asset_semantics"))

    family_key = workflow_id.removeprefix("scientific.")
    prepared_outputs = ["prepared_analysis_data"]
    if data_contract.asset_inputs:
        prepared_outputs.append("prepared_object_assets")
    artifact_roles = tuple(item.role for item in artifacts)
    analysis_roles = tuple(item.role for item in artifacts if item.tier not in {"diagnostic", "sensitivity"})
    diagnostic_roles = tuple(item.role for item in artifacts if item.tier in {"diagnostic", "sensitivity"})
    return (
        WorkflowStep(
            step_id="preflight",
            phase="preflight",
            implementation=(
                "workflow.preflight.r_object"
                if data_contract.shape == "r_object"
                else "workflow.preflight.tabular"
            ),
            consumes=input_roles,
            produces=("validated_inputs",),
            checks=tuple(preflight_checks),
            resource_hints=resource_hints,
        ),
        WorkflowStep(
            step_id="prepare",
            phase="prepare",
            implementation=f"workflow.prepare.{data_contract.shape}",
            depends_on=("preflight",),
            consumes=("validated_inputs", *input_roles),
            produces=tuple(prepared_outputs),
            checks=_ordered_strings(prerequisites),
            resource_hints=resource_hints,
        ),
        WorkflowStep(
            step_id="analyze",
            phase="analyze",
            implementation=f"workflow.analyze.{family_key}",
            depends_on=("prepare",),
            consumes=tuple(prepared_outputs),
            produces=("analysis_result", *analysis_roles),
            checks=_ordered_strings(assumptions),
            resource_hints=resource_hints,
        ),
        WorkflowStep(
            step_id="diagnose",
            phase="diagnose",
            implementation=f"workflow.diagnose.{family_key}",
            depends_on=("analyze",),
            consumes=("analysis_result",),
            produces=("diagnostic_evidence", *diagnostic_roles),
            checks=_ordered_strings(diagnostics) or ("scientific_diagnostics",),
            resource_hints=resource_hints,
        ),
        WorkflowStep(
            step_id="publish",
            phase="publish",
            implementation="workflow.publish.portfolio",
            depends_on=("diagnose",),
            consumes=("analysis_result", "diagnostic_evidence"),
            produces=artifact_roles,
            checks=("artifact_manifest", "portfolio_acceptance", "provenance_receipt"),
            resource_hints=resource_hints,
        ),
    )


def _input_spec_from_resolved(item: ResolvedInput) -> InputSpec:
    return InputSpec(
        id=item.input_id,
        label=item.label,
        kind=item.kind,
        requirement="critical",
        semantic_role=item.input_id,
        confirmation="never",
        auto_select="none",
        depends_on=item.depends_on,
    )


def _free_form_inputs(
    resolved_inputs: Sequence[ResolvedInput],
    variable_bindings: Mapping[str, str],
) -> tuple[InputSpec, ...]:
    inputs: list[InputSpec] = []
    seen: set[str] = set()
    represented_roles: set[str] = set()
    for item in resolved_inputs:
        if item.input_id not in seen:
            compiled = _input_spec_from_resolved(item)
            inputs.append(compiled)
            seen.add(item.input_id)
            represented_roles.update(
                value.casefold() for value in (compiled.id, compiled.label, compiled.semantic_role) if value
            )
    for role in variable_bindings:
        if str(role).strip().casefold() in represented_roles:
            continue
        input_id = _slug(role, "column")
        suffix = 2
        base = input_id
        while input_id in seen:
            input_id = f"{base}_{suffix}"
            suffix += 1
        inputs.append(
            InputSpec(
                id=input_id,
                label=str(role),
                kind="column",
                requirement="critical",
                semantic_role=str(role),
                confirmation="never",
                auto_select="none",
            )
        )
        seen.add(input_id)
        represented_roles.add(str(role).strip().casefold())
    return tuple(inputs)


def _profile_formats(
    dataset_profile: Mapping[str, Any],
    shape: str,
) -> tuple[str, ...]:
    formats = dataset_asset_formats(dataset_profile)
    if formats:
        return formats
    return ("rds",) if shape == "r_object" else _DEFAULT_TABULAR_FORMATS


def _run_dataset_profile(dataset_profile: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only path-free scientific facts needed by an execution plan."""
    selected: dict[str, Any] = {}
    for key in (
        "dataset_id",
        "format",
        "object_kind",
        "modality",
        "representation",
        "processing_stage",
        "rows",
        "columns",
        "input_shape",
    ):
        value = dataset_profile.get(key)
        if value is not None and not isinstance(value, (dict, list, tuple, set)):
            selected[key] = value
    scientific = dataset_profile.get("scientific")
    if isinstance(scientific, Mapping):
        selected["scientific"] = {
            key: scientific.get(key)
            for key in (
                "modality",
                "representation",
                "processing_stage",
                "axes",
                "assays",
                "capabilities",
                "indexes",
                "sample_metadata",
                "feature_metadata",
            )
            if scientific.get(key) is not None
        }
    assets: list[dict[str, Any]] = []
    raw_assets = dataset_profile.get("assets")
    if isinstance(raw_assets, list):
        for asset in raw_assets:
            if not isinstance(asset, Mapping):
                continue
            assets.append(
                {
                    key: asset.get(key)
                    for key in ("name", "format", "role", "kind", "relative_mount_name")
                    if asset.get(key) is not None
                }
            )
    if assets:
        selected["assets"] = assets
    selected["asset_formats"] = list(dataset_asset_formats(dataset_profile))
    return selected


class ScientificWorkflowCompiler:
    """Pure compiler; no catalog, R-runtime or filesystem mutation occurs here."""

    def __init__(
        self,
        portfolio_compiler: ScientificOutputPortfolioCompiler | None = None,
    ) -> None:
        self._portfolio_compiler = portfolio_compiler or ScientificOutputPortfolioCompiler()

    def definition_for_template(self, template: AnalysisTemplate) -> WorkflowDefinition:
        data_contract = DataContract(
            shape=template.input_shape,
            formats=accepted_template_formats(template),
            inputs=tuple(template.input_specs),
            required_roles=tuple(template.required_roles),
            optional_roles=tuple(template.optional_roles),
            input_alternatives=template.input_alternatives,
        )
        package_requirement = PackageRequirement.from_candidates(tuple(template.packages))
        portfolio = self._portfolio_compiler.compile(
            source="template",
            template_id=template.id,
            intent=template.title,
            method_family=template.family,
            scenario=template.scenario,
            baseline_outputs=template.required_outputs,
            dataset_profile={},
            packages=template.packages,
            packages_verified=False,
            diagnostics=template.diagnostics,
        )
        artifacts = _artifacts(portfolio.outputs)
        workflow_id = f"scientific.{_slug(template.family)}"
        resource_hints = _resource_hints(template.resource_class, data_contract, template.category)
        return WorkflowDefinition(
            workflow_id=workflow_id,
            variant=template.id,
            template_id=template.id,
            category=template.category,
            family=template.family,
            scenario=template.scenario,
            title=template.title,
            data_contract=data_contract,
            package_requirement=package_requirement,
            artifacts=artifacts,
            steps=_workflow_steps(
                workflow_id=workflow_id,
                category=template.category,
                data_contract=data_contract,
                artifacts=artifacts,
                prerequisites=template.prerequisites,
                assumptions=template.assumptions,
                diagnostics=template.diagnostics,
                resource_hints=resource_hints,
            ),
            resource_class=template.resource_class,
            resource_hints=resource_hints,
            contract_signature=_contract_signature(
                template.family,
                template.scenario,
                data_contract,
                artifacts,
            ),
        )

    def run_spec_from_template(
        self,
        definition: WorkflowDefinition,
        *,
        plan: AnalysisPlan | None = None,
        dataset_profile: Mapping[str, Any] | None = None,
    ) -> WorkflowRunSpec:
        profile = dataset_profile or {}
        runtime_definition = definition
        resolved_inputs: dict[str, Any] = {}
        variable_bindings: dict[str, str] = {}
        dataset_id = str(profile.get("dataset_id") or "").strip()
        source_plan_id = ""
        if plan is not None:
            if plan.source != "template":
                raise ValueError("template workflow compilation requires a template AnalysisPlan")
            if plan.template_id and plan.template_id != definition.template_id:
                raise ValueError(
                    f"workflow definition {definition.template_id} does not match plan {plan.template_id}"
                )
            dataset_id = plan.dataset_id
            source_plan_id = plan.plan_id
            resolved_inputs = {key: item.value for key, item in plan.resolved_inputs.items()}
            variable_bindings = dict(plan.variable_bindings)
            packages = PackageRequirement.from_candidates(
                tuple(plan.packages),
                queries=definition.package_requirement.queries,
            )
            artifacts = _artifacts(plan.output_portfolio)
            runtime_definition = self._replace_runtime_contract(
                definition,
                package_requirement=packages,
                artifacts=artifacts,
                prerequisites=plan.prerequisites,
                assumptions=plan.assumptions,
                diagnostics=plan.diagnostics,
            )
        run_id = self._run_id(
            source="template",
            dataset_id=dataset_id,
            workflow_id=runtime_definition.workflow_id,
            variant=runtime_definition.variant,
            source_plan_id=source_plan_id,
        )
        return WorkflowRunSpec(
            run_id=run_id,
            source="template",
            dataset_id=dataset_id,
            definition=runtime_definition,
            resolved_inputs=resolved_inputs,
            variable_bindings=variable_bindings,
            source_plan_id=source_plan_id,
            dataset_profile=_run_dataset_profile(profile),
        )

    def run_spec_from_free_form(
        self,
        request: ConversationPlanDraft | AnalysisPlan,
        *,
        dataset_profile: Mapping[str, Any] | None = None,
        resolved_packages: Sequence[str] = (),
    ) -> WorkflowRunSpec:
        profile = dataset_profile or {}
        if isinstance(request, AnalysisPlan):
            if request.source != "conversation":
                raise ValueError("free-form workflow compilation requires a conversation AnalysisPlan")
            intent = request.intent
            category = request.category
            family = request.method_family
            scenario = request.scenario
            resolved = tuple(request.resolved_inputs.values())
            variable_bindings = dict(request.variable_bindings)
            outputs = request.output_portfolio
            packages = tuple(request.packages)
            package_queries: tuple[str, ...] = ()
            prerequisites = request.prerequisites
            assumptions = request.assumptions
            diagnostics = request.diagnostics
            resource_class = request.resource_class
            dataset_id = request.dataset_id
            source_plan_id = request.plan_id
        elif isinstance(request, ConversationPlanDraft):
            intent = request.intent
            category = request.category
            family = request.method_family
            scenario = request.scenario
            resolved = tuple(request.analysis_inputs)
            variable_bindings = dict(request.variable_bindings)
            outputs = request.required_outputs
            packages = tuple(resolved_packages)
            package_queries = tuple(request.package_queries)
            prerequisites = request.prerequisites
            assumptions = request.assumptions
            diagnostics = request.diagnostics
            resource_class = request.resource_class
            dataset_id = str(profile.get("dataset_id") or "").strip()
            source_plan_id = ""
        else:
            raise TypeError("free-form request must be ConversationPlanDraft or conversation AnalysisPlan")
        if not intent.strip() or not family.strip() or not scenario.strip():
            raise ValueError("free-form intent, method family and scenario are required")
        if not outputs:
            raise ValueError("free-form workflow requires at least one output artifact")

        if isinstance(request, ConversationPlanDraft):
            portfolio = self._portfolio_compiler.compile(
                source="conversation",
                intent=intent,
                method_family=family,
                scenario=scenario,
                baseline_outputs=request.required_outputs,
                dataset_profile=profile,
                resolved_inputs={item.input_id: item for item in resolved},
                packages=packages,
                packages_verified=bool(packages),
                diagnostics=diagnostics,
            )
            outputs = portfolio.outputs

        inputs = _free_form_inputs(resolved, variable_bindings)
        requested_shape = str(profile.get("input_shape") or profile.get("shape") or "").strip()
        shape = (
            "r_object"
            if requested_shape == "r_object" or any(item.kind == "object" for item in inputs)
            else "tabular"
        )
        data_contract = DataContract(
            shape=shape,
            formats=_profile_formats(profile, shape),
            inputs=inputs,
        )
        package_requirement = PackageRequirement.from_candidates(
            tuple(packages),
            queries=package_queries,
        )
        artifacts = _artifacts(outputs)
        workflow_id = f"scientific.{_slug(family)}"
        signature = _contract_signature(family, scenario, data_contract, artifacts)
        resource_hints = _resource_hints(resource_class, data_contract, category)
        definition = WorkflowDefinition(
            workflow_id=workflow_id,
            variant=f"free_form_{signature[:12]}",
            template_id="",
            category=category,
            family=family,
            scenario=scenario,
            title=intent,
            data_contract=data_contract,
            package_requirement=package_requirement,
            artifacts=artifacts,
            steps=_workflow_steps(
                workflow_id=workflow_id,
                category=category,
                data_contract=data_contract,
                artifacts=artifacts,
                prerequisites=prerequisites,
                assumptions=assumptions,
                diagnostics=diagnostics,
                resource_hints=resource_hints,
            ),
            resource_class=resource_class,
            resource_hints=resource_hints,
            contract_signature=signature,
        )
        return WorkflowRunSpec(
            run_id=self._run_id(
                source="free_form",
                dataset_id=dataset_id,
                workflow_id=workflow_id,
                variant=definition.variant,
                source_plan_id=source_plan_id,
            ),
            source="free_form",
            dataset_id=dataset_id,
            definition=definition,
            resolved_inputs={item.input_id: item.value for item in resolved},
            variable_bindings=variable_bindings,
            source_plan_id=source_plan_id,
            dataset_profile=_run_dataset_profile(profile),
        )

    @staticmethod
    def _run_id(
        *,
        source: str,
        dataset_id: str,
        workflow_id: str,
        variant: str,
        source_plan_id: str,
    ) -> str:
        value = "|".join((source, dataset_id, workflow_id, variant, source_plan_id))
        return f"wf_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"

    @staticmethod
    def _replace_runtime_contract(
        definition: WorkflowDefinition,
        *,
        package_requirement: PackageRequirement,
        artifacts: tuple[ArtifactRequirement, ...],
        prerequisites: Sequence[str],
        assumptions: Sequence[str],
        diagnostics: Sequence[str],
    ) -> WorkflowDefinition:
        return replace(
            definition,
            package_requirement=package_requirement,
            artifacts=artifacts,
            steps=_workflow_steps(
                workflow_id=definition.workflow_id,
                category=definition.category,
                data_contract=definition.data_contract,
                artifacts=artifacts,
                prerequisites=prerequisites,
                assumptions=assumptions,
                diagnostics=diagnostics,
                resource_hints=definition.resource_hints,
            ),
            contract_signature=_contract_signature(
                definition.family,
                definition.scenario,
                definition.data_contract,
                artifacts,
            ),
        )
