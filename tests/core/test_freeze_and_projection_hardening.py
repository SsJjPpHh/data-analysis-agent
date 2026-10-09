# -*- coding: utf-8 -*-
"""Freeze-time statistical guards and projection error-visibility tests.

Freeze-time guards (2026-09-25): two silent-wrong-result classes are now
caught before any R runs —
  - survival event coding (SPSS-style 1=删失/2=事件 flips every HR);
  - multi-class outcomes bound to binary-only engines (glm / ROC).

Projection visibility: the model must see R's real error (stderr tail,
4000-char error budget) or every repair round is guesswork.
"""

from __future__ import annotations

from agent.analysis.input_validation import joint_input_issues
from agent.analysis.model_projection import ModelResultProjector


def _profile(columns: list[dict]) -> dict:
    return {"columns": columns}


def test_survival_event_coding_1_2_is_rejected_with_guidance():
    profile = _profile(
        [
            {"name": "event", "levels": ["1", "2"], "unique_count": 2},
            {"name": "time", "unique_count": 200},
        ]
    )
    issues = joint_input_issues(
        {"event": "event", "followup_time": "time"},
        template_id="survival_km_cox",
        dataset_profile=profile,
    )
    assert "event" in issues
    assert "0=删失、1=事件" in issues["event"]


def test_survival_event_coding_0_1_passes():
    profile = _profile(
        [
            {"name": "event", "levels": ["0", "1"], "unique_count": 2},
        ]
    )
    issues = joint_input_issues(
        {"event": "event"},
        template_id="survival_km_cox",
        dataset_profile=profile,
    )
    assert "event" not in issues


def test_multiclass_outcome_is_rejected_for_glm_with_redirect():
    profile = _profile(
        [
            {"name": "outcome3", "levels": ["轻型", "中型", "重型"], "unique_count": 3},
        ]
    )
    issues = joint_input_issues(
        {"outcome": "outcome3"},
        template_id="regression_glm",
        dataset_profile=profile,
    )
    assert "outcome" in issues
    assert "多分类" in issues["outcome"]


def test_binary_outcome_passes_glm_guard():
    profile = _profile(
        [
            {"name": "outcome", "levels": ["0", "1"], "unique_count": 2},
        ]
    )
    issues = joint_input_issues(
        {"outcome": "outcome"},
        template_id="regression_glm",
        dataset_profile=profile,
    )
    assert "outcome" not in issues


def test_guards_do_not_fire_without_profile_information():
    issues = joint_input_issues(
        {"event": "event", "outcome": "outcome"},
        template_id="survival_km_cox",
        dataset_profile=_profile([]),
    )
    assert issues == {}


# ── Projection error visibility ─────────────────────────────────────────────


def test_stderr_tail_is_exposed_and_tail_kept():
    filler = "starting analysis\r\n" * 300
    real_error = "Error in model.frame.default(...): object 'exposure_v' not found"
    payload = {
        "ok": False,
        "stderr": filler + real_error,
        "execution": {"status": "failed", "error": "R execution timed out after 600s"},
    }
    projected = ModelResultProjector().project(payload)

    tail = projected.get("stderr_tail") or ""
    assert "stderr_tail" in projected, "stderr must reach the model (redacted tail)"
    assert real_error in tail
    assert len(tail) <= ModelResultProjector.MAX_ERROR_FIELD_CHARS + 1
    # execution.error keeps its tail too (timeout reason arrives at the end).
    assert "timed out" in (projected.get("execution") or {}).get("error", "")


def test_error_fields_use_the_large_tail_budget():
    long_error = "E" * 600 + " | the actual failure"
    payload = {"ok": False, "execution": {"status": "failed", "error": long_error}}
    projected = ModelResultProjector().project(payload)
    error_text = (projected.get("execution") or {}).get("error", "")
    assert "the actual failure" in error_text


def test_survival_event_guard_reads_production_profile_shape():
    """Production profiles keep levels in local_semantics, not on the column.

    The first version of the guard read column["levels"] only — a shape no
    production caller produces — making it dead code (fixtures lied).
    """
    profile = {
        "columns": [
            {"name": "event", "unique_count": 2, "semantic_type": "categorical"},
            {"name": "time", "unique_count": 200, "semantic_type": "numeric"},
        ],
        "local_semantics": {"levels": {"event": ["1", "2"]}},
    }
    issues = joint_input_issues(
        {"event": "event", "followup_time": "time"},
        template_id="survival_km_cox",
        dataset_profile=profile,
    )
    assert "event" in issues
    assert "SPSS" in issues["event"]


def test_invalid_joint_inputs_has_a_readiness_route():
    from agent.analysis.readiness import (
        _ISSUE_ROUTES,
        RUNTIME_PREFLIGHT_ISSUE_CODES,
        uncovered_runtime_issue_codes,
    )

    assert "invalid_joint_inputs" in RUNTIME_PREFLIGHT_ISSUE_CODES
    assert _ISSUE_ROUTES["invalid_joint_inputs"] == "resolve_inputs"
    assert uncovered_runtime_issue_codes() == ()
