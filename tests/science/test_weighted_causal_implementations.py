"""Actual-R references for frozen weighted/causal template implementations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.weighted_causal_cores import CORES, INPUTS, ROLE_MAPS
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.protocol import DatasetRef, RRunRequest


def run_core(backend, tmp_path, template_id, data, inputs, probe, *, success=True):
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(
        plan_id="weighted-causal-reference",
        source="template",
        dataset_id="fixture",
        intent=template.title,
        category=template.category,
        method_family=template.family,
        scenario=template.scenario,
        variable_bindings={},
        packages=(),
        prerequisites=(),
        assumptions=(),
        diagnostics=template.diagnostics,
        required_outputs=template.required_outputs,
        resource_class="standard",
        template_id=template_id,
        resolved_inputs={
            k: ResolvedInput(k, k, specs[k].kind if k in specs else "specification", v, "user")
            for k, v in inputs.items()
        },
    )
    script = _make_engine(CORES[template_id], INPUTS[template_id], ())(plan, ROLE_MAPS[template_id])
    if not success:
        script = "tryCatch({\n" + script + "\n},error=function(e) invisible(NULL))"
    guarded = script + "\n" + ('if(!identical(res,"OK")) stop(res)\n' if success else "")
    guarded += 'baa_text("probe",jsonlite::toJSON(' + probe + ",auto_unbox=TRUE,digits=15))"
    code = data + "\n" + BindingHelpersMixin._frozen_input_prelude(plan)
    code += "\n" + BindingHelpersMixin._frozen_user_code_block(guarded,plan)
    if success:
        code += "\n" + BindingHelpersMixin._frozen_input_epilogue(plan)
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    result = backend.run_code(
        RRunRequest(
            job_id="weighted-causal",
            dataset=DatasetRef("fixture", str(dummy), "csv", "input.csv"),
            code=code,
            artifacts_dir=str(tmp_path / "artifacts"),
            timeout_seconds=120,
        )
    )
    assert result.ok, result.error + "\n" + result.stderr
    artifact = next(x for x in result.texts if x.name == "probe")
    return result, json.loads(Path(artifact.path).read_text(encoding="utf-8"))


def test_survey_keeps_psu_strata_and_covariates(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "survey_weighted",
        """
i <- seq_len(80)
dataset <- data.frame(y=sin(i*.4)+i/10,x=cos(i*.3),w=1+i%%5,
  stratum=rep(1:4,each=20),psu=rep(rep(1:2,each=10),4))
reference_design <- survey::svydesign(ids=~psu,strata=~stratum,weights=~w,data=dataset,nest=TRUE)
reference_mean <- survey::svymean(~y,reference_design)
reference_fit <- survey::svyglm(y~x,reference_design)
""",
        {"outcome": "y", "weight": "w", "strata": "stratum", "cluster_variable": "psu", "covariates": ["x"]},
        """list(mean=est_table$估计,se=est_table$标准误,ci=unname(as.numeric(mean_ci)),df=design_df,
          reference_mean=unname(coef(reference_mean)),reference_se=as.numeric(survey::SE(reference_mean)),
          reference_ci=unname(as.numeric(confint(reference_mean,df=survey::degf(reference_design)))),
          coefficients=unname(coef(fit)),reference_coef=unname(coef(reference_fit)))""",
    )
    assert p["mean"] == pytest.approx(p["reference_mean"])
    assert p["se"] == pytest.approx(p["reference_se"])
    assert p["ci"] == pytest.approx(p["reference_ci"])
    assert p["df"] == 4
    assert p["coefficients"] == pytest.approx(p["reference_coef"])
    assert {a.name for a in result.tables} == {"table_1", "table_2", "table_3", "table_4"}


def test_survey_categorical_outcome_has_design_ci(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "survey_weighted",
        """
dataset <- data.frame(y=rep(c("yes","no","no"),10),w=rep(1:5,6))
""",
        {"outcome": "y", "weight": "w"},
        "list(table=est_table,df=design_df)",
    )
    assert len(p["table"]) == 2
    assert sum(row["估计"] for row in p["table"]) == pytest.approx(1)
    assert all(row["标准误"] > 0 for row in p["table"])


def test_survey_lonely_psu_is_not_silently_adjusted(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "survey_weighted",
        """
