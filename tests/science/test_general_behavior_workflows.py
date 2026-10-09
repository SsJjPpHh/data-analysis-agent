"""Real-R frozen-input smoke and numerical checks for all new workflows."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.general_behavior_workflows import CORES, INPUTS, ROLE_MAPS
from agent.tools.research.r_binding import BindingHelpersMixin
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.protocol import DatasetRef, RRunRequest


@pytest.fixture(scope="module")
def behavior_backend(backend):
    env = dict(backend.runtime_env)
    return LocalRscriptBackend(rscript_bin=backend.rscript_bin, runtime_env=env)


def run(behavior_backend, tmp_path, template_id, data, inputs, probe):
    inputs = {k:json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v for k,v in inputs.items()}
    template = AnalysisCatalog().get(template_id)
    outputs = tuple(o for o in template.required_outputs if conditions_match(o.conditions,inputs))
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(plan_id="general-behavior-reference",source="template",dataset_id="fixture",intent=template.title,category=template.category,method_family=template.family,scenario=template.scenario,variable_bindings={},packages=(),prerequisites=(),assumptions=(),diagnostics=template.diagnostics,required_outputs=outputs,resource_class="extended",template_id=template_id,resolved_inputs={k:ResolvedInput(k,k,specs[k].kind if k in specs else "specification",v,"user") for k,v in inputs.items()})
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n",encoding="utf-8")
    script = _make_engine(CORES[template_id],INPUTS[template_id],())(plan,ROLE_MAPS[template_id])
    code = data + "\n" + BindingHelpersMixin._frozen_input_prelude(plan) + "\n" + BindingHelpersMixin._frozen_user_code_block(script, plan) + "\n" + BindingHelpersMixin._frozen_input_epilogue(plan)
    code += '\nif(!identical(res,"OK"))stop(res)\nbaa_text("probe",jsonlite::toJSON('+probe+',auto_unbox=TRUE,digits=15,na="null"))'
    result = behavior_backend.run_code(RRunRequest(job_id="general-behavior",dataset=DatasetRef("fixture",str(dummy),"csv","input.csv"),code=code,artifacts_dir=str(tmp_path/"artifacts"),timeout_seconds=240))
    assert result.ok, result.error+"\n"+result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    assert result.tables or result.plots
    artifacts=[]
    for a in (*result.tables,*result.figures,*result.texts):
        metadata=dict(a.metadata,dataset_id=plan.dataset_id,plan_id=plan.plan_id,source_tool="run_r_code",source_execution_status="succeeded",job_id="general-behavior")
        receipt=build_artifact_validation_receipt(a.path,kind=a.kind,metadata=metadata,job_id="general-behavior",artifacts_root=tmp_path/"artifacts")
        metadata.update(artifact_size_bytes=receipt["size_bytes"],artifact_sha256=receipt["sha256"],artifact_validation_receipt=receipt)
        artifacts.append(dict(a.to_dict(),metadata=metadata,source_tool="run_r_code",job_id="general-behavior"))
    execution_metadata={"dataset_id":plan.dataset_id,"plan_id":plan.plan_id,"status":"succeeded","diagnostic_status":{d:"not_assessed" for d in plan.diagnostics}}
    assessment=ResultValidator().validate(plan,execution_metadata,artifacts)
    assert assessment.complete,repr(assessment)
    return result,json.loads(Path(note.path).read_text(encoding="utf-8"))


ORDINARY = "set.seed(17);i<-1:120;dataset<-data.frame(x=rnorm(120),z=rnorm(120),arm=factor(rep(c('A','B'),60)));dataset$y<-1+2*dataset$x+(dataset$arm=='B')+rnorm(120);dataset$y2<-.8*dataset$x+rnorm(120)"
SCALE = "set.seed(22);z<-rnorm(180);dataset<-as.data.frame(lapply(1:6,function(j) z+rnorm(180,0,.6)));names(dataset)<-paste0('i',1:6);dataset$id<-1:180;dataset$group<-factor(rep(c('A','B'),90));dataset$external<-z+rnorm(180)"
PANEL = "set.seed(39);dataset<-expand.grid(id=1:130,t=1:4);ix<-dataset$id;tt<-dataset$t;ixv<-rnorm(130);iyv<-rnorm(130);dataset$x<-ixv[ix]+.2*tt+rnorm(nrow(dataset));dataset$y<-iyv[ix]+.2*dataset$x+.3*tt+rnorm(nrow(dataset))"
CLUSTER = "set.seed(42);dataset<-data.frame(cluster=rep(1:30,each=12),id=1:360,x=rnorm(360),z=rep(rnorm(30),each=12));u<-rep(rnorm(30),each=12);f<-.4*dataset$x+.7*dataset$z+u+rnorm(360);dataset$y<-f;dataset$i1<-f+rnorm(360,0,.5);dataset$i2<-f+rnorm(360,0,.5);dataset$i3<-f+rnorm(360,0,.5);dataset$i4<-f+rnorm(360,0,.5)"
CHOICE = "set.seed(56);dataset<-expand.grid(alt=c('A','B','C'),task=1:3,id=1:80);dataset$cost<-runif(nrow(dataset),1,5);dataset$quality<-runif(nrow(dataset));sets<-interaction(dataset$id,dataset$task,drop=TRUE);dataset$chosen<-0;for(ss in levels(sets)){ii<-which(sets==ss);u<-.6*dataset$quality[ii]-.3*dataset$cost[ii];dataset$chosen[sample(ii,1,prob=exp(u))]<-1}"
TEXT = "dataset<-data.frame(id=1:8,text=c('apple fruit orchard harvest','pear fruit orchard tree','apple banana fruit crop','pear apple tree harvest','river water lake fish','lake fish stream water','river stream water flow','fish lake water ecology'),group=rep(c('crop','water'),each=4))"

CASES = [
    ("distribution_visual",ORDINARY,{"continuous_variables":["x","y"],"group":"arm"},"list(n=summary_table$有效数)",lambda p: p["n"]==[120,120]),
    ("assumption_diagnostics",ORDINARY,{"outcome":"y","covariates":["x","z"],"group":"arm"},"list(coef=unname(coef(fit)),ref=unname(coef(lm(y~arm+x+z,data=dataset))))",None),
    ("multiple_testing_endpoints",ORDINARY,{"outcome":["y","y2"],"group":"arm"},"list(p=summary_table$HolmP,ref=p.adjust(summary_table$原始P,'holm'))",None),
    ("paired_matched_tests", "dataset<-data.frame(id=rep(1:30,2),t=rep(c('before','after'),each=30),y=c(1:30,1:30+2+sin(1:30)))",{"pair_id":"id","outcome":"y","time":"t"},"list(estimate=summary_table$平均差,ref=-mean(2+sin(1:30)))",None),
    ("stratified_categorical_trend","dataset<-expand.grid(out=c('no','yes'),arm=c('A','B'),strata=c('S1','S2'));dataset$n<-c(30,10,20,20,40,15,30,25)",{"outcome":"out","exposure":"arm","strata":"strata","weight":"n"},"list(value=summary_table$估计,ref=unname(mantelhaen.test(tab,correct=FALSE)$estimate))",None),
    ("nonlinear_spline_gam",ORDINARY,{"outcome":"y","exposure":"x","covariates":["z"]},"list(class=class(fit),edf=sum(fit$edf))",lambda p:"gam" in p["class"]),
    ("bayesian_robust_stats",ORDINARY,{"outcome":"y","group":"arm","covariates":["x"],"analysis_method":"robust"},"list(coef=unname(coef(fit)),ref=unname(coef(MASS::rlm(y~arm+x,data=dat,maxit=100))))",None),
    ("multivariate_ordination",ORDINARY,{"data_object":["x","z","y"],"group":"arm"},"list(values=unname(fit$sdev),ref=unname(prcomp(dataset[,c('x','z','y')],scale.=TRUE)$sdev))",None),
    ("irt_models","set.seed(91);theta<-rnorm(180);dataset<-as.data.frame(lapply(seq(-1,1,length.out=5),function(d)rbinom(180,1,plogis(theta-d))));names(dataset)<-paste0('i',1:5)",{"item":["i1","i2","i3","i4","i5"],"irt_model":"Rasch"},"list(converged=mirt::extract.mirt(fit,'converged'),n=nrow(parameters))",lambda p:p["converged"] and p["n"]==5),
    ("latent_class","set.seed(19);z<-rep(0:1,100);dataset<-as.data.frame(lapply(1:4,function(i)rbinom(200,1,.15+.7*z)+1));names(dataset)<-paste0('i',1:4)",{"categorical_variables":["i1","i2","i3","i4"],"candidate_class_count":2},"list(rows=nrow(comparison),prob=max(abs(rowSums(fit$posterior)-1)))",lambda p:p["rows"]==2 and p["prob"]<1e-8),
    ("psych_network",SCALE,{"categorical_variables":["i1","i2","i3","i4","i5","i6"],"resamples":10},"list(n=igraph::vcount(first_graph),edges=igraph::ecount(first_graph))",lambda p:p["n"]==6 and p["edges"]>0),
    ("social_network","dataset<-data.frame(a=c('A','A','A','B','B','C','D','E'),b=c('B','C','D','C','D','D','E','F'))",{"node_id":"a","edge_relation":"b"},"list(n=igraph::vcount(graph),edges=igraph::ecount(graph))",lambda p:p["n"]==6 and p["edges"]==8),
    ("text_social_science",TEXT,{"text":"text","document_id":"id","group":"group"},"list(doc=nrow(classification),simdiag=diag(similarity))",lambda p:p["doc"]==8 and all(abs(v-1)<1e-8 for v in p["simdiag"])),
    ("mixed_methods_open_text",TEXT,{"text":"text","respondent_id":"id"},"list(n=nrow(summary_table))",lambda p:p["n"]>4),
    ("measurement_invariance",SCALE,{"item_variables":["i1","i2","i3","i4"],"group":"group","latent_dimension_specification":"F =~ i1+i2+i3+i4"},"list(n=nrow(comparison),changes=nrow(changes))",lambda p:p["n"]==4 and p["changes"]==3),
    ("latent_growth_sem",PANEL,{"subject_id":"id","time":"t","repeated_measure_variables":["x"]},"list(n=nrow(wide),times=times,n_paths=nrow(summary_table))",lambda p:p["n"]==130 and p["times"]==[0,1,2,3]),
    ("multilevel_behavior_model",CLUSTER,{"outcome":"y","observation_unit_id":"id","cluster_id":"cluster","exposure":["x"],"exposure_2":["z"]},"list(coef=unname(lme4::fixef(fit)),ref=unname(lme4::fixef(lme4::lmer(y~x*z+(1|cluster),data=dataset))),icc=icc)",None),
    ("weighted_survey_scale",SCALE+";dataset$w<-runif(180,.5,2)",{"item_variables":["i1","i2","i3"],"weight":"w","group":"group"},"list(mean=summary_table$均值,ref=sapply(dataset[,c('i1','i2','i3')],weighted.mean,w=dataset$w))",None),
    ("conjoint_choice",CHOICE,{"respondent_id":"id","outcome":"chosen","task_id":"task","attributes_variable":["cost","quality"]},"list(importance=sum(importance$设计内相对重要性),sets=nlevels(setid))",lambda p:abs(p["importance"]-1)<1e-8 and p["sets"]==240),
    ("discrete_choice_model",CHOICE,{"respondent_id":"id","outcome":"chosen","alternative":"alt","task_id":"task","attributes_variable":["cost","quality"]},"list(maxerr=max(abs(tapply(prob,setid,sum)-1)),n=nrow(predictions))",lambda p:p["maxerr"]<1e-8 and p["n"]==720),
    ("behavior_scale_development",SCALE,{"item_variables":["i1","i2","i3","i4","i5","i6"],"external":"external","factors":1},"list(alpha=alpha,ref=psych::alpha(dataset[,1:6],check.keys=FALSE,warnings=FALSE)$total$raw_alpha)",None),
    ("behavior_riclpm_cross_lagged",PANEL,{"subject_id":"id","time":"t","repeated_measure_variables":["x","y"]},"list(ri=all(c('RIx','RIy')%in%pe$lhs),lags=sum(paths$op=='~'),converged=lavaan::lavInspect(fit,'converged'))",lambda p:p["ri"] and p["lags"]==12 and p["converged"]),
    ("behavior_apim_dyadic","set.seed(22);dataset<-data.frame(dyad=rep(1:150,each=2),role=rep(c('A','B'),150),x=rnorm(300));partner<-as.vector(t(matrix(dataset$x,ncol=2,byrow=TRUE)[,2:1]));dataset$y<-.4*dataset$x+.3*partner+rnorm(300)",{"dyad_id":"dyad","member_role":"role","exposure":"x","outcome":"y"},"list(n=nrow(wide),labels=sort(paths$label))",lambda p:p["n"]==150 and p["labels"]==["actorA","actorB","partnerA","partnerB"]),
    ("behavior_multilevel_sem",CLUSTER,{"cluster_id":"cluster","observation_unit_id":"id","measure_variable":["i1","i2","i3","i4"],"within_level_predictor":"x","between_level_predictor":"z"},"list(levels=sort(unique(summary_table$level)),paths=sum(summary_table$op=='~'))",lambda p:p["levels"]==[1,2] and p["paths"]==2),
    ("behavior_common_method_bias",SCALE,{"item_variables":["i1","i2","i3","i4"]},"list(n=nrow(summary_table),model=summary_table$模型)",lambda p:p["n"]==1),
    ("behavior_latent_transition","set.seed(17);dataset<-expand.grid(id=1:100,t=1:3);z<-rep(rbinom(100,1,.5),3);dataset$i1<-rbinom(300,1,.1+.8*z);dataset$i2<-rbinom(300,1,.15+.7*z);dataset$i3<-rbinom(300,1,.2+.6*z)",{"subject_id":"id","time":"t","categorical_variables":["i1","i2","i3"],"states":2},"list(states=fit$k,loglik=fit$lk,sums=rowSums(fit$Pi[,,2]))",lambda p:p["states"]==2 and all(abs(v-1)<1e-8 for v in p["sums"])),
    ("behavior_response_style_nonresponse",SCALE+";dataset$i1[1:5]<-NA;dataset$out<-rbinom(180,1,.7)",{"item_variables":["i1","i2","i3","i4"],"respondent_id":"id","outcome":"out","auxiliary_variable":"external"},"list(n=nrow(summary_table),missing=summary_table$缺失比例[1])",lambda p:p["n"]==180 and p["missing"]==.25),
]


@pytest.mark.parametrize("template_id,data,inputs,probe,check",CASES,ids=[c[0] for c in CASES])
def test_every_general_behavior_workflow_real_r(behavior_backend,tmp_path,template_id,data,inputs,probe,check):
    _, p = run(behavior_backend,tmp_path,template_id,data,inputs,probe)
    if check:
        assert check(p), p
    else:
        actual = next(p[k] for k in ("coef","values","mean","alpha","value","p","estimate") if k in p)
        assert actual == pytest.approx(p["ref"],abs=1e-7)


def test_bayesian_robust_posterior_and_bayes_factor_are_real(behavior_backend,tmp_path):
    _,p=run(behavior_backend,tmp_path,"bayesian_robust_stats",ORDINARY,{"outcome":"y","group":"arm","covariates":["x"],"analysis_method":"bayesian","prior_specification":{"b0":0,"B0":.01,"c0":2,"d0":2},"samples":500,"burnin":100},"list(bf=bf,n=nrow(posterior),sample_n=nrow(draws),mean=posterior$均值)")
    assert p["sample_n"] == 500
    assert p["bf"] > 0
    assert p["n"] == 4


def test_bayesian_sem_really_samples_stan(behavior_backend,tmp_path):
    _,p=run(behavior_backend,tmp_path,"bayesian_sem",SCALE,{"categorical_variables":["i1","i2","i3","i4"],"latent_variables":"F =~ i1+i2+i3+i4","path_specification":"","prior":{"lambda":"normal(0,2)"},"samples":100,"burnin":50},"list(class=class(fit),rhat_n=length(rhat),paths=sum(pe$op=='=~'))")
    assert "blavaan" in p["class"]
    assert p["rhat_n"] > 0
    assert p["paths"] == 4


def test_item_equating_joint_rasch_calibration(behavior_backend,tmp_path):
    data='''
set.seed(61);subjects<-1:220;forms<-ifelse(subjects<=110,"A","B");theta<-rnorm(220)+ifelse(forms=="B",.5,0);dataset<-do.call(rbind,lapply(subjects,function(s){items<-if(forms[s]=="A")c("a1","a2","a3","uA1","uA2")else c("a1","a2","a3","uB1","uB2");d<-match(items,c("a1","a2","a3","uA1","uA2","uB1","uB2"))*.25;data.frame(id=s,form=forms[s],itemid=items,y=rbinom(length(items),1,plogis(theta[s]-d)),anchor=startsWith(items,"a"))}))
'''
    _,p=run(behavior_backend,tmp_path,"item_equating",data,{"subject_id":"id","item_id":"itemid","item":"y","test_form":"form","anchor_item_indicator":"anchor"},"list(forms=sort(unique(parameters$形式)),anchor_n=length(anchors),rows=nrow(mapping),b=parameters$b[parameters$题目=='a1'])")
    assert p["forms"] == ["A","B"]
    assert p["anchor_n"] == 3
    assert p["rows"] == 12
    assert p["b"][0] == pytest.approx(p["b"][1])


def test_irt_group_dif_is_joint_item_test(behavior_backend,tmp_path):
    data="set.seed(91);theta<-rnorm(400);dataset<-as.data.frame(lapply(seq(-1,1,length.out=5),function(d)rbinom(400,1,plogis(theta-d))));names(dataset)<-paste0('i',1:5);dataset$g<-rep(c('A','B'),each=200)"
    _,p=run(behavior_backend,tmp_path,"irt_models",data,{"item":["i1","i2","i3","i4","i5"],"group":"g","irt_model":"Rasch"},"list(n=nrow(dif),p=as.numeric(dif[[pcol[1]]]),states=dif$状态)")
    assert p["n"] == 5
    assert len(p["p"]) == 5
    assert set(p["states"]) <= {"warning","passed"}


def test_social_network_ergm_fits_real_model(behavior_backend,tmp_path):
    data="dataset<-data.frame(a=c('A','A','A','B','B','C','D','E'),b=c('B','C','D','C','D','D','E','F'))"
    _,p=run(behavior_backend,tmp_path,"social_network",data,{"node_id":"a","edge_relation":"b","fit_ergm":"yes","ergm_formula":"edges"},"list(class=class(fit),coef=unname(coef(fit)),ref=qlogis(8/15))")
    assert "ergm" in p["class"]
    assert p["coef"] == pytest.approx(p["ref"])


def test_manual_open_text_coding_agreement(behavior_backend,tmp_path):
    data="dataset<-expand.grid(id=1:20,rater=c('R1','R2'));dataset$text<-rep(c('apple fruit tree','water river lake'),20);dataset$code<-rep(c('crop','water'),20);dataset$code[40]<-'crop'"
    _,p=run(behavior_backend,tmp_path,"mixed_methods_open_text",data,{"text":"text","manual_coding":"code","rater_id":"rater","respondent_id":"id"},"list(rows=nrow(do.call(rbind,agreement)),agreement=agreement[[1]]$观察一致率)")
    assert p["rows"] == 1
    assert p["agreement"] == pytest.approx(.95)
