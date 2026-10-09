"""Truthful, recoverable phase state for one scientific workflow run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from agent.analysis.workflows.contracts import WorkflowPhase, WorkflowRunSpec


WorkflowPhaseStatus = Literal[
    "not_started",
    "running",
    "completed",
    "failed",
    "incomplete",
]

_TERMINAL_STATUSES = frozenset({"completed", "failed", "incomplete"})
_VALID_STATUSES = frozenset({"not_started", "running", *_TERMINAL_STATUSES})
_STATE_VERSION = 1
_MAX_RUNTIME_ATTEMPTS = 100


@dataclass
class ScientificWorkflowLifecycle:
    """Track production evidence without pretending declared phases ran.

    The local R backend still owns arbitrary user/model-authored analysis code. This
    object is only the durable control-plane ledger around that execution:
    dataset preflight, worker attempts, and the cumulative result assessment.
    """

    run_spec: WorkflowRunSpec
    recovered_state: Mapping[str, Any] | None = None
    _status: dict[str, WorkflowPhaseStatus] = field(init=False)
    _preflight: dict[str, Any] = field(init=False)
    _runtime_attempts: list[dict[str, Any]] = field(init=False)
    _assessment: dict[str, Any] = field(init=False)
    _r_succeeded: bool = field(init=False)

    def __post_init__(self) -> None:
        self._status = {step.phase: "not_started" for step in self.run_spec.steps}
        self._preflight = {"ready": None, "issues": []}
        self._runtime_attempts = []
        self._assessment = {}
        self._r_succeeded = False
        self._restore(self.recovered_state)

    @property
    def status(self) -> Mapping[str, WorkflowPhaseStatus]:
        return dict(self._status)

    @property
    def state(self) -> dict[str, Any]:
        """Return the bounded JSON-safe state persisted with the portfolio."""

        return {
            "state_version": _STATE_VERSION,
            "run_id": self.run_spec.run_id,
            "source_plan_id": self.run_spec.source_plan_id,
            "workflow_id": self.run_spec.workflow_id,
            "variant": self.run_spec.variant,
            "contract_signature": self.run_spec.definition.contract_signature,
            "phase_status": dict(self._status),
            "preflight": {
                "ready": self._preflight.get("ready"),
                "issues": [dict(item) for item in self._preflight.get("issues", [])],
            },
            "runtime_attempts": [dict(item) for item in self._runtime_attempts],
            "r_succeeded": self._r_succeeded,
            "assessment": dict(self._assessment),
        }

    def begin(self, phase: WorkflowPhase) -> None:
        step = self._step(phase)
        for dependency in step.depends_on:
            dependency_step = next(
                (item for item in self.run_spec.steps if item.step_id == dependency),
                None,
            )
            if dependency_step is None:
                raise ValueError(f"unknown workflow dependency: {dependency}")
            if self._status[dependency_step.phase] != "completed":
                raise ValueError(
                    f"workflow phase {phase} cannot start before {dependency_step.phase} completes"
                )
        current = self._status[phase]
        if current not in {"not_started", "running"}:
            raise ValueError(f"workflow phase {phase} is already terminal: {current}")
        self._status[phase] = "running"

    def complete(self, phase: WorkflowPhase) -> None:
        if self._status.get(phase) not in {"running", "completed"}:
            raise ValueError(f"workflow phase {phase} was not started")
        self._status[phase] = "completed"

    def fail(self, phase: WorkflowPhase) -> None:
        if self._status.get(phase) == "completed":
            raise ValueError(f"completed workflow phase {phase} cannot be failed")
        self._status[phase] = "failed"

    def record_preflight(
        self,
        *,
        ready: bool,
        issues: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        """Record an actual dataset-specific preflight evaluation."""

        issue_rows = [
            {
                "code": str(item.get("code") or "workflow_preflight"),
                "message": str(item.get("message") or ""),
            }
            for item in issues
            if isinstance(item, Mapping)
        ]
        self._preflight = {"ready": bool(ready), "issues": issue_rows}
        self._status["preflight"] = "completed" if ready else "failed"
        if not ready:
            for phase in ("prepare", "analyze", "diagnose", "publish"):
                if phase in self._status:
                    self._status[phase] = "not_started"
            self._assessment = {}

    def mark_runtime_result(self, *, ok: bool, execution_stage: str) -> None:
        """Translate one real worker attempt into cumulative orchestration state."""

        if self._status.get("preflight") != "completed":
            raise ValueError("workflow runtime cannot start before preflight completes")

        stage = str(execution_stage or "").strip().casefold()
        had_success = self._r_succeeded
        next_attempt = (
            int(self._runtime_attempts[-1].get("attempt") or 0) + 1 if self._runtime_attempts else 1
        )
        self._runtime_attempts.append(
            {
                "attempt": next_attempt,
                "ok": bool(ok),
                "execution_stage": stage or "unknown",
            }
        )
        self._runtime_attempts = self._runtime_attempts[-_MAX_RUNTIME_ATTEMPTS:]
        # This flag describes the latest actual worker attempt, not whether
        # some older attempt in the same run once succeeded.
        self._r_succeeded = False
        self._assessment = {}
        for phase in ("diagnose", "publish"):
            if phase in self._status:
                self._status[phase] = "not_started"

        if stage == "preflight":
            self._status["preflight"] = "failed" if not ok else "completed"
            if not ok:
                self._status["prepare"] = "not_started"
                self._status["analyze"] = "not_started"
            return

        if stage == "prepare":
            if ok:
                self._status["prepare"] = "completed"
                self._status["analyze"] = "not_started"
            elif had_success:
                self._status["prepare"] = "completed"
                self._status["analyze"] = "incomplete"
            else:
                self._status["prepare"] = "failed"
                self._status["analyze"] = "not_started"
            return

        if stage in {"analyze", "complete"}:
            self._status["prepare"] = "completed"
            if ok:
                self._status["analyze"] = "completed"
                self._r_succeeded = True
            else:
                self._status["analyze"] = "incomplete" if had_success else "failed"
            return

        # Older workers did not report a stage. A successful return still
        # proves both preparation and submitted analysis completed. An
        # unlocated failure is preparation failure only when no earlier R
        # attempt in this run has succeeded.
        if ok:
            self._status["prepare"] = "completed"
            self._status["analyze"] = "completed"
            self._r_succeeded = True
        elif had_success:
            self._status["prepare"] = "completed"
            self._status["analyze"] = "incomplete"
        else:
            self._status["prepare"] = "failed"
            self._status["analyze"] = "not_started"

    def apply_assessment(
        self,
        *,
        diagnostics_complete: bool,
        portfolio_complete: bool,
        assessment: Mapping[str, Any] | None = None,
    ) -> None:
        """Apply cumulative validator evidence to post-R phases."""

        if assessment is not None:
            self._assessment = dict(assessment)
        if self._status.get("preflight") != "completed":
            return
        # Cumulative artifacts can satisfy publication requirements, but they
        # cannot erase a later failed R attempt. A new successful worker
        # result must repair ``analyze`` before post-R phases may advance.
        if self._status.get("analyze") != "completed":
            return
        self._status["diagnose"] = "completed" if diagnostics_complete else "incomplete"
        if diagnostics_complete:
            self._status["publish"] = "completed" if portfolio_complete else "incomplete"
        else:
            self._status["publish"] = "not_started"

    def _restore(self, recovered_state: Mapping[str, Any] | None) -> None:
        if not isinstance(recovered_state, Mapping):
            return
        if recovered_state.get("state_version", _STATE_VERSION) != _STATE_VERSION:
            return
        identity = {
            "run_id": self.run_spec.run_id,
            "source_plan_id": self.run_spec.source_plan_id,
            "workflow_id": self.run_spec.workflow_id,
            "variant": self.run_spec.variant,
            "contract_signature": self.run_spec.definition.contract_signature,
        }
        if any(str(recovered_state.get(key) or "") != value for key, value in identity.items()):
            return

        recovered_status = recovered_state.get("phase_status")
        if isinstance(recovered_status, Mapping):
            for phase in self._status:
                status = str(recovered_status.get(phase) or "not_started")
                if status in _VALID_STATUSES:
                    self._status[phase] = status  # type: ignore[assignment]

        preflight = recovered_state.get("preflight")
        if isinstance(preflight, Mapping):
            raw_issues = preflight.get("issues")
            issues = raw_issues if isinstance(raw_issues, (list, tuple)) else ()
            self._preflight = {
                "ready": preflight.get("ready") if isinstance(preflight.get("ready"), bool) else None,
                "issues": [
                    {
                        "code": str(item.get("code") or "workflow_preflight"),
                        "message": str(item.get("message") or ""),
                    }
                    for item in issues
                    if isinstance(item, Mapping)
                ],
            }

        attempts = recovered_state.get("runtime_attempts")
        if isinstance(attempts, (list, tuple)):
            for index, item in enumerate(attempts[-_MAX_RUNTIME_ATTEMPTS:], start=1):
                if not isinstance(item, Mapping):
                    continue
                try:
                    attempt_number = max(1, int(item.get("attempt") or index))
                except (TypeError, ValueError):
                    attempt_number = index
                self._runtime_attempts.append(
                    {
                        "attempt": attempt_number,
                        "ok": bool(item.get("ok")),
                        "execution_stage": str(item.get("execution_stage") or "unknown"),
                    }
                )
        if self._runtime_attempts:
            latest_attempt = self._runtime_attempts[-1]
            self._r_succeeded = bool(latest_attempt["ok"]) and latest_attempt[
                "execution_stage"
            ] not in {"preflight", "prepare"}
        else:
            self._r_succeeded = bool(recovered_state.get("r_succeeded"))
        if self._status.get("analyze") != "completed":
            self._r_succeeded = False
        assessment = recovered_state.get("assessment")
        if isinstance(assessment, Mapping):
            self._assessment = dict(assessment)
        self._sanitize_dependencies()

    def _sanitize_dependencies(self) -> None:
        """Fail closed when persisted state violates the declared phase order."""

        if self._status.get("preflight") != "completed":
            for phase in ("prepare", "analyze", "diagnose", "publish"):
                if phase in self._status:
                    self._status[phase] = "not_started"
            return
        if self._status.get("prepare") != "completed":
            for phase in ("analyze", "diagnose", "publish"):
                if phase in self._status:
                    self._status[phase] = "not_started"
            return
        if self._status.get("analyze") != "completed":
            for phase in ("diagnose", "publish"):
                if phase in self._status:
                    self._status[phase] = "not_started"
            return
        if self._status.get("diagnose") != "completed" and "publish" in self._status:
            self._status["publish"] = "not_started"

    def _step(self, phase: WorkflowPhase):
        step = next((item for item in self.run_spec.steps if item.phase == phase), None)
        if step is None:
            raise ValueError(f"workflow phase is not declared: {phase}")
        return step
