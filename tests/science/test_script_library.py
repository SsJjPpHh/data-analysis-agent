# -*- coding: utf-8 -*-
"""Deterministic script library for curated templates.

Covers the architectural fix for "one template = one method = one engine":
curated templates execute a reviewed, versioned R script (their method
family's statistical engine) before the LLM is consulted, instead of relying
on the model to improvise statistically known code every run.

Unit tests cover rendering; the parameterized integration test runs each
wired engine through the real execution machinery against local R (when
available) and asserts the portfolio validator accepts every role.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, RequiredOutput, ResolvedInput
from agent.analysis import script_library
from agent.agent import BusinessAgent


def _resolved(input_id: str, kind: str, value) -> ResolvedInput:
    return ResolvedInput(
        input_id=input_id,
        label=input_id,
        kind=kind,  # type: ignore[arg-type]
        value=value,
        provenance="dataset",
    )


def _plan(template_id: str, resolved: dict, dataset_id: str = "fixture") -> AnalysisPlan:
    from agent.analysis.method_policy import SUPPORTED_DESIGNS
    resolved = dict(resolved)
    if template_id in SUPPORTED_DESIGNS:
        resolved.setdefault("design_scope", _resolved("design_scope", "specification", SUPPORTED_DESIGNS[template_id]))
    if template_id == "diagnostic_roc":
        resolved.setdefault("positive_class", _resolved("positive_class", "level", "1"))
        resolved.setdefault("score_direction", _resolved("score_direction", "specification", "higher_positive"))
    fixture_inputs = {
        "age_standardization": {"standard_population": ("column", "standard")},
        "propensity_clinical": {"treatment_level": ("level", "B"), "reference_group": ("level", "A"), "positive_class": ("level", "1")},
        "clinical_doubly_robust_effect": {"treatment_level": ("level", "B"), "reference_group": ("level", "A")},
        "attributable_fraction": {"treatment_level": ("level", "B"), "reference_group": ("level", "A"), "positive_class": ("level", "1")},
        "negative_control_sensitivity": {"treatment_level": ("level", "B"), "reference_group": ("level", "A"), "positive_class": ("level", "1"), "negative_positive_class": ("level", "M")},
        "did_policy": {"treatment_level": ("level", "B"), "reference_group": ("level", "A")},

        "public_vaccine_test_negative": {"treatment_level": ("level", "B"), "reference_group": ("level", "A"), "positive_class": ("level", "1")},
        "trial_multiplicity_gatekeeping": {"outcome": ("column", "hypothesis"), "raw_p_value": ("column", "p"), "testing_hierarchy": ("column", "family")},
    }
    for key, (kind, value) in fixture_inputs.get(template_id, {}).items():
        resolved.setdefault(key, _resolved(key, kind, value))
    template = AnalysisCatalog().get(template_id)
    from agent.analysis.contract_conditions import conditions_match
    for spec in template.input_specs:
        if spec.default is not None and spec.id not in resolved:
            resolved[spec.id] = _resolved(spec.id, spec.kind, spec.default)
    return AnalysisPlan(
        plan_id=f"scripted-{template_id}",
        source="template",
        dataset_id=dataset_id,
        intent=template.title,
        category=template.category,
        method_family=template.family,
        scenario=template.scenario,
        variable_bindings={},
        packages=template.packages,
        prerequisites=template.prerequisites,
        assumptions=template.assumptions,
        diagnostics=template.diagnostics,
        required_outputs=tuple(o for o in template.required_outputs if conditions_match(o.conditions, resolved)),
        resource_class=template.resource_class,
        resolved_inputs=resolved,
        template_id=template.id,
        workspace_generation="scripted-generation",
        profile_revision="scripted-profile",
    )


def _rcs_plan(dataset_id: str = "rcs-fixture") -> AnalysisPlan:
    return _plan(
        "clinical_restricted_cubic_spline",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "exposure": _resolved("exposure", "column", "age"),
            "covariates": _resolved("covariates", "columns", ["sex"]),
            "reference_value": _resolved("reference_value", "parameter", 50.0),
        },
        dataset_id,
    )


def test_rcs_template_has_a_deterministic_script():
    plan = _rcs_plan()
    assert script_library.has_script(plan)
    assert "clinical_restricted_cubic_spline" in script_library.scripted_template_ids()


def test_render_accesses_only_resolved_inputs():
    plan = _rcs_plan()
    script = script_library.render_script(plan)

    assert 'baa_plan_input("outcome")' in script
    assert 'baa_plan_input("exposure")' in script
    assert 'baa_plan_input("covariates")' in script
    assert 'baa_plan_input("reference_value")' in script
    assert 'baa_plan_input("group")' not in script  # not resolved in this plan
    assert "baa_table(" in script and "baa_plot(" in script and "baa_text(" in script
    for role in ("primary_table", "diagnostic_plot", "manuscript_results"):
        assert f'"{role}"' in script
    assert "system(" not in script and "download.file" not in script


def test_all_wired_engines_have_registry_entries():
    from agent.analysis.skeleton_cores import ENGINES

    for template_id in script_library.scripted_template_ids():
        entry = script_library._WIRING[template_id]
        assert entry[0] in ENGINES or entry[0] == "rcs_dose_response"


def test_fixture_scripts_have_explicit_wiring_and_unwired_entries_do_not_fallback():
    """Fixtures require explicit method wiring; absent wiring cannot borrow an unrelated engine."""
    scripted = set(script_library.scripted_template_ids())
    for template_id in sorted(_ENGINE_PLANS):
        assert template_id in scripted, f"{template_id} has a fixture but no wiring"

    catalog = AnalysisCatalog().snapshot().templates
    unwired = [t.id for t in catalog if t.id not in scripted]
    for template_id in unwired:
        plan = _plan(template_id, {})
        assert not script_library.has_script(plan), template_id
        assert script_library.render_script(plan) is None, template_id


def test_tool_call_reasoning_is_preserved_for_each_provider_protocol_message():
    """DeepSeek thinking mode requires reasoning_content on ALL tool-call
    assistant messages (truncated to 2000 chars to limit bloat)."""
    history = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": " older reasoning kept but truncated",
            "tool_calls": [
                {"id": "call-0", "type": "function", "function": {"name": "run_r_code", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-0", "content": '{"result": {"ok": true}}'},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": " latest reasoning that DeepSeek needs",
            "tool_calls": [
                {"id": "call-1", "type": "function", "function": {"name": "run_r_code", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"result": {"ok": true}}'},
    ]
    client = _Client([_text_stream("最终结论")])
    agent = _StubbedRAgent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        reasoning_effort="max",
        workspace_id="",
    )

    events = list(agent.run("继续", history))

    sent = client.chat.completions.kwargs[0]["messages"]
    assistants = [m for m in sent if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")]
    # 全部 tool-call assistant 保留 reasoning_content（截断到 2000 字符以内）
    assert len(assistants) == 2
    assert assistants[0].get("reasoning_content") == " older reasoning kept but truncated"
    assert assistants[1].get("reasoning_content") == " latest reasoning that DeepSeek needs"
    assert events[-1] == {"type": "done"}


def test_scripted_template_executes_script_before_first_llm_call():
    """Round 1 runs the deterministic script; the model is consulted only
    afterwards to interpret results and repair genuine gaps."""
    plan = _rcs_plan()
    executed = []

    class _Agent(_StubbedRAgent):
        def _analysis_dataset_profile(self):
            return {"dataset_id": plan.dataset_id}

        def _tool_run_r_code(self, **kwargs):
            executed.append(str(kwargs.get("code", "")))
            return json.dumps(
                {
                    "result": {"ok": True, "tables": [], "figures": [], "texts": [], "models": []},
                    "execution": {"status": "succeeded", "dataset_id": plan.dataset_id},
                }
            )

    client = _Client([_text_stream("结果解读")])
    agent = _Agent(
        client=client,
        model="deepseek-flash",
        enable_thinking=True,
        workspace_id="",
        analysis_plan=plan,
    )

    events = list(agent.run("分析", []))

    assert len(executed) == 1
    assert executed[0] == script_library.render_script(plan)
    assert len(client.chat.completions.kwargs) >= 1
    first_messages = client.chat.completions.kwargs[0]["messages"]
    assert any(
        isinstance(m, dict) and "[PRE-EXECUTED TEMPLATE SCRIPT]" in str(m.get("content", ""))
        for m in first_messages
    )
    assert any(e.get("type") == "tool_audit" and e.get("tool") == "run_r_code" for e in events)
    assert events[-1] == {"type": "done"}


# ── Simulated model responses for template orchestration ────────────────────


def _text_stream(content: str, reasoning: str | None = None):
    def __iter__(self):
        delta = SimpleNamespace(
            content=content,
            reasoning_content=reasoning,
            tool_calls=None,
        )
        yield SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(finish_reason="stop", delta=delta)],
        )

    return type("_Stream", (), {"__iter__": __iter__})


class _ScriptedCompletions:
    """Return queued streams; record every create() kwargs for assertions."""

    def __init__(self, script):
        self.script = list(script)
        self.kwargs = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item()


class _Client:
    def __init__(self, script):
        self.chat = SimpleNamespace(completions=_ScriptedCompletions(script))


class _StubbedRAgent(BusinessAgent):
    def _analysis_plan_allows_r(self):
        return True, ""

    def _tool_run_r_code(self, **_kwargs):
        return json.dumps({"result": {"ok": True, "tables": [], "figures": [], "texts": [], "models": []}})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _s: None)


# ── Template prompt contract ───────────────────────────────────────────────


def test_template_plan_prompt_carries_one_pass_fast_path():
    from agent.agent import _analysis_plan_system_section

    section = _analysis_plan_system_section(_rcs_plan())

    assert "[TEMPLATE EXECUTION FAST PATH]" in section
    assert "single run_r_code call" in section


def test_conversation_plan_prompt_has_no_template_fast_path():
    from agent.agent import _analysis_plan_system_section
    import dataclasses

    plan = dataclasses.replace(_rcs_plan(), source="conversation")
    assert "[TEMPLATE EXECUTION FAST PATH]" not in _analysis_plan_system_section(plan)


# ── Real-R engine verification ───────────────────────────────────────────────


def _fixture_csv(path: Path) -> Path:
    import math

    rows = ["id,age,sex,group,outcome,bp,time,event,score,pre,post,item1,item2,item3,item4,severity,w,cluster,screen"]
    for i in range(1, 121):
        age = 30 + (i * 7) % 50
        sex = "F" if i % 2 else "M"
        group = "A" if i % 3 else "B"
        logit = -8.0 + 0.28 * (age - 30) - 0.0032 * (age - 30) ** 2 + (0.4 if group == "B" else 0.0)
        p = 1.0 / (1.0 + math.exp(-logit))
        outcome = 1 if (i * 37 % 100) / 100.0 < p else 0
        bp = round(110 + (age - 30) * 0.8 + (6 if group == "B" else 0) + (i % 11), 1)
        time = round(6 + (i * 13) % 30, 1)
        event = 1 if (i * 7 % 10) < 6 else 0
        score = round(p * 100 + ((i * 17) % 21) - 10, 1)
        pre = round(50 + age * 0.3 + (i % 9), 1)
        post = round(pre - 2.5 + ((i * 5) % 7) - 3, 1)
        import math as _m2
        it = lambda k: 1 + int(1.5 + 1.4 * _m2.sin(i * 0.7 + k * 1.1)) % 4
        severity = 1 + (1 if bp > 122 else 0) + (1 if outcome == 1 else 0)
        w = round(1 + (i % 5) * 0.2, 1)
        cluster = f"c{i % 8}"
        screen = 1 if score > 12 else 0
        rows.append(
            f"s{i},{age},{sex},{group},{outcome},{bp},{time},{event},{score},{pre},{post},"
            f"{it(3)},{it(5)},{it(7)},{it(2)},{severity},{w},{cluster},{screen}"
        )
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _pvals_csv(path: Path) -> Path:
    rows = ["p1,p2,p3"]
    vals = [(0.001, 0.012, 0.302), (0.023, 0.044, 0.118), (0.154, 0.233, 0.501),
            (0.010, 0.090, 0.744), (0.312, 0.045, 0.063), (0.041, 0.512, 0.090),
            (0.002, 0.301, 0.441), (0.078, 0.031, 0.219), (0.500, 0.601, 0.030),
            (0.031, 0.128, 0.912)]
    for a, b, c in vals:
        rows.append(f"{a},{b},{c}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _long_csv(path: Path) -> Path:
    rows = ["id,visit,score,group,sex,z,policy_time"]
    for s in range(1, 61):
        base = 40 + (s * 11) % 20
        group = "A" if s % 3 else "B"
        sex = "F" if s % 2 else "M"
        z_instr = round(2 + (s % 5) * 0.5, 1)
        for v in (1, 2, 3):
            score = round(base + (v - 1) * 2.5 + ((s * 7 + v * 3) % 5) - 2 + (1.5 if (group == "B" and v >= 2) else 0), 1)
            rows.append(f"s{s},{v},{score},{group},{sex},{z_instr},{2}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _iv_csv(path: Path) -> Path:
    import csv
    import random
    rng = random.Random(872)
    rows = []
    for i in range(100):
        z = rng.gauss(0, 1)
        exposure = .8 * z + rng.gauss(0, 1)
        rows.append(dict(id=f"s{i // 4}", visit=i % 4, z=z, exposure=exposure,
                         score=2*exposure + rng.gauss(0, 1)))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _agestd_csv(path: Path) -> Path:
    rows = ["age_group,events,denom,group,standard"]
    groups = {"A": [12, 34, 90, 210], "B": [18, 40, 110, 240]}
    bands = ["30-39", "40-49", "50-59", "60+"]
    for g, evs in groups.items():
        for band, ev, population in zip(bands, evs, [1000, 2000, 3000, 4000]):
            rows.append(f"{band},{ev},{ev * 8},{g},{population}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _rater_csv(path: Path) -> Path:
    rows = ["id,rater,rating"]
    for s in range(1, 41):
        base = s % 3
        r2 = base if (s * 5 % 7) else (base + 1) % 3
        rows.append(f"s{s},r1,{base}")
        rows.append(f"s{s},r2,{r2}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _series_csv(path: Path) -> Path:
    import math as _m
    rows = ["t,y"]
    for t in range(1, 73):
        y = round(50 + 8 * _m.sin(2 * _m.pi * t / 4) + t * 0.15 + ((t * 13) % 5) - 2, 2)
        rows.append(f"{t},{y}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _meta_csv(path: Path) -> Path:
    studies = [(0.50, 0.20, 2012), (0.30, 0.25, 2014), (0.70, 0.30, 2015), (0.20, 0.22, 2016),
               (0.60, 0.28, 2017), (0.40, 0.18, 2018), (0.55, 0.24, 2019), (0.35, 0.26, 2020)]
    rows = ["study,logor,se,year,events,n"]
    for i, (lor, se, yr) in enumerate(studies, 1):
        n = 40 + i * 12
        events = max(1, int(round(n * (0.10 + 0.02 * i))))
        rows.append(f"study{i},{lor},{se},{yr},{events},{n}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _clinical_csv(path: Path) -> Path:
    return _fixture_csv(path)


def _cluster_trial_csv(path: Path) -> Path:
    import csv
    _fixture_csv(path)
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields, rows = reader.fieldnames, list(reader)
    for row in rows:
        row["group"] = "A" if int(row["cluster"][1:]) % 2 else "B"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _gatekeeping_csv(path: Path) -> Path:
    path.write_text("hypothesis,p,family\nH1,.001,1\nH2,.01,2\nH3,.2,2\n", encoding="utf-8")
    return path


def _ml_csv(path: Path) -> Path:
    """Both classes need enough observations for stratified nested CV."""
    import csv
    import math

    _fixture_csv(path)
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = list(reader)
    for i, row in enumerate(rows):
        row["outcome"] = str(int(math.sin(i * .7) + (float(row["age"]) - 55) / 30 > 0))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


_ENGINE_DATASETS = {
    "cluster_rct_analysis": _cluster_trial_csv,
    "trial_multiplicity_gatekeeping": _gatekeeping_csv,
    "mixed_repeated": _long_csv,
    "tabular_ml_workflow": _ml_csv,
    "prognostic_model": _ml_csv,
    "pairwise_meta": _meta_csv,
    "interrater_agreement": _rater_csv,
    "forecasting": _series_csv,
    "did_policy": _long_csv,
    "iv_panel_public_health": _iv_csv,
    "evidence_meta_regression": _meta_csv,
    "interrupted_time_series": _series_csv,
    "evidence_proportion_rate_meta": _meta_csv,
    "age_standardization": _agestd_csv,
}


try:
    from r_runtime.environment import HostREnvironment, REnvironmentError

    _host = HostREnvironment.locate()
    _verified = _host.prepare()
    _rscript = str(_verified.rscript)
    _runtime_env = _host.process_environment(verified=_verified)
except REnvironmentError as exc:
    if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
        raise RuntimeError("Scientific reference tests require a working host R") from exc
    _rscript = None
    _runtime_env = {}

local_rscript_required = pytest.mark.skipif(_rscript is None, reason="Local R is unavailable")


_ENGINE_PLANS = {
    "clinical_restricted_cubic_spline": lambda: _rcs_plan(),
    "publication_table": lambda: _plan(
        "publication_table",
        {
            "group": _resolved("group", "column", "group"),
            "continuous_variables": _resolved("continuous_variables", "columns", ["age", "bp"]),
            "categorical_variables": _resolved("categorical_variables", "columns", ["sex"]),
        },
    ),
    "group_tests": lambda: _plan(
        "group_tests",
        {
            "group": _resolved("group", "column", "group"),
            "continuous_outcomes": _resolved("continuous_outcomes", "columns", ["age", "bp"]),
            "categorical_outcomes": _resolved("categorical_outcomes", "columns", ["sex"]),
        },
    ),
    "regression_glm": lambda: _plan(
        "regression_glm",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "exposure": _resolved("exposure", "column", "age"),
            "covariates": _resolved("covariates", "columns", ["sex", "group"]),
        },
    ),
    "survival_km_cox": lambda: _plan(
        "survival_km_cox",
        {
            "followup_time": _resolved("followup_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "exposure": _resolved("exposure", "column", "group"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "diagnostic_roc": lambda: _plan(
        "diagnostic_roc",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "diagnostic_score": _resolved("diagnostic_score", "column", "score"),
        },
    ),
    "correlation_agreement": lambda: _plan(
        "correlation_agreement",
        {
            "continuous_variables": _resolved("continuous_variables", "columns", ["bp", "age"]),
        },
    ),
    "effect_size_precision": lambda: _plan(
        "effect_size_precision",
        {
            "outcome": _resolved("outcome", "column", "bp"),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "ancova_adjusted_groups": lambda: _plan(
        "ancova_adjusted_groups",
        {
            "outcome": _resolved("outcome", "column", "bp"),
            "group": _resolved("group", "column", "group"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "bootstrap_permutation_inference": lambda: _plan(
        "bootstrap_permutation_inference",
        {
            "outcome": _resolved("outcome", "column", "bp"),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "propensity_clinical": lambda: _plan(
        "propensity_clinical",
        {
            "group": _resolved("group", "column", "group"),
            "outcome": _resolved("outcome", "column", "outcome"),
            "covariates": _resolved("covariates", "columns", ["age", "sex"]),
        },
    ),
    "mediation_moderation": lambda: _plan(
        "mediation_moderation",
        {
            "predictor": _resolved("predictor", "column", "age"),
            "mediator": _resolved("mediator", "column", "bp"),
            "outcome": _resolved("outcome", "column", "score"),
        },
    ),
    "categorical_ordinal_models": lambda: _plan(
        "categorical_ordinal_models",
        {
            "outcome": _resolved("outcome", "column", "severity"),
            "exposure": _resolved("exposure", "column", "group"),
            "analysis_method": _resolved("analysis_method", "specification", "multinomial"),
        },
    ),
    "mixed_repeated": lambda: _plan(
        "mixed_repeated",
        {
            "outcome": _resolved("outcome", "column", "score"),
            "time": _resolved("time", "column", "visit"),
            "subject_id": _resolved("subject_id", "column", "id"),
            "fixed_effects": _resolved("fixed_effects", "columns", ["group"]),
        },
    ),
    "noninferiority_equivalence": lambda: _plan(
        "noninferiority_equivalence",
        {
            "outcome": _resolved("outcome", "column", "bp"),
            "group": _resolved("group", "column", "group"),
            "noninferiority_margin": _resolved("noninferiority_margin", "parameter", 5.0),
            "analysis_population": _resolved("analysis_population", "column", "sex"),
            "analysis_population_level": _resolved("analysis_population_level", "level", "F"),
            "reference_group": _resolved("reference_group", "level", "A"),
            "outcome_direction": _resolved("outcome_direction", "specification", "higher_better"),
        },
    ),
    "pairwise_meta": lambda: _plan(
        "pairwise_meta",
        {
            "study_id": _resolved("study_id", "column", "study"),
            "effect_estimate": _resolved("effect_estimate", "column", "logor"),
            "standard_error": _resolved("standard_error", "column", "se"),
        },
    ),
    "subgroup_sensitivity_basic": lambda: _plan(
        "subgroup_sensitivity_basic",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "exposure": _resolved("exposure", "column", "group"),
            "subgroup": _resolved("subgroup", "column", "sex"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "epi_measures": lambda: _plan(
        "epi_measures",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "exposure": _resolved("exposure", "column", "group"),
            "positive_class": _resolved("positive_class", "level", "1"),
            "reference_group": _resolved("reference_group", "level", "A"),
        },
    ),
    "interrater_agreement": lambda: _plan(
        "interrater_agreement",
        {
            "data_object": _resolved("data_object", "column", "id"),
            "rater_id": _resolved("rater_id", "column", "rater"),
            "measure": _resolved("measure", "column", "rating"),
        },
    ),
    "scale_reliability": lambda: _plan(
        "scale_reliability",
        {
            "categorical_variables": _resolved("categorical_variables", "columns",
                ["item1", "item2", "item3", "item4"]),
        },
    ),
    "forecasting": lambda: _plan(
        "forecasting",
        {
            "time": _resolved("time", "column", "t"),
            "measure": _resolved("measure", "column", "y"),
            "frequency": _resolved("frequency", "parameter", 4),
        },
    ),
    "sample_size_power": lambda: _plan(
        "sample_size_power",
        {
            "effect_size": _resolved("effect_size", "parameter", 0.5),
            "alpha": _resolved("alpha", "parameter", 0.05),
            "power": _resolved("power", "parameter", 0.8),
        },
    ),
    "efa_factor": lambda: _plan(
        "efa_factor",
        {
            "categorical_variables": _resolved("categorical_variables", "columns",
                ["item1", "item2", "item3", "item4"]),
            "dimension": _resolved("dimension", "parameter", 1),
        },
    ),
    "cfa_sem": lambda: _plan(
        "cfa_sem",
        {
            "categorical_variables": _resolved("categorical_variables", "columns",
                ["item1", "item2", "item3", "item4"]),
            "latent_variables": _resolved("latent_variables", "specification",
                "F1 =~ item1 + item2 + item3 + item4"),
        },
    ),
    "did_policy": lambda: _plan(
        "did_policy",
        {
            "outcome": _resolved("outcome", "column", "score"),
            "treatment_group": _resolved("treatment_group", "column", "group"),
            "time": _resolved("time", "column", "visit"),
            "policy_start_time": _resolved("policy_start_time", "parameter", 2),
            "cluster_variable": _resolved("cluster_variable", "column", "id"),
        },
    ),
    "iv_panel_public_health": lambda: _plan(
        "iv_panel_public_health",
        {
            "outcome": _resolved("outcome", "column", "score"),
            "exposure": _resolved("exposure", "column", "exposure"),
            "instrumental_variable": _resolved("instrumental_variable", "column", "z"),
            "subject_id": _resolved("subject_id", "column", "id"),
            "time": _resolved("time", "column", "visit"),
        },
    ),
    "tabular_ml_workflow": lambda: _plan(
        "tabular_ml_workflow",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "features": _resolved("features", "columns", ["age", "bp", "score"]),
        },
    ),
    "evidence_meta_regression": lambda: _plan(
        "evidence_meta_regression",
        {
            "study_id": _resolved("study_id", "column", "study"),
            "effect_estimate": _resolved("effect_estimate", "column", "logor"),
            "standard_error": _resolved("standard_error", "column", "se"),
            "covariates": _resolved("covariates", "columns", ["year"]),
        },
    ),
    "interrupted_time_series": lambda: _plan(
        "interrupted_time_series",
        {
            "time": _resolved("time", "column", "t"),
            "outcome": _resolved("outcome", "column", "y"),
            "intervention_time": _resolved("intervention_time", "parameter", 36),
        },
    ),
    "survey_weighted": lambda: _plan(
        "survey_weighted",
        {
            "outcome": _resolved("outcome", "column", "score"),
            "weight": _resolved("weight", "column", "w"),
            "strata": _resolved("strata", "column", "sex"),
            "cluster_variable": _resolved("cluster_variable", "column", "cluster"),
        },
    ),
    "cluster_rct_analysis": lambda: _plan(
        "cluster_rct_analysis",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "group": _resolved("group", "column", "group"),
            "cluster_id": _resolved("cluster_id", "column", "cluster"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "public_cost_effectiveness_psa": lambda: _plan(
        "public_cost_effectiveness_psa",
        {
            "strategy": _resolved("strategy", "column", "group"),
            "cost": _resolved("cost", "column", "bp"),
            "health_outcome": _resolved("health_outcome", "column", "score"),
            "threshold": _resolved("threshold", "parameter", 20000),
            "parameter_distributions": _resolved("parameter_distributions", "specification", "bootstrap_individual"),
        },
    ),
    "evidence_proportion_rate_meta": lambda: _plan(
        "evidence_proportion_rate_meta",
        {
            "study_id": _resolved("study_id", "column", "study"),
            "event_count": _resolved("event_count", "column", "events"),
            "sample_size_person_time": _resolved("sample_size_person_time", "column", "n"),
        },
    ),
    "clinical_rmst_non_ph": lambda: _plan(
        "clinical_rmst_non_ph",
        {
            "followup_time": _resolved("followup_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "group": _resolved("group", "column", "group"),
            "truncation_time": _resolved("truncation_time", "parameter", 30),
        },
    ),
    "clinical_recurrent_events": lambda: _plan(
        "clinical_recurrent_events",
        {
            "subject_id": _resolved("subject_id", "column", "id"),
            "event_time": _resolved("event_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "time_dependent_roc": lambda: _plan(
        "time_dependent_roc",
        {
            "followup_time": _resolved("followup_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "prediction": _resolved("prediction", "column", "score"),
            "evaluation_time": _resolved("evaluation_time", "parameter", 20),
        },
    ),
    "clinical_cutpoint_maxstat": lambda: _plan(
        "clinical_cutpoint_maxstat",
        {
            "analysis_method": _resolved("analysis_method", "specification", "survival"),
            "measure": _resolved("measure", "column", "score"),
            "followup_time": _resolved("followup_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "clinical_subgroup_interaction": lambda: _plan(
        "clinical_subgroup_interaction",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "group": _resolved("group", "column", "group"),
            "subgroup": _resolved("subgroup", "column", "sex"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "extended_survival": lambda: _plan(
        "extended_survival",
        {
            "followup_time": _resolved("followup_time", "column", "time"),
            "event": _resolved("event", "column", "event"),
            "time_dependent_variable": _resolved("time_dependent_variable", "column", "bp"),
            "strata": _resolved("strata", "column", "group"),
        },
    ),
    "competing_risk": lambda: _plan(
        "competing_risk",
        {
            "followup_time": _resolved("followup_time", "column", "time"),
            "event_type": _resolved("event_type", "column", "severity"),
            "target_event_level": _resolved("target_event_level", "parameter", 3),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "prognostic_model": lambda: _plan(
        "prognostic_model",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "features": _resolved("features", "columns", ["age", "bp", "score"]),
        },
    ),
    "robust_outlier_analysis": lambda: _plan(
        "robust_outlier_analysis",
        {
            "continuous_variables": _resolved("continuous_variables", "columns", ["age", "bp"]),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "data_quality": lambda: _plan("data_quality", {}),
    "data_import_workflow": lambda: _plan("data_import_workflow", {}),
    "data_cleaning_recoding": lambda: _plan("data_cleaning_recoding", {}),
    "automated_eda_report": lambda: _plan("automated_eda_report", {}),
    "large_data_engine": lambda: _plan("large_data_engine", {}),
    "age_standardization": lambda: _plan(
        "age_standardization",
        {
            "age_group": _resolved("age_group", "column", "age_group"),
            "event_count": _resolved("event_count", "column", "events"),
            "denominator": _resolved("denominator", "column", "denom"),
            "group": _resolved("group", "column", "group"),
        },
    ),
    "attributable_fraction": lambda: _plan(
        "attributable_fraction",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "exposure": _resolved("exposure", "column", "group"),
        },
    ),
    "negative_control_sensitivity": lambda: _plan(
        "negative_control_sensitivity",
        {
            "exposure": _resolved("exposure", "column", "group"),
            "outcome": _resolved("outcome", "column", "outcome"),
            "negative_control_variable": _resolved("negative_control_variable", "column", "sex"),
            "covariates": _resolved("covariates", "columns", ["age"]),
        },
    ),
    "clinical_doubly_robust_effect": lambda: _plan(
        "clinical_doubly_robust_effect",
        {
            "group": _resolved("group", "column", "group"),
            "outcome": _resolved("outcome", "column", "bp"),
            "covariates": _resolved("covariates", "columns", ["age", "sex"]),
        },
    ),
    "missing_visual_diagnostics": lambda: _plan(
        "missing_visual_diagnostics",
        {
            "variables_with_missingness": _resolved("variables_with_missingness", "columns", ["bp"]),
        },
    ),
    "group_sequential": lambda: _plan(
        "group_sequential",
        {
            "information_time": _resolved("information_time", "specification", [0.3, 0.6, 1]),
            "alpha": _resolved("alpha", "parameter", 0.025),
            "effect_size": _resolved("effect_size", "parameter", 0.5),
        },
    ),
    "public_screening_evaluation": lambda: _plan(
        "public_screening_evaluation",
        {
            "outcome": _resolved("outcome", "column", "screen"),

            "outcome_2": _resolved("outcome_2", "column", "outcome"),
            "positive_class": _resolved("positive_class", "level", "1"),
        },
    ),
    "public_vaccine_test_negative": lambda: _plan(
        "public_vaccine_test_negative",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "vaccination_status": _resolved("vaccination_status", "column", "group"),
            "covariates": _resolved("covariates", "columns", ["age", "sex"]),
        },
    ),
    "trial_multiplicity_gatekeeping": lambda: _plan(
        "trial_multiplicity_gatekeeping",
        {},
    ),
    "health_inequality": lambda: _plan(
        "health_inequality",
        {
            "outcome": _resolved("outcome", "column", "outcome"),
            "socioeconomic_rank_variable": _resolved("socioeconomic_rank_variable", "column", "age"),
        },
    ),
}


def _complex_method_csv(path):
    import csv
    import random
    rng = random.Random(812)
    tid = path.stem
    if tid == "staggered_did_panel":
        rows = [{"id": i, "time": t, "cohort": 0 if i<=60 else 3 if i<=120 else 5,
                 "y": i*.01+t*.5+rng.gauss(0,.1)+(2 if (i>60 and t>=(3 if i<=120 else 5)) else 0)}
                for i in range(1,181) for t in range(1,7)]
    elif tid == "clinical_target_trial_emulation":
        rows = [{"id": i, "baseline": 0, "a": int(i>200), "x": (i-1)%200/50-2,
                 "y": int((i-1)%200 < (40 if i<=200 else 80))} for i in range(1,401)]
    else:
        rows = []
        for i in range(120):
            x, z = rng.gauss(0,1), rng.gauss(0,1)
            rows.append({"x": x if i%4 else "", "z": z, "y": 2*x+z+rng.gauss(0,1)})
    with path.open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    return path


_COMPLEX_INPUTS = {
    "missing_imputation": {"outcome":"y","variables_with_missingness":["x"],"predictors":["x","z"],"imputations":5,"iterations":5},
    "multiple_imputation_pooling": {"outcome":"y","imputation_variables":["x"],"predictors":["x","z"],"imputations":5,"iterations":5},
    "trial_simon_two_stage": {"outcome":"y","null_response_rate":.2,"target_response_rate":.4,"alpha":.05,"power":.8,"design_criterion":"optimal"},
    "staggered_did_panel": {"subject_id":"id","time":"time","treatment_point":"cohort","outcome":"y"},
    "clinical_target_trial_emulation": {"subject_id":"id","time_zero":"baseline","treatment_strategy":"a","outcome":"y","covariates_2":["x"]},
}


def _complex_plan(tid):
    specs = {s.id:s for s in AnalysisCatalog().get(tid).input_specs}
    return _plan(tid,{k:_resolved(k,specs[k].kind,v) for k,v in _COMPLEX_INPUTS[tid].items()})


for _tid in _COMPLEX_INPUTS:
    _ENGINE_PLANS[_tid] = lambda tid=_tid: _complex_plan(tid)
    _ENGINE_DATASETS[_tid] = _complex_method_csv


def _run_engine_template(tmp_path: Path, template_id: str):
    from agent.tools.research import RToolsMixin
    from r_runtime.backends.local_rscript import LocalRscriptBackend
    from r_runtime.protocol import DatasetRef, RRunRequest

    data_path = _ENGINE_DATASETS.get(template_id, _clinical_csv)(tmp_path / f"{template_id}.csv")
    plan = _ENGINE_PLANS[template_id]()
    script = script_library.render_script(plan)
    assert script, f"{template_id}: no script rendered"
    executed = "\n".join(
        (
            RToolsMixin._worker_compatibility_prelude(),
            RToolsMixin._frozen_input_prelude(plan),
            RToolsMixin._frozen_user_code_block(script, plan),
            RToolsMixin._frozen_input_epilogue(plan),
        )
    )
    artifacts_dir = tmp_path / template_id
    result = LocalRscriptBackend(rscript_bin=_rscript, runtime_env=_runtime_env).run_code(
        RRunRequest(
            job_id=f"scripted-{template_id}",
            dataset=DatasetRef(
                dataset_id=plan.dataset_id,
                path=str(data_path),
                format="csv",
                name=data_path.name,
            ),
            code=executed,
            artifacts_dir=str(artifacts_dir),
            timeout_seconds=300,
        )
    )

    class _StampAgent(RToolsMixin):
        def __init__(self):
            self._analysis_plan = plan
            self._research_workspace_generation = plan.workspace_generation

    _StampAgent()._attach_analysis_plan_metadata(
        result,
        plan.dataset_id,
        job_id=f"scripted-{template_id}",
        artifacts_root=str(artifacts_dir),
    )
    return plan, result, artifacts_dir


def _missing_r_package_reason(result) -> str:
    """Return the package name when the run failed on a missing R package.

    Engines call ``pkg::fun`` directly, so an uninstalled package surfaces as
    R's "there is no package called 'x'" — either in the run error/stderr or
    inside the emitted failure text (the tryCatch wrapper swallows the error
    into an artifact).  Scanning both lets clean machines skip with a
    clear reason instead of failing red.
    """
    needles = [str(getattr(result, "error", "") or ""), str(getattr(result, "stderr", "") or "")]
    for collection in (result.tables, result.figures, result.texts, result.models):
        for artifact in collection:
            path = getattr(artifact, "path", "") or ""
            if path:
                try:
                    needles.append(Path(path).read_text(encoding="utf-8", errors="ignore")[:20000])
                except OSError:
                    continue
    match = re.search(r"there is no package called '([^']+)'", "\n".join(needles))
    return match.group(1) if match else ""


@local_rscript_required
@pytest.mark.parametrize("template_id", sorted(_ENGINE_PLANS))
def test_engine_script_passes_portfolio_validation(tmp_path, template_id):
    from agent.analysis.result_validation import ResultValidator

    plan, result, artifacts_dir = _run_engine_template(tmp_path, template_id)
    missing_package = _missing_r_package_reason(result)
    if missing_package and os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
        pytest.fail(f"Required scientific reference package is missing: {missing_package}")
    if missing_package:
        pytest.skip(f"R package not installed on this machine: {missing_package}")
    assert result.ok is True, f"{template_id} failed: {result.error}\n{result.stderr[-2000:]}"

    artifacts = [
        {
            "name": artifact.name,
            "kind": artifact.kind,
            "path": str(getattr(artifact, "path", "") or ""),
            "metadata": artifact.metadata or {},
            "source_tool": (artifact.metadata or {}).get("source_tool", "run_r_code"),
            "job_id": (artifact.metadata or {}).get("job_id", f"scripted-{template_id}"),
        }
        for collection in (result.tables, result.figures, result.texts, result.models)
        for artifact in collection
    ]
    execution = {
        "status": "succeeded",
        "error": "",
        "dataset_id": plan.dataset_id,
        "plan_id": plan.plan_id,
        "diagnostic_status": {},
    }
    assessment = ResultValidator().validate(plan, execution, artifacts)
    assert assessment.missing_roles == (), f"{template_id} missing: {assessment.missing_roles}"
    assert not assessment.invalid_roles, f"{template_id} invalid: {assessment.invalid_roles}"


@local_rscript_required
def test_rcs_engine_script_passes_portfolio_validation(tmp_path):
    plan, result, artifacts_dir = _run_engine_template(tmp_path, "clinical_restricted_cubic_spline")
    assert result.ok is True
    produced = {a.name for c in (result.tables, result.texts) for a in c}
    assert {"primary_table", "manuscript_results"} <= produced
