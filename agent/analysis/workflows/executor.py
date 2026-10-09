"""Deterministic dispatcher for compiled scientific workflow run specs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from types import MappingProxyType
from typing import Any

from agent.analysis.workflows.contracts import WorkflowRunSpec, WorkflowStep
from agent.analysis.workflows.lifecycle import ScientificWorkflowLifecycle


WorkflowStepExecutor = Callable[
    [WorkflowStep, WorkflowRunSpec, Mapping[str, Any]],
    Mapping[str, Any] | None,
]

_PRODUCTION_PHASES = ("preflight", "prepare", "analyze", "diagnose", "publish")
_PRODUCTION_DEPENDENCIES = {
    "preflight": (),
    "prepare": ("preflight",),
    "analyze": ("prepare",),
    "diagnose": ("analyze",),
    "publish": ("diagnose",),
}


class WorkflowContractError(ValueError):
    """Raised before R starts when a compiled workflow cannot be executed safely."""


def _canonical_production_implementation(
    run_spec: WorkflowRunSpec,
    phase: str,
) -> str:
    family_key = run_spec.workflow_id.removeprefix("scientific.")
    implementations = {
        "preflight": (
            "workflow.preflight.r_object"
            if run_spec.data_contract.shape == "r_object"
            else "workflow.preflight.tabular"
        ),
        "prepare": f"workflow.prepare.{run_spec.data_contract.shape}",
        "analyze": f"workflow.analyze.{family_key}",
        "diagnose": f"workflow.diagnose.{family_key}",
        "publish": "workflow.publish.portfolio",
    }
    return implementations[phase]


def _consume_production_evidence(
    step: WorkflowStep,
    _run_spec: WorkflowRunSpec,
    evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Consume already-produced evidence without executing the R analysis twice."""

    if str(evidence.get("phase") or "") != step.phase:
        raise WorkflowContractError(f"workflow evidence does not match phase {step.phase}")
    return {"phase": step.phase, "evidence_consumed": True}


@dataclass(frozen=True)
class WorkflowExecutionResult:
    run_id: str
    completed_steps: tuple[str, ...]
    step_outputs: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)


