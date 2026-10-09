"""Real CSV preparation and registered production portfolios for core methods.

These checks retain the actual prepared plan, including automatically resolved
roles, its compiled output portfolio and server-stamped artifact receipts.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.science.test_general_behavior_workflows import CASES as BEHAVIOR_CASES
from tests.science.test_method_numerics import backend  # noqa: F401
from tests.science.test_script_library import _ENGINE_DATASETS, _ENGINE_PLANS, _fixture_csv
from tests.science.test_trial_evidence_workflows import CASES as TRIAL_CASES
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.result_validation import ResultValidator
from agent.analysis.script_library import render_script
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.inspection import inspection_summary
from r_runtime.protocol import DatasetRef, RRunRequest


METHOD_IDS = (
    "group_tests", "regression_glm", "survival_km_cox", "diagnostic_roc",
    "tabular_ml_workflow", "prognostic_model", "competing_risk", "time_dependent_roc",
    "pairwise_meta", "forecasting", "categorical_ordinal_models", "bayesian_robust_stats",
    "discrete_choice_model", "network_meta",
)
METHOD_CASES = tuple((method, "default") for method in METHOD_IDS) + (("competing_risk", "unadjusted"),)


def method_fixture(template_id, design="default"):
    """Reuse public synthetic cases; plans supply only user input values."""
    if template_id in _ENGINE_PLANS:
        inputs = {key:item.value for key,item in _ENGINE_PLANS[template_id]().resolved_inputs.items()}
        factory = _ENGINE_DATASETS.get(template_id, _fixture_csv)
        if template_id == "pairwise_meta":
            inputs["effect_scale"] = "log_ratio"
        elif template_id == "forecasting":
            inputs.update(analysis_method="ets", horizon=6, test_size=12)
        elif template_id == "categorical_ordinal_models":
            inputs["covariates"] = ["age"]
        elif template_id == "competing_risk":
            # Include real censoring and a confirmed group to exercise Gray,
            # group CIF and cause-specific Cox alongside Fine-Gray.
            data = "set.seed(451);dataset<-data.frame(t=rexp(150),type=sample(c(0,1,2),150,replace=TRUE),x=rnorm(150),group=rep(c('A','B'),75))"
            inputs = {"followup_time":"t", "event_type":"type", "target_event_level":"1",
                      "competing_event_level":"2", "censor_event_level":"0"}
            if design != "unadjusted":
                inputs.update(covariates=["x"], group="group")
            return data, inputs
        return factory, inputs
    if template_id == "network_meta":
        data, inputs, _ = TRIAL_CASES[template_id]
        return data, {**inputs, "outcome_direction":"lower_better"}
    _, data, inputs, _, _ = next(case for case in BEHAVIOR_CASES if case[0] == template_id)
    if template_id == "bayesian_robust_stats":
        inputs = {**inputs, "analysis_method":"bayesian",
                  "prior_specification":json.dumps({"b0":0,"B0":.01,"c0":2,"d0":2}),
                  "samples":500,"burnin":100,"seed":1729}
    return data, inputs


def _save(path, payload):
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


@pytest.mark.parametrize("template_id,design", METHOD_CASES,
                         ids=[method if design == "default" else method+"-"+design for method,design in METHOD_CASES])
def test_registered_method_csv_production_portfolio(backend, tmp_path, template_id, design):
    data, inputs = method_fixture(template_id, design)
    dataset_id = "production-method-"+template_id+"-"+design
    dataset_path = tmp_path / "input.csv"
    report_path = tmp_path / "production-validation.json"
    report = {"template_id":template_id,"design":design,"provided_inputs":inputs,"stage":"materialize"}
    _save(report_path, report)
    if callable(data):
        data(dataset_path)
    else:
        dummy = tmp_path / "dummy.csv"
        dummy.write_text("dummy\n1\n",encoding="utf-8")
        materialized = backend.run_code(RRunRequest(
            job_id="materialize-"+template_id, dataset=DatasetRef(dataset_id,str(dummy),"csv"),
            code=data+'\nbaa_table("source",dataset)', artifacts_dir=str(tmp_path/"source"), timeout_seconds=120,
        ))
        assert materialized.ok, materialized.error+'\n'+materialized.stderr
        source = next(a for a in materialized.tables if a.name=="source")
        dataset_path = Path(source.path)
    report.update(stage="inspect",dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest())
    _save(report_path,report)
    inspected = backend.inspect_dataset(DatasetRef(dataset_id,str(dataset_path),"csv"),timeout_seconds=120)
    assert inspected.ok, inspected.error+'\n'+inspected.stderr
    profile = inspection_summary(inspected)
    profile.update(dataset_id=dataset_id,format="csv",columns=profile["variables"])
    packages = backend.list_packages()
    assert packages["ok"]
    installed = [p.get("name",p.get("Package",p.get("package",""))) for p in packages["packages"]]
    report.update(stage="prepare",profile_columns=profile["variables"])
    _save(report_path,report)
    try:
        prepared = AnalysisPreparer(AnalysisCatalog(),runtime_packages=installed).prepare(
            template_id,profile,provided_inputs=inputs,
        )
    except Exception as exc:
        report["preparation_error"] = str(exc)
        _save(report_path,report)
        raise
    report.update(stage="prepare",preparation_status=prepared.status,reason=prepared.reason,
                  questions=[asdict(q) for q in prepared.questions],
                  resolved_inputs=[asdict(i) for i in prepared.resolved_inputs],
                  automatically_resolved_scientific_inputs=[asdict(i) for i in prepared.resolved_inputs
                      if i.input_id not in inputs and i.kind in {"column","columns","level","object"}])
    _save(report_path,report)
    assert prepared.status=="ready" and prepared.plan is not None, repr(prepared)
    plan = prepared.plan
    assert set(inputs) <= set(plan.resolved_inputs)
    if template_id=="prognostic_model":
        assert "event" not in plan.resolved_inputs, "An unselected event must not switch the confirmed binary outcome to survival."
    if template_id=="categorical_ordinal_models":
        assert "exposure_2" not in plan.resolved_inputs, "Multinomial analysis must not auto-add a count-model offset."
    if design=="unadjusted":
        assert not {"group","covariates"}.intersection(plan.resolved_inputs), "Unselected scientific roles must not change the unadjusted competing-risk design."
        forbidden_roles = {"table_1","survival_effect_estimates","cause_specific_cox",
                           "group_fine_gray","group_cause_specific_cox","gray_tests"}
        assert not forbidden_roles.intersection(o.role for o in plan.output_portfolio)
    report.update(stage="render",portfolio=[asdict(o) for o in plan.output_portfolio])
    _save(report_path,report)
    try:
        script = render_script(plan)
        assert script is not None
        checked = "\n".join((RToolsMixin._frozen_input_prelude(plan),
                              RToolsMixin._frozen_user_code_block(script,plan),
                              RToolsMixin._frozen_input_epilogue(plan),
                              'if(!identical(res,"OK"))stop(res)'))
    except Exception as exc:
        report["render_error"] = str(exc)
        _save(report_path,report)
        raise
    job_id = "production-"+template_id
    artifacts_root = tmp_path / "production"
    executed = backend.run_code(RRunRequest(
        job_id=job_id,dataset=DatasetRef(dataset_id,str(dataset_path),"csv"),
        code=checked,artifacts_dir=str(artifacts_root),timeout_seconds=240,
    ))
    report.update(stage="execute",execution_status="succeeded" if executed.ok else "failed",
                  error=executed.error,stderr=executed.stderr[-12000:])
    _save(report_path,report)
    assert executed.ok, executed.error+'\n'+executed.stderr
    diagnostic_status = RToolsMixin._attach_analysis_plan_metadata(
        SimpleNamespace(_analysis_plan=plan),executed,dataset_id,
        job_id=job_id,artifacts_root=str(artifacts_root),
    )
    artifacts = [dict(a.to_dict(),source_tool="run_r_code",job_id=job_id)
                 for a in (*executed.tables,*executed.figures,*executed.texts,*executed.models)]
    execution = dict(dataset_id=dataset_id,plan_id=plan.plan_id,status="succeeded",diagnostic_status=diagnostic_status)
    assessment = ResultValidator().validate(plan,execution,artifacts)
    report.update(stage="validate",assessment=asdict(assessment),artifacts=[
        dict(name=a["name"],kind=a["kind"],role=a["metadata"].get("role"),semantic_key=a["metadata"].get("semantic_key"),
             receipt=a["metadata"].get("artifact_validation_receipt")) for a in artifacts])
    _save(report_path,report)
    assert assessment.complete and assessment.next_action=="complete", repr(assessment)
    if design=="unadjusted":
        names = {a.name for a in executed.tables}
        assert not forbidden_roles.intersection(names), "Unadjusted CIF must not emit placeholder adjusted-model tables."
        counts = next(a for a in executed.tables if a.name=="table_2")
        cif = next(a for a in executed.tables if a.name=="cif_estimates")
        with Path(counts.path).open(encoding="utf-8-sig",newline="") as handle:
            count_rows = list(csv.DictReader(handle))
        with Path(cif.path).open(encoding="utf-8-sig",newline="") as handle:
            cif_rows = list(csv.DictReader(handle))
        with dataset_path.open(encoding="utf-8-sig",newline="") as handle:
            observed_counts = Counter(row["type"] for row in csv.DictReader(handle))
        assert sum(int(row["人数"]) for row in count_rows)==150
        assert {row["类型"] for row in count_rows}=={"删失","目标","竞争"}
        assert {row["编码"]:int(row["人数"]) for row in count_rows}==dict(observed_counts)
        assert cif_rows and all(0<=float(row["CIF"])<=1 for row in cif_rows)
        assert all(float(row["时间"])>=0 for row in cif_rows)
        assert {row["事件"] for row in cif_rows}=={"总体 1","总体 2"}
        for event in ("总体 1","总体 2"):
            curve = [row for row in cif_rows if row["事件"]==event]
            assert all(float(a["时间"])<=float(b["时间"]) for a,b in zip(curve,curve[1:]))
            assert all(float(a["CIF"])<=float(b["CIF"])+1e-12 for a,b in zip(curve,curve[1:]))
            assert float(curve[-1]["CIF"])>0
        report["unadjusted_result_checks"] = {
            "observed_event_count":150,"observed_event_counts":dict(observed_counts),
            "cif_rows":len(cif_rows),"nonzero_monotone_cif_curves":["总体 1","总体 2"],
            "adjusted_or_group_model_artifacts":[],
        }
        _save(report_path,report)