dataset <- data.frame(y=1:12,w=1,stratum=rep(1:3,each=4),psu=rep(c(1,1,2),each=4))
""",
        {"outcome": "y", "weight": "w", "strata": "stratum", "cluster_variable": "psu"},
        "list(message=res)",
        success=False,
    )
    assert p["message"] != "OK"


PS_DATA = """
i <- seq_len(140)
dataset <- data.frame(x=sin(i*.7),sex=rep(c("F","M"),70),t=as.integer(i%%4<2),sw=1+i%%3)
dataset$y <- 2*dataset$t+3*dataset$x+as.numeric(dataset$sex=="M")+cos(i*1.7)*.3
"""


def test_propensity_weighted_effect_balance_and_no_hidden_cap(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "propensity_clinical",
        PS_DATA,
        {
            "group": "t",
            "outcome": "y",
            "covariates": ["x", "sex"],
            "weight": "sw",
            "bootstrap_replicates": 39,
        },
        """list(estimate=ps_result$estimate,reference=weighted.mean(df$.y[df$.t==1],w_ate[df$.t==1])-weighted.mean(df$.y[df$.t==0],w_ate[df$.t==0]),
          w=w_ate,raw_weights=df$.base_w*ifelse(df$.t==1,1/ps,1/(1-ps)),balance=balance_df,
          bootstrap_success=boot$success)""",
    )
    assert p["estimate"] == pytest.approx(p["reference"])
    assert p["w"] == pytest.approx(p["raw_weights"])
    assert any("sex" in r["协变量"] for r in p["balance"])
    assert p["bootstrap_success"] >= 32
    assert len(result.tables) == 3 and len({a.metadata.get("role") for a in result.figures}) == 1


@pytest.mark.parametrize("estimand", ["ATT", "overlap"])
def test_propensity_honors_estimand(backend, tmp_path, estimand):
    _, p = run_core(
        backend,
        tmp_path,
        "propensity_clinical",
        PS_DATA,
        {
            "group": "t",
            "outcome": "y",
            "covariates": ["x", "sex"],
            "estimand": estimand,
            "bootstrap_replicates": 19,
        },
        """list(estimand=ps_result$estimand,w=w_ate,reference=if(estimand_v=="ATT") ifelse(df$.t==1,1,ps/(1-ps)) else ifelse(df$.t==1,1-ps,ps))""",
    )
    assert p["estimand"] == estimand
    assert p["w"] == pytest.approx(p["reference"])


def test_aipw_fits_covariate_outcome_models_and_distinct_ipw(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "clinical_doubly_robust_effect",
        PS_DATA,
        {
            "group": "t",
            "outcome": "y",
            "covariates": ["x", "sex"],
            "weight": "sw",
            "bootstrap_replicates": 39,
        },
        """list(estimate=aipw_est,reference=weighted.mean(aipw$m1-aipw$m0+df$.t*(df$.y-aipw$m1)/ps-(1-df$.t)*(df$.y-aipw$m0)/(1-ps),df$.base_w),
          models=lapply(aipw$outcome_models,function(f) unname(coef(f))),m1_sd=sd(aipw$m1),m0_sd=sd(aipw$m0),ipw=aipw$ipw,table=primary_table)""",
    )
    assert p["estimate"] == pytest.approx(p["reference"])
    assert p["estimate"] == pytest.approx(2, abs=0.12)
    assert p["m1_sd"] > 1 and p["m0_sd"] > 1
    assert p["table"][1]["效应"] == pytest.approx(p["ipw"])
    assert p["table"][2]["效应"] == pytest.approx(p["estimate"])
    assert len(result.tables) == 1 and len({a.metadata.get("role") for a in result.figures}) == 1


def test_aipw_uses_subgroup_and_binary_outcome(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "clinical_doubly_robust_effect",
        """
set.seed(913);i <- seq_len(500)
dataset <- data.frame(x=rnorm(500),t=rbinom(500,1,.5),sub=rep(c("A","B"),250))
dataset$y <- rbinom(500,1,plogis(-.4+dataset$t*.5+dataset$x*.3))
""",
        {"group": "t", "outcome": "y", "covariates": ["x"], "subgroup": "sub", "bootstrap_replicates": 19},
        "list(binary=outcome$binary,nrows=nrow(primary_table),estimate=aipw_est)",
    )
    assert p["binary"] is True
    assert p["nrows"] == 5
    assert -0.1 < p["estimate"] < 0.3


def test_vaccine_wires_vaccination_and_all_adjustment_columns(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "public_vaccine_test_negative",
        """
