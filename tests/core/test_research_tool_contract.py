#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Research-mode tool schema/filter contract tests."""

import csv
import hashlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools.research import RToolsMixin
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, RequiredOutput, ResolvedInput
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.workflows import default_workflow_registry
from agent.analysis.plan_projection import plan_browser_projection
from agent.tools.exposure import filter_tools_for_turn
from agent.tools.registry import BUILTIN_TOOL_REGISTRY
from agent.tools.schemas import AGENT_TOOLS
from agent.agent import (
    BusinessAgent,
    _attach_result_assessment,
    _analysis_plan_system_section,
    _research_artifacts_from_tool_result,
    _sanitize_r_tool_result_for_model,
    _sanitize_tool_artifacts_for_model,
)
from agent.analysis.result_validation import ResultAssessment
from data.analysis_plan_store import AnalysisPlanStore
from r_runtime.protocol import RArtifact, RRunResult


def _names(tools):
    return {(tool.get("function") or {}).get("name") for tool in tools}


def _tool_schema(name):
    return next(tool for tool in AGENT_TOOLS if (tool.get("function") or {}).get("name") == name)


def test_research_r_tools_are_registered_in_schema_and_registry():
    names = _names(AGENT_TOOLS)

    assert "inspect_dataset" in names
    assert "set_analysis_plan" in names
    assert "run_r_code" in names
    assert "get_r_package_inventory" in names
    assert "get_r_package_help" in names
    BUILTIN_TOOL_REGISTRY.validate_schema_names(AGENT_TOOLS)
    assert _tool_schema("run_r_code")["function"]["parameters"]["required"] == ["code"]

    inspect_spec = BUILTIN_TOOL_REGISTRY.get("inspect_dataset")
    assert inspect_spec is not None
    assert inspect_spec.category == "read"
    assert inspect_spec.requires_runtime is True

    plan_spec = BUILTIN_TOOL_REGISTRY.get("set_analysis_plan")
    assert plan_spec is not None
    assert plan_spec.category == "analysis"
    assert plan_spec.requires_runtime is True
    plan_parameters = _tool_schema("set_analysis_plan")["function"]["parameters"]
    analysis_inputs_schema = plan_parameters["properties"]["analysis_inputs"]
    analysis_input = analysis_inputs_schema["items"]
    assert "depends_on" in analysis_input["properties"]
    assert analysis_inputs_schema["minItems"] == 1
    assert "analysis_inputs" in plan_parameters["required"]

    run_spec = BUILTIN_TOOL_REGISTRY.get("run_r_code")
    assert run_spec is not None
    assert run_spec.category == "analysis"
    assert run_spec.requires_runtime is True
    assert run_spec.execution_mode == "auto"
    assert run_spec.job_threshold == "r_timeout_seconds_gt_60"

    package_spec = BUILTIN_TOOL_REGISTRY.get("get_r_package_inventory")
    assert package_spec is not None
    assert package_spec.category == "read"
    assert package_spec.requires_runtime is True

    package_help_spec = BUILTIN_TOOL_REGISTRY.get("get_r_package_help")
    assert package_help_spec is not None
    assert package_help_spec.category == "read"
    assert package_help_spec.requires_runtime is True


