"""Small immutable contracts for scientific workflow compilation and execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, Sequence

from agent.analysis.models import AnalysisPlan, ConversationPlanDraft, InputSpec


WorkflowPhase = Literal["preflight", "prepare", "analyze", "diagnose", "publish"]
WorkflowSource = Literal["template", "free_form"]


@dataclass(frozen=True)
class DataContract:
    """Typed inputs and accepted storage formats for one workflow variant.

    ``inputs`` deliberately retains the catalog's :class:`InputSpec` values.
    In particular, an R object or matrix asset is never flattened into a
    column binding merely to make execution planning easier.
    """

    shape: Literal["tabular", "r_object"]
    formats: tuple[str, ...]
    inputs: tuple[InputSpec, ...]
    required_roles: tuple[str, ...] = ()
    optional_roles: tuple[str, ...] = ()
    input_alternatives: tuple[tuple[str, ...], ...] = ()

    @property
    def critical_inputs(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.inputs if item.requirement == "critical")

    @property
    def object_inputs(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.inputs if item.kind == "object")

    @property
    def asset_inputs(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.inputs if item.kind in {"dataset", "object"})

    @property
    def column_inputs(self) -> tuple[str, ...]:
        return tuple(item.id for item in self.inputs if item.kind in {"column", "columns"})


@dataclass(frozen=True)
class PackageRequirement:
    """Ordered R-package choices with an explicit primary and fallbacks."""

    primary: str = ""
    alternatives: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()
    queries: tuple[str, ...] = ()

    @classmethod
    def from_candidates(
        cls,
        candidates: Sequence[str],
        *,
        queries: Sequence[str] = (),
    ) -> "PackageRequirement":
        ordered = tuple(
            dict.fromkeys(package for value in candidates if (package := str(value or "").strip()))
        )
        return cls(
            primary=ordered[0] if ordered else "",
            alternatives=ordered[1:],
            candidates=ordered,
            queries=tuple(dict.fromkeys(str(item).strip() for item in queries if str(item).strip())),
        )


@dataclass(frozen=True)
class ArtifactRequirement:
    """One addressable artifact in the scientific output portfolio."""

    role: str
    kind: Literal["table", "figure", "text", "model"]
    required: bool = True
    label: str = ""
    tier: Literal["core", "recommended", "conditional", "diagnostic", "sensitivity"] = "core"
    source: Literal["template", "conversation", "capability", "system"] = "template"
    rationale: str = ""
    conditions: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    semantic_key: str = ""
    legacy_roles: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowStep:
    """A dispatchable workflow stage backed by a shared implementation key."""

    step_id: str
    phase: WorkflowPhase
    implementation: str
    depends_on: tuple[str, ...] = ()
    consumes: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    resource_hints: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowDefinition:
    """Explicit executable definition for one stable catalog variant."""

    workflow_id: str
    variant: str
    template_id: str
    category: str
    family: str
    scenario: str
    title: str
    data_contract: DataContract
    package_requirement: PackageRequirement
    artifacts: tuple[ArtifactRequirement, ...]
    steps: tuple[WorkflowStep, ...]
    resource_class: Literal["standard", "extended"]
    resource_hints: tuple[str, ...]
    contract_signature: str

    @property
    def output_portfolio(self) -> tuple[ArtifactRequirement, ...]:
        return self.artifacts


@dataclass(frozen=True)
class WorkflowRunSpec:
    """Unified runnable contract for template and free-form entry paths."""

    run_id: str
    source: WorkflowSource
    dataset_id: str
    definition: WorkflowDefinition
    resolved_inputs: Mapping[str, Any] = field(default_factory=dict)
    variable_bindings: Mapping[str, str] = field(default_factory=dict)
    source_plan_id: str = ""
    dataset_profile: Mapping[str, Any] = field(default_factory=dict)

    @property
    def workflow_id(self) -> str:
        return self.definition.workflow_id

    @property
    def variant(self) -> str:
        return self.definition.variant

    @property
    def template_id(self) -> str:
        return self.definition.template_id

    @property
    def steps(self) -> tuple[WorkflowStep, ...]:
        return self.definition.steps

    @property
    def data_contract(self) -> DataContract:
        return self.definition.data_contract

    @property
    def package_requirement(self) -> PackageRequirement:
        return self.definition.package_requirement

    @property
    def packages(self) -> tuple[str, ...]:
        return self.definition.package_requirement.candidates

    @property
    def artifacts(self) -> tuple[ArtifactRequirement, ...]:
        return self.definition.artifacts

    @property
    def output_portfolio(self) -> tuple[ArtifactRequirement, ...]:
        return self.definition.artifacts

    @property
    def resource_hints(self) -> tuple[str, ...]:
        return self.definition.resource_hints


class WorkflowRegistry(Protocol):
    """Minimal lookup and compilation interface used by callers."""

    def definitions(self) -> tuple[WorkflowDefinition, ...]: ...

    def get(self, template_id: str) -> WorkflowDefinition: ...

    def compile_template(
        self,
        template_id: str,
        *,
        plan: AnalysisPlan | None = None,
        dataset_profile: Mapping[str, Any] | None = None,
    ) -> WorkflowRunSpec: ...

    def compile_free_form(
        self,
        request: ConversationPlanDraft | AnalysisPlan,
        *,
        dataset_profile: Mapping[str, Any] | None = None,
        resolved_packages: Sequence[str] = (),
    ) -> WorkflowRunSpec: ...
