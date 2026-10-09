import json
from dataclasses import asdict, replace

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.artifact_manifest import validation_evidence_from_manifest
from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import (
    ResultAssessment,
    ResultValidator,
    build_artifact_validation_receipt,
)
from agent.analysis.workflows import (
    ScientificWorkflowExecutor,
    WorkflowContractError,
    default_workflow_registry,
    validate_workflow_run_spec,
)
from agent.tools.research import RToolsMixin
from data.research_results import ResearchResultStore
from r_runtime.protocol import RRunResult


def _prepared_workflow():
    profile = {
        "dataset_id": "workflow-dataset",
        "format": "csv",
        "rows": 3,
        "columns": [
            {"name": "age", "semantic_type": "numeric", "unique_count": 3},
        ],
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare("data_quality", profile)
    assert prepared.plan is not None
    spec = default_workflow_registry().compile(prepared.plan, dataset_profile=profile)
    return profile, prepared.plan, spec


def test_production_executor_requires_real_preflight_runtime_and_assessment_evidence():
    profile, _plan, spec = _prepared_workflow()
    executor = ScientificWorkflowExecutor.for_run(spec)

    executor.record_preflight(validate_workflow_run_spec(spec, profile))
    executor.record_runtime_result(RRunResult(ok=True, execution_stage="complete"))

    assert executor.state["phase_status"] == {
        "preflight": "completed",
        "prepare": "completed",
        "analyze": "completed",
        "diagnose": "not_started",
        "publish": "not_started",
    }

    executor.apply_assessment(
        ResultAssessment(
            complete=False,
            r_executed=True,
            missing_roles=("analysis_readiness_report",),
            missing_diagnostics=(),
            warnings=(),
        )
    )

    assert executor.state["phase_status"]["diagnose"] == "completed"
    assert executor.state["phase_status"]["publish"] == "incomplete"
    assert executor.state["assessment"]["outstanding_roles"] == ["analysis_readiness_report"]
    assert len(executor.state["runtime_attempts"]) == 1


@pytest.mark.parametrize("mutation", ["dependency", "implementation", "check"])
def test_production_executor_fails_closed_on_unregistered_compiled_steps(mutation):
    _profile, _plan, spec = _prepared_workflow()
    expected_checks = {step.phase: step.checks for step in spec.steps}
    steps = list(spec.steps)
    analyze_index = next(index for index, step in enumerate(steps) if step.phase == "analyze")
    analyze = steps[analyze_index]
    if mutation == "dependency":
        analyze = replace(analyze, depends_on=("missing_prepare",))
    elif mutation == "implementation":
        analyze = replace(analyze, implementation="workflow.analyze.unregistered")
    else:
        analyze = replace(analyze, checks=(*analyze.checks, "unregistered_check"))
    steps[analyze_index] = analyze
    invalid = replace(spec, definition=replace(spec.definition, steps=tuple(steps)))

    with pytest.raises(WorkflowContractError):
        ScientificWorkflowExecutor.for_run(
            invalid,
            expected_checks_by_phase=expected_checks,
        )


def test_rtools_reuses_one_executor_across_flexible_r_calls(monkeypatch):
    profile, plan, spec = _prepared_workflow()
    dataset_profile = profile

    class Dataset:
        dataset_id = "workflow-dataset"
        session_id = "workflow-session"
        name = "clinical.csv"
        format = "csv"
        stored_path = "/tmp/clinical.csv"
        assets = ()
        profile = {
            "summary": {
                **dataset_profile,
                "variables": dataset_profile["columns"],
            }
        }

        def to_dict(self):
            return {
                "dataset_id": self.dataset_id,
                "name": self.name,
                "format": self.format,
            }

    class Agent(RToolsMixin):
        def __init__(self):
            self._session_id = "workflow-session"
            self._analysis_plan = plan
            self._workflow_run_spec = spec
            self._research_workspace_generation = ""
            self._conversation_job_id = ""
            self._prior_portfolio_manifest = {}
            self.dataset = Dataset()

        def _resolve_research_dataset(self, dataset_id=""):
            return self.dataset

    class Backend:
        calls = 0

        def run_code(self, request):
            self.calls += 1
            if self.calls == 1:
                return RRunResult(ok=True, code=request.code, execution_stage="complete")
            return RRunResult(
                ok=False,
                error="downstream model failed",
                code=request.code,
                execution_stage="analyze",
            )

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)
    agent = Agent()

    first = json.loads(agent._tool_run_r_code(code="summary(dataset)"))
    second_text = agent._tool_run_r_code(code="stop('downstream model failed')")
    second = json.loads(second_text.removeprefix("ERROR: "))

    first_workflow = first["execution"]["workflow"]
    second_workflow = second["execution"]["workflow"]
    assert first_workflow["run_id"] == second_workflow["run_id"] == spec.run_id
    assert len(first_workflow["runtime_attempts"]) == 1
    assert len(second_workflow["runtime_attempts"]) == 2
    assert second_workflow["phase_status"]["analyze"] == "incomplete"
    assert second_workflow["phase_status"]["diagnose"] == "not_started"
    assert second_workflow["phase_status"]["publish"] == "not_started"
    assert backend.calls == 2

    agent._scientific_workflow_executor.apply_assessment(
        ResultAssessment(
            complete=True,
            r_executed=True,
            missing_roles=(),
            missing_diagnostics=(),
            warnings=(),
        )
    )
    assessed = agent._scientific_workflow_executor.state["phase_status"]
    assert assessed["analyze"] == "incomplete"
    assert assessed["diagnose"] == "not_started"
    assert assessed["publish"] == "not_started"

    recovered_agent = Agent()
    recovered_agent._prior_portfolio_manifest = {"workflow_run": second_workflow}
    recovered_text = recovered_agent._tool_run_r_code(code="stop('retry still failed')")
    recovered = json.loads(recovered_text.removeprefix("ERROR: "))
    assert len(recovered["execution"]["workflow"]["runtime_attempts"]) == 3