def test_research_mode_exposes_research_tools_and_hides_legacy_bi(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")

    names = _names(filter_tools_for_turn(AGENT_TOOLS))

    assert "inspect_dataset" in names
    assert "set_analysis_plan" in names
    assert "run_r_code" in names
    assert "get_r_package_inventory" in names
    assert "get_r_package_help" in names
    assert "ask_user" in names
    assert "query_knowledge" not in names
    assert "get_schema" not in names
    assert "query_data" not in names
    assert "run_analysis" not in names


def test_research_mode_ignores_legacy_skill_allowlists(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")

    names = _names(
        filter_tools_for_turn(
            AGENT_TOOLS,
            skill_allowed_tools=frozenset({"query_knowledge"}),
        )
    )

    assert "run_r_code" in names
    assert "query_knowledge" not in names


def test_research_mode_empty_skill_allowlist_still_exposes_research_tools(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")

    names = _names(
        filter_tools_for_turn(
            AGENT_TOOLS,
            skill_allowed_tools=frozenset(),
        )
    )

    assert names == {
        "inspect_dataset",
        "set_analysis_plan",
        "run_r_code",
        "get_r_package_inventory",
        "get_r_package_help",
        "ask_user",
    }


def test_run_r_code_blocks_job_directory_escape_calls():
    reason = RToolsMixin._blocked_platform_probe_reason("download.file('https://example.com/x.csv', destfile = 'x.csv')")
    assert "download.file" in reason
    assert RToolsMixin._blocked_platform_probe_reason("system('rm -rf /')")
    assert RToolsMixin._blocked_platform_probe_reason("shell('echo hi')")


def test_freeform_plan_is_expanded_and_persisted_for_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    class PlanAgent:
        _tool_set_analysis_plan = BusinessAgent._tool_set_analysis_plan

        def __init__(self):
            self._session_id = "freeform-plan-session"
            self._analysis_plan = None

        def _analysis_dataset_profile(self):
            return {
                "dataset_id": "dataset-freeform",
                "format": "csv",
                "workspace_generation": "generation-1",
                "profile_revision": "profile-1",
                "columns": [
                    {"name": "score", "semantic_type": "numeric"},
                    {"name": "arm", "semantic_type": "categorical"},
                ],
            }

        def _tool_get_r_package_inventory(self, **_kwargs):
            return json.dumps({"recommended_candidate_packages": ["stats", "ggplot2"]})

        def _tool_get_r_package_help(self, **_kwargs):
            return json.dumps({"ok": True, "results": []})

    agent = PlanAgent()
    payload = json.loads(
        agent._tool_set_analysis_plan(
            {
                "intent": "比较两组连续结局",
                "category": "基础统计",
                "method_family": "classical_statistics",
                "scenario": "独立组比较",
                "variable_bindings": {"outcome": "score", "group": "arm"},
                "package_queries": ["group comparison"],
                "prerequisites": [],
                "assumptions": ["观测独立"],
                "diagnostics": ["分布与方差检查"],
                "required_outputs": [
                    {"role": "group_comparison_table", "kind": "table", "label": "组间比较表"}
                ],
                "resource_class": "standard",
            }
        )
    )

    plan_id = payload["analysis_plan"]["plan_id"]
    roles = {item["role"] for item in payload["analysis_plan"]["required_outputs"]}
    assert "group_comparison_table" in roles
    assert "fitted_model_summary" in roles
    stored = AnalysisPlanStore().load(
        "freeform-plan-session",
        plan_id,
        dataset_id="dataset-freeform",
        workspace_generation="generation-1",
        profile_revision="profile-1",
    )
    assert stored is not None
    assert stored.source == "conversation"
    assert stored.plan_id == plan_id


def test_freeform_plan_rejects_duplicate_or_empty_output_roles():
    class PlanAgent:
        _tool_set_analysis_plan = BusinessAgent._tool_set_analysis_plan

    agent = PlanAgent()
    args = {
        "required_outputs": [
            {"role": "", "kind": "table"},
            {"role": "same_role", "kind": "table"},
            {"role": "same_role", "kind": "figure"},
        ]
    }

    with pytest.raises(ValueError, match="output role"):
        agent._tool_set_analysis_plan(args)


def test_frozen_plan_projection_preserves_the_complete_portfolio_and_marks_baseline_as_floor():
    plan = AnalysisPlan(
        plan_id="plan-publication-table",
        source="template",
        dataset_id="dataset-1",
        intent="论文 Table 1 / 三线表",
        category="论文交付",
        method_family="publication_reporting",
        scenario="baseline comparison",
        variable_bindings={"group": "treatment_arm"},
        packages=("gtsummary", "ggplot2"),
        prerequisites=("分组变量可用",),
        assumptions=("观测独立",),
        diagnostics=("组间平衡检查",),
        required_outputs=(
            RequiredOutput(
                role="baseline_table",
                kind="table",
                label="Table 1",
                tier="core",
                source="template",
                semantic_key="baseline_table",
            ),
            RequiredOutput(
                role="baseline_balance_plot",
                kind="figure",
                required=False,
                label="基线平衡图",
                tier="recommended",
                source="capability",
                rationale="补充视觉平衡证据",
                acceptance=("包含所有基线变量",),
                semantic_key="baseline_balance_plot",
            ),
        ),
        resource_class="standard",
        template_id="publication_table",
        portfolio_version="1.0",
        capability_evidence=("profile:publication_reporting",),
    )

    projection = plan_browser_projection(plan, plan_id=plan.plan_id)

    assert projection["id"] == "publication_table"
    assert projection["variable_bindings"] == {"group": "treatment_arm"}
    contract = projection["template_contract"]["output_contract"]
    assert contract["baseline_is_floor"] is True
    assert contract["portfolio_version"] == "1.0"
    assert [item["role"] for item in contract["output_portfolio"]] == [
        "baseline_table",
        "baseline_balance_plot",
    ]
    assert contract["output_portfolio"][1]["tier"] == "recommended"
    assert contract["output_portfolio"][1]["source"] == "capability"
    assert contract["output_portfolio"][1]["acceptance"] == ["包含所有基线变量"]
    assert projection["template_contract"]["execution"]["authority"] == "scientific_workflow_run_spec"
    workflow = projection["workflow_run_spec"]
    assert workflow["workflow_id"]
    assert [item["phase"] for item in workflow["steps"]] == [
        "preflight",
        "prepare",
        "analyze",
        "diagnose",
        "publish",
    ]
    system_section = _analysis_plan_system_section(plan)
    assert "[ACTIVE ANALYSIS PLAN]" in system_section
    assert (
        "baseline_balance_plot [figure; tier=recommended; required=false; source=capability]"
        in system_section
    )
    assert "group=treatment_arm" in system_section


def test_active_plan_prompt_exposes_exact_accessor_ids_to_the_model():
    plan = AnalysisPlan(
        plan_id="plan-input-ids",
        source="conversation",
        dataset_id="dataset-input-ids",
        intent="按组比较",
        category="基础统计",
        method_family="group_comparison",
        scenario="两组比较",
        variable_bindings={"分组变量": "arm"},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("comparison", "table"),),
        resource_class="standard",
        resolved_inputs={
            "group": ResolvedInput("group", "分组变量", "column", "arm", "user")
        },
    )

    section = _analysis_plan_system_section(plan)

    assert "id=group; 分组变量" in section
    assert 'baa_plan_input("input_id")' in section


class _DummyDataset:
    dataset_id = "abcdef123456"
    session_id = "session-1"
    name = "clinical.csv"
    format = "csv"
    stored_path = "/tmp/clinical.csv"
    profile = {}

    def to_dict(self):
        return {
            "dataset_id": self.dataset_id,
            "session_id": self.session_id,
            "name": self.name,
            "format": self.format,
            "stored_path": self.stored_path,
            "profile": self.profile,
        }


class _DummyStore:
    def save(self, dataset):
        return dataset


class _DummyAgent(RToolsMixin):
    def __init__(self):
        self._session_id = "session-1"
        self.dataset = _DummyDataset()

    def _research_store(self):
        return _DummyStore()

    def _resolve_research_dataset(self, dataset_id=""):
        return self.dataset


def test_research_agent_reuses_one_local_r_backend(monkeypatch):
    created = []

    def factory():
        backend = object()
        created.append(backend)
        return backend

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", factory)
    agent = _DummyAgent()

    assert agent._local_r_backend() is agent._local_r_backend()
    assert len(created) == 1


def test_inspect_dataset_failed_backend_result_is_error_payload(monkeypatch):
    class Backend:
        def run_code(self, request):
            return RRunResult(
                ok=False,
                stderr="inspect failed at /volume/private/data.csv",
                error="r_error",
                code="source('/volume/private/script.R')",
            )

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    text = _DummyAgent()._tool_inspect_dataset()

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["result"]["ok"] is False
    assert payload["result"]["error"].startswith("数据理解失败（错误编号：")
    assert "r_error" not in text
    assert "/volume/private" not in text
    assert not ({"code", "stdout", "stderr"} & payload["result"].keys())


def test_inspect_dataset_persists_frontend_summary(tmp_path, monkeypatch):
    table_path = tmp_path / "dataset_profile.csv"
    table_path.write_text(
        "column,type,missing,missing_pct,example\nage,numeric,0,0,40 | 50\ngroup,character,1,50,A | B\n",
        encoding="utf-8",
    )
    text_path = tmp_path / "dataset_overview.txt"
    text_path.write_text("Rows: 2\nColumns: 2", encoding="utf-8")

    class Backend:
        def run_code(self, request):
            return RRunResult(
                ok=True,
                tables=[
                    RArtifact(
                        name="dataset_profile",
                        path=str(table_path),
                        kind="table",
                        mime_type="text/csv",
                    )
                ],
                texts=[
                    RArtifact(
                        name="dataset_overview",
                        path=str(text_path),
                        kind="text",
                        mime_type="text/plain",
                    )
                ],
            )

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    agent = _DummyAgent()
    text = agent._tool_inspect_dataset()

    payload = json.loads(text)
    assert payload["summary"]["rows"] == 2
    assert payload["summary"]["overall_missing_pct"] == 25.0
    assert str(table_path) not in text
    assert "stored_path" not in payload["dataset"]
    assert all("path" not in item for item in payload["result"]["tables"])
    assert not ({"code", "stdout", "stderr"} & payload["result"].keys())
    assert agent.dataset.profile["inspection"]["tables"][0]["path"] == str(table_path)
    assert agent.dataset.profile["summary"]["variables"][1]["name"] == "group"


def test_run_r_code_failed_backend_result_is_error_payload_and_timeout_safe(monkeypatch):
    class Backend:
        request = None

        def run_code(self, request):
            self.request = request
            return RRunResult(ok=False, stdout="partial", stderr="boom", error="r_error")

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_run_r_code(
        code="summary(dataset)",
        timeout_seconds=float("inf"),
    )

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["result"]["ok"] is False
    assert payload["result"]["error"] == "r_error"
    assert backend.request.timeout_seconds == 0
    assert backend.request.resource_class == "standard"


def test_run_r_code_pauses_before_worker_when_generated_code_package_is_missing(monkeypatch):
    class Backend:
        run_calls = 0
        runtime_env = {"PATH": ""}

        def package_readiness(self, packages, timeout_seconds=30):
            return {
                "ok": True,
                "packages": [
                    {
                        "package": package,
                        "installed": package != "DESeq2",
                        "loadable": package != "DESeq2",
                        "status": "missing" if package == "DESeq2" else "ready",
                        "warnings": [],
                        "error": "Package is not installed" if package == "DESeq2" else "",
                        "runtime_check": "not_required",
                        "runtime_detail": "",
                    }
                    for package in packages
                ],
            }

        def run_code(self, request):
            self.run_calls += 1
            return RRunResult(ok=True)

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_run_r_code(
        code="dds <- DESeq2::DESeqDataSetFromMatrix(dataset, data.frame(group='A'), ~ group)"
    )

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["execution"]["status"] == "waiting_for_dependencies"
    assert payload["dependency_gate"]["install_packages"] == ["DESeq2"]
    assert payload["dependency_gate"]["ticket_id"]
    assert backend.run_calls == 0


def test_run_r_code_failed_backend_with_artifacts_is_partial_payload(monkeypatch, tmp_path):
    class Backend:
        def run_code(self, request):
            table_path = Path(request.artifacts_dir) / "partial.csv"
            table_path.parent.mkdir(parents=True, exist_ok=True)
            table_path.write_text("term,estimate\nage,1.25\n", encoding="utf-8")
            return RRunResult(
                ok=False,
                stdout="saved partial results",
                stderr="Error: downstream step failed",
                error="Rscript exited with code 1",
                tables=[
                    RArtifact(
                        name="partial_table",
                        path=str(table_path),
                        kind="table",
                        mime_type="text/csv",
                    )
                ],
            )

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    text = _DummyAgent()._tool_run_r_code(code="baa_table('partial_table', dataset); stop('late failure')")

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["partial_result"] is True
    assert payload["result"]["ok"] is False
    assert payload["result"]["tables"][0]["name"] == "partial_table"
    partial_metadata = payload["result"]["tables"][0]["metadata"]
    assert partial_metadata["source_execution_status"] == "failed"
    assert partial_metadata["artifact_validation_receipt"]["execution_status"] == "failed"
    assert payload["repair_diagnostics"]["category"] == "r_execution_error"
    assert any("already saved" in item for item in payload["repair_diagnostics"]["next_actions"])


def test_run_r_code_adds_worker_metadata_compatibility_prelude(monkeypatch):
    class Backend:
        request = None

        def run_code(self, request):
            self.request = request
            return RRunResult(ok=True, stdout="ok", code=request.code)

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_run_r_code(
        code="baa_table('summary', dataset, metadata = list(title = 'Summary'))"
    )

    payload = json.loads(text)
    assert payload["result"]["ok"] is True
    assert "unused argument (metadata=...)" in backend.request.code
    assert "baa_wrap_metadata" in backend.request.code
    assert "baa_table('summary', dataset, metadata = list(title = 'Summary'))" in backend.request.code


@pytest.mark.parametrize("reverse", [False, True])
def test_diagnostics_use_valid_artifacts_and_preserve_conflicts(tmp_path, reverse):
    artifacts = []
    for name, status in [("first", "failed"), ("second", "passed")]:
        path = tmp_path / f"{name}.csv"
        path.write_text("term,estimate\nx,1.2\n", encoding="utf-8")
        artifacts.append(RArtifact(name=name, path=str(path), kind="table", metadata={
            "diagnostic_status": {"收敛检查": status},
        }))
    if reverse:
        artifacts.reverse()
    artifacts.append(RArtifact(name="missing", path=str(tmp_path / "missing.csv"), kind="table", metadata={
        "diagnostic_status": {"残差检查": "passed", "收敛检查": "passed"},
    }))
    result = RRunResult(ok=True, tables=artifacts)
    diagnostics = _DummyAgent()._attach_analysis_plan_metadata(
        result, "abcdef123456", job_id="diagnostic-job", artifacts_root=str(tmp_path),
    )
    assert diagnostics == {"收敛检查": "failed"}
    assert {artifact.name for artifact in result.tables} == {"first", "second"}
    assert any("诊断声明冲突" in message for message in result.warnings)


def test_run_r_code_stamps_plan_ownership_on_saved_artifacts(monkeypatch, tmp_path):
    class Backend:
        def run_code(self, request):
            table_path = Path(request.artifacts_dir) / "summary.csv"
            table_path.parent.mkdir(parents=True, exist_ok=True)
            table_path.write_text("term,estimate\nx,1.2\n", encoding="utf-8")
            return RRunResult(
                ok=True,
                tables=[
                    RArtifact(
                        name="summary",
                        path=str(table_path),
                        kind="table",
                        metadata={
                            "role": "primary_table",
                            "diagnostic_status": {"分布检查": "passed"},
                            "artifact_size_bytes": 999999,
                            "artifact_sha256": "0" * 64,
                            "artifact_validation_receipt": {"validator": "worker"},
                        },
                    )
                ],
            )

    agent = _DummyAgent()
    agent._analysis_plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abcdef123456",
        intent="描述",
        category="基础统计",
        method_family="descriptive",
        scenario="描述性统计",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=("分布检查",),
        required_outputs=(RequiredOutput("primary_table", "table"),),
        resource_class="standard",
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    payload = json.loads(agent._tool_run_r_code(code="baa_table('summary', dataset)"))

    metadata = payload["result"]["tables"][0]["metadata"]
    assert metadata["dataset_id"] == "abcdef123456"
    assert metadata["plan_id"] == "plan-1"
    assert metadata["role"] == "primary_table"
    assert metadata["source_tool"] == "run_r_code"
    assert metadata["source_execution_status"] == "succeeded"
    assert metadata["artifact_size_bytes"] == len(b"term,estimate\nx,1.2\n")
    assert metadata["artifact_sha256"] == hashlib.sha256(b"term,estimate\nx,1.2\n").hexdigest()
    assert metadata["artifact_validation_receipt"]["validator"] == "data-analysis-agent"
    assert metadata["artifact_validation_receipt"]["structure"]["format"] == "csv"
    assert metadata["artifact_validation_receipt"]["job_id"] == payload["job_id"]
    assert metadata["artifact_validation_receipt"]["relative_path"] == "summary.csv"
    assert payload["execution"]["diagnostic_status"] == {"分布检查": "passed"}


@pytest.mark.parametrize(
    ("code", "expected_category"),
    [
        (
            'group_var <- "cvd"\nbaa_table("primary", table(dataset[[group_var]]))',
            "frozen_binding_mismatch",
        ),
        (
            "fit <- lm(age ~ cvd, data=dataset)\nbaa_model_summary('model', fit)",
            "frozen_binding_unverified",
        ),
    ],
)
def test_run_r_code_blocks_conflicting_or_unverified_frozen_bindings(
    monkeypatch, code, expected_category
):
    class Backend:
        called = False

        def run_code(self, request):
            self.called = True
            return RRunResult(ok=True, code=request.code)

    backend = Backend()
    agent = _DummyAgent()
    agent.dataset.profile = {
        "summary": {
            "variables": [
                {"name": "gender", "semantic_type": "categorical"},
                {"name": "cvd", "semantic_type": "categorical"},
                {"name": "age", "semantic_type": "numeric"},
            ]
        }
    }
    agent._analysis_plan = AnalysisPlan(
        plan_id="plan-frozen-gender",
        source="conversation",
        dataset_id="abcdef123456",
        intent="按性别比较",
        category="基础统计",
        method_family="group_comparison",
        scenario="描述性比较",
        variable_bindings={"分组变量": "gender"},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("primary_table", "table"),),
        resource_class="standard",
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = agent._tool_run_r_code(code=code)

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["repair_diagnostics"]["category"] == expected_category
    assert "gender" in payload["result"]["error"]
    if expected_category == "frozen_binding_mismatch":
        assert "cvd" in payload["result"]["error"]
    assert backend.called is False


def _binding_report(bindings, code):
    plan = SimpleNamespace(variable_bindings=bindings)
    dataset = SimpleNamespace(
        profile={
            "summary": {
                "variables": [
                    {"name": name}
                    for name in (
                        "gender",
                        "cvd",
                        "event_status",
                        "wrong_status",
                        "patient_id",
                        "center_code",
                        "site",
                    )
                ]
            }
        }
    )
    return RToolsMixin._frozen_binding_report(code, plan, dataset)


@pytest.mark.parametrize(
    ("bindings", "code", "expected_family"),
    [
        ({"事件状态": "event_status"}, 'status_var <- c("wrong_status")', "event"),
        ({"个体/中心ID": "patient_id"}, 'center_id_var <- "center_code"', "identifier"),
        (
            {"分组变量": "gender", "分层变量": "site"},
            'group_var <- "site"\nstrata_var <- "gender"',
            "group",
        ),
        ({"分组变量": "gender"}, 'baa_table_one("t1", dataset, by="cvd")', "group"),
    ],
)
def test_frozen_binding_report_covers_catalog_roles_wrappers_and_table_one(
    bindings, code, expected_family
):
    report = _binding_report(bindings, code)

    assert report["status"] == "failed"
    assert any(item["family"] == expected_family for item in report["mismatches"])


def test_frozen_binding_report_rejects_dynamic_role_indirection():
    report = _binding_report({"分组变量": "gender"}, "group_var <- names(dataset)[7]")

    assert report["status"] == "failed"
    assert report["mismatches"][0]["reason"] == "dynamic_role_assignment"


def test_frozen_binding_report_requires_explicit_roles_for_formula_code():
    report = _binding_report({"分组变量": "gender"}, "fit <- lm(outcome ~ cvd, data=dataset)")

    assert report["status"] == "unverified"
    assert report["observed"] == []
    assert report["missing_families"] == ["group"]


def test_frozen_binding_report_accepts_matching_literal_and_table_one_symbol():
    report = _binding_report(
        {"分组变量": "gender"},
        'group_var <- as.character("gender")\nbaa_table_one("t1", dataset, by=group_var)',
    )

    assert report["status"] == "passed"
    assert report["mismatches"] == []


def test_run_r_code_maps_helper_default_roles_to_the_frozen_portfolio(monkeypatch, tmp_path):
    class Backend:
        def run_code(self, request):
            output_dir = Path(request.artifacts_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            table_path = output_dir / "table_one.csv"
            table_path.write_text("variable,overall\nage,42\n", encoding="utf-8")
            model_path = output_dir / "model.txt"
            model_path.write_text("Call: lm(y ~ x)", encoding="utf-8")
            return RRunResult(
                ok=True,
                tables=[
                    RArtifact(
                        name="table_one",
                        path=str(table_path),
                        kind="table",
                        metadata={"role": "baseline"},
                    )
                ],
                models=[
                    RArtifact(
                        name="model",
                        path=str(model_path),
                        kind="model",
                        metadata={"role": "model_summary"},
                    )
                ],
            )

    agent = _DummyAgent()
    agent._analysis_plan = AnalysisPlan(
        plan_id="plan-helpers",
        source="template",
        dataset_id="abcdef123456",
        intent="Table 1 与调整模型",
        category="基础统计",
        method_family="publication_tables_and_reporting",
        scenario="baseline and model",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(
            RequiredOutput("baseline_table", "table", legacy_roles=("baseline",)),
            RequiredOutput("fitted_model_summary", "model"),
        ),
        resource_class="standard",
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    payload = json.loads(agent._tool_run_r_code(code="baa_table_one(...); baa_model_summary(...)"))

    assert payload["result"]["tables"][0]["metadata"]["role"] == "baseline_table"
    assert payload["result"]["models"][0]["metadata"]["role"] == "fitted_model_summary"


def test_run_r_code_does_not_assign_a_portfolio_role_by_artifact_order(monkeypatch, tmp_path):
    class Backend:
        def run_code(self, request):
            table_path = Path(request.artifacts_dir) / "unassigned.csv"
            table_path.parent.mkdir(parents=True, exist_ok=True)
            table_path.write_text("term,estimate\nx,1.2\n", encoding="utf-8")
            return RRunResult(
                ok=True,
                tables=[RArtifact(name="unassigned", path=str(table_path), kind="table")],
            )

    agent = _DummyAgent()
    agent._analysis_plan = AnalysisPlan(
        plan_id="plan-explicit",
        source="conversation",
        dataset_id="abcdef123456",
        intent="模型",
        category="基础统计",
        method_family="classical_statistics_and_mixed_models",
        scenario="回归",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("effect_estimate_table", "table"),),
        resource_class="standard",
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    payload = json.loads(agent._tool_run_r_code(code="baa_table('unassigned', result)"))

    metadata = payload["result"]["tables"][0]["metadata"]
    assert "role" not in metadata
    assert metadata["artifact_size_bytes"] > 0
    assert metadata["artifact_validation_receipt"]["sha256"] == metadata["artifact_sha256"]


def test_run_r_code_excludes_artifacts_that_fail_server_structure_validation(monkeypatch):
    class Backend:
        def run_code(self, request):
            figure_path = Path(request.artifacts_dir) / "fake.png"
            figure_path.parent.mkdir(parents=True, exist_ok=True)
            figure_path.write_text("not a PNG", encoding="utf-8")
            return RRunResult(
                ok=True,
                figures=[RArtifact(name="fake", path=str(figure_path), kind="figure")],
            )

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    payload = json.loads(_DummyAgent()._tool_run_r_code(code="baa_plot('fake', plot(1))"))

    assert payload["result"]["figures"] == []
    assert any("failed server validation" in warning for warning in payload["result"]["warnings"])


def test_run_r_code_failed_backend_adds_repair_diagnostics(monkeypatch):
    class Backend:
        def run_code(self, request):
            return RRunResult(
                ok=False,
                stderr='Error in coxph(Surv(time, status) ~ age): could not find function "coxph"',
                error="Rscript exited with code 1",
                code=request.code,
            )

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    text = _DummyAgent()._tool_run_r_code(code="coxph(Surv(time, status) ~ age)")

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    diagnostics = payload["repair_diagnostics"]
    assert diagnostics["category"] == "missing_function"
    assert any("coxph" in item for item in diagnostics["facts"])
    assert any("get_r_package_help" in item for item in diagnostics["next_actions"])


def test_run_r_code_reports_the_real_failed_workflow_stage(monkeypatch):
    profile = {
        "dataset_id": "abcdef123456",
        "format": "csv",
        "rows": 2,
        "columns": [{"name": "age", "semantic_type": "numeric", "unique_count": 2}],
    }
    catalog = AnalysisCatalog()
    prepared = AnalysisPreparer(catalog).prepare("data_quality", profile)
    assert prepared.plan is not None

    class Backend:
        def run_code(self, request):
            return RRunResult(
                ok=False,
                error="Scientific data adapter failed",
                code=request.code,
                execution_stage="prepare",
            )

    agent = _DummyAgent()
    agent.dataset.profile = {"summary": {**profile, "variables": profile["columns"]}}
    agent._analysis_plan = prepared.plan
    agent._workflow_run_spec = default_workflow_registry().compile(
        prepared.plan, dataset_profile=profile
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    text = agent._tool_run_r_code(code="summary(dataset)")

    payload = json.loads(text.removeprefix("ERROR: "))
    phases = payload["execution"]["workflow"]["phase_status"]
    assert phases == {
        "preflight": "completed",
        "prepare": "failed",
        "analyze": "not_started",
        "diagnose": "not_started",
        "publish": "not_started",
    }


@pytest.mark.parametrize("mutation", ["dependency", "implementation", "check"])
def test_run_r_code_fails_closed_before_backend_for_unregistered_workflow_step(
    monkeypatch, mutation,
):
    profile = {
        "dataset_id": "abcdef123456",
        "format": "csv",
        "rows": 2,
        "columns": [{"name": "age", "semantic_type": "numeric", "unique_count": 2}],
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare("data_quality", profile)
    assert prepared.plan is not None
    spec = default_workflow_registry().compile(prepared.plan, dataset_profile=profile)
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

    class Backend:
        called = False

        def run_code(self, request):
            self.called = True
            return RRunResult(ok=True)

    backend = Backend()
    agent = _DummyAgent()
    agent.dataset.profile = {"summary": {**profile, "variables": profile["columns"]}}
    agent._analysis_plan = prepared.plan
    agent._workflow_run_spec = replace(
        spec,
        definition=replace(spec.definition, steps=tuple(steps)),
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = agent._tool_run_r_code(code="summary(dataset)")

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["repair_diagnostics"]["category"] == "workflow_contract"
    assert backend.called is False


def test_result_assessment_does_not_advance_post_r_phases_after_analysis_failure():
    raw = json.dumps(
        {
            "execution": {
                "workflow": {
                    "phase_status": {
                        "preflight": "completed",
                        "prepare": "completed",
                        "analyze": "failed",
                        "diagnose": "not_started",
                        "publish": "not_started",
                    }
                }
            }
        }
    )
    assessment = ResultAssessment(
        complete=False,
        r_executed=False,
        missing_roles=("report",),
        missing_diagnostics=("model check",),
        warnings=(),
    )

    payload = json.loads(_attach_result_assessment(raw, assessment))

    phases = payload["execution"]["workflow"]["phase_status"]
    assert phases["analyze"] == "failed"
    assert phases["diagnose"] == "not_started"
    assert phases["publish"] == "not_started"


def test_run_r_code_stops_before_backend_when_workflow_preflight_drifts(monkeypatch):
    class Backend:
        called = False

        def run_code(self, request):
            self.called = True
            return RRunResult(ok=True)

    profile = {
        "dataset_id": "another-dataset",
        "format": "csv",
        "rows": 2,
        "columns": [{"name": "age", "semantic_type": "numeric", "unique_count": 2}],
    }
    catalog = AnalysisCatalog()
    prepared = AnalysisPreparer(catalog).prepare("data_quality", profile)
    assert prepared.plan is not None
    backend = Backend()
    agent = _DummyAgent()
    agent._analysis_plan = prepared.plan
    agent._workflow_run_spec = default_workflow_registry().compile(
        prepared.plan, dataset_profile=profile
    )
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = agent._tool_run_r_code(code="summary(dataset)")

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["repair_diagnostics"]["category"] == "workflow_preflight"
    assert payload["preparation_plan"]["status"] == "preparation_required"
    assert payload["preparation_plan"]["actions"]
    assert payload["execution"]["workflow"]["phase_status"]["preflight"] == "failed"
    assert backend.called is False


def test_run_r_code_blocks_platform_helper_probe_before_worker(monkeypatch):
    class Backend:
        called = False

        def run_code(self, request):
            self.called = True
            return RRunResult(ok=True, stdout="should not run")

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_run_r_code(code='exists("baa_table_one"); args(baa_table_one)')

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["repair_diagnostics"]["category"] == "platform_artifact_probe"
    assert "preloaded" in payload["result"]["error"]
    assert backend.called is False


def test_run_r_code_blocks_direct_saved_artifact_file_read_before_worker(monkeypatch):
    class Backend:
        called = False

        def run_code(self, request):
            self.called = True
            return RRunResult(ok=True, stdout="should not run")

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    blocked_snippets = [
        'print(read.csv("cvd_key_numbers.csv"))',
        'print(read.csv(file = "table_one_cvd.csv"))',
        'print(readr::read_csv("table_one_cvd.csv"))',
        'if (file.exists("cvd_key_numbers.csv")) print("ok")',
        'print(read.csv(baa_path("cvd_key_numbers")))',
        'if (file.exists(baa_path("table_one_cvd"))) print("ok")',
    ]

    for code in blocked_snippets:
        text = _DummyAgent()._tool_run_r_code(code=code)

        assert text.startswith("ERROR: ")
        payload = json.loads(text.removeprefix("ERROR: "))
        assert payload["repair_diagnostics"]["category"] == "platform_artifact_probe"
        assert "isolated artifact directory" in payload["result"]["error"]
    assert backend.called is False


def test_get_r_package_inventory_uses_live_worker_and_chinese_intent(monkeypatch):
    class Backend:
        def list_packages(self, timeout_seconds=30):
            return {
                "ok": True,
                "r_version": "4.6.1",
                "package_count": 3,
                "packages": [
                    {"package": "survival", "version": "3.8-3", "title": "Survival Analysis"},
                    {"package": "survminer", "version": "0.5.0", "title": "Survival plots"},
                    {"package": "ggplot2", "version": "4.0.0", "title": "Create Elegant Data Visualisations"},
                ],
            }

    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: Backend())

    text = _DummyAgent()._tool_get_r_package_inventory(query="生存分析", include_all=True)
    payload = json.loads(text)

    assert payload["ok"] is True
    assert payload["runtime"]["live_inventory_ok"] is True
    assert payload["runtime"]["r_version"] == "4.6.1"
    assert payload["counts"]["live_installed_packages"] == 3
    assert any(
        item["name"] == "Survival, competing risks, prognosis" for item in payload["matched_capabilities"]
    )
    assert "survival" in payload["all_installed_package_names"]
    assert "survival" in payload["recommended_candidate_packages"]
    assert "rmda" not in payload["recommended_candidate_packages"]
    for capability in payload["matched_capabilities"]:
        assert set(capability["packages"]) <= {"survival", "survminer", "ggplot2"}
    assert "requireNamespace" in " ".join(payload["usage_protocol"])


def test_get_r_package_help_uses_backend_docs(monkeypatch):
    class Backend:
        call = None

        def package_help(
            self,
            package,
            query="",
            limit=30,
            include_docs=True,
            doc_limit=3,
            max_doc_chars=4000,
            timeout_seconds=30,
        ):
            self.call = {
                "package": package,
                "query": query,
                "limit": limit,
                "include_docs": include_docs,
                "doc_limit": doc_limit,
                "max_doc_chars": max_doc_chars,
                "timeout_seconds": timeout_seconds,
            }
            return {
                "ok": True,
                "package": package,
                "version": "3.8-3",
                "title": "Survival Analysis",
                "matched_exports": ["coxph", "survfit"],
                "help_topics": [{"topic": "coxph", "title": "Fit Proportional Hazards Regression Model"}],
                "help_documents": [{"topic": "coxph", "text": "Usage: coxph(formula, data, ...)"}],
                "vignettes": [],
            }

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_get_r_package_help(
        packages=["survival"],
        query="cox model",
        limit=10,
        include_docs=True,
        doc_limit=2,
        max_doc_chars=2000,
    )
    payload = json.loads(text)

    assert payload["ok"] is True
    assert payload["packages"] == ["survival"]
    assert payload["results"][0]["package"] == "survival"
    assert "coxph" in payload["results"][0]["matched_exports"]
    assert payload["results"][0]["help_documents"][0]["topic"] == "coxph"
    assert backend.call["include_docs"] is True
    assert backend.call["doc_limit"] == 2
    assert backend.call["max_doc_chars"] == 2000
    assert "not a per-package template" in payload["usage_protocol"][0]


def test_get_r_package_help_without_package_cross_searches_installed_docs(monkeypatch):
    class Backend:
        call = None

        def package_help(
            self,
            package,
            query="",
            limit=30,
            include_docs=True,
            doc_limit=3,
            max_doc_chars=4000,
            timeout_seconds=30,
        ):
            self.call = {
                "package": package,
                "query": query,
                "limit": limit,
                "include_docs": include_docs,
                "doc_limit": doc_limit,
                "max_doc_chars": max_doc_chars,
                "timeout_seconds": timeout_seconds,
            }
            return {
                "ok": True,
                "mode": "cross_package_search",
                "query": query,
                "recommended_packages": [{"package": "survival", "top_topics": ["coxph"]}],
                "search_results": [{"package": "survival", "topic": "coxph", "title": "Fit Cox Model"}],
                "help_documents": [{"package": "survival", "topic": "coxph", "text": "Usage: coxph(...)"}],
            }

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    text = _DummyAgent()._tool_get_r_package_help(
        query="coxph",
        limit=10,
        include_docs=True,
        doc_limit=2,
        max_doc_chars=2000,
    )
    payload = json.loads(text)

    assert payload["ok"] is True
    assert payload["mode"] == "cross_package_search"
    assert payload["packages"] == []
    assert payload["cross_package_search"]["recommended_packages"][0]["package"] == "survival"
    assert backend.call == {
        "package": "",
        "query": "coxph",
        "limit": 10,
        "include_docs": True,
        "doc_limit": 2,
        "max_doc_chars": 2000,
        "timeout_seconds": 30,
    }
    assert "installed R help index" in payload["usage_protocol"][0]


def test_run_r_code_artifacts_are_extracted_for_job_history():
    raw = json.dumps(
        {
            "job_id": "job-history-1",
            "result": {
                "tables": [
                    {
                        "name": "table_one_baseline",
                        "path": "/tmp/table_one_baseline.csv",
                        "kind": "table",
                        "mime_type": "text/csv",
                    }
                ],
                "figures": [
                    {
                        "name": "age_plot",
                        "path": "/tmp/age_plot.png",
                        "kind": "figure",
                        "mime_type": "image/png",
                    }
                ],
                "texts": [
                    {
                        "name": "interpretation",
                        "path": "/tmp/interpretation.txt",
                        "kind": "text",
                        "mime_type": "text/plain",
                    }
                ],
                "models": [
                    {
                        "name": "primary_model",
                        "path": "/tmp/primary_model.txt",
                        "kind": "model",
                        "mime_type": "text/plain",
                    }
                ],
            },
        }
    )

    artifacts = _research_artifacts_from_tool_result(raw)

    assert [artifact["type"] for artifact in artifacts] == ["table", "figure", "text", "model"]
    assert artifacts[0]["name"] == "table_one_baseline"
    assert artifacts[0]["path"] == "/tmp/table_one_baseline.csv"
    assert artifacts[0]["artifact_id"].startswith("research:tables:")
    assert all(artifact["source_tool"] == "run_r_code" for artifact in artifacts)
    assert {artifact["job_id"] for artifact in artifacts} == {"job-history-1"}


def test_run_r_code_model_payload_hides_local_artifact_paths():
    raw = json.dumps(
        {
            "dataset": {
                "dataset_id": "abcdef123456",
                "name": "clinical.csv",
                "stored_path": "/Users/researcher/Projects/data-analysis-agent/uploads/research/clinical.csv",
            },
            "result": {
                "stdout": "saved to /Users/researcher/Projects/data-analysis-agent/outputs/research/sid/job/table.csv",
                "tables": [
                    {
                        "name": "table_one_baseline",
                        "path": "/Users/researcher/Projects/data-analysis-agent/outputs/research/sid/job/table_one_baseline.csv",
                        "kind": "table",
                        "mime_type": "text/csv",
                    }
                ],
                "figures": [],
                "texts": [],
            },
            "artifact_previews": {
                "tables": [
                    {
                        "name": "table_one_baseline",
                        "preview_csv": "Variable,Overall\nage,62.1",
                    }
                ],
            },
        }
    )

    sanitized = _sanitize_r_tool_result_for_model(raw)
    payload = json.loads(sanitized)

    assert "/Users/researcher" not in sanitized
    assert "stored_path" not in payload["dataset"]
    assert "stdout" not in payload["result"]
    assert "path" not in payload["result"]["tables"][0]
    assert payload["artifact_previews"]["tables"][0]["preview_csv"] == "Variable,Overall\nage,62.1"


def test_r_artifact_model_preview_reports_bounded_shape_instead_of_silent_truncation(tmp_path):
    table_path = tmp_path / "wide.csv"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"column_{index}" for index in range(35)])
    for row_index in range(25):
        writer.writerow([("x" * 300) if column_index == 0 else row_index for column_index in range(35)])
    table_path.write_text(buffer.getvalue(), encoding="utf-8")
    result = RRunResult(
        ok=True,
        tables=[
            RArtifact(
                name="wide",
                path=str(table_path),
                kind="table",
                mime_type="text/csv",
            )
        ],
    )

    preview = _DummyAgent()._artifact_previews(result)["tables"][0]
    rows = list(csv.reader(io.StringIO(preview["preview_csv"])))

    assert preview["truncated"] is True
    assert preview["total_rows"] == 25
    assert preview["total_columns"] == 35
    assert len(rows) == 21
    assert all(len(row) <= 30 for row in rows)
    assert max(len(field) for row in rows for field in row) <= 256


def test_run_r_code_model_artifacts_omit_download_paths():
    artifacts = [
        {
            "type": "table",
            "artifact_id": "research:tables:/Users/researcher/Desktop/out/table.csv",
            "name": "table_one_baseline",
            "path": "/Users/researcher/Desktop/out/table.csv",
            "kind": "table",
            "mime_type": "text/csv",
            "metadata": {"role": "baseline"},
            "source_tool": "run_r_code",
            "url": "/api/session/sid/research/artifact?path=/Users/researcher/Desktop/out/table.csv",
        }
    ]

    sanitized = _sanitize_tool_artifacts_for_model(artifacts)

    assert sanitized == [
        {
            "type": "table",
            "name": "table_one_baseline",
            "kind": "table",
            "mime_type": "text/csv",
            "metadata": {"role": "baseline"},
            "source_tool": "run_r_code",
        }
    ]


def test_single_uploaded_dataset_tolerates_inferred_display_dataset_id():
    class Store:
        def __init__(self):
            self.dataset = _DummyDataset()

        def list(self, sid):
            return [self.dataset]

        def get(self, sid, dataset_id):
            if dataset_id == self.dataset.dataset_id:
                return self.dataset
            raise ValueError("Invalid dataset_id")

    agent = _DummyAgent()
    agent._research_store = lambda: Store()

    assert agent._resolve_research_dataset("clinical").dataset_id == "abcdef123456"


def test_missing_frozen_dataset_never_falls_back_to_the_only_other_dataset():
    class Store:
        def __init__(self):
            self.dataset = _DummyDataset()

        def list(self, sid):
            return [self.dataset]

        def get(self, sid, dataset_id):
            raise ValueError("Invalid dataset_id")

    agent = _DummyAgent()
    agent._research_store = lambda: Store()
    agent._research_dataset_id = "missing-frozen-dataset"

    with pytest.raises(FileNotFoundError, match="missing-frozen-dataset"):
        RToolsMixin._resolve_research_dataset(agent)


def test_multiple_datasets_require_an_explicit_active_dataset():
    first = _DummyDataset()
    second = _DummyDataset()
    second.dataset_id = "fedcba654321"

    class Store:
        def list(self, sid):
            return [first, second]

        def get(self, sid, dataset_id):
            return next((item for item in (first, second) if item.dataset_id == dataset_id), None)

    agent = _DummyAgent()
    agent._research_store = lambda: Store()

    with pytest.raises(ValueError, match="请选择当前数据集"):
        RToolsMixin._resolve_research_dataset(agent)

    agent._research_dataset_id = second.dataset_id
    assert RToolsMixin._resolve_research_dataset(agent).dataset_id == second.dataset_id
    with pytest.raises(ValueError, match="不能在分析中切换"):
        RToolsMixin._resolve_research_dataset(agent, first.dataset_id)


def test_run_r_code_huge_finite_timeout_is_clamped(monkeypatch):
    class Backend:
        requests = []

        def run_code(self, request):
            self.requests.append(request)
            return RRunResult(ok=True, stdout="ok")

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)

    _DummyAgent()._tool_run_r_code(
        code="summary(dataset)",
        timeout_seconds=10**100,
    )
    _DummyAgent()._tool_run_r_code(
        code="summary(dataset)",
        timeout_seconds="999999999999999999999999999",
    )

    assert [request.timeout_seconds for request in backend.requests] == [
        2_147_483_647,
        2_147_483_647,
    ]


def test_run_r_code_keeps_invented_package_names_inside_the_ai_repair_loop(monkeypatch):
    """An invented package id (e.g. "p") is a code bug only the model can fix:
    the run fails with repair guidance and no dependency_gate payload — the
    user never sees a modal they cannot act on."""
    import r_runtime.dependency_gate as dependency_gate_module

    class Backend:
        run_calls = 0
        runtime_env = {"PATH": ""}

        def package_readiness(self, packages, timeout_seconds=30):
            return {
                "ok": True,
                "packages": [
                    {
                        "package": package,
                        "installed": package != "p",
                        "loadable": package != "p",
                        "status": "missing" if package == "p" else "ready",
                        "warnings": [],
                        "error": "Package is not installed" if package == "p" else "",
                        "runtime_check": "not_required",
                        "runtime_detail": "",
                    }
                    for package in packages
                ],
            }

        def available_package_names(self, timeout_seconds=90):
            return ("jsonlite", "ggplot2", "survival")  # "p" is on no mirror

        def run_code(self, request):
            self.run_calls += 1
            return RRunResult(ok=True)

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)
    monkeypatch.setattr(
        dependency_gate_module,
        "_repo_snapshot_cache",
        {"at": float("-inf"), "names": None},
    )

    text = _DummyAgent()._tool_run_r_code(code="p::foo(dataset)")

    assert text.startswith("ERROR: ")
    payload = json.loads(text.removeprefix("ERROR: "))
    assert payload["execution"]["status"] == "failed"
    assert payload["result"]["execution_stage"] == "dependency_preflight"
    assert "不存在的 R 包" in payload["result"]["error"]
    assert "p" in payload["result"]["error"]
    # The gate payload is what pops the user modal — it must stay off this path.
    assert "dependency_gate" not in payload
    assert payload["repair_diagnostics"]["category"] == "invalid_package_reference"
    assert backend.run_calls == 0


def test_two_round_package_name_self_heal(monkeypatch):
    """Closure proof for the invented-package repair loop.

    Round 1: code references invented "p" → preflight fails with concrete
    repair guidance.  Round 2: corrected code passes preflight and R runs.
    The user never sees a modal; the model self-corrects.
    """
    import r_runtime.dependency_gate as dependency_gate_module

    class Backend:
        run_calls = 0
        executed_code = ""
        runtime_env = {"PATH": ""}

        def package_readiness(self, packages, timeout_seconds=30):
            return {
                "ok": True,
                "packages": [
                    {
                        "package": package,
                        "installed": package != "p",
                        "loadable": package != "p",
                        "status": "missing" if package == "p" else "ready",
                        "warnings": [],
                        "error": "Package is not installed" if package == "p" else "",
                        "runtime_check": "not_required",
                        "runtime_detail": "",
                    }
                    for package in packages
                ],
            }

        def available_package_names(self, timeout_seconds=90):
            return ("jsonlite",)

        def run_code(self, request):
            assert "p::" not in request.code, "corrected code must reach R"
            self.run_calls += 1
            self.executed_code = request.code
            return RRunResult(ok=True)

    backend = Backend()
    monkeypatch.setattr("agent.tools.research.r_tools.get_r_backend", lambda: backend)
    monkeypatch.setattr(
        dependency_gate_module,
        "_repo_snapshot_cache",
        {"at": float("-inf"), "names": None},
    )
    agent = _DummyAgent()

    # Round 1 — the model's first draft invents a package.
    text1 = agent._tool_run_r_code(code="p::foo(dataset)")
    payload1 = json.loads(text1.removeprefix("ERROR: "))
    assert payload1["execution"]["status"] == "failed"
    assert "不存在的 R 包" in payload1["result"]["error"]
    assert "区分大小写" in payload1["result"]["error"]
    assert "dependency_gate" not in payload1  # no user modal
    assert backend.run_calls == 0

    # Round 2 — guided by the repair diagnostics, the model drops the
    # invented reference; preflight passes and R executes for real.
    text2 = agent._tool_run_r_code(code="jsonlite::fromJSON('{\"a\":1}')")
    payload2 = json.loads(text2.removeprefix("ERROR: "))
    assert payload2["result"]["ok"] is True
    assert backend.run_calls == 1
