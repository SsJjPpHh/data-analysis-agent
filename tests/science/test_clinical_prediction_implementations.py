"""Clinical model references through real local R and frozen getters."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.clinical_prediction_cores import CORES, INPUTS, ROLE_MAPS
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.skeleton_cores import _make_engine
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.protocol import DatasetRef, RRunRequest


def run_clinical(backend, tmp_path, template_id, data, inputs, probe, *, failure=False):
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(
        plan_id="clinical-prediction-fixed", source="template", dataset_id="clinical-synthetic",
        intent=template.title, category=template.category, method_family=template.family,
        scenario=template.scenario, variable_bindings={}, packages=(), prerequisites=(),
        assumptions=(), diagnostics=template.diagnostics, required_outputs=template.required_outputs,
        resource_class="standard", template_id=template_id,
        resolved_inputs={k: ResolvedInput(k, k, specs[k].kind if k in specs else "parameter", v, "user") for k, v in inputs.items()},
    )
    script = _make_engine(CORES[template_id], INPUTS[template_id], ())(plan, ROLE_MAPS[template_id])
    if failure:
        script = "tryCatch({\n" + script + "\n},error=function(e) invisible(NULL))"
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    code = data + "\n" + script + "\n" + (
        "" if failure else 'if(!identical(res,"OK")) stop(res)\n'
    ) + 'baa_text("probe",jsonlite::toJSON(' + probe + ',auto_unbox=TRUE,digits=15))'
    code = "\n".join((RToolsMixin._frozen_input_prelude(plan),
                        RToolsMixin._frozen_user_code_block(code, plan),
                        RToolsMixin._frozen_input_epilogue(plan)))
    result = backend.run_code(RRunRequest(
        job_id="clinical-fixed-test", dataset=DatasetRef("clinical-synthetic", str(dummy), "csv", "input.csv"),
        code=code, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=120,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    if not failure:
        roles = {a.name for a in (*result.tables, *result.figures, *result.texts)}
        assert {s.role for s in template.required_outputs if s.required} <= roles
    return result, json.loads(Path(note.path).read_text(encoding="utf-8"))


BINARY_PREDICTION = r'''
set.seed(419)
dataset<-data.frame(x=rnorm(180),z=rnorm(180),test=seq_len(180)>120)
dataset$y<-rbinom(180,1,plogis(-.3+.8*dataset$x-.4*dataset$z))
ref<-glm(y~x+z,data=dataset[!dataset$test,],family=binomial())
'''


def test_prediction_honors_split_named_coefficients_and_actual_dca(backend, tmp_path):
    _, p = run_clinical(backend, tmp_path, "prognostic_model", BINARY_PREDICTION,
        {"outcome": "y", "features": ["x", "z"], "validation_split": "test"},
        "list(beta=as.numeric(coef(fit)),reference=unname(coef(ref)),ntrain=nrow(train),ntest=nrow(test),terms=coef_table$term,dca=dca_data[dca_data$threshold==.5,,drop=FALSE],prediction=prediction,y=test$.y)")
    assert p["beta"] == pytest.approx(p["reference"], abs=1e-5)
    assert p["ntrain"] == 120 and p["ntest"] == 60
    assert p["terms"] == ["Intercept", "x", "z"]
    expected = sum((1 if y else -1) for y, risk in zip(p["y"], p["prediction"]) if risk >= .5)/60
    row = next(r for r in p["dca"] if r["strategy"] == "prediction model")
    assert row["net_benefit"] == pytest.approx(expected, abs=1e-12)


def test_validation_outcomes_do_not_change_fitted_prediction_model(backend, tmp_path):
    probes = []
    for flip in (False, True):
        folder = tmp_path / str(flip)
        folder.mkdir()
        _, p = run_clinical(backend, folder, "prognostic_model", BINARY_PREDICTION +
            ("\ndataset$y[dataset$test]<-1-dataset$y[dataset$test]\n" if flip else ""),
            {"outcome": "y", "features": ["x", "z"], "validation_split": "test"},
            "list(beta=as.numeric(coef(fit)),prediction=prediction)")
        probes.append(p)
    assert probes[0]["beta"] == pytest.approx(probes[1]["beta"], abs=1e-12)
    assert probes[0]["prediction"] == pytest.approx(probes[1]["prediction"], abs=1e-12)


def test_survival_prediction_uses_event_time_and_ipcw_reference(backend, tmp_path):
    _, p = run_clinical(backend, tmp_path, "prognostic_model", r'''
set.seed(422);dataset<-data.frame(x=rnorm(300),z=rnorm(300),test=seq_len(300)>210)
event_time<-rexp(300,exp(.6*dataset$x-.3*dataset$z)*.3);censor<-rexp(300,.1)
dataset$time<-pmin(event_time,censor);dataset$event<-as.integer(event_time<=censor)
ref<-survival::coxph(survival::Surv(time,event)~x+z,data=dataset[!dataset$test,],x=TRUE,y=TRUE)
ref_risk<-riskRegression::predictRisk(ref,newdata=dataset[dataset$test,],times=2)
''', {"outcome": "time", "event": "event", "features": ["x", "z"], "validation_split": "test", "time_point": 2},
        "list(beta=as.numeric(coef(fit)),reference=unname(coef(ref)),risk=prediction,ref_risk=as.numeric(ref_risk),AUC=auc_table$estimate,Brier=perf_table$Brier,weights=weights)")
    assert p["beta"] == pytest.approx(p["reference"], abs=1e-8)
    assert p["risk"] == pytest.approx(p["ref_risk"], abs=1e-8)
    assert 0 <= p["AUC"] <= 1 and 0 <= p["Brier"] <= 1
    assert any(w == 0 for w in p["weights"])


def test_binary_cutpoint_requires_no_survival_inputs_and_matches_youden(backend, tmp_path):
    _, p = run_clinical(backend, tmp_path, "clinical_cutpoint_maxstat", r'''
set.seed(8);dataset<-data.frame(y=rep(c(0,1),each=50))
dataset$x<-rnorm(100,mean=dataset$y*.9)
''', {"measure": "x", "outcome": "y", "analysis_method": "binary", "resamples": 50},
        "list(table=cut_table,x=dataset$x,y=dataset$y)")
    selected = next(r for r in p["table"] if r["selected"])
    pos = sum(y for y in p["y"])
    neg = len(p["y"])-pos
    tp = sum(y == 1 and x > selected["cutoff"] for x, y in zip(p["x"], p["y"]))
    fp = sum(y == 0 and x > selected["cutoff"] for x, y in zip(p["x"], p["y"]))
    assert selected["sensitivity"] == pytest.approx(tp/pos)
    assert selected["specificity"] == pytest.approx(1-fp/neg)
    assert selected["statistic"] == pytest.approx(tp/pos-fp/neg)
    assert selected["statistic"] == max(r["statistic"] for r in p["table"])
    assert selected["p_method"] == "Monte Carlo permutation of maximum Youden"
    assert 1/51 <= selected["search_adjusted_p"] <= 1


def test_survival_cutpoint_matches_maxstat_adjusted_search(backend, tmp_path):
    _, p = run_clinical(backend, tmp_path, "clinical_cutpoint_maxstat", r'''
set.seed(9);dataset<-data.frame(x=rnorm(120))
et<-rexp(120,exp(.7*dataset$x));ct<-rexp(120,.2)
dataset$time<-pmin(et,ct);dataset$event<-as.integer(et<=ct)
ref<-maxstat::maxstat.test(survival::Surv(time,event)~x,data=dataset,smethod="LogRank",pmethod="Lau94",minprop=.1,maxprop=.9)
''', {"measure": "x", "followup_time": "time", "event": "event", "analysis_method": "survival", "resamples": 50},
        "list(cutoff=cut_table$cutoff,reference=unname(ref$estimate),p=cut_table$search_adjusted_p,ref_p=unname(ref$p.value),label=cut_table$p_method)")
    assert p["cutoff"] == pytest.approx(p["reference"], abs=1e-12)
    assert p["p"] == pytest.approx(p["ref_p"], abs=1e-12)
    assert p["label"] == "maxstat Lau94"


@pytest.mark.parametrize("binary", [False, True])
def test_subgroup_outputs_actual_interaction_and_joint_test(backend, tmp_path, binary):
    _, p = run_clinical(backend, tmp_path, "clinical_subgroup_interaction", r'''
set.seed(11);dataset<-data.frame(arm=rep(c("A","B"),each=100),subgroup=rep(c("S1","S2"),100),x=rnorm(200))
eta<-.1+.5*(dataset$arm=="B")+.7*(dataset$subgroup=="S2")+.8*(dataset$arm=="B" & dataset$subgroup=="S2")+.2*dataset$x
''' + ("dataset$y<-rbinom(200,1,plogis(eta))" if binary else "dataset$y<-eta+rnorm(200)"),
        {"outcome": "y", "group": "arm", "subgroup": "subgroup", "covariates": ["x"]},
        "list(interaction=interaction_table,p=interaction_p,family=fam$family,reference=coef(summary(f1))[grep(':',rownames(coef(summary(f1)))),1])")
    assert p["family"] == ("binomial" if binary else "gaussian")
    assert p["interaction"][0]["estimate"] == pytest.approx(p["reference"], abs=1e-12)
    assert p["interaction"][0]["global_interaction_p"] == pytest.approx(p["p"], abs=1e-12)


@pytest.mark.parametrize("binary", [False, True])
def test_rcs_uses_real_knots_and_reference_contrasts(backend, tmp_path, binary):
    _, p = run_clinical(backend, tmp_path, "clinical_restricted_cubic_spline", r'''
set.seed(12);dataset<-data.frame(x=seq(-2,2,length.out=180),z=rnorm(180))
''' + ("dataset$y<-rbinom(180,1,plogis(.1+.7*dataset$x+.3*dataset$x^2+.2*dataset$z))" if binary else
        "dataset$y<-1+.7*dataset$x+.3*dataset$x^2+.2*dataset$z+rnorm(180,sd=.2)") + r'''
ref_knots<-as.numeric(quantile(dataset$x,c(.05,.35,.65,.95)))
''', {"outcome": "y", "exposure": "x", "covariates": ["z"], "reference_value": 0, "knot_count": 4},
        "list(knots=knots,ref_knots=ref_knots,k=counts[best],nonlinear=nl_p,reference=as.numeric(reference_contrast$Contrast),curve=curve)")
    assert p["knots"] == pytest.approx(p["ref_knots"], abs=1e-12)
    assert p["k"] == 4
    assert p["reference"] == pytest.approx(0, abs=1e-12)
    assert isinstance(p["nonlinear"], (int, float)) and 0 <= p["nonlinear"] <= 1
    assert all(r["lower"] <= r["estimate"] <= r["upper"] for r in p["curve"])


@pytest.mark.parametrize("template_id,code,inputs,message", [
    ("prognostic_model", BINARY_PREDICTION, {"outcome": "y", "features": ["x", "test"], "validation_split": "test"}, "不包含"),
    ("clinical_cutpoint_maxstat", "dataset<-data.frame(x=1:100,y=rep(c(0,1),50))",
     {"measure": "x", "outcome": "y", "analysis_method": "survival"}, "需要随访"),
    ("clinical_restricted_cubic_spline", "dataset<-data.frame(x=1:100,y=sin(1:100))",
     {"outcome": "y", "exposure": "x", "reference_value": 1000}, "参考值"),
])
def test_invalid_settings_are_explicit_failures(backend, tmp_path, template_id, code, inputs, message):
    _, p = run_clinical(backend, tmp_path, template_id, code, inputs, "list(message=res)", failure=True)
    assert message in p["message"] and p["message"] != "OK"


def test_clinical_maps_cover_all_declared_selections_and_outputs():
    for template_id in CORES:
        t = AnalysisCatalog().get(template_id)
        assert {s.id for s in t.input_specs} <= set(INPUTS[template_id])
        # Compiled production portfolios can add canonical aliases to genuine
        # slots. Every catalog output must remain mapped; extra aliases are
        # checked by the full production-portfolio contracts separately.
        assert {s.role for s in t.required_outputs} <= set(ROLE_MAPS[template_id])
