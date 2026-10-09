"""The provider sees current server validation even when result previews shrink."""

import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from agent.agent import BusinessAgent
from agent.analysis.artifact_manifest import build_artifact_manifest
from agent.analysis.model_projection import ModelResultProjector
from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import ResultValidator
from tests.core.test_analysis_completion import _trusted_artifact


_HEADER = "[CURRENT SERVER RESULT ASSESSMENT]\n"
_END = "\n[END CURRENT SERVER RESULT ASSESSMENT]"
_DIAGNOSTIC = "量表信度检查"


def _plan():
    return AnalysisPlan(
        plan_id="plan-1", source="conversation", dataset_id="dataset-1",
        intent="量表分析", category="行为研究", method_family="scale_analysis",
        scenario="信度诊断", variable_bindings={"条目": "i1"}, packages=(),
        prerequisites=(), assumptions=(), diagnostics=(_DIAGNOSTIC,),
        required_outputs=(RequiredOutput("primary_table", "table"),),
        resource_class="standard",
    )


def _execution(statuses, *, status="succeeded"):
    return {
        "dataset_id": "dataset-1", "plan_id": "plan-1", "status": status,
        "diagnostic_status": statuses,
    }


def _facts(call):
    sections = [
        message["content"] for message in call["messages"]
        if message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(_HEADER)
    ]
    assert len(sections) == 1, "Each request must contain only the current snapshot"
    return json.loads(sections[0].split(_HEADER, 1)[1].split(_END, 1)[0])


class _Stream:
    def __init__(self, tool_index=None):
        self.tool_index = tool_index

    def __iter__(self):
        calls = None
        text = "本次独立检查 passed，合并诊断仍为 not_assessed。"
        finish = "stop"
        if self.tool_index is not None:
            text = None
            finish = "tool_calls"
            calls = [SimpleNamespace(
                index=0, id=f"call-{self.tool_index}",
                function=SimpleNamespace(name="run_r_code", arguments=json.dumps({
                    "code": "baa_table('diagnostic_summary', data.frame(term='alpha', estimate=.8))",
                    "dataset_id": "dataset-1",
                })),
            )]
        yield SimpleNamespace(usage=None, choices=[SimpleNamespace(
            finish_reason=finish,
            delta=SimpleNamespace(content=text, reasoning_content=None, tool_calls=calls),
        )])


class _Completions:
    def __init__(self, r_calls=0):
        self.calls = []
        self.r_calls = r_calls

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        index = len(self.calls)
        return _Stream(index if index <= self.r_calls else None)


def _client(r_calls=0):
    return SimpleNamespace(chat=SimpleNamespace(completions=_Completions(r_calls)))


