"""Package reference checks through frozen AST/accessors and real artifact validation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.trial_evidence_workflows import CORES, INPUTS, ROLE_MAPS
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.protocol import DatasetRef, RRunRequest


def run_trial(backend, tmp_path, template_id, data, inputs, probe, *, failure=False):
    inputs = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else v for k, v in inputs.items()}
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    conditional = {("dose_response_trial", "table_4"): "outcome_2", ("ipd_meta_mixed", "table_3"): "subgroup", ("survival_trial_design", "table_2"): "event_probability"}
    outputs = tuple(replace(s, required=False) if conditional.get((template_id, s.role)) and
                    conditional[template_id, s.role] not in inputs else s for s in template.required_outputs)
    plan = AnalysisPlan(
        plan_id="trial-evidence-reference", source="template", dataset_id="trial-synthetic",
        intent=template.title, category=template.category, method_family=template.family,
        scenario=template.scenario, variable_bindings={}, packages=(), prerequisites=(),
        assumptions=(), diagnostics=template.diagnostics, required_outputs=outputs,
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
        job_id="trial-reference", dataset=DatasetRef(plan.dataset_id, str(dummy), "csv", "input.csv"),
        code=code, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=180,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    if not failure:
        artifacts = []
        for a in (*result.tables, *result.figures, *result.texts):
            metadata = dict(a.metadata, dataset_id=plan.dataset_id, plan_id=plan.plan_id,
                            source_tool="run_r_code", source_execution_status="succeeded", job_id="trial-reference")
            receipt = build_artifact_validation_receipt(a.path, kind=a.kind, metadata=metadata,
                                                       job_id="trial-reference", artifacts_root=tmp_path / "artifacts")
            metadata.update(artifact_size_bytes=receipt["size_bytes"], artifact_sha256=receipt["sha256"],
                            artifact_validation_receipt=receipt)
            artifacts.append(dict(a.to_dict(), metadata=metadata, source_tool="run_r_code", job_id="trial-reference"))
        assessment = ResultValidator().validate(plan, dict(dataset_id=plan.dataset_id,
                    plan_id=plan.plan_id, status="succeeded", diagnostic_status={d:"not_assessed" for d in plan.diagnostics}), artifacts)
        assert assessment.complete, repr(assessment)
    return result, json.loads(Path(note.path).read_text(encoding="utf-8"))


META_DATA = 'dataset<-data.frame(study=paste0("S",1:12),effect=c(.1,.3,-.2,.4,.1,.2,.8,-.1,.3,.4,.15,.2),se=seq(.1,.25,length.out=12),risk=rep(c("low","high"),6))'
TRIAL_DATA = r'''
set.seed(717);dataset<-data.frame(id=1:120,arm=rep(c("C","T"),60),x=rnorm(120),pop=rep(c("ITT","PP"),each=60),stratum=rep(c("S1","S2","S3"),40))
dataset$y<-1+.6*(dataset$arm=="T")+.4*dataset$x+rnorm(120)
dataset$ice<-as.integer(dataset$id%%9==0)
'''
CROSS_DATA = r'''
set.seed(718);dataset<-data.frame(id=rep(1:40,each=2),period=rep(1:2,40),sequence=rep(rep(c("RT","TR"),each=20),each=2))
dataset$arm<-ifelse((dataset$sequence=="RT" & dataset$period==1) | (dataset$sequence=="TR" & dataset$period==2),"R","T")
subject<-rnorm(40,sd=.5);dataset$y<-2+subject[dataset$id]+.1*(dataset$arm=="T")+.15*(dataset$period==2)+rnorm(80,sd=.2)
'''

CASES = {
 "network_meta": (r'''dataset<-data.frame(study=rep(paste0("S",1:9),each=2),arm=rep(c("a","b"),9),treatment=c("A","B","A","B","A","B","B","C","B","C","B","C","A","C","A","C","A","C"),events=c(8,6,10,7,9,5,8,5,9,6,10,7,12,5,9,4,10,6),n=50)''',
  {"study_id":"study","treatment_arm":"arm","event_count":"events","intervention":"treatment","sample_size":"n","reference_treatment":"A"},
  "list(rank=rank_table,reference=as.numeric(netmeta::netrank(fit,small.values='good')$Pscore.random),rank_values=as.numeric(ranking$Pscore.random),comparisons=nrow(pw),league_rows=nrow(league))"),
 "diagnostic_meta": (r'''dataset<-data.frame(study=paste0("D",1:8),tp=c(42,38,35,45,47,41,39,44),fn=c(8,12,15,5,3,9,11,6),fp=c(12,14,18,10,9,13,15,11),tn=c(48,46,42,50,51,47,45,49))''',
  {"study_id":"study","true_positive":"tp","false_positive":"fp","false_negative":"fn","true_negative":"tn"},
  "list(sensitivity=sensitivity$estimate,specificity=specificity$estimate,ref_coef=as.numeric(coef(mada::reitsma(d,correction=.5,correction.control='single'))),beta=as.numeric(beta),sroc_n=nrow(sroc))"),
 "bayesian_meta": (META_DATA,{"study_id":"study","effect_estimate":"effect","standard_error":"se","prior":{"mu_mean":0,"mu_sd":2,"tau_prior":"half_normal","tau_scale":.5,"sensitivity_tau_scales":[.25,.5]}},
  "list(mu=posterior$mean[1],reference=fit$summary['mean','mu'],lower=prediction$lower,upper=prediction$upper,sensitivity_n=nrow(sensitivity))"),
 "meta_bias_sensitivity": (META_DATA,{"study_id":"study","effect_estimate":"effect","standard_error":"se","risk_of_bias":"risk"},
  "list(estimate=as.numeric(fit$b),reference=as.numeric(metafor::rma(yi=dataset$effect,sei=dataset$se,method='REML')$b),loo=nrow(sensitivity),cook=as.numeric(influence_data$cook),bias_p=bias$p)"),
 "safety_meta_analysis": (r'''dataset<-data.frame(study=paste0("Z",1:6),et=c(0,1,3,5,0,2),ec=c(0,2,1,6,1,3),nt=c(50,60,70,80,60,50),nc=c(50,60,70,80,60,50),sub=rep(c("a","b"),3))''',
  {"study_id":"study","treatment_event_count":"et","treatment_sample_size":"nt","control_event_count":"ec","control_sample_size":"nc","subgroup":"sub"},
  "list(RR=exp(fit$TE.random),reference=exp(meta::metabin(dataset$et,dataset$nt,dataset$ec,dataset$nc,sm='RR',method='MH',method.tau='DL',incr=.5,method.incr='only0')$TE.random),doublezero=sensitivity$double_zero_studies)"),
 "living_review_update": (META_DATA + '\ndataset$version<-1:12;dataset<-rbind(dataset,transform(dataset[1,],effect=.9,version=13))',
  {"study_id":"study","effect_estimate":"effect","standard_error":"se","study_version":"version","subgroup":"risk"},
  "list(k=tail(updates$k,1),last=tail(updates$estimate,1),reference=as.numeric(metafor::rma(yi=c(dataset$effect[2:12],.9),sei=c(dataset$se[2:12],dataset$se[1]),method='REML')$b),snapshots=nrow(updates))"),
 "grade_evidence_summary": (META_DATA+'\ndataset$outcome<-"mortality";dataset$certainty<-"moderate";dataset$risk0<-.1',
  {"outcome":"outcome","study_id":"study","effect_estimate":"effect","standard_error":"se","evidence_certainty":"certainty","absolute_risk":"risk0","effect_scale":"log_rr"},
  "list(certainty=sof$certainty,source=sof$certainty_source,risk_difference=sof$risk_difference_per_1000,reference=(.1*exp(as.numeric(metafor::rma(yi=dataset$effect,sei=dataset$se,method='REML')$b))-.1)*1000)"),
 "ipd_meta_mixed": (TRIAL_DATA + '\ndataset$study<-rep(1:6,each=20);dataset$y<-dataset$y+rep(c(-.8,.1,.6,-.3,.5,-.1),each=20)',
  {"study_id":"study","subject_id":"id","group":"arm","outcome":"y","covariates":["x"]},
  "list(estimate=model_table$estimate[2],reference=as.numeric(lme4::fixef(lme4::lmer(y~arm+x+(1|study),data=dataset))[2]),variance=heterogeneity$vcov[1])"),
 "dose_response_trial": (r'''set.seed(719);dataset<-data.frame(dose=rep(0:5,each=20),x=rnorm(120));dataset$y<-2+.7*dataset$dose-.08*dataset$dose^2+.2*dataset$x+rnorm(120,.0,.3);dataset$tox<-rbinom(120,1,plogis(-4+.5*dataset$dose))''',
  {"dose":"dose","outcome":"y","outcome_2":"tox","covariates":["x"]},
  "list(selected=recommendation$selected_model,AIC=comparison$AIC,models=comparison$model,dose=recommendation$dose,curve_n=nrow(curve),safety_n=nrow(safety))"),
 "rct_primary_analysis": (TRIAL_DATA,{"group":"arm","outcome":"y","analysis_population":"pop","covariates":["x"],"strata":["stratum"],"analysis_population_level":"ITT"},
  "list(estimate=primary$estimate,reference=unname(coef(glm(y~arm+x+stratum,data=dataset[dataset$pop=='ITT',],family=gaussian()))['armT']),n=primary$n)"),
 "sap_protocol_stats": (TRIAL_DATA+'\ndataset$missing<-0',{"treatment":"arm","outcome":"y","analysis_population":"pop","missingness_indicator":"missing","covariates":["x"],"sap_spec":{"estimand":"ITT mean difference","primary_method":"ANCOVA","alpha":.05,"missing_data_strategy":"MI under MAR"}},
  "list(outline=outline$section,shell_status=shell$result_status,n=sum(shell$available_rows))"),
 "randomization_allocation": ('dataset<-data.frame(stratum=rep(c("A","B"),each=24))',{"sample_size":48,"strata":"stratum","allocation_ratio":1,"block_size":[4,8],"seed":719},
  "list(n=nrow(allocation),T=sum(allocation$assigned_treatment=='T'),C=sum(allocation$assigned_treatment=='C'),block_table=as.data.frame(table(allocation$block,allocation$assigned_treatment)))"),
 "crossover_trial_analysis": (CROSS_DATA,{"subject_id":"id","group":"arm","period_variable":"period","outcome":"y","series_variable":"sequence"},
  "list(estimate=contrast$estimate,reference=unname(nlme::fixef(nlme::lme(y~arm+factor(period)+sequence,data=dataset,random=~1|id))['armT']),df=contrast$df)"),
 "bioequivalence_pk": (CROSS_DATA+'\ndataset$pk<-exp(dataset$y)',{"subject_id":"id","formulation_variable":"arm","period_variable":"period","outcome":"pk","series_variable":"sequence","equivalence_lower":.8,"equivalence_upper":1.25},
  "list(GMR=equivalence$geometric_mean_ratio,reference=exp(unname(nlme::fixef(nlme::lme(log(pk)~arm+factor(period)+sequence,data=dataset,random=~1|id))['armT'])),lower=equivalence$lower_90,upper=equivalence$upper_90,equivalent=equivalence$equivalent)"),
 "survival_trial_design": ('dataset<-data.frame(dummy=1)',{"effect_size":.75,"alpha":.05,"power":.8,"allocation_ratio":1,"event_probability":.6,"followup_duration":24},
  "list(events=event_table$events_unrounded,reference=gsDesign::nEvents(hr=.75,alpha=.05,beta=.2,ratio=1,sided=2),N=sample_table$total)"),
 "trial_estimand_ich_e9": (TRIAL_DATA,{"randomized_group":"arm","outcome":"y","analysis_population":"pop","intercurrent_event":"ice","baseline_value":"x","strata":"stratum","estimand_spec":{"intercurrent_strategy":"treatment_policy","population_level":"ITT","summary_measure":"MD"}},
  "list(estimate=primary$estimate,reference=unname(coef(glm(y~arm+x+stratum,data=dataset[dataset$pop=='ITT',],family=gaussian()))['armT']),ICE=primary$intercurrent_events,n=nrow(df))"),
 "trial_missing_tipping_point": (TRIAL_DATA+'\ndataset$missing<-as.integer(dataset$id%%7==0);dataset$y[dataset$missing==1]<-NA',
  {"randomized_group":"arm","outcome":"y","missingness_indicator":"missing","baseline_value":"x","delta_grid":{"control":[0,1],"treatment":[0,1]},"imputations":5,"max_iterations":5,"seed":719},
  "list(rows=nrow(primary),missing=primary$missing,lower=primary$lower,upper=primary$upper,est=primary$estimate,m=primary$m)"),
 "trial_phase1_dose_escalation": ('dataset<-data.frame(id=1:9,dose=c(rep(10,3),rep(20,6)),cohort=rep(1:3,each=3),DLT=c(0,0,0,0,1,0,0,0,0),complete=1)',
  {"dose":"dose","outcome":"DLT","subject_id":"id","cohort":"cohort","observation_window_complete":"complete","observation_window":28,"dose_levels":[10,20,30]},
  "list(current=dose_table[dose_table$current,,drop=FALSE],low=dose_table$lower,high=dose_table$upper)"),
 "trial_sample_size_reestimation": (TRIAL_DATA+'\ndataset$stage<-1',{"randomized_group":"arm","outcome":"y","interim_stage":"stage","baseline_value":"x","strata":"stratum","target_difference":.5,"alpha":.05,"power":.8,"allocation_ratio":1},
  "list(N=primary$reestimated_total,reference=ceiling(rpact::getSampleSizeMeans(alpha=.05,beta=.2,sided=2,alternative=.5,stDev=sigma(lm(y~x+stratum,data=dataset)),allocationRatioPlanned=1)$nFixed),blinded=primary$treatment_effect_used)"),
 "trial_adaptive_randomization": (TRIAL_DATA+'\ndataset$order<-dataset$id;dataset$stage<-ifelse(dataset$id<=60,1,2);dataset$prob<-.5',
  {"subject_id":"id","treatment_assignment":"arm","enrollment_order":"order","strata":["stratum"],"stage":"stage","outcome":"y","allocation_probability":"prob","target_treatment_fraction":.5},
  "list(n=sum(summary$n),fraction=tail(trajectory$treatment_fraction,1),weighted=summary$IPW_treatment_hajek[1],reference=mean(dataset$y[dataset$arm=='T']))"),
}


@pytest.mark.parametrize("template_id", list(CASES))
def test_declared_workflow_real_r_and_portfolio(backend, tmp_path, template_id):
    data, inputs, probe = CASES[template_id]
    _, p = run_trial(backend, tmp_path, template_id, data, inputs, probe)
    if "reference" in p:
        field = next(k for k in ("rank_values", "beta", "mu", "RR", "last", "risk_difference", "estimate", "GMR", "events", "N", "weighted") if k in p)
        assert p[field] == pytest.approx(p["reference"], rel=1e-7, abs=1e-8)
    if template_id == "network_meta":
        assert p["comparisons"] == 9 and p["league_rows"] == 9
    elif template_id == "diagnostic_meta":
        assert 0 < p["sensitivity"] < 1 and 0 < p["specificity"] < 1
    elif template_id == "bayesian_meta":
        assert p["lower"] < p["mu"] < p["upper"] and p["sensitivity_n"] == 2
    elif template_id == "meta_bias_sensitivity":
        assert p["loo"] == 12 and 0 <= p["bias_p"] <= 1
    elif template_id == "living_review_update":
        assert p["k"] == 12 and p["snapshots"] == 11
    elif template_id == "grade_evidence_summary":
        assert p["certainty"] == "moderate" and "user supplied" in p["source"]
    elif template_id == "dose_response_trial":
        assert p["selected"] == p["models"][p["AIC"].index(min(p["AIC"]))]
        assert 0 <= p["dose"] <= 5 and p["curve_n"] == 100 and p["safety_n"] == 6
    elif template_id == "rct_primary_analysis":
        assert p["n"] == 60
    elif template_id == "sap_protocol_stats":
        assert "estimand" in p["outline"] and p["n"] == 120
        assert all("no hypothesis" in s for s in p["shell_status"])
    elif template_id == "randomization_allocation":
        assert p["T"] == p["C"] == 24 and p["n"] == 48
    elif template_id == "trial_missing_tipping_point":
        assert p["rows"] == 4 and all(m == 5 for m in p["m"])
        assert all(l < e < u for l, e, u in zip(p["lower"], p["est"], p["upper"]))
    elif template_id == "trial_phase1_dose_escalation":
        z = p["current"][0]
        assert z["dose"] == 20 and z["n"] == 6 and z["DLT"] == 1 and z["next_or_candidate_dose"] == 30
        assert "escalate" in z["decision"]
    elif template_id == "trial_sample_size_reestimation":
        assert p["blinded"] is False
    elif template_id == "trial_adaptive_randomization":
        assert p["n"] == 120 and p["fraction"] == .5


def test_trial_maps_cover_current_catalog():
    assert len(CORES) == 20
    for k in CORES:
        template = AnalysisCatalog().get(k)
        assert {s.id for s in template.input_specs} <= set(INPUTS[k])
        assert {s.role for s in template.required_outputs} == set(ROLE_MAPS[k])


def test_missing_delta_changes_only_actual_missing_rows_and_has_adjusted_tipping(backend, tmp_path):
    data, inputs, _ = CASES["trial_missing_tipping_point"]
    _, p = run_trial(backend, tmp_path, "trial_missing_tipping_point", data, inputs,
                    "list(rows=primary[,c('delta_control','delta_treatment','estimate')],missing=sum(df$.missing))")
    assert p["missing"] == 17
    rows = {(r["delta_control"], r["delta_treatment"]): r["estimate"] for r in p["rows"]}
    assert rows[0, 1] > rows[0, 0] and rows[1, 0] < rows[0, 0]


def test_composite_estimand_actually_recodes_intercurrent_events(backend, tmp_path):
    data = TRIAL_DATA + '\ndataset$y<-as.integer(dataset$id%%3==0)'
    inputs = {"randomized_group":"arm", "outcome":"y", "analysis_population":"pop", "intercurrent_event":"ice",
              "estimand_spec":{"intercurrent_strategy":"composite", "population_level":"ITT", "composite_event_value":1, "summary_measure":"OR"}}
    _, p = run_trial(backend, tmp_path, "trial_estimand_ich_e9", data, inputs,
                    "list(recode=all(df$.y[df$.ice==1]==1),n=nrow(df),reference=exp(unname(coef(glm(I(pmax(y,ice))~arm,data=dataset[dataset$pop=='ITT',],family=binomial()))['armT'])),estimate=primary$estimate)")
    assert p["recode"] is True and p["n"] == 60
    assert p["estimate"] == pytest.approx(p["reference"])


def test_optional_safety_table_has_no_placeholder_when_unselected(backend, tmp_path):
    data, inputs, _ = CASES["dose_response_trial"]
    inputs = {k:v for k,v in inputs.items() if k != "outcome_2"}
    result, _ = run_trial(backend, tmp_path, "dose_response_trial", data, inputs, "list(no_safety=is.null(.out[['safety']]))")
    assert all(a.name != "table_4" for a in result.tables)


@pytest.mark.parametrize("template_id,data,inputs,message", [
    ("bayesian_meta", META_DATA, {"study_id":"study", "effect_estimate":"effect", "standard_error":"se", "prior":{"mu_mean":0,"mu_sd":1,"tau_prior":"half_normal","tau_scale":.5,"unused":1}}, "未知字段"),
    ("trial_phase1_dose_escalation", 'dataset<-data.frame(dose=rep(10,3),DLT=0,complete=c(1,1,0))', {"dose":"dose","outcome":"DLT","observation_window_complete":"complete"}, "待定"),
    ("sap_protocol_stats", TRIAL_DATA, {"treatment":"arm","outcome":"y","sap_spec":{"alpha":.05}}, "SAP 需明确"),
    ("trial_sample_size_reestimation", TRIAL_DATA+'\ndataset$stage<-1;dataset$event<-2', {"randomized_group":"arm","outcome":"y","interim_stage":"stage","event_time":"event","target_difference":.5}, "连续终点盲态方差"),
])
def test_unsupported_or_unconfirmed_settings_fail_explicitly(backend, tmp_path, template_id, data, inputs, message):
    _, p = run_trial(backend, tmp_path, template_id, data, inputs, "list(message=res)", failure=True)
    assert message in p["message"] and p["message"] != "OK"


def test_events_only_design_preserves_no_probability_branch(backend, tmp_path):
    result, p = run_trial(backend, tmp_path, "survival_trial_design", 'dataset<-data.frame(dummy=1)',
       {"effect_size":.75,"followup_duration":24}, "list(events=event_table$events_unrounded,reference=gsDesign::nEvents(hr=.75,alpha=.05,beta=.2,ratio=1,sided=2),no_sample=is.null(.out[['sample']]))")
    assert p["events"] == pytest.approx(p["reference"]) and p["no_sample"] is True
    assert all(a.name != "table_2" for a in result.tables)


def test_confirmed_trial_settings_pass_production_normalizers():
    from tests.science.test_time_space_text_workflows import normalized_inputs
    for key, (_, inputs, _) in CASES.items():
        normalized = normalized_inputs(AnalysisCatalog().get(key), inputs)
        assert set(normalized) == set(inputs)
        for input_id, value in normalized.items():
            if isinstance(inputs[input_id], dict):
                assert isinstance(value, str) and json.loads(value) == inputs[input_id]