def test_result_store_persists_workflow_state_for_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    profile, plan, spec = _prepared_workflow()
    executor = ScientificWorkflowExecutor.for_run(spec)
    executor.record_preflight(validate_workflow_run_spec(spec, profile))
    executor.record_runtime_result(RRunResult(ok=True, execution_stage="complete"))
    executor.apply_assessment(
        ResultAssessment(
            complete=False,
            r_executed=True,
            missing_roles=("analysis_readiness_report",),
            missing_diagnostics=(),
            warnings=(),
        )
    )
    payload = {
        "job_id": "r-job-1",
        "result": {"ok": True, "tables": [], "figures": [], "texts": [], "models": []},
        "result_assessment": executor.state["assessment"],
        "execution": {
            "dataset_id": plan.dataset_id,
            "plan_id": plan.plan_id,
            "status": "succeeded",
            "diagnostic_status": {},
            "portfolio_version": plan.portfolio_version,
            "output_portfolio": [
                {
                    "role": item.role,
                    "kind": item.kind,
                    "required": item.required,
                    "tier": item.tier,
                }
                for item in plan.output_portfolio
            ],
            "planned_diagnostics": list(plan.diagnostics),
            "workflow": executor.state,
        },
    }
    store = ResearchResultStore("workflow-session")
    store.set_dataset({"dataset_id": plan.dataset_id, "name": "clinical.csv", "format": "csv"})

    state = store.append_r_payload(
        turn_id="turn-1",
        question="检查数据质量",
        template_context={"id": "data_quality"},
        payload=payload,
        dataset_id=plan.dataset_id,
    )

    saved = state["portfolio_manifests"][plan.plan_id]["workflow_run"]
    assert saved["run_id"] == spec.run_id
    assert saved["phase_status"]["publish"] == "incomplete"
    execution_ref = state["analysis_turns"][0]["execution_refs"][0]
    assert "workflow" not in execution_ref
    restored = ScientificWorkflowExecutor.for_run(spec, recovered_state=saved)
    assert restored.state["phase_status"] == saved["phase_status"]
    assert restored.state["runtime_attempts"] == saved["runtime_attempts"]

    mismatched = {**saved, "run_id": "wf_from_another_plan"}
    rejected = ScientificWorkflowExecutor.for_run(spec, recovered_state=mismatched)
    assert rejected.state["phase_status"] == {
        "preflight": "not_started",
        "prepare": "not_started",
        "analyze": "not_started",
        "diagnose": "not_started",
        "publish": "not_started",
    }
    assert rejected.state["runtime_attempts"] == []


def test_recovered_runtime_attempt_history_stays_bounded_and_monotonic():
    profile, _plan, spec = _prepared_workflow()
    executor = ScientificWorkflowExecutor.for_run(spec)
    executor.record_preflight(validate_workflow_run_spec(spec, profile))
    for _ in range(105):
        executor.record_runtime_result(RRunResult(ok=True, execution_stage="complete"))

    attempts = executor.state["runtime_attempts"]
    assert len(attempts) == 100
    assert attempts[0]["attempt"] == 6
    assert attempts[-1]["attempt"] == 105

    restored = ScientificWorkflowExecutor.for_run(spec, recovered_state=executor.state)
    restored.record_preflight(validate_workflow_run_spec(spec, profile))
    restored.record_runtime_result(RRunResult(ok=True, execution_stage="complete"))
    assert restored.state["runtime_attempts"][-1]["attempt"] == 106