set.seed(221);i <- seq_len(600)
dataset <- data.frame(vax=rbinom(600,1,.5),age=runif(600,20,70),day=rep(1:3,200),region=rep(c("A","B"),300),x=rnorm(600))
dataset$y <- rbinom(600,1,plogis(-1-dataset$vax*.7+dataset$age*.01+dataset$x*.2))
reference <- glm(y~vax+age+factor(day)+factor(region)+x,data=dataset,family=binomial())
""",
        {
            "outcome": "y",
            "vaccination_status": "vax",
            "age": "age",
            "encounter_time": "day",
            "region": "region",
            "covariates": ["x"],
        },
        """list(or=or_adj,reference=exp(coef(reference)["vax"]),
          ci=adj_ci,ve_low=ve_table$VE_CI下限[2],ve_high=ve_table$VE_CI上限[2],covars=covs)""",
    )
    assert p["or"] == pytest.approx(p["reference"], rel=1e-8)
    assert set(p["covars"]) == {"age", "day", "region", "x"}
    assert p["ve_low"] == pytest.approx(100 * (1 - p["ci"][1]))
    assert p["ve_high"] == pytest.approx(100 * (1 - p["ci"][0]))
    assert len(result.tables) == 1 and len({a.metadata.get("role") for a in result.figures}) == 1


def test_vaccine_two_by_two_direction_is_positive_vaccinated(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "public_vaccine_test_negative",
        """
dataset <- data.frame(vax=rep(0:1,each=100),y=c(rep(1,40),rep(0,60),rep(1,20),rep(0,80)))
""",
        {"outcome": "y", "vaccination_status": "vax"},
        "list(or=or_raw,adj=or_adj,ve=ve_table$VE百分比[1])",
    )
    assert p["or"] == pytest.approx(0.375)
    assert p["adj"] == pytest.approx(0.375)
    assert p["ve"] == pytest.approx(62.5)


def test_age_standardization_uses_common_standard_and_gamma_ci(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "age_standardization",
        """
dataset <- data.frame(age=rep(c(1,2),2),events=c(10,30,20,15),pop=c(100,100,100,50),standard=rep(c(80,20),2),g=rep(c("A","B"),each=2))
reference <- epitools::ageadjust.direct(c(10,30),c(100,100),stdpop=c(80,20))
""",
        {
            "age_group": "age",
            "event_count": "events",
            "denominator": "pop",
            "standard_population": "standard",
            "group": "g",
            "rate_scale": 1,
        },
        "list(table=std_table,reference=unname(reference))",
    )
    assert p["table"][0]["粗率"] == pytest.approx(0.2)
    assert p["table"][0]["标化率"] == pytest.approx(0.14)
    assert p["table"][1]["标化率"] == pytest.approx(0.22)
    assert p["table"][0]["CI下限"] == pytest.approx(p["reference"][2])
    assert p["table"][0]["CI上限"] == pytest.approx(p["reference"][3])
    assert len(result.tables) == 1 and len({a.metadata.get("role") for a in result.figures}) == 1


def test_age_requires_standard_instead_of_returning_crude_rate(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "age_standardization",
        """
dataset <- data.frame(age=1:3,events=c(10,20,30),pop=c(100,100,100))
""",
        {"age_group": "age", "event_count": "events", "denominator": "pop"},
        "list(message=res)",
        success=False,
    )
    assert "标准人口" in p["message"]


def test_inequality_weighted_tie_ridit_sii_and_concentration(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "health_inequality",
        """
dataset <- data.frame(rank=rep(1:3,each=20),w=rep(c(1,2,3),each=20),g=rep(c("A","B"),30))
ridit_ref <- rep(c(1/12,1/3,3/4),each=20)
dataset$y <- 10+4*ridit_ref+sin(seq_len(60))*.01
reference_fit <- lm(y~ridit_ref,data=dataset,weights=w)
reference_c <- 2*weighted.mean((dataset$y-weighted.mean(dataset$y,dataset$w))*(ridit_ref-.5),dataset$w)/weighted.mean(dataset$y,dataset$w)
""",
        {"outcome": "y", "socioeconomic_rank_variable": "rank", "weight": "w", "group": "g"},
        "list(ridit=df$.ridit,reference_ridit=ridit_ref,sii=sii,reference_sii=unname(coef(reference_fit)[2]),c=concentration,reference_c=reference_c,ng=nrow(group_df))",
    )
    assert p["ridit"] == pytest.approx(p["reference_ridit"])
    assert p["sii"] == pytest.approx(p["reference_sii"])
    assert p["c"] == pytest.approx(p["reference_c"])
    assert p["ng"] == 6


def test_inequality_binary_sii_is_risk_difference_and_rii_risk_ratio(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "health_inequality",
        """
