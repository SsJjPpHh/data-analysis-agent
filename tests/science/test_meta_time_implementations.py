"""Real-R checks for selected inputs, calculations and correct artifact roles."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.meta_time_cores import CORES, INPUTS, ROLE_MAPS
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.skeleton_cores import _make_engine
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.protocol import DatasetRef, RRunRequest


def run_core(backend, tmp_path, template_id, data_code, inputs, probe, *, failure=False):
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(
        plan_id="meta-time-software-reference", source="template", dataset_id="synthetic-reference",
        intent=template.title, category=template.category, method_family=template.family,
        scenario=template.scenario, variable_bindings={}, packages=(), prerequisites=(),
        assumptions=(), diagnostics=template.diagnostics, required_outputs=template.required_outputs,
        resource_class="standard", template_id=template_id,
        resolved_inputs={k: ResolvedInput(k, k, specs[k].kind if k in specs else "parameter", v, "user")
                         for k, v in inputs.items()},
    )
    code = _make_engine(CORES[template_id], INPUTS[template_id], ())(plan, ROLE_MAPS[template_id])
    if failure:
        code = "tryCatch({\n" + code + "\n},error=function(e) invisible(NULL))"
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    script = (
        data_code + "\n" + code + "\n"
        + ("" if failure else 'if(!identical(res,"OK")) stop(res)\n')
        + 'baa_text("probe",jsonlite::toJSON(' + probe + ',auto_unbox=TRUE,digits=15))'
    )
    script = "\n".join((RToolsMixin._frozen_input_prelude(plan),
                          RToolsMixin._frozen_user_code_block(script, plan),
                          RToolsMixin._frozen_input_epilogue(plan)))
    result = backend.run_code(RRunRequest(
        job_id="meta-time-test", dataset=DatasetRef("synthetic-reference", str(dummy), "csv", "input.csv"),
        code=script, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=120,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    value = json.loads(Path(note.path).read_text(encoding="utf-8"))
    if not failure:
        roles = {a.name for a in (*result.tables, *result.figures, *result.texts)}
        assert set(ROLE_MAPS[template_id]) <= roles
    return result, value


META_INPUTS = {"study_id": "study", "effect_estimate": "effect", "standard_error": "se"}


def test_pairwise_meta_keeps_identity_scale_and_matches_weighted_fixed_effect(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "pairwise_meta", '''
dataset<-data.frame(study=letters[1:5],effect=c(.1,.2,-.1,.3,.15),se=c(.1,.2,.15,.3,.1))
''', META_INPUTS, "list(pooled=pooled_table,het=het_table,loo=loo_table)")
    effects, ses = [.1, .2, -.1, .3, .15], [.1, .2, .15, .3, .1]
    weights = [1/s**2 for s in ses]
    expected = sum(w*x for w, x in zip(weights, effects)) / sum(weights)
    fixed = p["pooled"][0]
    assert fixed["estimate"] == pytest.approx(expected, abs=1e-12)
    assert fixed["lower"] == pytest.approx(expected-1.959963984540054/math.sqrt(sum(weights)), abs=1e-12)
    assert p["het"][0]["Q"] == pytest.approx(sum(w*(x-expected)**2 for w, x in zip(weights, effects)), abs=1e-12)
    assert len(p["loo"]) == 5


def test_meta_log_ratio_and_selected_subgroup_are_used(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "pairwise_meta", '''
dataset<-data.frame(study=letters[1:8],effect=log(c(.8,.9,.85,1.1,1.4,1.3,1.6,1.5)),se=seq(.1,.24,length.out=8),group=rep(c("A","B"),each=4))
ref<-metafor::rma(yi=effect,sei=se,data=dataset,method="DL")
''', {**META_INPUTS, "effect_scale": "log_ratio", "method_tau": "DL", "subgroup": "group"},
        "list(estimate=pooled_table$estimate[2],reference=exp(as.numeric(ref$b)),rows=pooled_table$component,subgroup_p=het_table$subgroup_p)")
    assert p["estimate"] == pytest.approx(p["reference"], abs=1e-12)
    assert p["rows"] == ["fixed", "random_DL", "subgroup:A", "subgroup:B"]
    assert isinstance(p["subgroup_p"], float)


def test_meta_regression_uses_selected_categorical_moderators_and_kh_interval(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "evidence_meta_regression", '''
i<-seq_len(32)
dataset<-data.frame(study=paste0("s",i),effect=.2+.025*i+.2*(i%%3==0)+sin(i)*.1,
 se=.1+i%%4*.03,followup=i,quality=factor(ifelse(i%%3==0,"high","low")))
ref<-metafor::rma(yi=effect,sei=se,mods=~followup+quality,data=dataset,method="REML",test="knha")
''', {**META_INPUTS, "covariates": ["followup"], "risk_of_bias": "quality"},
        "list(beta=as.numeric(fit$b),reference=as.numeric(ref$b),lower=fit$ci.lb,ref_lower=ref$ci.lb,loo=dim(loo_matrix),points=bubble_data$predicted)")
    assert p["beta"] == pytest.approx(p["reference"], abs=1e-10)
    assert p["lower"] == pytest.approx(p["ref_lower"], abs=1e-10)
    assert p["loo"] == [32, 3]
    assert len(p["points"]) == 32


@pytest.mark.parametrize("denominator", ["sample_size", "person_time"])
def test_proportion_and_person_time_rate_keep_zero_studies(backend, tmp_path, denominator):
    data = ('''dataset<-data.frame(study=letters[1:5],events=c(0,3,8,10,4),n=c(10,10,10,10,20))'''
            if denominator == "sample_size" else
            '''dataset<-data.frame(study=letters[1:5],events=c(0,3,8,20,4),n=c(10,.5,2,.5,20))''')
    _, p = run_core(backend, tmp_path, "evidence_proportion_rate_meta", data,
        {"study_id": "study", "event_count": "events", "sample_size_person_time": "n", "denominator_type": denominator},
        "list(k=fit$k,yi=df$.yi,vi=df$.vi,table=pool_table)")
    assert p["k"] == 5
    assert all(math.isfinite(x) for x in p["yi"] + p["vi"])
    if denominator == "sample_size":
        assert p["yi"][0] == pytest.approx(math.log(.5/10.5), abs=1e-12)
        assert p["vi"][0] == pytest.approx(1/.5+1/10.5, abs=1e-12)
        assert all(0 <= row["estimate"] <= 1 for row in p["table"])
    else:
        assert p["yi"][3] == pytest.approx(math.log(40), abs=1e-12)
        assert p["vi"][3] == pytest.approx(1/20, abs=1e-12)


def test_continuous_cluster_rct_matches_lmer_and_cr2_and_correct_roles(backend, tmp_path):
    result, p = run_core(backend, tmp_path, "cluster_rct_analysis", '''
set.seed(208)
dataset<-expand.grid(id=seq_len(20),person=seq_len(12))
dataset$arm<-ifelse(dataset$id%%2,"A","B");dataset$x<-rnorm(nrow(dataset))
u<-rnorm(20);dataset$y<-2+as.numeric(dataset$arm=="B")*.7+dataset$x*.2+u[dataset$id]+rnorm(nrow(dataset))
dataset$id<-factor(dataset$id);dataset$arm<-factor(dataset$arm)
ref<-lme4::lmer(y~arm+x+(1|id),data=dataset,REML=TRUE)
ref_ct<-clubSandwich::coef_test(ref,vcov="CR2",test="Satterthwaite",cluster=dataset$id)
''', {"cluster_id": "id", "group": "arm", "outcome": "y", "covariates": ["x"]},
        "list(beta=model_table$estimate,reference=lme4::fixef(ref),SE=model_table$SE,ref_SE=ref_ct$SE,ICC=icc_table$ICC)")
    assert p["beta"] == pytest.approx(p["reference"], abs=1e-7)
    assert p["SE"] == pytest.approx(p["ref_SE"], abs=1e-7)
    icc_path = next(a.path for a in result.tables if a.name == "table_1")
    model_path = next(a.path for a in result.tables if a.name == "table_2")
    assert "ICC" in Path(icc_path).read_text(encoding="utf-8-sig").splitlines()[0]
    assert "estimate" in Path(model_path).read_text(encoding="utf-8-sig").splitlines()[0]


def test_weighted_binary_cluster_gee_matches_selected_estimator(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "cluster_rct_analysis", '''
set.seed(133)
dataset<-expand.grid(person=seq_len(20),id=seq_len(30))
dataset$arm<-ifelse(dataset$id%%2,"A","B");dataset$x<-rnorm(nrow(dataset));dataset$w<-runif(nrow(dataset),.5,1.5)
dataset$y<-rbinom(nrow(dataset),1,plogis(-.4+.5*(dataset$arm=="B")+.2*dataset$x))
dataset$arm<-factor(dataset$arm);dataset<-dataset[order(dataset$id),]
ref<-geepack::geeglm(y~arm+x,id=id,weights=w,data=dataset,family=binomial(),corstr="exchangeable")
''', {"cluster_id": "id", "group": "arm", "outcome": "y", "covariates": ["x"], "weight": "w", "analysis_method": "gee"},
        "list(OR=model_table$estimate,ref_OR=exp(coef(ref)),scale=model_table$effect_scale)")
    assert p["OR"] == pytest.approx(p["ref_OR"], rel=1e-7)
    assert set(p["scale"]) == {"population-averaged OR"}


def test_serial_gatekeeping_honors_named_p_values_zero_and_blocked_families(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "trial_multiplicity_gatekeeping", '''
dataset<-data.frame(hypothesis=c("H1","H2","H3","H4"),p=c(0,.06,.001,1),family=c(1,1,2,2),
 weights=c(1,1,1,1),irrelevant=c(.00001,.0001,.0001,.0001))
''', {"outcome": "hypothesis", "raw_p_value": "p", "testing_hierarchy": "family", "weight": "weights"},
        "list(table=mult_table,alpha=.diag_status[['alpha 守恒检查']])")
    table = p["table"]
    assert len(table) == 4
    assert [r["raw_p"] for r in table] == [0, .06, .001, 1]
    assert [r["gate_open"] for r in table] == [True, True, False, False]
    assert [r["rejected"] for r in table] == [True, False, False, False]
    assert table[2]["gate_adjusted_p"] == pytest.approx(.12)
    assert p["alpha"] == "passed"


def test_gatekeeping_weighted_alpha_and_subgroup_budget(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "trial_multiplicity_gatekeeping", '''
dataset<-data.frame(hypothesis=c("H1","H2","H1","H2"),p=c(.001,.001,.01,.001),family=1,
 weights=c(1,3,1,3),stratum=c("A","A","B","B"))
''', {"outcome": "hypothesis", "raw_p_value": "p", "testing_hierarchy": "family", "weight": "weights", "subgroup": "stratum", "alpha": .04},
        "list(table=mult_table)")
    assert [r["allocated_alpha"] for r in p["table"]] == pytest.approx([.005, .015, .005, .015])
    assert [r["rejected"] for r in p["table"]] == [True, True, False, True]


def test_forecast_ets_matches_holdout_and_requested_horizon(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "forecasting", '''
i<-seq_len(72);dataset<-data.frame(time=i,y=20+.1*i+sin(i*pi/6)*3+cos(i*.8))
ref_train<-forecast::ets(ts(dataset$y[1:60],frequency=12))
ref_pred<-as.numeric(forecast::forecast(ref_train,h=12)$mean)
ref_error<-sqrt(mean((dataset$y[61:72]-ref_pred)^2))
ref_full<-forecast::forecast(forecast::ets(ts(dataset$y,frequency=12)),h=6,level=95)
''', {"time": "time", "measure": "y", "frequency": 12, "horizon": 6, "test_size": 12, "analysis_method": "ets"},
        "list(prediction=fc_table$prediction,reference=as.numeric(ref_full$mean),RMSE=err_table$RMSE,ref_RMSE=ref_error,n_train=err_table$n_train,trend_n=nrow(trend_table))")
    assert p["prediction"] == pytest.approx(p["reference"], abs=1e-8)
    assert p["RMSE"] == pytest.approx(p["ref_RMSE"], abs=1e-8)
    assert p["n_train"] == 60
    assert p["trend_n"] == 72


def test_forecast_regions_have_separate_regular_monthly_sequences(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "forecasting", '''
dates<-seq(as.Date("2020-01-01"),by="month",length.out=60)
dataset<-data.frame(time=rep(dates,2),region=rep(c("A","B"),each=60),y=c(seq_len(60)+sin(seq_len(60)),200+seq_len(60)*2+cos(seq_len(60))))
''', {"time": "time", "measure": "y", "group_region": "region", "frequency": 12, "horizon": 3, "analysis_method": "ets"},
        "list(regions=fc_table$region,nrow=nrow(fc_table),errors=err_table$region)")
    assert p["regions"] == ["A"]*3 + ["B"]*3
    assert p["errors"] == ["A", "B"]


@pytest.mark.parametrize("method", ["ols", "ar1"])
def test_its_exact_policy_level_slope_and_covariate_match_reference(backend, tmp_path, method):
    _, p = run_core(backend, tmp_path, "interrupted_time_series", '''
set.seed(123);i<-seq_len(48);dataset<-data.frame(time=i,x=cos(i*.73))
dataset$y<-5+.1*i+2*(i>=19)+.3*pmax(i-19,0)+dataset$x*.5+as.numeric(arima.sim(list(ar=.2),n=48,sd=.2))
ref_data<-transform(dataset,post=as.integer(time>=19),after=pmax(time-19,0))
''', {"time": "time", "outcome": "y", "intervention_time": 19, "covariates": ["x"], "analysis_method": method},
        '''list(beta=as.numeric(beta),reference=as.numeric(coef(if(method=="ols") lm(y~time+post+after+x,data=ref_data)
          else nlme::gls(y~time+post+after+x,data=ref_data,correlation=nlme::corAR1(form=~time),method="REML"))),
          first_post=first_post,after0=df$.after[first_post],slope=slope_table$estimate)''')
    assert p["beta"] == pytest.approx(p["reference"], abs=1e-7)
    assert p["first_post"] == 19 and p["after0"] == 0
    assert p["slope"][1] == pytest.approx(p["beta"][1]+p["beta"][3], abs=1e-12)


def test_cea_paired_draws_use_supplied_joint_ids_and_nmb(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "public_cost_effectiveness_psa", '''
dataset<-data.frame(strategy=rep(c("A","B"),each=30),draw=rep(seq_len(30),2),cost=c(rep(100,30),rep(150,30)),effect=c(rep(1,30),rep(1.1,30)))
''', {"strategy": "strategy", "cost": "cost", "health_outcome": "effect", "threshold": 1000,
         "parameter_distributions": {"method": "paired_draws", "draw_id": "draw", "reference_strategy": "A"}},
        "list(table=cea_table,ceac=ceac_table[ceac_table$x==1000,,drop=FALSE])")
    b = next(row for row in p["table"] if row["strategy"] == "B")
    assert b["incremental_cost"] == 50
    assert b["incremental_effect"] == pytest.approx(.1)
    assert b["ICER"] == pytest.approx(500)
    assert b["mean_NMB"] == pytest.approx(50)
    assert b["draws"] == 30
    assert b["probability_vs_reference"] == 1
    assert next(r for r in p["ceac"] if r["strategy"] == "B")["y"] == 1


def test_cea_bootstrap_preserves_joint_cost_effect_relation(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "public_cost_effectiveness_psa", '''
dataset<-data.frame(strategy=rep(c("A","B"),each=30),effect=c(seq(1,2,length.out=30),seq(2,3,length.out=30)))
dataset$cost<-1000*dataset$effect
''', {"strategy": "strategy", "cost": "cost", "health_outcome": "effect", "threshold": 1000,
         "parameter_distributions": {"method": "bootstrap_individual", "reference_strategy": "A", "nsim": 300, "seed": 7}},
        "list(table=cea_table,ceac=ceac_table[ceac_table$x==1000,,drop=FALSE])")
    b = next(row for row in p["table"] if row["strategy"] == "B")
    assert b["mean_NMB"] == pytest.approx(0, abs=1e-9)
    assert b["NMB_lower"] == pytest.approx(0, abs=1e-9)
    assert b["NMB_upper"] == pytest.approx(0, abs=1e-9)
    assert [r["y"] for r in p["ceac"]] == pytest.approx([.5, .5])


def test_screening_uses_actual_gold_standard_and_correct_positive_direction(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "public_screening_evaluation", '''
dataset<-data.frame(gold=rep(c(1,0),each=5),screen=c(1,1,1,1,0,1,0,0,0,0))
''', {"outcome": "screen", "outcome_2": "gold"},
        "list(table=scr_table,curve=curve_data[curve_data$strategy=='screen cutoff=0.5' & abs(curve_data$x-.2)<1e-9,,drop=FALSE])")
    assert [r["estimate"] for r in p["table"]] == pytest.approx([.8, .8, .8, .8])
    assert all(r["TP"] == 4 and r["TN"] == 4 for r in p["table"])
    assert p["curve"][0]["y"] == pytest.approx(.4-.1*.2/.8)
    assert all(0 <= r["lower"] <= r["upper"] <= 1 for r in p["table"])


def test_screening_continuous_direction_threshold_subgroups_and_followup(backend, tmp_path):
    _, p = run_core(backend, tmp_path, "public_screening_evaluation", '''
dataset<-data.frame(gold=rep(rep(c(1,0),each=6),2),score=rep(c(.1,.2,.3,.4,.8,.9,.5,.6,.7,.8,.9,1),2),
 subgroup=rep(c("A","B"),each=12),cost=2)
dataset$followup<-dataset$gold; dataset$cutoff<-.5
''', {"outcome": "score", "outcome_2": "gold", "outcome_3": "followup", "subgroup": "subgroup",
        "threshold": "cutoff", "cost": "cost", "score_direction": "lower_positive", "score_type": "continuous"},
        "list(table=scr_table,rows=nrow(scr_table))")
    assert p["rows"] == 16
    assert {r["endpoint"] for r in p["table"]} == {"gold", "followup"}
    assert {r["stratum"] for r in p["table"]} == {"A", "B"}
    assert all(r["mean_screening_cost"] == 2 for r in p["table"])
    assert all(r["cutoff"] == .5 for r in p["table"])


@pytest.mark.parametrize("template_id,code,inputs,message", [
    ("pairwise_meta", "dataset<-data.frame(study=c('A','A','B'),effect=c(.1,.2,.3),se=.1)", META_INPUTS, "唯一"),
    ("evidence_proportion_rate_meta", "dataset<-data.frame(study=letters[1:3],events=c(5,12,3),n=10)",
     {"study_id": "study", "event_count": "events", "sample_size_person_time": "n"}, "事件数不得超过"),
    ("trial_multiplicity_gatekeeping", "dataset<-data.frame(h=c('H1','H2'),p=c(.01,.02),family=1)",
     {"outcome": "h", "raw_p_value": "p", "testing_hierarchy": "family", "transition_rules": "arbitrary_graph"}, "转移规则"),
    ("forecasting", "dataset<-data.frame(time=c(1:20,22:40),y=1:39)", {"time": "time", "measure": "y"}, "不等间隔"),
    ("interrupted_time_series", "dataset<-data.frame(time=1:40,y=sin(1:40))",
     {"time": "time", "outcome": "y", "intervention_time": 15.5}, "准确对应"),
    ("public_cost_effectiveness_psa", "dataset<-data.frame(strategy=rep(c('A','B'),each=3),cost=1:6,effect=1:6)",
     {"strategy": "strategy", "cost": "cost", "health_outcome": "effect", "threshold": 10,
      "parameter_distributions": "随便用一个分布"}, "模拟方案"),
    ("public_screening_evaluation", "dataset<-data.frame(gold=rep(c(0,1),each=5),score=seq(.1,1,length.out=10))",
     {"outcome": "score", "outcome_2": "gold", "score_type": "continuous"}, "需指定"),
])
def test_invalid_or_unimplemented_settings_are_not_silently_ignored(backend, tmp_path, template_id, code, inputs, message):
    _, p = run_core(backend, tmp_path, template_id, code, inputs, "list(message=res)", failure=True)
    assert p["message"] != "OK"
    assert message in p["message"]


def test_maps_cover_every_existing_input_and_exact_artifact_kind():
    catalog = AnalysisCatalog()
    for template_id in CORES:
        template = catalog.get(template_id)
        assert {s.id for s in template.input_specs} <= set(INPUTS[template_id])
        assert {s.role for s in template.required_outputs} == set(ROLE_MAPS[template_id])
