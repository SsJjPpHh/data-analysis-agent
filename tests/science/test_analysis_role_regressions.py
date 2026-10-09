"""Regression coverage for editable, frozen scientific analysis inputs."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.agent import BusinessAgent
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.memory import DatasetRoleMemory
from agent.analysis.models import AnalysisPlan, RequiredOutput, ResolvedInput
from agent.analysis.preparation import AnalysisPreparer
from agent.tools.research import RToolsMixin


ROOT = Path(__file__).resolve().parents[2]
WORKBENCH = ROOT / "static" / "js" / "research_workbench.js"


def _column(name: str, semantic_type: str, *, unique_count: int, missing_pct: float = 0) -> dict:
    return {
        "name": name,
        "type": semantic_type,
        "semantic_type": semantic_type,
        "unique_count": unique_count,
        "missing_pct": missing_pct,
    }


def _table_one_profile(*, arbitrary_group: bool = False) -> dict:
    group_name = "x1" if arbitrary_group else "gender"
    group_type = "numeric" if arbitrary_group else "categorical"
    return {
        "dataset_id": "elsa",
        "format": "xlsx",
        "object_kind": "tbl_df/tbl/data.frame",
        "modality": "tabular",
        "representation": "data_frame",
        "workspace_generation": "generation-1",
        "profile_revision": "profile-1",
        "rows": 1_941,
        "columns": [
            _column(group_name, group_type, unique_count=2),
            _column("cvd", "categorical", unique_count=2),
            _column("age", "numeric", unique_count=72),
            _column("bmi", "numeric", unique_count=501),
        ],
    }


def _template_plan() -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="frozen-template-plan",
        source="template",
        dataset_id="elsa",
        intent="论文 Table 1",
        category="论文交付",
        method_family="publication_reporting",
        scenario="按分组比较基线特征",
        variable_bindings={"分组变量": "cvd"},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("baseline_table", "table", label="Table 1"),),
        resource_class="standard",
        resolved_inputs={
            "group": ResolvedInput("group", "分组变量", "column", "cvd", "user"),
            "categorical_variables": ResolvedInput(
                "categorical_variables",
                "分类基线变量",
                "columns",
                ["gender"],
                "inferred",
            ),
            "continuous_variables": ResolvedInput(
                "continuous_variables",
                "连续基线变量",
                "columns",
                ["age", "bmi"],
                "inferred",
            ),
        },
        template_id="publication_table",
        workspace_generation="generation-1",
        profile_revision="profile-1",
    )


def test_composite_tibble_class_is_still_a_tabular_dataset():
    class ProfileAgent:
        _analysis_dataset_profile = BusinessAgent._analysis_dataset_profile
        _research_workspace_generation = "generation-1"

        def _resolve_research_dataset(self, _dataset_id):
            return SimpleNamespace(
                dataset_id="elsa",
                format="xlsx",
                assets=(),
                profile={
                    "summary": {
                        "object_kind": "tbl_df/tbl/data.frame",
                        "modality": "tabular",
                        "representation": "data_frame",
                        "variables": [],
                    }
                },
            )

    assert ProfileAgent()._analysis_dataset_profile()["input_shape"] == "tabular"


def test_template_frozen_plan_cannot_be_replaced_by_conversation_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    class PlanAgent:
        _tool_set_analysis_plan = BusinessAgent._tool_set_analysis_plan

        def __init__(self):
            self._analysis_plan = _template_plan()
            self._workflow_run_spec = None
            self._session_id = "session-frozen-plan"

        def _analysis_dataset_profile(self):
            return _table_one_profile()

        def _tool_get_r_package_inventory(self, **_kwargs):
            return json.dumps({"recommended_candidate_packages": ["stats"]})

        def _tool_get_r_package_help(self, **_kwargs):
            return json.dumps({"ok": True, "results": []})

    with pytest.raises(ValueError, match="模板.*冻结|冻结.*模板"):
        PlanAgent()._tool_set_analysis_plan(
            {
                "intent": "改写当前 Table 1 计划",
                "category": "基础统计",
                "method_family": "classical_statistics",
                "scenario": "按 cvd 比较",
                "variable_bindings": {"group": "cvd"},
                "required_outputs": [
                    {"role": "baseline_table", "kind": "table", "label": "Table 1"}
                ],
            }
        )


def test_free_form_frozen_plan_cannot_be_replaced_during_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    class PlanAgent:
        _tool_set_analysis_plan = BusinessAgent._tool_set_analysis_plan

        def __init__(self):
            self._analysis_plan = replace(
                _template_plan(), source="conversation", template_id=""
            )

    with pytest.raises(ValueError, match="自由对话.*冻结|冻结.*自由对话"):
        PlanAgent()._tool_set_analysis_plan({"intent": "覆盖原分组"})


def test_table_one_role_memory_does_not_silently_reuse_prior_research_question():
    memory = DatasetRoleMemory()
    preparer = AnalysisPreparer(AnalysisCatalog(), memory)
    profile = _table_one_profile()

    first = preparer.prepare("publication_table", profile, {"group": "cvd"})
    assert first.status == "ready"
    assert first.plan is not None
    assert first.plan.resolved_inputs["group"].value == "cvd"
    assert first.plan.resolved_inputs["categorical_variables"].value == ["gender"]
    assert "cvd" not in first.plan.resolved_inputs["categorical_variables"].value

    second = preparer.prepare("publication_table", profile)
    assert second.status == "ready"
    assert second.plan is not None
    assert second.plan.resolved_inputs["group"].value == "gender"
    assert second.plan.resolved_inputs["group"].provenance != "memory"


def test_stale_user_variable_set_is_rebalanced_when_scalar_role_changes():
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        _table_one_profile(),
        {
            "group": "cvd",
            "categorical_variables": ["gender", "cvd"],
            "continuous_variables": ["age", "bmi"],
        },
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert prepared.plan.resolved_inputs["group"].value == "cvd"
    assert prepared.plan.resolved_inputs["categorical_variables"].value == ["gender"]
    assert any("cvd" in warning and "移除" in warning for warning in prepared.warnings)


def test_exhausted_auto_selected_set_is_reinferred_after_group_change():
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        _table_one_profile(),
        {
            "group": "cvd",
            "categorical_variables": ["cvd"],
            "continuous_variables": ["age", "bmi"],
        },
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert prepared.plan.resolved_inputs["categorical_variables"].value == ["gender"]


def test_legacy_hashed_template_input_id_is_migrated_to_the_readable_role():
    profile = {
        "dataset_id": "apc-data",
        "format": "csv",
        "object_kind": "data.frame",
        "rows": 120,
        "columns": [
            {"name": "age_band", "semantic_type": "numeric", "unique_count": 8},
            {"name": "calendar_period", "semantic_type": "categorical", "unique_count": 6},
            {"name": "cases", "semantic_type": "numeric", "unique_count": 50},
            {"name": "population", "semantic_type": "numeric", "unique_count": 60},
        ],
    }

    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "age_period_cohort",
        profile,
        {
            "age_group": "age_band",
            "role_40b8ab9b9055": "calendar_period",
            "outcome": "cases",
            "denominator": "population",
        },
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert prepared.plan.resolved_inputs["period"].value == "calendar_period"


def test_user_can_use_arbitrary_low_cardinality_numeric_column_as_group():
    result = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        _table_one_profile(arbitrary_group=True),
        {"group": "x1"},
    )

    assert result.status == "ready"
    assert result.plan is not None
    assert result.plan.resolved_inputs["group"].value == "x1"


def test_binding_guard_covers_complete_frozen_column_sets_not_only_group():
    plan = _template_plan()
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [
                    {"name": name} for name in ("gender", "cvd", "age", "bmi")
                ]
            }
        }
    )
    report = RToolsMixin._frozen_binding_report(
        'group_var <- "cvd"\nvars <- c("age", "cvd")\n'
        'baa_table_one("t1", dataset, by=group_var, vars=vars)',
        plan,
        dataset,
    )

    assert report["status"] == "failed"
    assert {
        item.get("input_id") for item in report["mismatches"]
    } & {"categorical_variables", "continuous_variables"}


def test_zero_missingness_does_not_plan_a_missingness_by_group_plot():
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        _table_one_profile(),
    )

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert "missingness_by_group_plot" not in {
        item.role for item in prepared.plan.output_portfolio
    }


def test_optional_reference_levels_are_not_synthesized_outside_catalog_contract():
    profile = _table_one_profile()
    profile["local_semantics"] = {
        "levels": {"gender": ["1", "2"], "cvd": ["0", "1"]}
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        profile,
    )

    assert all(
        item.input_id != "group_reference_level" for item in prepared.input_controls
    )
    with pytest.raises(ValueError, match="Unknown template inputs"):
        AnalysisPreparer(AnalysisCatalog()).prepare(
            "publication_table",
            profile,
            {"group": "cvd", "group_reference_level": "0"},
        )


def test_unconfirmed_numeric_category_labels_are_reported_not_invented():
    profile = _table_one_profile()
    for item in profile["columns"]:
        if item["name"] in {"gender", "cvd"}:
            item["numeric_coding"] = "labels_unconfirmed"

    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "publication_table",
        profile,
        {"group": "cvd"},
    )

    assert prepared.status == "ready"
    assert any(
        "gender" in warning and "cvd" in warning and "不推断" in warning
        for warning in prepared.warnings
    )


def test_complete_frozen_inputs_pass_through_the_trusted_r_accessor():
    plan = _template_plan()
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [
                    {"name": name} for name in ("gender", "cvd", "age", "bmi")
                ]
            }
        }
    )
    report = RToolsMixin._frozen_binding_report(
        'group_var <- baa_plan_input("group")\n'
        'categorical_vars <- baa_plan_input("categorical_variables")\n'
        'continuous_vars <- baa_plan_input("continuous_variables")\n'
        'baa_table_one("t1", dataset, by=group_var, '
        'vars=c(categorical_vars, continuous_vars), '
        'categorical=categorical_vars, continuous=continuous_vars)',
        plan,
        dataset,
    )

    assert report["status"] == "passed"
    assert report["missing_inputs"] == []


def test_frozen_inputs_allow_identity_preserving_character_normalization():
    plan = _template_plan()
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [
                    {"name": name} for name in ("gender", "cvd", "age", "bmi")
                ]
            }
        }
    )
    code_variants = (
        (
            'group_var <- as.character(baa_plan_input("group"))\n'
            'categorical_vars <- as.character('
            'baa_plan_input("categorical_variables"))\n'
            'continuous_vars <- as.character('
            'baa_plan_input("continuous_variables"))\n'
        ),
        (
            'group_var <- baa_plan_input("group")\n'
            'categorical_vars <- baa_plan_input("categorical_variables")\n'
            'continuous_vars <- baa_plan_input("continuous_variables")\n'
            'group_var <- as.character(group_var)\n'
            'categorical_vars <- as.character(categorical_vars)\n'
            'continuous_vars <- as.character(continuous_vars)\n'
        ),
    )
    table_call = (
        'baa_table_one("t1", dataset, by=group_var, '
        'vars=c(categorical_vars, continuous_vars), '
        'categorical=categorical_vars, continuous=continuous_vars)'
    )

    for code in code_variants:
        report = RToolsMixin._frozen_binding_report(code + table_call, plan, dataset)

        assert report["status"] == "passed"
        assert report["mismatches"] == []


def _rendered_meta_binding_fixture(*, scale="identity", tau="REML"):
    from agent.analysis.script_library import render_script

    profile = {
        "dataset_id": "meta-binding-regression",
        "format": "csv",
        "object_kind": "data.frame",
        "rows": 5,
        "columns": [
            _column("study", "categorical", unique_count=5),
            _column("effect", "numeric", unique_count=5),
            _column("se", "numeric", unique_count=4),
        ],
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "pairwise_meta", profile,
        {
            "study_id": "study", "effect_estimate": "effect",
            "standard_error": "se", "effect_scale": scale,
            "method_tau": tau,
        },
    )
    assert prepared.status == "ready" and prepared.plan is not None
    code = render_script(prepared.plan)
    assert code is not None
    dataset = SimpleNamespace(profile={"summary": {"variables": profile["columns"]}})
    return prepared.plan, code, dataset


@pytest.mark.parametrize("scale", ["identity", "log_ratio"])
@pytest.mark.parametrize("tau", ["REML", "DL"])
def test_rendered_meta_script_keeps_frozen_values_through_registered_helpers(scale, tau):
    plan, code, dataset = _rendered_meta_binding_fixture(scale=scale, tau=tau)
    assert 'scale_v <- .mt_select(.mt_get("scale_v","identity")' in code
    assert 'tau_v <- .mt_select(.mt_get("tau_v","REML")' in code

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "passed", report
    assert report["mismatches"] == []
    assert report["missing_inputs"] == []


@pytest.mark.parametrize(
    "tampering",
    ["helper_definition", "getter_symbol", "extra_assignment", "source_provenance"],
)
def test_meta_helper_names_do_not_authorize_modified_or_unregistered_code(tampering):
    plan, code, dataset = _rendered_meta_binding_fixture()
    if tampering == "helper_definition":
        code = code.replace("\n  value\n}\n.mt_integer", "\n  'log_ratio'\n}\n.mt_integer", 1)
        assert "'log_ratio'\n}\n.mt_integer" in code
    elif tampering == "getter_symbol":
        code = code.replace(
            '.mt_select(.mt_get("scale_v","identity")',
            '.mt_select(.mt_get("tau_v","identity")', 1,
        )
    elif tampering == "extra_assignment":
        code += '\nscale_v <- "log_ratio"\nresult <- scale_v\n'
    else:
        plan = replace(plan, source="conversation", template_id="")

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "failed", report
    assert any(item.get("reason") == "frozen_input_reassigned" for item in report["mismatches"])


def _rendered_catalog_binding_fixture(template_id):
    """Populate declared inputs to exercise the production renderer/guard.

    These synthetic names do not validate numerical applicability. Including
    optional inputs catches getter paths omitted by minimal plan fixtures.
    """
    from agent.analysis.script_library import render_script

    template = AnalysisCatalog().get(template_id)
    inputs = {}
    for spec in template.input_specs:
        if spec.default is not None:
            value = spec.default
        elif spec.choices:
            value = spec.choices[0]
        elif spec.kind == "column":
            value = f"{spec.id}_field"
        elif spec.kind == "columns":
            value = [f"{spec.id}_field_{i}" for i in range(max(spec.min_items, 1))]
        elif spec.kind == "level":
            value = "1"
        elif spec.kind == "parameter":
            value = 1
        elif spec.kind in {"dataset", "object"}:
            value = "synthetic-binding-dataset"
        else:
            value = "declared_specification"
        inputs[spec.id] = ResolvedInput(
            spec.id, spec.label, spec.kind, value, "user",
        )
    plan = replace(
        _template_plan(), template_id=template_id,
        variable_bindings={item.label: str(item.value) for item in inputs.values() if item.kind == "column"},
        resolved_inputs=inputs, packages=template.packages,
        required_outputs=template.required_outputs,
    )
    code = render_script(plan)
    assert code is not None
    return plan, code, SimpleNamespace(profile={})


@pytest.mark.parametrize("template_id", [
    "panel_forecast", "bulk_rnaseq", "distribution_visual", "weighted_survey_scale",
    "extended_survival", "time_dependent_roc", "missing_imputation",
    "propensity_clinical", "survey_weighted", "event_sequence_patterns",
    "tabular_ml_workflow", "clinical_restricted_cubic_spline", "mixed_repeated",
    "calibration_dca",
])
def test_registered_getter_and_derived_role_paths_keep_frozen_input_provenance(template_id):
    plan, code, dataset = _rendered_catalog_binding_fixture(template_id)

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "passed", report
    assert report["missing_inputs"] == []


@pytest.mark.parametrize("template_id", [template.id for template in AnalysisCatalog().snapshot().templates])
def test_every_registered_template_renders_with_all_declared_frozen_inputs_consumed(template_id):
    """Check production renderer/guard interoperability, without R/model use."""
    plan, code, dataset = _rendered_catalog_binding_fixture(template_id)

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "passed", report
    assert report["mismatches"] == []
    assert report["missing_inputs"] == []


@pytest.mark.parametrize("template_id", ["panel_forecast", "distribution_visual", "propensity_clinical"])
def test_fixed_getter_and_derived_role_exceptions_require_exact_registered_source(template_id):
    plan, code, dataset = _rendered_catalog_binding_fixture(template_id)

    report = RToolsMixin._frozen_binding_report(code + "\n# edited model code\n", plan, dataset)

    assert report["status"] == "failed", report
    assert any(item.get("reason") in {"dynamic_role_assignment", "frozen_input_not_consumed"} for item in report["mismatches"])


def _free_group_outcome_binding_fixture():
    plan = replace(
        _template_plan(), source="conversation", template_id="",
        variable_bindings={"outcome": "score", "group": "arm"},
        resolved_inputs={
            "outcome": ResolvedInput("outcome", "结局变量", "column", "score", "user"),
            "group": ResolvedInput("group", "分组变量", "column", "arm", "user"),
        },
    )
    return plan, SimpleNamespace(profile={})


@pytest.mark.parametrize("index", ["[1]", "[[1]]"])
@pytest.mark.parametrize("reassign", [False, True])
def test_scalar_column_index_one_and_actual_dataframe_named_fields_preserve_binding(index, reassign):
    plan, dataset = _free_group_outcome_binding_fixture()
    if reassign:
        accessors = (
            'outcome_col <- baa_plan_input("outcome")\n'
            'group_col <- baa_plan_input("group")\n'
            f'outcome_col <- as.character(outcome_col){index}\n'
            f'group_col <- as.character(group_col){index}\n'
        )
    else:
        accessors = (
            f'outcome_col <- as.character(baa_plan_input("outcome")){index}\n'
            f'group_col <- as.character(baa_plan_input("group")){index}\n'
        )
    code = accessors + (
        'dat <- data.frame(\n'
        '  outcome = suppressWarnings(as.numeric(dataset[[outcome_col]])),\n'
        '  group = trimws(as.character(dataset[[group_col]])))\n'
        'fit <- lm(outcome ~ group, data=dat)\n'
    )

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "passed", report
    assert report["mismatches"] == []


@pytest.mark.parametrize("index", ["[2]", "[[2]]", "[1:2]"])
@pytest.mark.parametrize("symbol", ["outcome", "outcome_col"])
def test_scalar_frozen_column_index_other_than_one_remains_blocked(index, symbol):
    plan, dataset = _free_group_outcome_binding_fixture()
    code = (
        f'{symbol} <- as.character(baa_plan_input("outcome")){index}\n'
        'group <- baa_plan_input("group")\n'
        f'fit <- lm(reformulate(group,response={symbol}),data=dataset)\n'
    )

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "failed", report
    assert any(item.get("reason") == "frozen_input_selection_changed" for item in report["mismatches"])


@pytest.mark.parametrize("index", ["[1]", "[[1]]"])
@pytest.mark.parametrize("reassign", [False, True])
def test_multi_column_frozen_input_cannot_be_truncated_by_scalar_index_exception(index, reassign):
    plan = replace(
        _template_plan(), variable_bindings={},
        resolved_inputs={"variables": ResolvedInput("variables", "分析变量", "columns", ["x", "y"], "user")},
    )
    accessors = (
        f'variables <- baa_plan_input("variables")\nvariables <- variables{index}\n'
        if reassign else f'variables <- baa_plan_input("variables"){index}\n'
    )
    code = accessors + 'summary(dataset[,variables])'

    report = RToolsMixin._frozen_binding_report(code, plan, SimpleNamespace(profile={}))

    assert report["status"] == "failed", report
    assert any(item.get("reason") in {"frozen_input_reassigned", "frozen_input_selection_changed"} for item in report["mismatches"])


@pytest.mark.parametrize("operator", ["<-", "="])
@pytest.mark.parametrize("role", ["outcome", "group"])
def test_top_level_role_assignment_is_not_hidden_as_a_dataframe_named_field(operator, role):
    plan, dataset = _free_group_outcome_binding_fixture()
    code = (
        'outcome <- baa_plan_input("outcome")\n'
        'group <- baa_plan_input("group")\n'
        'dat <- data.frame(outcome=dataset[[outcome]],group=dataset[[group]])\n'
        f'{role} {operator} "different_column"\n'
        'fit <- lm(reformulate(group,response=outcome),data=dataset)\n'
    )

    report = RToolsMixin._frozen_binding_report(code, plan, dataset)

    assert report["status"] == "failed", report
    assert any(item.get("reason") == "frozen_input_reassigned" for item in report["mismatches"])


def test_each_same_family_input_requires_its_own_frozen_accessor_call():
    plan = replace(
        _template_plan(),
        variable_bindings={"主要结局": "efficacy", "安全结局": "safety"},
        resolved_inputs={
            "outcome": ResolvedInput(
                "outcome", "主要结局", "column", "efficacy", "user"
            ),
            "outcome_2": ResolvedInput(
                "outcome_2", "安全结局", "column", "safety", "user"
            ),
        },
    )
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [{"name": "efficacy"}, {"name": "safety"}]
            }
        }
    )

    report = RToolsMixin._frozen_binding_report(
        'primary_outcome <- baa_plan_input("outcome")\n'
        'primary_values <- dataset[[primary_outcome]]',
        plan,
        dataset,
    )

    assert report["status"] == "unverified"
    assert report["missing_inputs"] == ["outcome_2"]


def test_frozen_accessor_cannot_be_redefined_by_generated_r_code():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    dataset = SimpleNamespace(profile={"summary": {"variables": []}})

    report = RToolsMixin._frozen_binding_report(
        'baa_plan_input <- function(input_id) 0.99\nalpha <- baa_plan_input("alpha")',
        plan,
        dataset,
    )

    assert report["status"] == "failed"
    assert report["mismatches"][0]["reason"] == "frozen_accessor_override"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (
            'alpha <- baa_plan_input("alpha")\nalpha <- 0.99\nresult <- 1 - alpha',
            "frozen_input_reassigned",
        ),
        (
            'alpha <- baa_plan_input("alpha"); alpha <- 0.99; result <- 1 - alpha',
            "frozen_input_reassigned",
        ),
        ('invisible(baa_plan_input("alpha"))\nresult <- 0.01', "frozen_input_not_consumed"),
        (
            'alpha <- baa_plan_input("alpha")\ninvisible(alpha)\nresult <- 0.01',
            "frozen_input_not_consumed",
        ),
        (
            'accessor_env <- environment(baa_plan_input)\n'
            'alpha <- baa_plan_input("alpha")\nresult <- 1 - alpha',
            "frozen_accessor_override",
        ),
    ],
)
def test_frozen_input_must_be_consumed_without_reassignment_or_reflection(code, reason):
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )

    report = RToolsMixin._frozen_binding_report(
        code, plan, SimpleNamespace(profile={"summary": {"variables": []}})
    )

    assert report["status"] == "failed"
    assert reason in {item.get("reason") for item in report["mismatches"]}


@pytest.mark.parametrize(
    "code",
    [
        'alpha <- baa_plan_input("alpha")\nif (FALSE) dummy <- alpha + 1\nresult <- 0.99',
        'alpha <- baa_plan_input("alpha")\nquoted <- quote(alpha + 1)\nresult <- 0.99',
    ],
)
def test_frozen_input_dead_or_quoted_uses_are_not_accepted(code):
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )

    report = RToolsMixin._frozen_binding_report(
        code,
        plan,
        SimpleNamespace(profile={"summary": {"variables": []}}),
    )

    assert report["status"] == "failed"
    assert "frozen_input_not_consumed" in {
        item.get("reason") for item in report["mismatches"]
    }


def test_ordinary_objects_named_like_reflection_functions_are_not_blocked():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        'alpha <- baa_plan_input("alpha")\n'
        'source <- 1 - alpha\n'
        'eval <- source * 2\n'
        'result <- eval'
    )

    report = RToolsMixin._frozen_binding_report(
        code,
        plan,
        SimpleNamespace(profile={"summary": {"variables": []}}),
    )

    assert report["status"] == "passed"


def test_frozen_input_in_an_unreferenced_closure_is_not_accepted():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        'alpha <- baa_plan_input("alpha")\n'
        'unused <- function() { alpha + 1 }\n'
        'result <- 0.99'
    )

    report = RToolsMixin._frozen_binding_report(
        code,
        plan,
        SimpleNamespace(profile={"summary": {"variables": []}}),
    )

    assert report["status"] == "failed"
    assert "frozen_input_not_consumed" in {
        item.get("reason") for item in report["mismatches"]
    }


def test_indirect_base_environment_reflection_cannot_forge_runtime_access_log():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        'if(FALSE){ alpha <- baa_plan_input("alpha"); dummy <- alpha + 1 }\n'
        'envfun <- baseenv()[["environment"]]\n'
        'unlock <- baseenv()[["unlockBinding"]]\n'
        'f <- .GlobalEnv[["baa_plan_input"]]\n'
        'e <- envfun(f)\n'
        'fake <- new.env(parent=emptyenv())\n'
        'assign("alpha", TRUE, envir=fake)\n'
        'unlock("accessed_inputs", e)\n'
        'assign("accessed_inputs", fake, envir=e)\n'
        'result <- .99'
    )

    report = RToolsMixin._frozen_binding_report(
        code, plan, SimpleNamespace(profile={"summary": {"variables": []}})
    )

    assert report["status"] == "failed"
    assert "frozen_accessor_reflection" in {
        item.get("reason") for item in report["mismatches"]
    }

    rscript = shutil.which("Rscript")
    if not rscript:
        return
    protected_code = (
        RToolsMixin._frozen_input_prelude(plan)
        + RToolsMixin._frozen_user_code_block(code, plan)
        + RToolsMixin._frozen_input_epilogue(plan)
    )
    completed = subprocess.run(
        [rscript, "--vanilla", "-e", protected_code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode != 0
    assert "BAA_FROZEN_INPUT_AST" in completed.stderr


def test_legitimate_function_environment_inspection_is_not_misclassified_as_accessor_attack():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        'alpha <- baa_plan_input("alpha")\n'
        'threshold <- 1 - alpha\n'
        'combined <- do.call(c, list(threshold))\n'
        'retrieved <- get("threshold")\n'
        'fit_env <- environment(stats::lm)'
    )

    report = RToolsMixin._frozen_binding_report(
        code, plan, SimpleNamespace(profile={"summary": {"variables": []}})
    )

    assert report["status"] == "passed"
    rscript = shutil.which("Rscript")
    if rscript:
        protected_code = (
            RToolsMixin._frozen_input_prelude(plan)
            + RToolsMixin._frozen_user_code_block(code, plan)
            + RToolsMixin._frozen_input_epilogue(plan)
        )
        completed = subprocess.run(
            [rscript, "--vanilla", "-e", protected_code],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "code",
    [
        (
            'alpha <- baa_plan_input("alpha")\n'
            'helper <- function(value, offset = 1, ...) value + offset + alpha\n'
            'result <- helper(2)'
        ),
        (
            'alpha <- baa_plan_input("alpha")\n'
            f'note <- "{"中文说明" * 2200}"\n'
            'result <- 1 - alpha'
        ),
    ],
    ids=["ordinary-function-formals", "long-unicode-source"],
)
def test_frozen_user_code_block_accepts_ordinary_generated_r(code):
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript is required for the runtime frozen-input audit")
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    protected_code = (
        RToolsMixin._frozen_input_prelude(plan)
        + RToolsMixin._frozen_user_code_block(code, plan)
        + RToolsMixin._frozen_input_epilogue(plan)
    )

    completed = subprocess.run(
        [rscript, "--vanilla", "-e", protected_code],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LANG": "C", "LC_ALL": "C"},
    )

    assert completed.returncode == 0, completed.stderr


def test_frozen_user_code_block_chunks_long_unicode_source_literals():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        'alpha <- baa_plan_input("alpha")\n'
        f'note <- "{"中文说明" * 2200}"\n'
        'result <- 1 - alpha'
    )

    protected_block = RToolsMixin._frozen_user_code_block(code, plan)

    assert max(map(len, protected_block.splitlines())) < 9000


def test_runtime_audit_rejects_accessor_call_hidden_in_a_dead_branch():
    rscript = shutil.which("Rscript")
    if not rscript:
        pytest.skip("Rscript is required for the runtime frozen-input audit")
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    code = (
        RToolsMixin._frozen_input_prelude(plan)
        + '\nif (FALSE) alpha <- baa_plan_input("alpha")\n'
        + RToolsMixin._frozen_input_epilogue(plan)
    )

    completed = subprocess.run(
        [rscript, "--vanilla", "-e", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "BAA_FROZEN_INPUT_AUDIT" in completed.stderr


def test_frozen_object_input_accessor_returns_the_loaded_r_object_not_dataset_id():
    base = _template_plan()
    plan = replace(
        base,
        variable_bindings={},
        resolved_inputs={
            "data_object": ResolvedInput(
                "data_object",
                "分析对象",
                "object",
                base.dataset_id,
                "dataset",
            )
        },
    )

    prelude = RToolsMixin._frozen_input_prelude(plan)

    assert '"data_object" = dataset' in prelude
    assert f'"data_object" = "{base.dataset_id}"' not in prelude
    assert 'lockBinding("baa_plan_input", .GlobalEnv)' in prelude

    missing = RToolsMixin._frozen_binding_report(
        "summary(dataset)",
        plan,
        SimpleNamespace(profile={}),
    )
    assert missing["status"] == "unverified"
    assert missing["missing_inputs"] == ["data_object"]

    accessed = RToolsMixin._frozen_binding_report(
        'analysis_object <- baa_plan_input("data_object")\n'
        'object_class <- class(analysis_object)',
        plan,
        SimpleNamespace(profile={}),
    )
    assert accessed["status"] == "passed"


def test_frozen_input_accessor_mentioned_only_in_r_comment_is_not_verified():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    dataset = SimpleNamespace(profile={"summary": {"variables": []}})

    report = RToolsMixin._frozen_binding_report(
        '# baa_plan_input("alpha")\nalpha <- 0.10',
        plan,
        dataset,
    )

    assert report["status"] == "unverified"
    assert report["missing_inputs"] == ["alpha"]


@pytest.mark.parametrize("metadata_value", ['"y"', "outcome"])
def test_multiline_model_metadata_is_not_a_frozen_role_reassignment(metadata_value):
    plan = replace(
        _template_plan(),
        variable_bindings={"outcome": "y"},
        resolved_inputs={
            "outcome": ResolvedInput("outcome", "因变量", "column", "y", "user")
        },
    )
    code = (
        'outcome <- baa_plan_input("outcome")\n'
        'fit <- lm(reformulate("x", response=outcome), data=dataset)\n'
        'baa_model_summary("fit", fit, metadata=list(\n'
        f'  outcome = {metadata_value}, exposure = "x",\n'
        '  title = "Synthetic model"))\n'
    )

    report = RToolsMixin._frozen_binding_report(code, plan, SimpleNamespace(profile={}))

    assert report["status"] == "passed", report
    assert report["mismatches"] == []


@pytest.mark.parametrize("operator", ["=", "<-"])
def test_actual_role_reassignment_inside_call_block_remains_blocked(operator):
    plan = replace(
        _template_plan(),
        variable_bindings={"outcome": "y"},
        resolved_inputs={
            "outcome": ResolvedInput("outcome", "因变量", "column", "y", "user")
        },
    )
    code = (
        'outcome <- baa_plan_input("outcome")\n'
        'identity({\n'
        f'  outcome {operator} "wrong"\n'
        '  lm(reformulate("x", response=outcome), data=dataset)\n'
        '})\n'
    )

    report = RToolsMixin._frozen_binding_report(code, plan, SimpleNamespace(profile={}))

    assert report["status"] == "failed"
    assert any(item.get("reason") == "frozen_input_reassigned" for item in report["mismatches"])


def test_frozen_input_accessor_mentioned_only_in_r_string_is_not_verified():
    plan = replace(
        _template_plan(),
        variable_bindings={},
        resolved_inputs={
            "alpha": ResolvedInput("alpha", "检验水准", "parameter", 0.05, "user")
        },
    )
    dataset = SimpleNamespace(profile={"summary": {"variables": []}})

    report = RToolsMixin._frozen_binding_report(
        'note <- \'use baa_plan_input("alpha")\'',
        plan,
        dataset,
    )

    assert report["status"] == "unverified"
    assert report["missing_inputs"] == ["alpha"]


def test_table_one_call_mentioned_only_in_r_string_is_not_validated_as_execution():
    plan = _template_plan()
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [
                    {"name": name} for name in ("gender", "cvd", "age", "bmi")
                ]
            }
        }
    )

    report = RToolsMixin._frozen_binding_report(
        'group_var <- baa_plan_input("group")\n'
        'categorical_vars <- baa_plan_input("categorical_variables")\n'
        'continuous_vars <- baa_plan_input("continuous_variables")\n'
        'analysis_frame <- dataset[c(group_var, categorical_vars, continuous_vars)]\n'
        'note <- "Do not run baa_table_one(by=\\\"gender\\\") here"',
        plan,
        dataset,
    )

    assert report["status"] == "passed"
    assert report["mismatches"] == []


def test_semantic_catalog_role_does_not_create_an_undeclared_level_contract():
    profile = {
        "dataset_id": "forecasting-data",
        "format": "csv",
        "object_kind": "data.frame",
        "workspace_generation": "generation-1",
        "profile_revision": "profile-1",
        "rows": 60,
        "columns": [
            _column("visit_date", "datetime", unique_count=60),
            _column("measurement", "numeric", unique_count=55),
            _column("x1", "numeric", unique_count=2),
        ],
        "local_semantics": {"levels": {"x1": ["0", "1"]}},
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "forecasting",
        profile,
        {
            "time": "visit_date",
            "measure": "measurement",
            "group_region": "x1",
        },
    )

    assert prepared.status == "ready"
    assert all(
        control.input_id != "group_region_reference_level"
        for control in prepared.input_controls
    )


def test_catalog_has_no_opaque_hashed_analysis_input_roles():
    opaque = []
    generic = []
    for template in AnalysisCatalog().snapshot().templates:
        for spec in template.input_specs:
            if re.fullmatch(r"role_[0-9a-f]{8,}|input_[0-9]+", spec.id):
                opaque.append(f"{template.id}:{spec.id}")
            if spec.id in {"sample", "variable"} or spec.semantic_role in {
                "sample",
                "variable",
            }:
                generic.append(f"{template.id}:{spec.id}")

    assert opaque == []
    assert generic == []


def test_diagnostic_meta_recognizes_semantic_confusion_matrix_columns():
    columns = [
        _column(name, "numeric", unique_count=10)
        for name in ("true_positive", "false_positive", "false_negative", "true_negative")
    ]
    columns.append(_column("study_id", "identifier", unique_count=10))
    profile = {
        "dataset_id": "diagnostic-meta",
        "format": "csv",
        "object_kind": "data.frame",
        "rows": 10,
        "columns": columns,
    }

    prepared = AnalysisPreparer(AnalysisCatalog()).prepare("diagnostic_meta", profile)

    assert prepared.status == "ready"
    assert prepared.plan is not None
    assert {
        input_id: item.value
        for input_id, item in prepared.plan.resolved_inputs.items()
        if input_id in {"true_positive", "false_positive", "false_negative", "true_negative"}
    } == {
        "true_positive": "true_positive",
        "false_positive": "false_positive",
        "false_negative": "false_negative",
        "true_negative": "true_negative",
    }


def test_ready_template_still_renders_all_editable_scientific_inputs():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the workbench interaction test")

    script = f"""
