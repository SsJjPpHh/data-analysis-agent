"""Scientific workflow definitions shared by templates and free-form requests."""

from agent.analysis.workflows.compiler import ScientificWorkflowCompiler
from agent.analysis.workflows.contracts import (
    ArtifactRequirement,
    DataContract,
    PackageRequirement,
    WorkflowDefinition,
    WorkflowPhase,
    WorkflowRegistry,
    WorkflowRunSpec,
    WorkflowSource,
    WorkflowStep,
)
from agent.analysis.workflows.executor import (
    ScientificWorkflowExecutor,
    WorkflowContractError,
    WorkflowExecutionResult,
    WorkflowStepExecutor,
)
from agent.analysis.workflows.lifecycle import (
    ScientificWorkflowLifecycle,
    WorkflowPhaseStatus,
)
from agent.analysis.workflows.registry import (
    CatalogWorkflowRegistry,
    default_workflow_registry,
)
from agent.analysis.workflows.projection import (
    workflow_run_spec_projection,
    workflow_system_section,
)
from agent.analysis.workflows.validation import (
    WorkflowPreflightReport,
    WorkflowValidationIssue,
    WorkflowValidationReport,
    validate_workflow_run_spec,
    validate_workflow_definitions,
)

__all__ = [
    "ArtifactRequirement",
    "CatalogWorkflowRegistry",
    "DataContract",
    "PackageRequirement",
    "ScientificWorkflowCompiler",
    "ScientificWorkflowExecutor",
    "ScientificWorkflowLifecycle",
    "WorkflowContractError",
    "WorkflowDefinition",
    "WorkflowExecutionResult",
    "WorkflowPhase",
    "WorkflowRegistry",
    "WorkflowRunSpec",
    "WorkflowSource",
    "WorkflowStep",
    "WorkflowStepExecutor",
    "WorkflowPhaseStatus",
    "WorkflowPreflightReport",
    "WorkflowValidationIssue",
    "WorkflowValidationReport",
    "default_workflow_registry",
    "validate_workflow_run_spec",
    "validate_workflow_definitions",
    "workflow_run_spec_projection",
    "workflow_system_section",
]
