"""Real-R checks for clinical input wiring and actual package computations."""

import csv
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend, run_method  # noqa: F401


SURVIVAL = '''set.seed(271); n <- 150
x <- rnorm(n); t <- rexp(n,exp(.2*x)); c <- rexp(n,.25)
dataset <- data.frame(t=pmin(t,c),e=as.integer(t<=c),x=x,score=x,arm=rep(c("A","B"),each=75))'''


def test_time_interaction_cox_matches_declared_survival_model(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "extended_survival", SURVIVAL,
                      {"followup_time": "t", "event": "e", "time_dependent_variable": "x"},
                      '''list(coef=unname(coef(.fit)),ref=unname(coef(survival::coxph(
                          survival::Surv(t,e)~x+tt(x),data=dataset,tt=function(x,t,...) x*log1p(t)))))''')
    assert p["coef"] == pytest.approx(p["ref"], abs=1e-10)


def test_counting_process_requires_actual_intervals(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "extended_survival", SURVIVAL,
                      {"followup_time": "t", "event": "e", "time_dependent_variable": "x",
                       "analysis_method": "counting_process"}, "list(message=res)", expect_success=False)
    assert "起始时间" in p["message"]


def test_fine_gray_is_crr_not_cause_specific_cox(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "competing_risk",
                      '''set.seed(451); dataset <- data.frame(t=rexp(150),type=sample(c(0,1,2),150,replace=TRUE),x=rnorm(150))''',
                      {"followup_time": "t", "event_type": "type", "target_event_level": "1",
                       "competing_event_level": "2", "covariates": ["x"]},
                      "list(coef=unname(.fg$coef),ref=unname(cmprsk::crr(dataset$t,dataset$type,as.matrix(dataset['x']))$coef))")
    assert p["coef"] == pytest.approx(p["ref"], abs=1e-10)


def test_time_roc_accounts_for_early_censoring(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "time_dependent_roc", SURVIVAL,
                      {"followup_time": "t", "event": "e", "prediction": "score", "evaluation_time": .6},
                      "list(auc=.auc,ref=timeROC::timeROC(T=dataset$t,delta=dataset$e,marker=dataset$score,cause=1,times=.6,weighting='marginal')$AUC[2],ci=.auc_table)")
    assert p["auc"] == pytest.approx(p["ref"], abs=1e-12)


def test_probability_brier_matches_uncensored_squared_error(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "time_dependent_roc",
                      '''set.seed(8); dataset <- data.frame(t=runif(120,0,2),e=1,p=runif(120,.1,.9))''',
                      {"followup_time": "t", "event": "e", "prediction": "p", "evaluation_time": 1,
                       "prediction_scale": "risk_probability"},
                      "list(brier=.brier$IPCW_Brier,ref=mean((as.numeric(dataset$t<=1)-dataset$p)^2))")
    assert p["brier"] == pytest.approx(p["ref"], abs=1e-12)


def test_rmst_uses_tau_group_direction_and_covariates(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "clinical_rmst_non_ph", SURVIVAL,
                      {"followup_time": "t", "event": "e", "group": "arm", "covariates": ["x"],
                       "truncation_time": .5, "reference_group": "B"},
                      "list(result=unname(.rmst$adjusted.result),ref=unname(survRM2::rmst2(dataset$t,dataset$e,as.integer(dataset$arm!='B'),tau=.5,covariates=as.matrix(dataset['x']))$adjusted.result))")
    assert p["result"] == p["ref"]


RECURRENT = '''set.seed(47); ids <- rep(seq_len(60),each=3)
dataset <- data.frame(id=ids,t=rep(c(.25,.5,.9),60),e=rbinom(180,1,.4),g=rep(rep(c('A','B'),each=30),each=3))'''


def test_recurrent_model_uses_true_subunit_intervals_and_cluster_variance(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "clinical_recurrent_events", RECURRENT,
                      {"subject_id": "id", "event_time": "t", "event": "e", "group": "g"},
                      "list(start=.df$.start[1:3],coef=unname(coef(.fit)),se=unname(sqrt(diag(vcov(.fit)))))")
    assert p["start"] == [0, .25, .5]
    assert isinstance(p["coef"], (int, float))
    assert p["se"] > 0


