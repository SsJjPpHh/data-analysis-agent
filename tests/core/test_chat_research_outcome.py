"""A finished conversation must not turn an unfinished R analysis into success."""

from __future__ import annotations

import json

import pytest


def _audit(payload):
    return {
        "type": "tool_audit", "tool": "run_r_code",
        "ok": payload["result"]["ok"],
        "error": payload["result"].get("error", ""),
        "content": json.dumps(payload),
    }


def _chat_job(monkeypatch, tmp_path, events, *, plan_id=""):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from api import create_app, chat

    class Agent:
        if plan_id:
            from types import SimpleNamespace
            _analysis_plan = SimpleNamespace(plan_id=plan_id)

        def run(self, *_args, **_kwargs):
            yield from events
            yield {"type": "text", "content": "本轮处理结束。"}
            yield {"type": "done"}

    monkeypatch.setattr(chat, "_build_agent", lambda *_a, **_k: Agent())
    client = create_app().test_client()
    response = client.post("/api/session/outcome-check/chat", json={"message": "分析"})
    assert response.status_code == 200
    body = response.get_data(as_text=True)  # drain until the background worker has finalized
    frames = [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]
    assert frames[-1]["type"] == "done"
    assert sum(event["type"] == "done" for event in frames) == 1
    assert any(event["type"] == "analysis_outcome" for event in frames[:-1])
    jobs = client.get("/api/session/outcome-check/jobs").get_json()["jobs"]
    return next(job for job in jobs if job["type"] == "conversation_analysis")


@pytest.mark.parametrize("ok,status", [(False, "blocked"), (True, "succeeded")])
def test_r_failure_or_missing_portfolio_does_not_become_chat_success(monkeypatch, tmp_path, ok, status):
    job = _chat_job(monkeypatch, tmp_path, [_audit({
        "result": {"ok": ok, "error": "blocked" if not ok else ""},
        "execution": {"plan_id": "plan-one", "status": status},
        "result_assessment": {"complete": False, "r_executed": ok, "missing_roles": ["primary_table"]},
    })])
    assert job["status"] == "failed"
    assert job["result"]["completed"] is False
    assert job["result"]["turn_completed"] is True
    assert job["result"]["scientific_review_status"] == "not_reviewed"


def test_explanation_without_an_r_analysis_remains_a_successful_conversation(monkeypatch, tmp_path):
    job = _chat_job(monkeypatch, tmp_path, [])
    assert job["status"] == "succeeded"
    assert job["result"]["analysis_status"] == "conversation_only"


def test_frozen_plan_without_execution_is_incomplete(monkeypatch, tmp_path):
    job = _chat_job(monkeypatch, tmp_path, [], plan_id="unexecuted-plan")
    assert job["status"] == "failed"
    assert job["result"]["analysis_status"] == "incomplete"


def test_successful_repair_uses_current_canonical_portfolio(monkeypatch, tmp_path):
    from agent.analysis.result_validation import build_artifact_validation_receipt

    table = tmp_path / "outputs/research/outcome-check/repair/table.csv"
    table.parent.mkdir(parents=True)
    table.write_text("estimate\n2\n", encoding="utf-8")
    metadata = {"dataset_id": "", "plan_id": "plan-one", "job_id": "repair", "source_execution_status": "succeeded", "source_tool": "run_r_code", "role": "primary_table", "semantic_key": "primary_table"}
    receipt = build_artifact_validation_receipt(table, kind="table", metadata=metadata, job_id="repair", artifacts_root=table.parent)
    metadata.update(artifact_validation_receipt=receipt, artifact_sha256=receipt["sha256"], artifact_size_bytes=receipt["size_bytes"])
    failed = _audit({"result": {"ok": False, "error": "first step failed"}, "execution": {"plan_id": "plan-one", "status": "failed"}, "result_assessment": {"complete": False}})
    repaired = _audit({
        "job_id": "repair", "result": {"ok": True, "tables": [{"name": "primary_table", "path": str(table), "kind": "table", "metadata": metadata}]},
        "execution": {"plan_id": "plan-one", "status": "succeeded", "output_portfolio": [{"role": "primary_table", "kind": "table", "tier": "core", "required": True}]},
        "result_assessment": {"complete": True, "r_executed": True, "diagnostic_quality": "passed"},
    })
    job = _chat_job(monkeypatch, tmp_path, [failed, repaired])
    assert job["status"] == "succeeded", job.get("error")
    assert job["result"]["portfolio_complete"] is True
    assert job["result"]["scientific_review_status"] == "not_reviewed"