set.seed(876);i<-seq_len(500)
dataset <- data.frame(rank=rep(1:5,100),x=rnorm(500))
dataset$y <- rbinom(500,1,plogis(-1+dataset$rank*.2+dataset$x*.1))
""",
        {"outcome": "y", "socioeconomic_rank_variable": "rank", "covariates": ["x"]},
        "list(sii=sii,m0=m0,m1=m1,rii=rii,coef=unname(coef(fit)[2]))",
    )
    assert p["sii"] == pytest.approx(p["m1"] - p["m0"])
    assert p["rii"] == pytest.approx(p["m1"] / p["m0"])
    assert p["sii"] != pytest.approx(p["coef"])


@pytest.mark.parametrize("aggregate", [False, True])
def test_attributable_fraction_matches_levin_and_scenarios(backend, tmp_path, aggregate):
    data = """dataset <- data.frame(t=rep(0:1,each=50),y=c(rep(1,10),rep(0,40),rep(1,20),rep(0,30)))"""
    inputs = {"outcome": "y", "exposure": "t"}
    if aggregate:
        data = """dataset <- data.frame(t=rep(0:1,each=2),y=c(5,5,10,10),n=25)"""
        inputs["denominator"] = "n"
    _, p = run_core(
        backend,
        tmp_path,
        "attributable_fraction",
        data,
        inputs,
        "list(rr=rr,afe=afe,paf=paf,scenario=cmp_table)",
    )
    assert p["rr"] == pytest.approx(2)
    assert p["afe"] == pytest.approx(0.5)
    assert p["paf"] == pytest.approx(1 / 3)
    assert p["scenario"][-1]["预计减少事件"] == pytest.approx(10)


def test_attributable_fraction_covariates_use_standardized_counterfactual(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "attributable_fraction",
        """
set.seed(45);i <- seq_len(500)
dataset <- data.frame(x=rnorm(500),t=rbinom(500,1,.5))
dataset$y <- rbinom(500,1,plogis(-1+dataset$t*.8+dataset$x*.7))
""",
        {"outcome": "y", "exposure": "t", "covariates": ["x"]},
        "list(paf=paf,reference=1-weighted.mean(m0,df$.n)/weighted.mean(observed_pred,df$.n),source=source)",
    )
    assert p["paf"] == pytest.approx(p["reference"])
    assert "g-computation" in p["source"]


@pytest.mark.parametrize("mode", ["outcome", "exposure"])
def test_negative_control_honors_type_and_continuous_outcomes(backend, tmp_path, mode):
    _, p = run_core(
        backend,
        tmp_path,
        "negative_control_sensitivity",
        """
i <- seq_len(160)
dataset <- data.frame(t=as.integer(i%%3==0),x=sin(i*.7),nc=cos(i*.3))
dataset$y <- 2*dataset$t+.4*dataset$x+.7*dataset$nc+sin(i*1.7)*.1
""",
        {
            "outcome": "y",
            "exposure": "t",
            "negative_control_variable": "nc",
            "covariates": ["x"],
            "negative_control_type": mode,
        },
        'list(mode=mode,main=main_table$效应,nc=negative_table$效应,reference=if(mode=="outcome") unname(coef(lm(nc~t+x,data=dataset))["t"]) else unname(coef(lm(y~nc+t+x,data=dataset))["nc"]),main_family=family(main_fit)$family)',
    )
    assert p["mode"] == mode
    assert p["main_family"] == "gaussian"
    assert p["nc"] == pytest.approx(p["reference"])


def test_did_estimate_cluster_inference_joint_pretest_and_placebo(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "did_policy",
        """
dataset <- expand.grid(id=seq_len(40),time=1:6)
dataset$t <- as.integer(dataset$id>20)
dataset$y <- dataset$id*.1+dataset$time*.2+3*dataset$t*(dataset$time>=4)+sin(dataset$id*dataset$time)*.1
reference_fit <- lm(y~t+factor(time)+I(t*(time>=4)),data=dataset)
reference_covariance <- sandwich::vcovCL(reference_fit,cluster=dataset$id,type="HC1")
""",
        {
            "outcome": "y",
            "treatment_group": "t",
            "time": "time",
            "policy_start_time": 4,
            "cluster_variable": "id",
        },
        "list(estimate=did_est,se=did_se,reference=unname(tail(coef(reference_fit),1)),reference_se=sqrt(tail(diag(reference_covariance),1)),pre_p=pre_p,placebo_p=placebo_p,df=inf$df)",
    )
    assert p["estimate"] == pytest.approx(p["reference"])
    assert p["se"] == pytest.approx(p["reference_se"])
    assert 0 <= p["pre_p"] <= 1 and 0 <= p["placebo_p"] <= 1
    assert p["df"] == 39


def test_did_missing_pretrend_is_not_marked_passed(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "did_policy",
        """
