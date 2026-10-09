from agent.analysis.completion import AnalysisCompletionController
from agent.analysis.artifact_manifest import build_artifact_manifest, validation_evidence_from_manifest
from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import ResultAssessment
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt


def _trusted_artifact(
    path,
    *,
    kind: str,
    role: str,
    status: str = "",
    reason: str = "",
    diagnostic_status: dict | None = None,
) -> dict:
    if kind == "table":
        if role == "baseline_table":
            path.write_text(
                "Variable,Overall,Group A,Group B,P value,SMD\nage,42,40,44,0.20,0.10\n",
                encoding="utf-8",
            )
        else:
            path.write_text("term,estimate\nage,1.2\n", encoding="utf-8")
    else:
        path.write_text("Scientific disposition.\n", encoding="utf-8")
    metadata = {
        "dataset_id": "dataset-1",
        "plan_id": "plan-1",
        "job_id": "job-1",
        "source_execution_status": "succeeded",
        "role": role,
        "semantic_key": role,
        "portfolio_status": status,
        "portfolio_reason": reason,
        "source_tool": "run_r_code",
    }
    if diagnostic_status:
        metadata["diagnostic_status"] = diagnostic_status
    receipt = build_artifact_validation_receipt(
        path,
        kind=kind,
        metadata=metadata,
        job_id="job-1",
        artifacts_root=path.parent,
    )
    metadata.update(
        {
            "artifact_size_bytes": receipt["size_bytes"],
            "artifact_sha256": receipt["sha256"],
            "artifact_validation_receipt": receipt,
        }
    )
    return {
        "artifact_id": f"{role}-{kind}",
        "turn_id": "turn-1",
        "type": kind,
        "kind": kind,
        "portfolio_role": role,
        "plan_id": "plan-1",
        "portfolio_status": status,
        "portfolio_reason": reason,
        "path": str(path),
        "source_tool": "run_r_code",
        "job_id": "job-1",
        "metadata": metadata,
    }


def _plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-1",
        source="template",
        dataset_id="dataset-1",
        intent="论文 Table 1",
        category="基础统计",
        method_family="publication_tables_and_reporting",
        scenario="分组基线比较",
        variable_bindings={"分组变量": "arm"},
        packages=("gtsummary",),
        prerequisites=(),
        assumptions=(),
        diagnostics=("缺失比例检查",),
        required_outputs=(
            RequiredOutput("baseline_table", "table"),
            RequiredOutput("baseline_balance_plot", "figure", tier="recommended"),
        ),
        resource_class="standard",
    )


def test_completion_controller_continues_with_exact_outstanding_portfolio_items():
    assessment = ResultAssessment(
        complete=False,
        r_executed=True,
        missing_roles=("baseline_balance_plot",),
        missing_diagnostics=("缺失比例检查",),
        warnings=(),
        outstanding_roles=("baseline_balance_plot",),
        next_action="continue",
    )

    decision = AnalysisCompletionController().decide(
        _plan(),
        assessment,
        r_calls=1,
        max_r_calls=12,
        nudge_count=0,
    )

    assert decision.action == "continue"
    assert decision.allow_final is False
    assert "baseline_balance_plot" in decision.instruction
    assert "缺失比例检查" in decision.instruction


def test_completion_controller_allows_final_only_after_portfolio_completion():
    assessment = ResultAssessment(
        complete=True,
        r_executed=True,
        missing_roles=(),
        missing_diagnostics=(),
        warnings=(),
    )

    decision = AnalysisCompletionController().decide(
        _plan(),
        assessment,
        r_calls=2,
        max_r_calls=12,
        nudge_count=0,
    )

    assert decision.action == "finalize"
    assert decision.allow_final is True