const fs = require("fs");
const vm = require("vm");
const assert = require("assert");
process.env.NODE_ENV = "test";
global.localStorage = {{ getItem: () => null, setItem: () => {{}} }};
global.sessionStorage = {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }};
global.document = {{
  addEventListener: () => {{}},
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
}};
global.window = global;
global.HTMLElement = class HTMLElement {{}};
global.Element = class Element {{}};
global.__RSW_TEST_HARNESS__ = {{}};

let source = fs.readFileSync({json.dumps(str(WORKBENCH))}, "utf8");
source = source.replace(
  "Object.assign(globalThis.__RSW_TEST_HARNESS__, {{\\n      state,",
  "Object.assign(globalThis.__RSW_TEST_HARNESS__, {{\\n      state,\\n      renderPreparationQuestions,"
);
vm.runInThisContext(source, {{ filename: {json.dumps(str(WORKBENCH))} }});
const hooks = global.__RSW_TEST_HARNESS__;
const html = hooks.renderPreparationQuestions(
  {{ id: "publication_table", input_contract: {{ inputs: [] }} }},
  {{
    status: "ready",
    questions: [],
    input_controls: [
      {{ input_id: "group", label: "分组变量", kind: "column", required: true,
         value: "gender", provenance: "inferred", candidates: [
           {{ value: "gender", label: "gender" }}, {{ value: "cvd", label: "cvd" }}
         ] }},
      {{ input_id: "categorical_variables", label: "分类基线变量", kind: "columns",
         required: true, value: ["cvd"], provenance: "inferred", candidates: [
           {{ value: "gender", label: "gender" }}, {{ value: "cvd", label: "cvd" }}
         ] }},
      {{ input_id: "continuous_variables", label: "连续基线变量", kind: "columns",
         required: true, value: ["age", "bmi"], provenance: "inferred", candidates: [
           {{ value: "age", label: "age" }}, {{ value: "bmi", label: "bmi" }}
         ] }},
    ],
  }}
);
assert.match(html, /data-template-input-id="group"/);
assert.match(html, /data-template-input-id="categorical_variables"/);
assert.match(html, /data-template-input-id="continuous_variables"/);
assert.match(html, /value="cvd"/);
"""

    subprocess.run([node, "-e", script], check=True, capture_output=True, text=True)