dataset <- expand.grid(id=1:20,time=1:2);dataset$t <- as.integer(dataset$id>10)
dataset$y <- dataset$id*.1+2*dataset$t*(dataset$time==2)+cos(dataset$id*dataset$time)*.1
""",
        {"outcome": "y", "treatment_group": "t", "time": "time", "policy_start_time": 2},
        "list(status=.diag_status,estimate=did_est)",
    )
    assert p["status"]["平行趋势检查"] == "not_assessed"
    assert p["status"]["安慰剂时间点检查"] == "not_assessed"
    assert p["status"]["聚类稳健标准误"] == "not_assessed"


def test_iv_preserves_continuous_exposure_and_matches_2sls(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "iv_panel_public_health",
        """
i<-seq_len(160);dataset<-data.frame(z=sin(i*.7),u=cos(i*.3))
dataset$x <- 2*dataset$z+dataset$u;dataset$y <- 1+3*dataset$x+dataset$u+sin(i*1.3)*.1
reference <- AER::ivreg(y~x|z,data=dataset)
""",
        {"outcome": "y", "exposure": "x", "instrumental_variable": "z", "analysis_method": "iv"},
        "list(coef=unname(coef(fit)),reference=unname(coef(reference)),x=df$.x,raw=dataset$x,F=weak_f)",
    )
    assert p["coef"] == pytest.approx(p["reference"])
    assert p["x"] == pytest.approx(p["raw"])
    assert p["F"] > 10


def test_fixed_effect_branch_needs_no_instrument_and_uses_subject_time(backend, tmp_path):
    result, p = run_core(
        backend,
        tmp_path,
        "iv_panel_public_health",
        """
dataset<-expand.grid(id=1:30,time=1:5)
dataset$x <- sin(dataset$id*dataset$time*.4)+dataset$id*.02
dataset$y <- dataset$id*.3+dataset$time*.2+2*dataset$x+cos(dataset$id*dataset$time)*.1
reference <- lm(y~x+factor(id)+factor(time),data=dataset)
""",
        {
            "outcome": "y",
            "exposure": "x",
            "subject_id": "id",
            "time": "time",
            "analysis_method": "fixed_effects",
        },
        'list(coef=unname(coef(fit)[".x"]),reference=unname(coef(reference)["x"]),mode=mode,df=inference$df)',
    )
    assert p["coef"] == pytest.approx(p["reference"])
    assert p["mode"] == "fixed_effects" and p["df"] == 29
    assert {a.name for a in result.tables} == {"table_2", "table_4", "table_5", "table_6"}


def test_iv_with_panel_controls_matches_fixed_effect_2sls(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "iv_panel_public_health",
        """
