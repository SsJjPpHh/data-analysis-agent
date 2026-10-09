"""Native R and real frozen-binding checks for clinical/public workflows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.clinical_public_workflows import CORES, INPUTS, ROLE_MAPS
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt
from agent.analysis.skeleton_cores import _make_engine
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment
from r_runtime.protocol import DatasetRef, RRunRequest


@pytest.fixture(scope="module")
def backend():
    host = HostREnvironment.locate()
    verified = host.prepare()
    env = host.process_environment(verified=verified)
    return LocalRscriptBackend(rscript_bin=str(verified.rscript), runtime_env=env)


def run_workflow(backend, tmp_path, tid, data, inputs, probe="list(ok=TRUE)", success=True):
    template = AnalysisCatalog().get(tid)
    specs = {s.id: s for s in template.input_specs}
    resolved={k:ResolvedInput(k,k,specs[k].kind if k in specs else "specification",v,"user") for k,v in inputs.items()}
    plan = AnalysisPlan(
        plan_id="clinical-public-native",
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
        required_outputs=tuple(o for o in template.required_outputs if conditions_match(o.conditions,resolved)),
        resource_class="standard",
        template_id=tid,
        resolved_inputs=resolved,
    )
    script = _make_engine(CORES[tid], INPUTS[tid], ())(plan, ROLE_MAPS[tid])
    if not success:
        script = "tryCatch({\n" + script + "\n},error=function(e) invisible(NULL))"
    guarded = script + '\nbaa_text("probe",jsonlite::toJSON(' + probe + ",auto_unbox=TRUE,digits=15))"
    code = data + "\n" + BindingHelpersMixin._frozen_input_prelude(plan)
    code += "\n" + BindingHelpersMixin._frozen_user_code_block(guarded,plan)
    if success:
        code += "\n" + BindingHelpersMixin._frozen_input_epilogue(plan)
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    result = backend.run_code(
        RRunRequest(
            job_id="clinical-public-native",
            dataset=DatasetRef("fixture", str(dummy), "csv", "input.csv"),
            code=code,
            artifacts_dir=str(tmp_path / "artifacts"),
            timeout_seconds=240,
        )
    )
    assert result.ok, result.error + "\n" + result.stderr
    if success:
        validate_result(plan,result,tmp_path,"clinical-public-native")
    artifact = next(x for x in result.texts if x.name == "probe")
    return result, json.loads(Path(artifact.path).read_text(encoding="utf-8"))


def validate_result(plan,result,tmp_path,job_id):
    artifacts=[]
    for a in (*result.tables,*result.figures,*result.texts,*result.models):
        metadata=dict(a.metadata,dataset_id=plan.dataset_id,plan_id=plan.plan_id,source_tool="run_r_code",source_execution_status="succeeded",job_id=job_id)
        receipt=build_artifact_validation_receipt(a.path,kind=a.kind,metadata=metadata,job_id=job_id,artifacts_root=tmp_path/"artifacts")
        metadata.update(artifact_size_bytes=receipt["size_bytes"],artifact_sha256=receipt["sha256"],artifact_validation_receipt=receipt)
        artifacts.append(dict(a.to_dict(),metadata=metadata,source_tool="run_r_code",job_id=job_id))
    assessment=ResultValidator().validate(plan,dict(dataset_id=plan.dataset_id,plan_id=plan.plan_id,status="succeeded",diagnostic_status={d:"not_assessed" for d in plan.diagnostics}),artifacts)
    assert assessment.complete,repr(assessment)


PREDICTION_DATA = """set.seed(818);i<-1:300;dataset<-data.frame(y=rbinom(300,1,.4),p=runif(300,.05,.85))
dataset$source<-rep(c("A","B"),150);dataset$sub<-rep(c("F","M"),each=150)"""
MATCHED_DATA = """suppressPackageStartupMessages(library(survival));set.seed(551);dataset<-data.frame(set=rep(1:60,each=3),y=rep(c(1,0,0),60),x=rnorm(180))
dataset$x[dataset$y==1]<-dataset$x[dataset$y==1]+.3
reference<-survival::clogit(y~x+survival::strata(set),data=dataset,method="exact")"""
LONG_DATA = """set.seed(891);dataset<-expand.grid(id=1:50,t=1:5);dataset$g<-as.integer(dataset$id>25)
u<-rnorm(50);dataset$y<-1+u[dataset$id]+dataset$t*.4+dataset$g*.3+dataset$t*dataset$g*.2+rnorm(250,sd=.2)"""


CASES = {
    "calibration_dca": (
        PREDICTION_DATA,
        {"outcome": "y", "prediction": "p", "threshold": [0.1, 0.2, 0.3]},
        "list(brier=validation$brier,ref=mean((dataset$y-dataset$p)^2),slope=unname(coef(validation$cal)[2]),reference=unname(coef(glm(y~qlogis(p),dataset,family=binomial()))[2]),thresholds=dca$阈值)",
        "calibration",
    ),
    "external_validation_model": (
        PREDICTION_DATA,
        {"outcome": "y", "prediction": "p", "data_source": "source", "subgroup": "sub"},
        "list(groups=nrow(metrics),auc=validation$auc)",
        "external",
    ),
    "nri_idi_reclassification": (
        """dataset<-data.frame(y=rep(0:1,each=50),old=rep(.5,100),new=c(rep(.2,50),rep(.8,50)))""",
        {"outcome": "y", "original_prediction": "old", "updated_prediction": "new"},
        "list(nri=nri,idi=idi)",
        "nri",
    ),
    "matched_case_control": (
        MATCHED_DATA,
        {"matched_set_id": "set", "outcome": "y", "exposure": "x"},
        "list(estimate=unname(coef(fit)),ref=unname(coef(reference)))",
        "matched",
    ),
    "nested_case_control": (
        MATCHED_DATA,
        {"matched_set_id": "set", "outcome": "y", "exposure": "x", "analysis_method": "nested_case_control"},
        "list(estimate=unname(coef(fit)),ref=unname(coef(reference)))",
        "matched",
    ),
    "longitudinal_clinical_outcome": (
        LONG_DATA,
        {"subject_id": "id", "followup_time": "t", "outcome": "y", "group": "g"},
        'list(nmeans=nrow(means),interactions=sum(grepl(":",names(lme4::fixef(fit)))))',
        "longitudinal",
    ),
    "biomarker_trajectory": (
        LONG_DATA,
        {"subject_id": "id", "followup_time": "t", "measure": "y", "group": "g"},
        'list(re=any(grepl("id",rownames(summary_fit$s.table))),n=nrow(df))',
        "trajectory",
    ),
    "safety_adverse_events": (
        """dataset<-data.frame(id=rep(1:100,each=2),g=rep(rep(0:1,each=50),each=2),event=0,type=rep(c("A","B"),100),pt=10)