def test_latest_failed_attempt_keeps_manifest_turn_and_recovery_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    plan = AnalysisPlan(
        plan_id="plan-latest-failure",
        source="conversation",
        dataset_id="dataset-latest-failure",
        intent="latest failure wins",
        category="基础统计",
        method_family="descriptive_statistics",
        scenario="failure recovery",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("core_table", "table"),),
        resource_class="standard",
    )
    profile = {
        "dataset_id": plan.dataset_id,
        "format": "csv",
        "rows": 1,
        "columns": [{"name": "x", "semantic_type": "numeric", "unique_count": 1}],
    }
    spec = default_workflow_registry().compile(plan, dataset_profile=profile)
    executor = ScientificWorkflowExecutor.for_run(spec)
    executor.record_preflight({"ready": True, "issues": []})
    executor.record_runtime_result(RRunResult(ok=True, execution_stage="complete"))

    output_dir = tmp_path / "job-success"
    output_dir.mkdir()
    table_path = output_dir / "core.csv"
    table_path.write_text("term,estimate\nage,1.2\n", encoding="utf-8")
    metadata = {
        "dataset_id": plan.dataset_id,
        "plan_id": plan.plan_id,
        "job_id": "job-success",
        "source_execution_status": "succeeded",
        "role": "core_table",
        "semantic_key": "core_table",
        "source_tool": "run_r_code",
    }
    receipt = build_artifact_validation_receipt(
        table_path,
        kind="table",
        metadata=metadata,
        job_id="job-success",
        artifacts_root=output_dir,
    )
    metadata.update(
        artifact_size_bytes=receipt["size_bytes"],
        artifact_sha256=receipt["sha256"],
        artifact_validation_receipt=receipt,
    )
    artifact = {
        "name": "core",
        "path": str(table_path),
        "kind": "table",
        "source_tool": "run_r_code",
        "job_id": "job-success",
        "metadata": metadata,
    }
    success = ResultValidator().validate(
        plan,
        {"dataset_id": plan.dataset_id, "plan_id": plan.plan_id, "status": "succeeded"},
        [artifact],
    )
    executor.apply_assessment(success)
    success_workflow = executor.state

    store = ResearchResultStore("latest-failure-session")
    store.set_dataset({"dataset_id": plan.dataset_id, "name": "data.csv", "format": "csv"})
    portfolio = [asdict(item) for item in plan.output_portfolio]
    store.append_r_payload(
        turn_id="turn-1",
        question="run then fail",
        template_context={"title": "latest failure"},
        payload={
            "job_id": "job-success",
            "result": {"ok": True, "tables": [artifact]},
            "execution": {
                "dataset_id": plan.dataset_id,
                "plan_id": plan.plan_id,
                "status": "succeeded",
                "output_portfolio": portfolio,
                "workflow": success_workflow,
            },
            "result_assessment": asdict(success),
        },
        dataset_id=plan.dataset_id,
    )

    executor.record_runtime_result(
        RRunResult(ok=False, error="late failure", execution_stage="analyze")
    )
    failed = ResultValidator().validate(
        plan,
        {"dataset_id": plan.dataset_id, "plan_id": plan.plan_id, "status": "failed"},
        [artifact],
    )
    executor.apply_assessment(failed)
    state = store.append_r_payload(
        turn_id="turn-1",
        question="run then fail",
        template_context={"title": "latest failure"},
        payload={
            "job_id": "job-failed",
            "result": {"ok": False, "error": "late failure"},
            "execution": {
                "dataset_id": plan.dataset_id,
                "plan_id": plan.plan_id,
                "status": "failed",
                "output_portfolio": portfolio,
                "workflow": executor.state,
            },
            "result_assessment": asdict(failed),
        },
        dataset_id=plan.dataset_id,
    )

    manifest = state["portfolio_manifests"][plan.plan_id]
    evidence = validation_evidence_from_manifest(
        manifest,
        dataset_id=plan.dataset_id,
        plan_id=plan.plan_id,
    )
    assert manifest["complete"] is False
    assert manifest["workflow_run"]["phase_status"]["analyze"] == "incomplete"
    assert state["analysis_turns"][0]["status"] == "failed"
    assert evidence.r_executed is False