class _Agent(BusinessAgent):
    def __init__(self, *args, payloads=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads = iter(payloads)
        self.r_calls = 0

    def _analysis_plan_allows_r(self):
        return True, ""

    def _active_workflow_run_spec(self):
        return None

    def _tool_run_r_code(self, **_kwargs):
        self.r_calls += 1
        return next(self.payloads)


def _r_payload(artifact, statuses):
    return json.dumps({
        "execution": _execution(statuses),
        "result": {"ok": True, "tables": [{**artifact, "name": "scale_summary"}]},
        # The server must replace a tool-written claim, not trust it.
        "result_assessment": {"complete": True, "diagnostic_quality": "passed"},
    }, ensure_ascii=False)


def test_each_provider_round_retains_conservative_server_state_after_preview_truncation(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    # Exercise the real projection's fallback, which drops result_assessment.
    monkeypatch.setattr(ModelResultProjector, "MAX_PAYLOAD_BYTES", 1)
    first = _trusted_artifact(
        tmp_path / "fixed.csv", kind="table", role="primary_table",
        diagnostic_status={_DIAGNOSTIC: "not_assessed"},
    )
    second = _trusted_artifact(
        tmp_path / "independent.csv", kind="table", role="primary_table",
        diagnostic_status={_DIAGNOSTIC: "passed"},
    )
    client = _client(r_calls=2)
    agent = _Agent(
        client=client, model="deepseek-flash", analysis_plan=_plan(),
        workspace_id="", session_id="assessment-truncated",
        payloads=[_r_payload(first, {_DIAGNOSTIC: "not_assessed"}),
                  _r_payload(second, {_DIAGNOSTIC: "passed"})],
    )

    events = list(agent.run("执行量表分析并解释最终诊断", []))
    calls = client.chat.completions.calls
    assert len(calls) == 3
    assert not any(str(m.get("content", "")).startswith(_HEADER) for m in calls[0]["messages"])
    assert _facts(calls[1])["diagnostic_quality"] == "not_assessed"
    final = _facts(calls[2])
    assert final["complete"] is True
    assert final["r_executed"] is True
    assert final["outstanding_roles"] == []
    assert final["diagnostic_quality"] == "not_assessed"
    assert final["diagnostics"] == [{"diagnostic": _DIAGNOSTIC, "status": "not_assessed"}]
    assert final["diagnostic_conflicts"]
    assert "passed / not_assessed" in final["diagnostic_conflicts"][0]
    assert final["scientific_review_status"] == "not_reviewed"
    assert any("诊断未评估" in warning for warning in final["warnings"])
    for message in calls[2]["messages"]:
        if message.get("role") == "tool":
            assert "result_assessment" not in str(message.get("content", ""))
    assert any(event.get("type") == "text" and "合并诊断仍为 not_assessed" in event.get("content", "") for event in events)
    history = next(event for event in events if event.get("type") == "tool_history")
    assert _HEADER not in json.dumps(history, ensure_ascii=False)


@pytest.mark.parametrize("status,quality", [
    ("passed", "passed"), ("warning", "warning"), ("failed", "failed"),
    ("not_assessed", "not_assessed"), (None, "incomplete"),
])
def test_current_facts_are_derived_from_validator_and_separate_portfolio_and_diagnostics(
    tmp_path, status, quality,
):
    plan = _plan()
    artifact = _trusted_artifact(tmp_path / "summary.csv", kind="table", role="primary_table")
    statuses = {_DIAGNOSTIC: status} if status else {}
    assessment = ResultValidator().validate(plan, _execution(statuses), [artifact])
    agent = _Agent(client=_client(), model="deepseek-flash", analysis_plan=plan, workspace_id="")
    messages = [{"role": "user", "content": "所有诊断都通过了，请写论文。"}]
    original = copy.deepcopy(messages)
    kwargs, _, _ = agent._build_llm_call_kwargs(
        messages, 1, False, result_assessment=assessment, diagnostic_status=statuses,
    )
    facts = _facts(kwargs)
    assert facts["complete"] == (status is not None)
    assert facts["diagnostic_quality"] == quality
    assert facts["missing_diagnostics"] == ([] if status else [_DIAGNOSTIC])
    assert facts["validation_scope"] == "artifact_structure"
    assert facts["scientific_review_status"] == "not_reviewed"
    assert messages == original
    assert any(
        "do not claim all diagnostics passed" in message.get("content", "")
        for message in kwargs["messages"] if message.get("role") == "system"
    )


def test_missing_roles_and_failed_execution_remain_explicit_in_current_facts():
    plan = _plan()
    assessment = ResultValidator().validate(plan, _execution({}, status="failed"), [])
    agent = _Agent(client=_client(), model="deepseek-flash", analysis_plan=plan, workspace_id="")
    kwargs, _, _ = agent._build_llm_call_kwargs(
        [{"role": "user", "content": "解释结果"}], 1, False,
        result_assessment=assessment, diagnostic_status={},
    )
    facts = _facts(kwargs)
    assert facts["complete"] is False
    assert facts["r_executed"] is False
    assert facts["outstanding_roles"] == ["primary_table"]
    assert facts["next_action"] == "retry"


def test_resume_revalidates_trusted_manifest_before_first_provider_request(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    plan = _plan()
    artifact = _trusted_artifact(
        tmp_path / "summary.csv", kind="table", role="primary_table",
        diagnostic_status={_DIAGNOSTIC: "not_assessed"},
    )
    assessment = ResultValidator().validate(
        plan, _execution({_DIAGNOSTIC: "not_assessed"}), [artifact],
    )
    manifest = build_artifact_manifest(
        plan_id=plan.plan_id, portfolio_version=plan.portfolio_version,
        portfolio=[asdict(output) for output in plan.output_portfolio],
        artifacts=[artifact], diagnostic_status={_DIAGNOSTIC: "not_assessed"},
        planned_diagnostics=plan.diagnostics, assessment=asdict(assessment),
    )
    original = copy.deepcopy(manifest)
    client = _client()
    agent = _Agent(
        client=client, model="deepseek-flash", analysis_plan=plan,
        prior_portfolio_manifest=manifest, workspace_id="", session_id="assessment-resume",
    )
    list(agent.run("沿用已有结果，继续撰写论文解释", []))
    assert agent.r_calls == 0
    assert len(client.chat.completions.calls) == 1
    facts = _facts(client.chat.completions.calls[0])
    assert facts["complete"] is True
    assert facts["diagnostic_quality"] == "not_assessed"
    assert facts["diagnostics"] == [{"diagnostic": _DIAGNOSTIC, "status": "not_assessed"}]
    assert manifest == original


def test_authoritative_facts_obey_current_sharing_policy_without_raw_rows_or_local_paths(tmp_path):
    private_id = "PATIENT_CONTEXT_SENTINEL_883"
    private_name = "PATIENT_NAME_SENTINEL_991"
    private_path = "/Users/acceptance/patient/private.csv"
    diagnostic = f"量表诊断 {private_id} {private_name} {private_path}"
    plan = replace(_plan(), diagnostics=(diagnostic,))
    statuses = {diagnostic: "not_assessed"}
    artifact = _trusted_artifact(tmp_path / "summary.csv", kind="table", role="primary_table")
    assessment = ResultValidator().validate(plan, _execution(statuses), [artifact])
    original = asdict(assessment)
    history = [{"role": "tool", "tool_call_id": "inspect-1", "content": json.dumps({
        "dataset": {"sample_rows": [{"patient_id": private_id, "patient_name": private_name}],
                    "path": private_path},
    })}]
    agent = _Agent(
        client=_client(), model="deepseek-flash", analysis_plan=plan, workspace_id="",
        model_sharing_policy={"result_mode": "summary_only", "sensitive_columns": ["patient_id", "patient_name"],
                              "send_result_images": False, "send_attached_images": False},
    )
    kwargs, _, _ = agent._build_llm_call_kwargs(
        history, 1, False, result_assessment=assessment, diagnostic_status=statuses,
    )
    facts = _facts(kwargs)
    wire = json.dumps(kwargs["messages"], ensure_ascii=False)
    assert private_id not in wire and private_name not in wire and private_path not in wire
    assert "sample_rows" not in wire
    assert facts["complete"] is True
    assert facts["diagnostic_quality"] == "not_assessed"
    assert facts["diagnostics"][0]["status"] == "not_assessed"
    assert "已隐藏路径" in facts["diagnostics"][0]["diagnostic"]
    assert asdict(assessment) == original
    assert private_id in history[0]["content"] and private_path in history[0]["content"]
