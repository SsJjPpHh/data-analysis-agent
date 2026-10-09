"""Actual R coverage for previously unread frozen inputs and their boundaries."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.method_policy import SUPPORTED_DESIGNS
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.analysis.script_library import render_script
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.protocol import DatasetRef, RRunRequest
from tests.science.test_clinical_public_workflows import validate_result
from tests.science.test_method_numerics import backend  # noqa: F401


def run_checked(backend, tmp_path, template_id, data, inputs, probe=None, error=None):
    template = AnalysisCatalog().get(template_id)
    inputs = dict(inputs)
    if template_id in SUPPORTED_DESIGNS:
        inputs["design_scope"] = SUPPORTED_DESIGNS[template_id]
    specs = {item.id: item for item in template.input_specs}
    resolved = {key: ResolvedInput(key, key, specs[key].kind, value, "user") for key, value in inputs.items()}
    outputs = tuple(item for item in template.required_outputs if conditions_match(item.conditions, resolved))
    if template_id == "publication_table":
        # Use the same production portfolio normalization as AnalysisPreparer,
        # including the actual overall-only Table 1 acceptance contract.
        outputs = (
            ScientificOutputPortfolioCompiler()
            .compile(
                source="template",
                template_id=template_id,
                intent=template.title,
                method_family=template.family,
                scenario=template.scenario,
                baseline_outputs=template.required_outputs,
                dataset_profile={"dataset_id": "numeric-fixture", "format": "csv"},
                resolved_inputs=resolved,
                packages=template.packages,
            )
            .outputs
        )
    plan = AnalysisPlan(
        plan_id="additional-input-native",
        source="template",
        dataset_id="numeric-fixture",
        intent=template.title,
        category=template.category,
        method_family=template.family,
        scenario=template.scenario,
        variable_bindings={},
        packages=(),
        prerequisites=(),
        assumptions=(),
        diagnostics=template.diagnostics,
        required_outputs=outputs,
        resource_class="standard",
        template_id=template_id,
        resolved_inputs=resolved,
    )
    script = render_script(plan)
    assert script is not None
    if probe:
        script += '\nbaa_text("native_probe",jsonlite::toJSON(' + probe + ",auto_unbox=TRUE,digits=15))"
    code = (
        data
        + "\n"
        + BindingHelpersMixin._frozen_input_prelude(plan)
        + "\n"
        + BindingHelpersMixin._frozen_user_code_block(script, plan)
        + "\n"
        + BindingHelpersMixin._frozen_input_epilogue(plan)
    )
    path = tmp_path / "input.csv"
    path.write_text("x\n1\n", encoding="utf-8")
    result = backend.run_code(
        RRunRequest(
            job_id="additional-input-native",
            dataset=DatasetRef("numeric-fixture", str(path), "csv", path.name),
            code=code,
            artifacts_dir=str(tmp_path / "artifacts"),
            timeout_seconds=90,
        )
    )
    if error is not None:
        assert not result.ok
        assert error in result.error + "\n" + result.stderr
        return result, None
    assert result.ok, result.error + "\n" + result.stderr
    validate_result(plan, result, tmp_path, "additional-input-native")
    if not probe:
        return result, None
    artifact = next(item for item in result.texts if item.name == "native_probe")
    return result, json.loads(Path(artifact.path).read_text(encoding="utf-8"))


def test_publication_missing_indicator_reports_group_counts_and_unknowns(backend, tmp_path):
    result, probe = run_checked(
        backend,
        tmp_path,
        "publication_table",
        'dataset<-data.frame(g=rep(c("A","B"),each=4),y=seq_len(8),miss=factor(c(0,1,NA,0,1,1,0,NA)))',
        {"group": "g", "continuous_variables": ["y"], "missingness_indicator": "miss"},
        "list(audit=.missing_indicator_summary,note=.t1_note)",
    )
    rows = probe["audit"]
    assert [(row["分组"], row["标记缺失行数"], row["标记已知行数"], row["标记未知行数"]) for row in rows] == [
        ("A", 1, 3, 1),
        ("B", 2, 3, 1),
    ]
    assert [row["标记缺失率"] for row in rows] == pytest.approx([1 / 3, 2 / 3])
    assert "不据此推断缺失机制" in probe["note"]
    exported = next(item for item in result.tables if item.name == "table_5")
    with Path(exported.path).open(encoding="utf-8-sig", newline="") as handle:
        audits = [row for row in csv.DictReader(handle) if row.get("类型") == "missingness_indicator(0/1)"]
    assert [(row["分组"], int(row["标记缺失行数"])) for row in audits] == [("A", 1), ("B", 2)]


def test_publication_logical_missing_indicator_without_group(backend, tmp_path):
    _, probe = run_checked(
        backend,
        tmp_path,
        "publication_table",
        "dataset<-data.frame(y=1:4,miss=c(TRUE,FALSE,NA,TRUE))",
        {"continuous_variables": ["y"], "missingness_indicator": "miss"},
        "list(missing=.missing_indicator_summary$标记缺失行数,known=.missing_indicator_summary$标记已知行数,unknown=.missing_indicator_summary$标记未知行数,group=.missing_indicator_summary$分组)",
    )
    assert probe == {"missing": 2, "known": 3, "unknown": 1, "group": "总体"}


def test_publication_rejects_nonbinary_missing_indicator(backend, tmp_path):
    run_checked(
        backend,
        tmp_path,
        "publication_table",
        "dataset<-data.frame(y=1:4,miss=c(0,1,2,0))",
        {"continuous_variables": ["y"], "missingness_indicator": "miss"},
        error="缺失标记必须明确编码为 0=未缺失、1=缺失",
    )


DESIGN_INPUTS = {
    "sample_size_power": {
        "outcome": "y",
        "effect_size": 0.5,
        "alpha": 0.05,
        "power": 0.8,
        "allocation_ratio": 2,
    },
    "group_sequential": {
        "outcome": "y",
        "information_time": [0.3, 0.6, 1],
        "effect_size": 0.5,
        "alpha": 0.025,
        "power": 0.8,
        "alpha_spending": "OBrienFleming",
    },
}


@pytest.mark.parametrize("template_id", list(DESIGN_INPUTS))
def test_design_outcome_audit_does_not_reestimate_confirmed_effect_or_power(backend, tmp_path, template_id):
    results = []
    for index, size in enumerate((12, 24)):
        directory = tmp_path / str(index)
        directory.mkdir()
        design = "ss_table$样本量" if template_id == "sample_size_power" else "c(n_total,gs$upper$bound)"
        note = "plan_txt" if template_id == "sample_size_power" else "results_txt"
        _, probe = run_checked(
            backend,
            directory,
            template_id,
            f"dataset<-data.frame(y={100 * index}+seq_len({size})*{index + 1});dataset$y[1]<-NA",
            DESIGN_INPUTS[template_id],
            f"list(audit=.outcome_audit,design={design},note={note})",
        )
        assert probe["audit"]["bound"] is True
        assert probe["audit"]["rows"] == size
        assert probe["audit"]["observed"] == size - 1
        assert probe["audit"]["missing"] == 1
        assert "不计算观察/事后 power" in probe["note"]
        assert "不估计或替代预先指定的效应量" in probe["note"]
        results.append(probe["design"])
    assert results[0] == pytest.approx(results[1]), (
        "Data-derived outcome differences must not change predeclared design parameters"
    )


@pytest.mark.parametrize("template_id", list(DESIGN_INPUTS))
def test_two_continuous_pilot_observations_are_audited_without_replacing_design_parameters(
    backend, tmp_path, template_id
):
    _, probe = run_checked(
        backend,
        tmp_path,
        template_id,
        "dataset<-data.frame(y=c(.4,.7))",
        DESIGN_INPUTS[template_id],
        "list(observed=.outcome_audit$observed,distinct=.outcome_audit$distinct,note=.outcome_audit$note)",
    )
    assert probe["observed"] == 2 and probe["distinct"] == 2
    assert "当前观测不足以评估终点分布" in probe["note"]
    assert "设计计算仍使用已确认的外部参数" in probe["note"]


@pytest.mark.parametrize("template_id", list(DESIGN_INPUTS))
@pytest.mark.parametrize(
    "outcome,error",
    [
        ('rep(c("A","B"),6)', "连续数值终点"),
        ("rep(c(0,1),6)", "两个观测水平"),
        ("c(Inf,2:12)", "非有限数值"),
    ],
)
def test_design_rejects_incompatible_selected_response(backend, tmp_path, template_id, outcome, error):
    run_checked(
        backend,
        tmp_path,
        template_id,
        f"dataset<-data.frame(y={outcome})",
        DESIGN_INPUTS[template_id],
        error=error,
    )


def test_large_data_uses_verified_frozen_dataset_object_and_reports_contract(backend, tmp_path):
    result, probe = run_checked(
        backend,
        tmp_path,
        "large_data_engine",
        'dataset<-data.frame(g=c("A","A","B","B"),x=c(1,3,5,7),z=c(9,8,7,6))',
        {"dataset": "numeric-fixture", "group": "g", "measure": "x"},
        "list(bound=.dataset_binding_checked,summary=as.data.frame(.summary),note=.dataset_audit_note,size=.size)",
    )
    assert probe["bound"] is True
    assert [(row["g"], row["x"], row["行数"]) for row in probe["summary"]] == [("A", 2, 2), ("B", 6, 2)]
    assert "4 行、3 列" in probe["note"] and "已与当前数据对象核对一致" in probe["note"]
    assert "numeric-fixture" not in probe["note"]
    assert probe["size"][0]["数据集绑定"] == "当前冻结数据集已核对"
    recommendation = next(item for item in result.texts if item.name == "analysis_recommendation")
    assert "选定数据集：当前已导入表格" in Path(recommendation.path).read_text(encoding="utf-8")


def test_large_data_rejects_a_reference_to_another_dataset(backend, tmp_path):
    run_checked(
        backend,
        tmp_path,
        "large_data_engine",
        "dataset<-data.frame(x=1:4)",
        {"dataset": "different-dataset", "measure": "x"},
        error="冻结数据集引用未匹配当前已导入表格",
    )