def test_recurrent_invalid_same_time_intervals_are_rejected(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "clinical_recurrent_events",
                      RECURRENT + "\ndataset$t[2] <- dataset$t[1]",
                      {"subject_id": "id", "event_time": "t", "event": "e", "group": "g"},
                      "list(message=res)", expect_success=False)
    assert "区间" in p["message"]


TIME_UNIT_CASES = [
    ("extended_survival", SURVIVAL,
     {"followup_time": "t", "event": "e", "time_dependent_variable": "x"},
     "c(unname(coef(.fit)),.effect$时间,.effect$HR)", "character(0)"),
    ("competing_risk", "set.seed(451); dataset <- data.frame(t=rexp(150),type=sample(c(0,1,2),150,replace=TRUE),x=rnorm(150))",
     {"followup_time": "t", "event_type": "type", "target_event_level": "1", "competing_event_level": "2", "covariates": ["x"]},
     "c(unname(.fg$coef),.curve$时间,.curve$CIF,.curve$CI下限,.curve$CI上限)",
     'if("时间单位" %in% names(.curve)) unique(.curve$时间单位) else character(0)'),
    ("clinical_rmst_non_ph", SURVIVAL,
     {"followup_time": "t", "event": "e", "group": "arm", "covariates": ["x"], "truncation_time": .5, "reference_group": "B"},
     "c(as.numeric(.matrix),as.numeric(.sensitivity_matrix),.tau,.curve$时间,.curve$生存)",
     'if("单位" %in% names(.table)) .table$单位 else character(0)'),
]


@pytest.mark.parametrize("template_id,data_code,inputs,numeric,units", TIME_UNIT_CASES, ids=[case[0] for case in TIME_UNIT_CASES])
def test_frozen_time_unit_labels_notes_and_tables_without_rescaling(backend, tmp_path, template_id, data_code, inputs, numeric, units):
    from agent.analysis.clinical_survival_cores import INPUTS
    assert INPUTS[template_id]["time_unit"] == "unit_v"
    probe = f"list(numeric={numeric},axis=.fig$labels$x,note=.out[['notes']]$payload,unit=.unit,table_units={units})"
    observed = []
    for directory, selected in (("unspecified", {}), ("months", {"time_unit": "months"})):
        folder = tmp_path / directory
        folder.mkdir()
        result, values = run_method(backend, folder, template_id, data_code, {**inputs, **selected}, probe)
        observed.append(values)
        if selected:
            # Check the persisted tool artifact, not only the in-memory notes.
            assert any("时间单位：months" in Path(text.path).read_text(encoding="utf-8")
                       for text in result.texts if text.name != "numeric_probe")
            if template_id == "clinical_rmst_non_ph":
                sensitivity = next(table for table in result.tables if table.name == "rmst_tau_sensitivity")
                with Path(sensitivity.path).open(encoding="utf-8-sig") as handle:
                    rows = list(csv.DictReader(handle))
                assert [row["单位"] for row in rows] == ["months", "无量纲", "无量纲"]
    unspecified, months = observed
    assert months["numeric"] == unspecified["numeric"], "Time labels must not alter estimates or time values"
    assert unspecified["axis"] == "时间" and unspecified["unit"] == ""
    assert "时间单位：" not in unspecified["note"]
    assert unspecified["table_units"] == []
    assert months["axis"] == "时间（months）" and months["unit"] == "months"
    assert "未进行单位换算" in months["note"]
    if template_id == "competing_risk":
        assert months["table_units"] == "months"
    if template_id == "clinical_rmst_non_ph":
        assert months["table_units"] == ["months", "无量纲", "无量纲"]


@pytest.mark.parametrize("invalid_unit", ["", "   ", ["months", "years"], 12], ids=["empty", "whitespace", "vector", "numeric"])
@pytest.mark.parametrize("template_id,data_code,inputs,numeric,units", TIME_UNIT_CASES, ids=[case[0] for case in TIME_UNIT_CASES])
def test_selected_time_unit_requires_a_nonempty_string_scalar(backend, tmp_path, template_id, data_code, inputs, numeric, units, invalid_unit):
    _, probe = run_method(backend, tmp_path, template_id, data_code, {**inputs, "time_unit": invalid_unit},
                          "list(message=res)", expect_success=False)
    assert "时间单位必须为非空字符串标量" in probe["message"]