dataset<-expand.grid(id=1:25,time=1:5)
dataset$z<-sin(dataset$id*dataset$time*.6);dataset$u<-cos(dataset$id*dataset$time*.3)
dataset$x<-dataset$id*.1+dataset$z+dataset$u
dataset$y<-dataset$id*.2+dataset$time*.3+2*dataset$x+dataset$u
reference<-AER::ivreg(y~x+factor(id)+factor(time)|z+factor(id)+factor(time),data=dataset)
""",
        {
            "outcome": "y",
            "exposure": "x",
            "instrumental_variable": "z",
            "subject_id": "id",
            "time": "time",
            "analysis_method": "iv",
        },
        'list(coef=unname(coef(fit)[".x"]),reference=unname(coef(reference)["x"]),df=inference$df)',
    )
    assert p["coef"] == pytest.approx(p["reference"])
    assert p["df"] == 24


def test_noncanonical_treatment_requires_explicit_direction(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "propensity_clinical",
        PS_DATA + '\ndataset$t <- ifelse(dataset$t==1,"treated","control")',
        {"group": "t", "outcome": "y", "covariates": ["x", "sex"], "bootstrap_replicates": 19},
        "list(message=res)",
        success=False,
    )
    assert "类别" in p["message"] and p["message"] != "OK"


def test_propensity_explicit_treatment_direction_and_requested_weight_cap(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "propensity_clinical",
        PS_DATA + '\ndataset$t <- ifelse(dataset$t==1,"treated","control")',
        {
            "group": "t",
            "outcome": "y",
            "covariates": ["x", "sex"],
            "treatment_level": "control",
            "weight_cap": 1.5,
            "bootstrap_replicates": 19,
        },
        "list(estimate=ps_result$estimate,max_w=max(w_ate),selected=unique(as.character(df$t)[df$.t==1]))",
    )
    assert p["estimate"] < -1.5
    assert p["max_w"] <= 1.5
    assert p["selected"] == "control"


def test_survey_explicit_lonely_psu_adjustment(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "survey_weighted",
        """dataset <- data.frame(y=1:24,w=1,stratum=rep(1:3,each=8),psu=c(rep(1,8),rep(1:2,each=4),rep(1:2,each=4)))""",
        {
            "outcome": "y",
            "weight": "w",
            "strata": "stratum",
            "cluster_variable": "psu",
            "lonely_psu": "adjust",
        },
        'list(mean=est_table$估计,se=est_table$标准误,policy=lonely,design_status=.diag_status[["设计变量完整性检查"]])',
    )
    assert p["mean"] == pytest.approx(12.5)
    assert p["se"] > 0
    assert p["policy"] == "adjust"
    assert p["design_status"] == "passed"


def test_age_rejects_conflicting_standard_population(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "age_standardization",
        """dataset<-data.frame(age=rep(1:2,2),e=c(10,30,20,15),n=c(100,100,100,50),s=c(80,20,70,30),g=rep(c("A","B"),each=2))""",
        {
            "age_group": "age",
            "event_count": "e",
            "denominator": "n",
            "standard_population": "s",
            "group": "g",
        },
        "list(message=res)",
        success=False,
    )
    assert "一致" in p["message"]


def test_attributable_external_rr_is_used_and_nonconstant_rr_rejected(backend, tmp_path):
    data = """dataset<-data.frame(t=rep(0:1,each=2),y=c(5,5,10,10),n=25,rr=3)"""
    inputs = {"outcome": "y", "exposure": "t", "denominator": "n", "effect_estimate": "rr"}
    first = tmp_path / "valid"
    first.mkdir()
    _, p = run_core(backend, first, "attributable_fraction", data, inputs, "list(rr=rr,paf=paf,afe=afe)")
    assert p == pytest.approx({"rr": 3, "paf": 0.5, "afe": 2 / 3})
    second = tmp_path / "invalid"
    second.mkdir()
    _, p = run_core(
        backend,
        second,
        "attributable_fraction",
        data + "\ndataset$rr[1]<-4",
        inputs,
        "list(message=res)",
        success=False,
    )
    assert "风险比" in p["message"]


def test_did_subject_fixed_effects_and_date_policy_are_wired(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "did_policy",
        """dataset<-expand.grid(id=1:30,day=1:5);dataset$date<-as.Date("2024-01-01")+dataset$day-1
dataset$t<-as.integer(dataset$id>15);dataset$y<-dataset$id*.2+dataset$day*.3+2*dataset$t*(dataset$day>=3)+sin(dataset$id*dataset$day)*.1
reference<-lm(y~factor(id)+factor(date)+I(t*(day>=3)),data=dataset)""",
        {
            "outcome": "y",
            "treatment_group": "t",
            "time": "date",
            "policy_start_time": "2024-01-03",
            "subject_id": "id",
        },
        "list(estimate=did_est,reference=unname(tail(coef(reference),1)),df=inf$df)",
    )
    assert p["estimate"] == pytest.approx(p["reference"])
    assert p["df"] == 29


def test_inequality_explicit_category_order_and_reverse_direction(backend, tmp_path):
    _, p = run_core(
        backend,
        tmp_path,
        "health_inequality",
        """dataset<-data.frame(rank=rep(c("low","medium","high"),each=20),y=rep(c(1,2,3),each=20)+sin(1:60)*.01)""",
        {
            "outcome": "y",
            "socioeconomic_rank_variable": "rank",
            "rank_order": ["low", "medium", "high"],
            "rank_direction": "higher_to_lower",
        },
        "list(sii=sii,rank_first=df$.ridit[1],rank_last=tail(df$.ridit,1))",
    )
    assert p["sii"] < 0
    assert p["rank_first"] == pytest.approx(5 / 6)
    assert p["rank_last"] == pytest.approx(1 / 6)
