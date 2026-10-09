"""Immutable data contracts shared by template and conversational analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


OutputKind = Literal["table", "figure", "text", "model"]
OutputTier = Literal["core", "recommended", "conditional", "diagnostic", "sensitivity"]
OutputSource = Literal["template", "conversation", "capability", "system"]
ResourceClass = Literal["standard", "extended"]
TemplateVisibility = Literal["user", "internal"]
InputKind = Literal[
    "dataset",
    "column",
    "columns",
    "level",
    "parameter",
    "object",
    "specification",
]
InputRequirement = Literal["critical", "conditional", "optional"]
ConfirmationPolicy = Literal["never", "on_ambiguity", "always"]
AutoSelectPolicy = Literal["none", "dataset", "best_match", "all_eligible"]
PreparationStatus = Literal["ready", "needs_input", "not_applicable"]
ResolutionProvenance = Literal["dataset", "user", "memory", "inferred", "default"]


@dataclass(frozen=True)
class RequiredOutput:
    """One portfolio item that an analysis plan is expected to produce.

    The historical name remains part of the persisted-plan compatibility
    contract.  ``tier`` and ``source`` let the same value represent both an
    existing template baseline and outputs added by scientific capability
    planning.
    """

    role: str
    kind: OutputKind
    required: bool = True
    label: str = ""
    tier: OutputTier = "core"
    source: OutputSource = "template"
    rationale: str = ""
    conditions: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    semantic_key: str = ""
    legacy_roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class InputSpec:
    """One typed scientific input declared by a catalog template."""

    id: str
    label: str
    kind: InputKind
    requirement: InputRequirement
    semantic_role: str
    accepts: tuple[str, ...] = ()
    confirmation: ConfirmationPolicy = "on_ambiguity"
    auto_select: AutoSelectPolicy = "best_match"
    default: Any = None
    has_default: bool = False
    description: str = ""
    depends_on: str = ""

    choices: tuple[str, ...] = ()
    required_when: tuple[str, ...] = ()
    min_items: int = 1
    legacy_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedInput:
    """A validated input value and the local evidence used to resolve it."""

    input_id: str
    label: str
    kind: InputKind
    value: Any
    provenance: ResolutionProvenance
    confidence: float = 1.0
    depends_on: str = ""


@dataclass(frozen=True)
class InputCandidate:
    """One locally derived choice shown only when user confirmation is needed."""

    value: Any
    label: str
    confidence: float = 0.0
    reason: str = ""


@dataclass(frozen=True)
class InputQuestion:
    """A minimal unresolved critical input returned by the preparation layer."""

    input_id: str
    label: str
    kind: InputKind
    required: bool
    candidates: tuple[InputCandidate, ...] = ()
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class InputControl:
    """One editable analysis input, including the preparer's current proposal.

    Questions remain the blocking subset.  Controls expose the complete typed
    contract so a researcher can change roles, column sets, levels, and
    parameters before a new plan is frozen.
    """

    input_id: str
    label: str
    kind: InputKind
    required: bool
    value: Any = None
    provenance: str = ""
    candidates: tuple[InputCandidate, ...] = ()
    default: Any = None
    description: str = ""
    editable: bool = True


@dataclass(frozen=True)
class AnalysisTemplate:
    """Normalized user or internal template loaded from catalog V4."""

    id: str
    category: str
    family: str
    scenario: str
    title: str
    aliases: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    input_shape: Literal["tabular", "r_object"] = "tabular"
    formats: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    optional_roles: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    required_outputs: tuple[RequiredOutput, ...] = ()
    resource_class: ResourceClass = "standard"
    visibility: TemplateVisibility = "user"
    prompt: str = ""
    input_specs: tuple[InputSpec, ...] = ()
    input_alternatives: tuple[tuple[str, ...], ...] = ()
    diagnostic_conditions: dict[str, tuple[str, ...]] = field(default_factory=dict)
    package_branches: dict[str, tuple[str, ...]] = field(default_factory=dict)
    package_policy: Literal["candidate", "required"] = "candidate"


@dataclass(frozen=True)
class CatalogSnapshot:
    """A complete, versioned catalog view returned to callers."""

    schema_version: str
    categories: tuple[str, ...]
    templates: tuple[AnalysisTemplate, ...]
    aliases: dict[str, str]


@dataclass(frozen=True)
class ConversationPlanDraft:
    """Structured draft produced from a natural-language request."""

    intent: str
    category: str
    method_family: str
    scenario: str
    variable_bindings: dict[str, str] = field(default_factory=dict)
    analysis_inputs: tuple[ResolvedInput, ...] = ()
    package_queries: tuple[str, ...] = ()
    prerequisites: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    required_outputs: tuple[RequiredOutput, ...] = ()
    resource_class: ResourceClass = "standard"


@dataclass(frozen=True)
class AnalysisPlan:
    """Frozen execution contract shared by templates and AI conversation."""

    plan_id: str
    source: Literal["template", "conversation"]
    dataset_id: str
    intent: str
    category: str
    method_family: str
    scenario: str
    variable_bindings: dict[str, str]
    packages: tuple[str, ...]
    prerequisites: tuple[str, ...]
    assumptions: tuple[str, ...]
    diagnostics: tuple[str, ...]
    required_outputs: tuple[RequiredOutput, ...]
    resource_class: ResourceClass
    resolved_inputs: dict[str, ResolvedInput] = field(default_factory=dict)
    template_id: str = ""
    workspace_generation: str = ""
    profile_revision: str = ""
    portfolio_version: str = "1.0"
    capability_evidence: tuple[str, ...] = ()

    @property
    def output_portfolio(self) -> tuple[RequiredOutput, ...]:
        """Return the complete portfolio while preserving the V4 field name."""

        return self.required_outputs


@dataclass(frozen=True)
class PreparationResult:
    """Outcome of preparing a template or conversational analysis request."""

    status: PreparationStatus
    template_id: str
    resolved_inputs: tuple[ResolvedInput, ...] = ()
    questions: tuple[InputQuestion, ...] = ()
    input_controls: tuple[InputControl, ...] = ()
    reason: str = ""
    warnings: tuple[str, ...] = ()
    plan: AnalysisPlan | None = None