class ScientificWorkflowExecutor:
    """Run a spec through one injected scientific step boundary.

    The executor owns ordering and dependency checks. R, Python, or another
    remote execution remains an injected boundary selected by each step's
    shared ``implementation`` key, so this module does not duplicate 213
    template-specific scripts or bypass the existing runtime ownership model.
    """

    def __init__(
        self,
        execute_step: WorkflowStepExecutor | Mapping[str, WorkflowStepExecutor] | None = None,
        *,
        run_spec: WorkflowRunSpec | None = None,
        recovered_state: Mapping[str, Any] | None = None,
        expected_checks_by_phase: Mapping[str, Sequence[str]] | None = None,
    ):
        if execute_step is None:
            self._execute_step = None
            self._handlers = {}
        elif callable(execute_step):
            self._execute_step: WorkflowStepExecutor | None = execute_step
            self._handlers: Mapping[str, WorkflowStepExecutor] = {}
        elif isinstance(execute_step, Mapping):
            self._execute_step = None
            self._handlers = dict(execute_step)
        else:
            raise TypeError("execute_step must be a callback or implementation-handler mapping")
        if execute_step is None and run_spec is None:
            raise TypeError("a step callback or production run_spec is required")
        self._run_spec = run_spec
        self._expected_checks_by_phase = {
            str(phase): tuple(str(item) for item in checks)
            for phase, checks in dict(expected_checks_by_phase or {}).items()
        }
        if run_spec is not None and execute_step is None:
            self._handlers = {
                _canonical_production_implementation(run_spec, phase): _consume_production_evidence
                for phase in _PRODUCTION_PHASES
            }
            if not self._expected_checks_by_phase:
                self._expected_checks_by_phase = {
                    step.phase: tuple(step.checks) for step in run_spec.steps
                }
        self._lifecycle = (
            ScientificWorkflowLifecycle(run_spec, recovered_state=recovered_state)
            if run_spec is not None
            else None
        )
        if run_spec is not None:
            self.validate_production_contract()

    @classmethod
    def for_run(
        cls,
        run_spec: WorkflowRunSpec,
        *,
        recovered_state: Mapping[str, Any] | None = None,
        expected_checks_by_phase: Mapping[str, Sequence[str]] | None = None,
    ) -> "ScientificWorkflowExecutor":
        """Create the production ledger bound to one immutable run spec."""

        return cls(
            run_spec=run_spec,
            recovered_state=recovered_state,
            expected_checks_by_phase=expected_checks_by_phase,
        )

    @property
    def run_spec(self) -> WorkflowRunSpec | None:
        return self._run_spec

    @property
    def state(self) -> dict[str, Any]:
        return self._production_lifecycle().state

    def validate_production_contract(self) -> None:
        """Fail closed unless all five compiled steps match registered production handlers."""

        run_spec = self._run_spec
        if run_spec is None:
            raise WorkflowContractError("production workflow requires a compiled run spec")
        steps = tuple(run_spec.steps)
        phases = tuple(step.phase for step in steps)
        if phases != _PRODUCTION_PHASES:
            raise WorkflowContractError("workflow phases are incomplete or out of order")
        if tuple(step.step_id for step in steps) != _PRODUCTION_PHASES:
            raise WorkflowContractError("workflow step IDs must match the canonical phase order")
        for step in steps:
            expected_dependencies = _PRODUCTION_DEPENDENCIES[step.phase]
            if tuple(step.depends_on) != expected_dependencies:
                raise WorkflowContractError(
                    f"workflow phase {step.phase} has unregistered dependencies"
                )
            expected_implementation = _canonical_production_implementation(run_spec, step.phase)
            if step.implementation != expected_implementation:
                raise WorkflowContractError(
                    f"workflow implementation is not registered: {step.implementation}"
                )
            if self._handlers.get(step.implementation) is None:
                raise WorkflowContractError(
                    f"workflow implementation has no production handler: {step.implementation}"
                )
            expected_checks = self._expected_checks_by_phase.get(step.phase)
            if expected_checks is None or tuple(step.checks) != tuple(expected_checks):
                raise WorkflowContractError(
                    f"workflow phase {step.phase} contains unregistered checks"
                )

    def _consume_production_step(
        self,
        phase: str,
        evidence: Mapping[str, Any],
    ) -> None:
        run_spec = self._run_spec
        if run_spec is None:
            raise WorkflowContractError("production workflow requires a compiled run spec")
        step = next((item for item in run_spec.steps if item.phase == phase), None)
        if step is None:
            raise WorkflowContractError(f"workflow phase is not declared: {phase}")
        handler = self._handlers.get(step.implementation)
        if handler is None:
            raise WorkflowContractError(
                f"workflow implementation has no production handler: {step.implementation}"
            )
        value = handler(
            step,
            run_spec,
            MappingProxyType({**dict(evidence), "phase": phase}),
        )
        if value is not None and not isinstance(value, Mapping):
            raise WorkflowContractError(
                f"workflow production handler returned invalid evidence for {phase}"
            )

    def record_preflight(self, report: Any) -> None:
        """Record the real dataset validation report used to gate R."""

        issues = report.get("issues", ()) if isinstance(report, Mapping) else getattr(report, "issues", ())
        ready = report.get("ready", False) if isinstance(report, Mapping) else getattr(report, "ready", False)
        issue_rows = [_json_safe(item) for item in issues if is_dataclass(item) or isinstance(item, Mapping)]
        self._consume_production_step(
            "preflight",
            {"ready": bool(ready), "issues": issue_rows},
        )
        self._production_lifecycle().record_preflight(
            ready=bool(ready),
            issues=[item for item in issue_rows if isinstance(item, Mapping)],
        )

    def record_runtime_result(self, result: Any) -> None:
        """Record one actual local-R result without constraining its code."""

        ok = result.get("ok", False) if isinstance(result, Mapping) else getattr(result, "ok", False)
        execution_stage = (
            result.get("execution_stage", "")
            if isinstance(result, Mapping)
            else getattr(result, "execution_stage", "")
        )
        stage = str(execution_stage or "").strip().casefold()
        evidence = {"ok": bool(ok), "execution_stage": stage or "unknown"}
        if stage == "prepare":
            self._consume_production_step("prepare", evidence)
        elif stage in {"analyze", "complete"} or bool(ok):
            self._consume_production_step("prepare", evidence)
            self._consume_production_step("analyze", evidence)
        elif stage != "preflight":
            self._consume_production_step("prepare", evidence)
        self._production_lifecycle().mark_runtime_result(
            ok=bool(ok),
            execution_stage=str(execution_stage or ""),
        )

    def apply_assessment(self, assessment: Any) -> None:
        """Advance diagnose/publish only from the cumulative validator result."""

        payload = _json_safe(assessment)
        if not isinstance(payload, dict):
            raise TypeError("assessment must be a dataclass or mapping")
        missing_roles = [str(item) for item in payload.get("missing_roles") or []]
        invalid_roles = [str(item) for item in payload.get("invalid_roles") or []]
        if not payload.get("outstanding_roles"):
            payload["outstanding_roles"] = sorted(set((*missing_roles, *invalid_roles)))
        lifecycle = self._production_lifecycle()
        if lifecycle.state["phase_status"].get("analyze") == "completed":
            self._consume_production_step("diagnose", {"assessment": payload})
            if not payload.get("missing_diagnostics"):
                self._consume_production_step("publish", {"assessment": payload})
        lifecycle.apply_assessment(
            diagnostics_complete=not bool(payload.get("missing_diagnostics")),
            portfolio_complete=bool(payload.get("complete")),
            assessment=payload,
        )

    @staticmethod
    def required_implementations(run_spec: WorkflowRunSpec) -> tuple[str, ...]:
        return tuple(dict.fromkeys(step.implementation for step in run_spec.steps))

    def execute(
        self,
        run_spec: WorkflowRunSpec,
        *,
        initial_state: Mapping[str, Any] | None = None,
    ) -> WorkflowExecutionResult:
        state: dict[str, Any] = dict(initial_state or {})
        outputs: dict[str, Mapping[str, Any]] = {}
        completed: list[str] = []
        seen: set[str] = set()
        for step in run_spec.steps:
            if step.step_id in seen:
                raise ValueError(f"duplicate workflow step ID: {step.step_id}")
            missing = tuple(item for item in step.depends_on if item not in seen)
            if missing:
                raise ValueError(
                    f"workflow step {step.step_id} has incomplete dependencies: " + ", ".join(missing)
                )
            executor = self._executor_for(step)
            value = executor(
                step,
                run_spec,
                MappingProxyType(dict(state)),
            )
            if value is None:
                step_output: Mapping[str, Any] = {}
            elif isinstance(value, Mapping):
                step_output = dict(value)
            else:
                raise TypeError(f"workflow step {step.step_id} returned a non-mapping result")
            outputs[step.step_id] = step_output
            state[step.step_id] = step_output
            completed.append(step.step_id)
            seen.add(step.step_id)
        return WorkflowExecutionResult(
            run_id=run_spec.run_id,
            completed_steps=tuple(completed),
            step_outputs=outputs,
            state=state,
        )

    def _executor_for(self, step: WorkflowStep) -> WorkflowStepExecutor:
        if self._execute_step is not None:
            return self._execute_step
        for key in (step.implementation, step.phase, "*"):
            handler = self._handlers.get(key)
            if handler is not None:
                return handler
        raise KeyError(f"no executor registered for workflow implementation: {step.implementation}")

    def _production_lifecycle(self) -> ScientificWorkflowLifecycle:
        if self._lifecycle is None:
            raise RuntimeError("production evidence methods require ScientificWorkflowExecutor.for_run()")
        return self._lifecycle


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