dataset$event[dataset$id<=10 | (dataset$id>50 & dataset$id<=70)]<-1""",
        {"group": "g", "event": "event", "event_type": "type", "exposure": "pt", "subject_id": "id"},
        "list(rr=ae_table$RR[1],n1=ae_table$处理人数[1],pt=sum(rate_table$总暴露时间))",
        "safety",
    ),
    "composite_endpoint": (
        """dataset<-data.frame(g=rep(0:1,each=80),y=c(rep(1,20),rep(0,60),rep(1,40),rep(0,40)),component=rep(c("A","B"),80))""",
        {"group": "g", "outcome": "y", "component_event_variable": "component"},
        'list(or=primary$效应[primary$变量==".t"])',
        "composite",
    ),
    "clinical_multistate_illness_death": (
        """dataset<-data.frame(id=1:80,from="healthy",to=c(rep("ill",25),rep("dead",15),rep("healthy",40)),t=seq(.1,8,length.out=80))""",
        {"subject_id": "id", "start_state": "from", "end_state": "to", "transition_time": "t"},
        "list(prob_error=max(abs(rowSums(occupation$pstate)-1)),events=sum(primary$事件数))",
        "multistate",
    ),
    "clinical_landmark_dynamic_prediction": (
        """set.seed(319);i<-1:240;dataset<-data.frame(id=i,t=1+rexp(240,rate=.2),e=rbinom(240,1,.7),p=runif(240,.05,.8))""",
        {
            "subject_id": "id",
            "followup_time": "t",
            "event": "e",
            "landmark_prediction": "p",
            "landmark_time": 1,
            "prediction_horizon": 3,
        },
        "list(auc=auc,brier=brier,N=nrow(df))",
        "landmark",
    ),
    "clinical_joint_longitudinal_survival": (
        """data(aids,package="JM");dataset<-subset(aids,patient%in%1:100)""",
        {
            "subject_id": "patient",
            "time": "obstime",
            "outcome": "CD4",
            "followup_time": "Time",
            "event": "death",
            "group": "drug",
        },
        "list(alpha=association,class=class(joint),method=joint$method)",
        "joint",
    ),
    "multilevel_public_health": (
        """set.seed(851);dataset<-data.frame(region=rep(1:20,each=15),z=rep(rnorm(20),each=15),x=rnorm(300));u<-rnorm(20);dataset$y<-1+dataset$z*.6+dataset$x*.3+u[dataset$region]+rnorm(300,sd=.2)""",
        {"outcome": "y", "region_id": "region", "region_level_variables": ["z"], "covariates": ["x"]},
        "list(icc=variance$ICC,regions=nrow(regional_effect))",
        "multilevel",
    ),
    "dag_causal": (
        """set.seed(155);dataset<-data.frame(z=rnorm(160),x=rnorm(160));dataset$y<-2*dataset$x+3*dataset$z+rnorm(160)*.2""",
        {
            "exposure": "x",
            "outcome": "y",
            "covariates": ["z"],
            "dag_specification": "dag {z -> x; z -> y; x -> y}",
        },
        "list(adjusted=sensitivity$效应,set=adjustment$变量)",
        "dag",
    ),
    "environment_mixture": (
        """set.seed(217);dataset<-data.frame(x1=rnorm(200),x2=rnorm(200),z=rnorm(200));dataset$y<-dataset$x1^2+dataset$x2*.5+dataset$z*.2+rnorm(200,sd=.1)""",
        {"outcome": "y", "exposure": ["x1", "x2"], "covariates": ["z"]},
        "list(smooths=nrow(model),joint=nrow(joint_table))",
        "mixture",
    ),
    "burden_economic": (
        """set.seed(157);dataset<-data.frame(strategy=rep(c("usual","new"),each=100),cost=c(rnorm(100,100,10),rnorm(100,120,10)),effect=c(rnorm(100,1,.1),rnorm(100,1.3,.1)),burden=1)""",
        {
            "outcome": "burden",
            "cost_variable": "cost",
            "effect_measure": "effect",
            "scenario_intervention_variable": "strategy",
            "reference_strategy": "usual",
            "willingness_to_pay": [0, 100, 1000],
            "bootstrap_replicates": 39,
        },
        'list(icer=summary$ICER[summary$策略=="new"],p=ceac$成本有效概率)',
        "economic",
    ),
    "spatial_epi": (
        """grid<-expand.grid(x=0:2,y=0:2);wkt<-vapply(1:9,function(i) {x<-grid$x[i];y<-grid$y[i];sprintf("POLYGON ((%s %s,%s %s,%s %s,%s %s,%s %s))",x,y,x+1,y,x+1,y+1,x,y+1,x,y)},character(1));dataset<-data.frame(id=1:9,e=c(1,2,4,6,8,9,12,15,20),n=100,wkt=wkt)""",
        {"region_id": "id", "outcome": "e", "denominator": "n", "spatial_boundary": "wkt"},
        "list(I=primary$估计,neighbors=sum(spdep::card(neighbors)),regions=nrow(description))",
        "spatial",
    ),
    "age_period_cohort": (
        """set.seed(885);dataset<-expand.grid(age=seq(20,70,5),period=2000:2020);dataset$n<-1000;dataset$e<-rpois(nrow(dataset),1000*exp(-6+.03*dataset$age+.02*(dataset$period-2000)))""",
        {"age_group": "age", "period": "period", "outcome": "e", "denominator": "n"},
        "list(parts=unique(components$分量),n=nrow(components))",
        "apc",
    ),
    "small_area_survey": (
        """dataset<-data.frame(region=rep(1:5,each=20),y=1:100,w=rep(1:5,20))""",
        {"region_id": "region", "outcome": "y", "weight": "w"},
        "list(estimates=table$直接估计,reference=as.numeric(tapply(seq_len(100)*dataset$w,dataset$region,sum)/tapply(dataset$w,dataset$region,sum)))",
        "survey",
    ),
    "environment_time_series": (
        """set.seed(188);dataset<-data.frame(t=1:150,x=rnorm(150));dataset$y<-rpois(150,exp(1.5+.1*dataset$x))""",
        {"time": "t", "exposure": "x", "outcome": "y", "maximum_lag": 3, "lag_response":"linear"},
        "list(cumulative=cumulative,sum_coef=sum(b),variance=cs^2,reference_variance=sum(V),rows=nrow(lag_table))",
        "environment",
    ),
    "demographic_projection": (
        """dataset<-expand.grid(t=1990:2024,age=c(20,40));dataset$n<-1000+20*(dataset$t-1990)+10*sin(dataset$t)+dataset$age""",
        {"time": "t", "age_group": "age", "population_size": "n", "forecast_horizon": 3},
        "list(rows=nrow(projections),first=min(projections$时间),ci=all(projections$CI上限>=projections$CI下限))",
        "demographic",
    ),
    "infectious_surveillance": (
        """set.seed(109);dataset<-data.frame(t=1:90,n=rpois(90,20+5*sin(2*pi*(1:90)/52)),region="A",pathogen="P");dataset$n[89]<-150""",
        {
            "time": "t",
            "event_count": "n",
            "region_institution": "region",
            "pathogen_subtype": "pathogen",
            "baseline_periods": 52,
        },
        "list(alarms=sum(monitor$报警),first=min(monitor$时间),last_alarm=monitor$报警[monitor$时间==89])",
        "surveillance",
    ),
    "public_joinpoint_segmented_trend": (
        """set.seed(866);dataset<-data.frame(t=1980:2020,n=10000);dataset$e<-10000*exp(-3+.02*(dataset$t-1980)+.03*pmax(dataset$t-2000,0)+rnorm(41,sd=.015))""",
        {"time": "t", "outcome": "e", "denominator": "n", "breakpoint_count": 1},
        "list(slopes=primary$log率斜率,apc=primary$APC百分比,reference=100*(exp(primary$log率斜率)-1))",
        "joinpoint",
    ),
    "public_burden_decomposition": (
        """dataset<-data.frame(t=rep(1:2,each=3),s=rep(1:3,2),n=c(100,200,300,150,250,350),e=c(10,30,60,20,50,90),std=rep(c(1,2,3),2))""",
        {"outcome": "e", "denominator": "n", "strata": "s", "time": "t", "standard_population": "std"},
        "list(parts=head(primary$事件变化,3),total=tail(primary$事件变化,1),reference=sum(dataset$e[dataset$t==2])-sum(dataset$e[dataset$t==1]))",
        "burden",
    ),
    "public_rt_nowcasting": (
        """dataset<-expand.grid(onset=1:40,delay=0:2);dataset$report<-dataset$onset+dataset$delay;dataset$n<-c(5,3,2)[dataset$delay+1];dataset<-dataset[dataset$report<=40,]""",
        {
            "time": "report",
            "time_2": "onset",
            "event_count": "n",
            "delay_distribution": [0.5, 0.3, 0.2],
            "serial_interval": [0, 0.1, 0.2, 0.3, 0.25, 0.15],
        },
        "list(rt=tail(na.omit(primary$Rt),1),latest_nowcast=tail(primary$Nowcast点估计,1),latest_rt=tail(primary$Rt,1))",
        "rt",
    ),
}


@pytest.mark.parametrize("tid", list(CASES))
def test_native_workflow(backend, tmp_path, tid):
    data, inputs, probe, check = CASES[tid]
    result, p = run_workflow(backend, tmp_path, tid, data, inputs, probe)
    expected = set(ROLE_MAPS[tid])
    actual = {a.metadata.get("role") for a in [*result.tables, *result.figures, *result.texts]}
    assert expected <= actual, (tid, expected - actual)
    if check == "calibration":
        assert p["brier"] == pytest.approx(p["ref"])
        assert p["slope"] == pytest.approx(p["reference"])
        assert p["thresholds"] == pytest.approx([0.1, 0.2, 0.3])
    elif check == "external":
        assert p["groups"] == 4
    elif check == "nri":
        assert p == pytest.approx({"nri": 2, "idi": 0.6})
    elif check == "matched":
        assert p["estimate"] == pytest.approx(p["ref"])
    elif check == "longitudinal":
        assert p["nmeans"] == 10 and p["interactions"] == 4
    elif check == "trajectory":
        assert p["re"] is True and p["n"] == 250
    elif check == "safety":
        assert p == pytest.approx({"rr": 2, "n1": 50, "pt": 1000})
    elif check == "composite":
        assert p["or"] == pytest.approx(3)
    elif check == "multistate":
        assert p["prob_error"] < 1e-10 and p["events"] == 40
    elif check == "landmark":
        assert 0 <= p["auc"] <= 1 and p["brier"] >= 0 and p["N"] == 240
    elif check == "joint":
        assert "jointModel" in p["class"] and abs(p["alpha"]) < 10
    elif check == "multilevel":
        assert 0 < p["icc"] < 1 and p["regions"] == 20
    elif check == "dag":
        assert p["adjusted"] == pytest.approx(2, abs=0.1) and p["set"] == "z"
    elif check == "mixture":
        assert p["smooths"] == 3 and p["joint"] == 3
    elif check == "economic":
        assert 30 < p["icer"] < 150 and p["p"][0] < p["p"][-1]
    elif check == "spatial":
        assert -1 < p["I"] < 1 and p["neighbors"] > 9 and p["regions"] == 9
    elif check == "apc":
        assert set(p["parts"]) == {"Age", "Per", "Coh"} and p["n"] > 10
    elif check == "survey":
        assert p["estimates"] == pytest.approx(p["reference"])
    elif check == "environment":
        assert p["cumulative"] == pytest.approx(p["sum_coef"])
        assert p["variance"] == pytest.approx(p["reference_variance"])
        assert p["rows"] == 5
    elif check == "demographic":
        assert p["rows"] == 6 and p["first"] == 2025 and p["ci"] is True
    elif check == "surveillance":
        assert p["alarms"] >= 1 and p["first"] == 53 and p["last_alarm"] is True
    elif check == "joinpoint":
        assert p["apc"] == pytest.approx(p["reference"]) and len(p["slopes"]) == 2
    elif check == "burden":
        assert sum(p["parts"]) == pytest.approx(p["total"]) and p["total"] == pytest.approx(p["reference"])
    elif check == "rt":
        assert 0.5 < p["rt"] < 1.5 and p["latest_nowcast"] == pytest.approx(10) and p["latest_rt"] in (None, "NA")


def test_nonlinear_distributed_lag(backend,tmp_path):
    data='''set.seed(1808);dataset<-data.frame(t=1:240,x=rnorm(240));dataset$y<-rpois(240,exp(1.2+.22*dataset$x^2))'''
    _,p=run_workflow(backend,tmp_path,"environment_time_series",data,dict(time="t",exposure="x",outcome="y",maximum_lag=2),"list(smooths=nrow(summary(fit)$s.table),rows=nrow(lag_table),variance=cs^2,reference_variance=tail(rowSums((contrast%*%vcov(fit))*contrast),1),curve=nrow(curve),CI=all(curve$下限<=curve$上限))")
    assert p['smooths']==4 and p['rows']==4 and p['curve']==80 and p['CI'] is True
    assert p['variance']==pytest.approx(p['reference_variance'])
