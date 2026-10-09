"""Stable analysis-kernel contracts for research workflows."""

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import (
    AnalysisPlan,
    AnalysisTemplate,
    CatalogSnapshot,
    ConversationPlanDraft,
    InputCandidate,
    InputControl,
    InputQuestion,
    InputSpec,
    PreparationResult,
    RequiredOutput,
    ResolvedInput,
)
from agent.analysis.planner import AnalysisPlanner, CapabilityResolver, RuntimeCapabilityResolver
from agent.analysis.model_projection import ModelResultProjector
from agent.analysis.result_validation import ResultAssessment, ResultValidator
from agent.analysis.completion import AnalysisCompletionController, CompletionDecision
from agent.analysis.repair_policy import RRepairDecision, RRepairPolicy
from agent.analysis.artifact_manifest import (
    ManifestValidationEvidence,
    build_artifact_manifest,
    validation_evidence_from_manifest,
)
from agent.analysis.plan_projection import plan_browser_projection
from agent.analysis.run_control import (
    CallbackCancelHandle,
    CancelHandle,
    ProcessCancelHandle,
    ResearchRunCancelled,
    ResearchRunController,
    research_run_controller,
    run_cancellable_process,
)
from agent.analysis.workflows import (
    ArtifactRequirement,
    CatalogWorkflowRegistry,
    DataContract,
    PackageRequirement,
    ScientificWorkflowCompiler,
    ScientificWorkflowExecutor,
    ScientificWorkflowLifecycle,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRunSpec,
    WorkflowStep,
    default_workflow_registry,
    workflow_run_spec_projection,
    workflow_system_section,
)

__all__ = [
    "AnalysisCatalog",
    "AnalysisPlan",
    "AnalysisTemplate",
    "CatalogSnapshot",
    "ConversationPlanDraft",
    "InputCandidate",
    "InputControl",
    "InputQuestion",
    "InputSpec",
    "PreparationResult",
    "RequiredOutput",
    "ResolvedInput",
    "AnalysisPlanner",
    "CapabilityResolver",
    "RuntimeCapabilityResolver",
    "ModelResultProjector",
    "ResultAssessment",
    "ResultValidator",
    "AnalysisCompletionController",
    "CompletionDecision",
    "RRepairDecision",
    "RRepairPolicy",
    "build_artifact_manifest",
    "ManifestValidationEvidence",
    "validation_evidence_from_manifest",
    "plan_browser_projection",
    "CallbackCancelHandle",
    "CancelHandle",
    "ProcessCancelHandle",
    "ResearchRunCancelled",
    "ResearchRunController",
    "research_run_controller",
    "run_cancellable_process",
    "ArtifactRequirement",
    "CatalogWorkflowRegistry",
    "DataContract",
    "PackageRequirement",
    "ScientificWorkflowCompiler",
    "ScientificWorkflowExecutor",
    "ScientificWorkflowLifecycle",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "WorkflowRunSpec",
    "WorkflowStep",
    "default_workflow_registry",
    "workflow_run_spec_projection",
    "workflow_system_section",
]
