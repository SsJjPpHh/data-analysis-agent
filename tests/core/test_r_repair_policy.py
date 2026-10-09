import json

from agent.analysis.repair_policy import RRepairPolicy


def _r_payload(
    *,
    ok: bool,
    category: str = "",
    fact: str = "",
    execution_status: str = "failed",
    execution_stage: str = "run",
    dependency_gate: dict | None = None,
    preparation_plan: dict | None = None,
    error: str = "",
) -> str:
    payload = {
        "result": {
            "ok": ok,
            "error": "" if ok else (error or fact),
            "execution_stage": execution_stage,
            "tables": [],
            "figures": [],
            "texts": [],
            "models": [],
        },
        "execution": {"status": "succeeded" if ok else execution_status},
    }
    if category:
        payload["repair_diagnostics"] = {"category": category, "facts": [fact]}
    if dependency_gate is not None:
        payload["dependency_gate"] = dependency_gate
    if preparation_plan is not None:
        payload["preparation_plan"] = preparation_plan
    text = json.dumps(payload, ensure_ascii=False)
    return text if ok else f"ERROR: {text}"


def test_repair_policy_stops_after_the_same_r_failure_repeats_once():
    policy = RRepairPolicy()
    first = policy.observe(
        _r_payload(
            ok=False,
            category="frozen_binding_mismatch",
            fact="group_var 被设为 <dynamic expression>，但当前方案要求 cvd",
            execution_stage="binding_preflight",
        )
    )
    second = policy.observe(
        _r_payload(
            ok=False,
            category="frozen_binding_mismatch",
            fact="group_var 被设为 <dynamic expression>，但当前方案要求 cvd",
            execution_stage="binding_preflight",
        )
    )

    assert first.stop is False
    assert second.stop is True
    assert second.reason == "repeated_failure"
    assert "frozen_binding_mismatch" in second.user_message


def test_repair_policy_stops_after_three_distinct_consecutive_r_failures():
    policy = RRepairPolicy()

    decisions = [
        policy.observe(
            _r_payload(ok=False, category=category, fact=fact)
        )
        for category, fact in (
            ("missing_function", "could not find function alpha"),
            ("type_mismatch", "invalid type for beta"),
            ("r_execution_error", "gamma failed"),
        )
    ]

    assert [item.stop for item in decisions] == [False, False, True]
    assert decisions[-1].reason == "consecutive_failures"


def test_success_resets_the_consecutive_and_repeated_failure_counters():
    policy = RRepairPolicy()
    failure = _r_payload(
        ok=False,
        category="missing_function",
        fact="could not find function alpha",
    )

    assert policy.observe(failure).stop is False
    assert policy.observe(_r_payload(ok=True)).stop is False
    assert policy.observe(failure).stop is False


def test_same_category_and_generic_fact_do_not_merge_different_concrete_errors():
    policy = RRepairPolicy()

    first = policy.observe(
        _r_payload(
            ok=False,
            category="missing_column",
            fact="A referenced column is unavailable",
            error="undefined column: old_name",
        )
    )
    second = policy.observe(
        _r_payload(
            ok=False,
            category="missing_column",
            fact="A referenced column is unavailable",
            error="undefined column: another_name",
        )
    )

    assert first.stop is False
    assert second.stop is False


def test_dependency_and_preparation_states_pause_instead_of_triggering_retries():
    dependency = RRepairPolicy().observe(
        _r_payload(
            ok=False,
            category="dependency_preflight",
            fact="需要安装 R 包",
            execution_status="waiting_for_dependencies",
            execution_stage="dependency_preflight",
            dependency_gate={"status": "needs_action", "headline": "需要安装 R 包"},
        )
    )
    preparation = RRepairPolicy().observe(
        _r_payload(
            ok=False,
            category="workflow_preflight",
            fact="当前矩阵需要整理",
            execution_status="blocked",
            execution_stage="preflight",
            preparation_plan={"headline": "需要先准备数据"},
        )
    )

    assert dependency.stop is True
    assert dependency.reason == "waiting_for_dependencies"
    assert preparation.stop is True
    assert preparation.reason == "waiting_for_preparation"