def test_completion_limit_is_explicitly_incomplete_and_actionable_for_the_user():
    assessment = ResultAssessment(
        complete=False,
        r_executed=True,
        missing_roles=("baseline_balance_plot",),
        missing_diagnostics=(),
        warnings=(),
        outstanding_roles=("baseline_balance_plot",),
    )

    decision = AnalysisCompletionController().decide(
        _plan(),
        assessment,
        r_calls=12,
        max_r_calls=12,
        nudge_count=0,
    )

    assert decision.action == "limit"
    assert "尚未完成" in decision.user_message
    assert "baseline_balance_plot" in decision.user_message
    assert "补齐产物" in decision.user_message


def test_zero_r_call_limit_means_unlimited_local_execution():
    assessment = ResultAssessment(
        complete=False,
        r_executed=True,
        missing_roles=("baseline_balance_plot",),
        missing_diagnostics=(),
        warnings=(),
        outstanding_roles=("baseline_balance_plot",),
    )

    decision = AnalysisCompletionController().decide(
        _plan(),
        assessment,
        r_calls=250,
        max_r_calls=0,
        nudge_count=0,
    )

    assert decision.action == "continue"
    assert decision.allow_final is False


def test_continuation_manifest_restores_prior_artifacts_for_semantic_completion(tmp_path):
    artifacts = [
        _trusted_artifact(
            tmp_path / "baseline.csv",
            kind="table",
            role="baseline_table",
            diagnostic_status={"缺失比例检查": "passed"},
        ),
        _trusted_artifact(
            tmp_path / "balance-limitation.txt",
            kind="text",
            role="baseline_balance_plot",
            status="limited",
            reason="当前数据只能支持有限的平衡诊断。",
        ),
    ]
    manifest = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[
            {"role": "baseline_table", "kind": "table", "tier": "core", "required": True},
            {
                "role": "baseline_balance_plot",
                "kind": "figure",
                "tier": "recommended",
                "required": True,
            },
        ],
        artifacts=artifacts,
        diagnostic_status={"缺失比例检查": "passed"},
        planned_diagnostics=("缺失比例检查",),
        assessment={"complete": True, "r_executed": True},
    )

    evidence = validation_evidence_from_manifest(
        manifest,
        dataset_id="dataset-1",
        plan_id="plan-1",
    )
    assessment = ResultValidator().validate(
        _plan(),
        {
            "dataset_id": "dataset-1",
            "plan_id": "plan-1",
            "status": "succeeded",
            "diagnostic_status": evidence.diagnostic_status,
        },
        list(evidence.artifacts),
    )

    assert evidence.r_executed is True
    assert assessment.complete is True


def test_manifest_does_not_accept_an_unowned_artifact_from_another_analysis():
    manifest = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[
            {
                "role": "baseline_table",
                "kind": "table",
                "required": True,
            }
        ],
        artifacts=[
            {
                "artifact_id": "old-table",
                "kind": "table",
                "portfolio_role": "baseline_table",
                "metadata": {},
            },
            {
                "artifact_id": "other-plan-table",
                "kind": "table",
                "portfolio_role": "baseline_table",
                "plan_id": "plan-2",
            },
        ],
    )

    assert manifest["complete"] is False
    assert manifest["items"][0]["status"] == "missing"


def test_manifest_treats_justified_limits_as_terminal_only_for_non_core_outputs(tmp_path):
    reason = "样本量不足以稳定估计该敏感性图。"
    artifacts = [
        _trusted_artifact(
            tmp_path / "limit.txt",
            kind="text",
            role="sensitivity_plot",
            status="limited",
            reason=reason,
        )
    ]

    recommended = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[
            {
                "role": "sensitivity_plot",
                "kind": "figure",
                "tier": "sensitivity",
                "required": True,
            }
        ],
        artifacts=artifacts,
        assessment={"complete": True, "r_executed": True},
    )
    core = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[
            {
                "role": "sensitivity_plot",
                "kind": "figure",
                "tier": "core",
                "required": True,
            }
        ],
        artifacts=artifacts,
        assessment={"complete": False, "r_executed": True},
    )

    assert recommended["complete"] is True
    assert recommended["outstanding_roles"] == []
    assert core["complete"] is False
    assert core["outstanding_roles"] == ["sensitivity_plot"]
