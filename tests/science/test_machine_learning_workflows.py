"""Real local R, frozen-input AST and output-contract checks for ML workflows."""
from __future__ import annotations

import json
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt
from agent.analysis.script_library import render_script
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.machine_learning_workflows import CORES, INPUTS, ROLE_MAPS
from agent.tools.research.r_binding import BindingHelpersMixin
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.inspection import inspection_summary
from r_runtime.protocol import DatasetRef, RRunRequest


def run(backend,tmp_path,template_id,data,inputs,probe):
    def encode(v):
        return json.dumps(v,ensure_ascii=False) if isinstance(v,dict) or isinstance(v,list) and any(isinstance(x,(dict,list)) for x in v) else v
    inputs={k:encode(v) for k,v in inputs.items()}
    template=AnalysisCatalog().get(template_id);specs={s.id:s for s in template.input_specs}
    outputs=tuple(o for o in template.required_outputs if conditions_match(o.conditions,inputs))
    plan=AnalysisPlan(plan_id="machine-learning-reference",source="template",dataset_id="fixture",intent=template.title,category=template.category,method_family=template.family,scenario=template.scenario,variable_bindings={},packages=(),prerequisites=(),assumptions=(),diagnostics=template.diagnostics,required_outputs=outputs,resource_class="extended",template_id=template_id,resolved_inputs={k:ResolvedInput(k,k,specs[k].kind if k in specs else "parameter",v,"user") for k,v in inputs.items()})
    dummy=tmp_path/"input.csv";dummy.write_text("x\n1\n",encoding="utf-8")
    script=_make_engine(CORES[template_id],INPUTS[template_id],())(plan,ROLE_MAPS[template_id])
    code=data+'\n'+BindingHelpersMixin._frozen_input_prelude(plan)+'\n'+BindingHelpersMixin._frozen_user_code_block(script,plan)+'\n'+BindingHelpersMixin._frozen_input_epilogue(plan)+'\nif(!identical(res,"OK"))stop(res)\nbaa_text("probe",jsonlite::toJSON('+probe+',auto_unbox=TRUE,digits=15,na="null"))'
    result=backend.run_code(RRunRequest(job_id="machine-learning-reference",dataset=DatasetRef("fixture",str(dummy),"csv","input.csv"),code=code,artifacts_dir=str(tmp_path/"artifacts"),timeout_seconds=240))
    assert result.ok,result.error+'\n'+result.stderr
    note=next(a for a in result.texts if a.name=="probe");artifacts=[]
    for a in (*result.tables,*result.figures,*result.texts):
        metadata=dict(a.metadata,dataset_id=plan.dataset_id,plan_id=plan.plan_id,source_tool="run_r_code",source_execution_status="succeeded",job_id="machine-learning-reference")
        receipt=build_artifact_validation_receipt(a.path,kind=a.kind,metadata=metadata,job_id="machine-learning-reference",artifacts_root=tmp_path/"artifacts")
        metadata.update(artifact_size_bytes=receipt["size_bytes"],artifact_sha256=receipt["sha256"],artifact_validation_receipt=receipt)
        artifacts.append(dict(a.to_dict(),metadata=metadata,source_tool="run_r_code",job_id="machine-learning-reference"))
    execution_metadata=dict(dataset_id=plan.dataset_id,plan_id=plan.plan_id,status="succeeded",diagnostic_status={d:"not_assessed" for d in plan.diagnostics})
    assessment=ResultValidator().validate(plan,execution_metadata,artifacts)
    assert assessment.complete,repr(assessment)
    return result,json.loads(Path(note.path).read_text(encoding="utf-8"))


REG="set.seed(5);dataset<-data.frame(id=1:180,x=rnorm(180),z=rnorm(180),split=rep(c('train','test'),c(135,45)));dataset$y<-1+2*dataset$x-.5*dataset$z+rnorm(180,0,.5)"
CLASS="set.seed(6);dataset<-data.frame(id=1:240,x=rnorm(240),z=rnorm(240),split=rep(c('train','test'),c(180,60)));pr<-plogis(1.5*dataset$x-.6*dataset$z);dataset$y<-factor(ifelse(rbinom(240,1,pr),'yes','no'),levels=c('no','yes'));dataset$p<-pr;dataset$g<-factor(rep(c('A','B'),120));dataset$w<-runif(240,.5,2)"
SCORES=CLASS+";dataset$cal<-ifelse(dataset$split=='train','calibration','test')"

CASES=[
 ("tree_boosting",CLASS,{"outcome":"y","features":["x","z"],"test_split":"split","analysis_method":"ranger","tuning_grid":[{"trees":60,"mtry":1},{"trees":60,"mtry":2}]},"list(ntrain=nrow(train),ntest=nrow(test),mtry=fit$model$mtry)",lambda p:p['ntrain']==180 and p['ntest']==60),
 ("regularized_models",REG,{"outcome":"y","features":["x","z"],"validation_split":"split","penalty":"lasso","lambda_grid":[.01,.1]},"list(coef=coef_table$系数,nonzero=nrow(selected),test=nrow(test))",lambda p:p['nonzero']==2 and p['test']==45),
 ("imbalanced_classification",CLASS,{"outcome":"y","features":["x","z"],"threshold":.4,"data_split":"split","weight":"balanced"},"list(total=sum(confusion$Freq),n=nrow(test),train_weight=sum(weights*yytrain)/sum(weights))",lambda p:p['total']==60 and abs(p['train_weight']-.5)<1e-9),
 ("feature_selection",REG,{"outcome":"y","features":["x","z"],"test_split":"split","resampling_specification":{"resamples":8,"fraction":.8,"lambda":.05}},"list(freq=ranking$入选频率,test=nrow(test),B=nrow(selection))",lambda p:p['test']==45 and p['B']==8 and all(0<=v<=1 for v in p['freq'])),
 ("automl_tabular",REG,{"outcome":"y","features":["x","z"],"data_split":"split"},"list(models=length(models),winner=winner,test=nrow(test))",lambda p:p['models']==3 and p['test']==45),
 ("stacking_ensemble_ml",REG,{"outcome":"y","features":["x","z"],"data_split":"split"},"list(oof=nrow(oof),held=nrow(held),any_na=anyNA(oof),coef=as.numeric(coef(meta))[-1])",lambda p:p['oof']==135 and p['held']==45 and not p['any_na'] and all(v>=0 for v in p['coef'])),
 ("probability_calibration_ml",CLASS,{"outcome":"y","prediction":"p","data_split":"split"},"list(n=length(j),coef=unname(coef(fit)),ref=unname(coef(glm(y[i]~q[i],family=binomial()))))",None),
 ("cost_sensitive_threshold",CLASS,{"outcome":"y","prediction":"p","false_positive_cost":2,"false_negative_cost":5,"data_split":"split","group":"g"},"list(value=chosen,ref=thresholds[which.min(costcurve$训练平均成本)],test=summary_table$测试数[1])",None),
 ("model_drift_shift",REG+";dataset$source<-ifelse(dataset$split=='train','reference','target');dataset$x[dataset$source=='target']<-dataset$x[dataset$source=='target']+1",{"data_source":"source","features":["x","z"],"reference_source":"reference"},"list(n=nrow(summary_table),psi=summary_table$PSI)",lambda p:p['n']==2 and p['psi'][0]>.1),
 ("ml_external_transportability",CLASS+";dataset$source<-dataset$split",{"outcome":"y","prediction":"p","data_source":"source","subgroup":"g"},"list(layers=length(unique(summary_table$评估层)),n=sum(unique(summary_table[,c('评估层','n')])$n))",lambda p:p['layers']==4 and p['n']==240),
 ("ml_fairness_subgroup",CLASS+";dataset$t<-.4",{"outcome":"y","prediction":"p","subgroup":"g","threshold":"t","weight":"w"},"list(sens=summary_table$结果[summary_table$指标=='Sensitivity'][1],ref=weighted.mean(dataset$p[dataset$g=='A'&dataset$y=='yes']>=.4,dataset$w[dataset$g=='A'&dataset$y=='yes']))",None),
 ("ml_conformal_prediction",REG+";dataset$p<-1+2*dataset$x-.5*dataset$z;dataset$cal<-ifelse(dataset$split=='train','calibration','test')",{"outcome":"y","prediction":"p","calibration_split":"cal","alpha":.1},"list(q=q[1],ref=sort(s[i])[ceiling((length(i)+1)*.9)],test=nrow(predictions))",None),
 ("unsupervised_ml","set.seed(7);dataset<-data.frame(x=c(rnorm(60,-3,.4),rnorm(60,3,.4)),z=c(rnorm(60,-2,.4),rnorm(60,2,.4)),id=1:120)",{"data_object":["x","z"],"sample_id":"id","analysis_method":"kmeans"},"list(k=length(unique(membership)),n=nrow(coords),sil=max(quality$平均轮廓))",lambda p:p['k']==2 and p['n']==120 and p['sil']>.7),
 ("dimensionality_reduction_ml",REG,{"features":["x","z","y"],"analysis_method":"pca"},"list(n=nrow(coords),cols=ncol(coords),variance=fit$sdev^2/sum(fit$sdev^2))",lambda p:p['n']==180 and p['cols']==2 and abs(sum(p['variance'])-1)<1e-9),
 ("anomaly_detection_ml",REG+";dataset$x[1:3]<-c(8,9,10)",{"features":["x","z"],"neighbors":10,"threshold":1.5},"list(n=nrow(anomalies),flags=sum(flags))",lambda p:p['n']==180 and p['flags']>=3),
 ("association_recommender","set.seed(8);dataset<-do.call(rbind,lapply(1:80,function(t)data.frame(id=t,item=c('A','B',if(t%%2==0)'C'else'D'))))",{"transaction_id":"id","item_id":"item","support":.1,"confidence":.5},"list(rules=length(rules),train=length(trainids),test=length(testids))",lambda p:p['rules']>0 and p['train']==60 and p['test']==20),
 ("sparse_text_ml","set.seed(9);dataset<-data.frame(text=rep(c('apple fruit orchard sweet','river water lake ecology'),80),y=factor(rep(c('crop','water'),80)),split=rep(c('train','test'),c(120,40)))",{"text":"text","class_label":"y","data_split":"split"},"list(vocab=ncol(x),test=nrow(test),ap=performance$结果[performance$指标=='AveragePrecision'])",lambda p:p['vocab']==8 and p['test']==40 and p['ap']>.9),
 ("ml_nested_cv_leakage",REG,{"outcome":"y","features":["x","z"],"external_validation_split":"split","lambda_grid":[.01,.1]},"list(n=length(audit),overlap=max(sapply(audit,function(a)length(intersect(a$train,a$test)))),external=sum(summary_table$评估=='external'))",lambda p:p['n']==3 and p['overlap']==0 and p['external']==3),
 ("ml_multiclass_multilabel_ordinal",REG+";dataset$y<-factor(cut(dataset$y,quantile(dataset$y,c(0,1/3,2/3,1)),include.lowest=TRUE),labels=c('low','middle','high'))",{"outcome":"y","features":["x","z"],"data_split":"split"},"list(n=sum(confusion$Freq),type=method,accuracy=summary_table$结果[summary_table$指标=='Accuracy'])",lambda p:p['n']==45 and p['accuracy']>.7),
 ("ml_causal_uplift","set.seed(10);dataset<-data.frame(x=rnorm(500),z=rnorm(500),w=rbinom(500,1,.5),split=rep(c('train','test'),c(400,100)),e=.5);dataset$y<-.3*dataset$x+(.8+.3*dataset$z)*dataset$w+rnorm(500,0,.2)",{"group":"w","outcome":"y","features":["x","z"],"propensity_score":"e","data_split":"split","trees":200},"list(class=class(forest),ate=ate,test=nrow(individual),policy=pv)",lambda p:'causal_forest' in p['class'] and p['test']==100 and abs(p['ate']-.8)<.4),
 ("ml_clinical_score_simplification",CLASS,{"outcome":"y","features":["x","z"],"external_validation_split":"split","lambda_grid":[.02,.1],"point_unit":.25},"list(points=points,test=nrow(test),integer=all(points==round(points)))",lambda p:p['test']==60 and p['integer']),
 ("survival_ml","set.seed(11);dataset<-data.frame(x=rnorm(300),z=rnorm(300),split=rep(c('train','test'),c(225,75)));eventtime<-rexp(300,exp(.7*dataset$x)*.08);censor<-rexp(300,.03);dataset$t<-pmin(eventtime,censor);dataset$e<-as.numeric(eventtime<=censor)",{"followup_time":"t","event":"e","features":["x","z"],"validation_split":"split","trees":60,"prediction_times":[2,5,8]},"list(forest=fit$treetype,test=nrow(te),cindex=summary_table$测试Cindex)",lambda p:p['forest']=='Survival' and p['test']==75 and p['cindex'][0]>.5),
]


@pytest.mark.parametrize('template_id,data,inputs,probe,check',CASES,ids=[x[0] for x in CASES])
def test_all_ml_workflows_real_r(backend,tmp_path,template_id,data,inputs,probe,check):
    _,p=run(backend,tmp_path,template_id,data,inputs,probe)
    if check:
        assert check(p),p
    else:
        actual=next(p[k] for k in ('coef','value','sens','q') if k in p)
        assert actual==pytest.approx(p['ref'],abs=1e-7)


def test_explainability_uses_actual_trained_model_bundle(backend,tmp_path):
    data=REG+";train<-dataset[dataset$split=='train',];held<-dataset[dataset$split=='test',];model<-lm(y~x+z,data=train);dataset<-list(model=model,data=held,training_data=train)"
    _,p=run(backend,tmp_path,'ml_explainability',data,{'trained_model':'fixture','features':['x','z'],'outcome':'y','subject_id':'id','resamples':30},"list(rows=nrow(shap_table),n=nrow(dat),err=max(abs(rowSums(shap)+baseline-pred)),class=class(model))")
    assert 'lm' in p['class']
    assert p['rows']==90
    assert p['err']<1e-5


@pytest.mark.parametrize("method",["randomForest","gbm","xgboost"])
def test_tree_algorithm_choices_are_actual_models(backend,tmp_path,method):
    _,p=run(backend,tmp_path,"tree_boosting",CLASS,{"outcome":"y","features":["x","z"],"test_split":"split","analysis_method":method,"tuning_grid":[{"trees":40,"mtry":1} if method=="randomForest" else {"trees":40,"max_depth":2,"eta":.1}]},"list(class=class(fit$model),n=length(pred),binary=fit$binary)")
    expected={"randomForest":"randomForest","gbm":"gbm","xgboost":"xgb.Booster"}[method]
    assert expected in p["class"] and p["n"]==60 and p["binary"]


@pytest.mark.parametrize("method",["mclust","dbscan"])
def test_unsupervised_algorithms_keep_actual_model_meanings(backend,tmp_path,method):
    data="set.seed(7);dataset<-data.frame(x=c(rnorm(60,-3,.4),rnorm(60,3,.4)),z=c(rnorm(60,-2,.4),rnorm(60,2,.4)),id=1:120)"
    _,p=run(backend,tmp_path,"unsupervised_ml",data,{"data_object":["x","z"],"analysis_method":method,"eps":.4,"min_pts":5},"list(class=class(fit),n=length(membership))")
    assert p["n"]==120
    assert {"mclust":"Mclust","dbscan":"dbscan"}[method] in p["class"]


@pytest.mark.parametrize("method",["umap","tsne"])
def test_actual_nonlinear_projection_choices(backend,tmp_path,method):
    _,p=run(backend,tmp_path,"dimensionality_reduction_ml",REG,{"features":["x","z","y"],"analysis_method":method,"neighbors":10,"perplexity":10},"list(n=nrow(coords),cols=ncol(coords),finite=all(is.finite(coords)))")
    assert p["n"]==180 and p["cols"]==2 and p["finite"]


@pytest.mark.parametrize("method",["pca","umap","tsne"])
def test_projection_tables_preserve_coordinates_and_method_structure(backend,tmp_path,method):
    data=REG+";dataset$x[c(3,57)]<-NA_real_"
    result,p=run(backend,tmp_path,"dimensionality_reduction_ml",data,
        {"features":["x","z","y"],"analysis_method":method,"neighbors":10,"perplexity":10},
        "list(rows=nrow(projection_table),source=projection_table$源数据行,method=unique(projection_table$方法),coord_error=max(abs(as.matrix(projection_table[,c('维度1','维度2')])-coords)),structure=if(method=='pca')list(rows=nrow(pca_structure),load_error=max(abs(pca_structure$载荷-as.numeric(fit$rotation))),variance_error=max(abs(unique(pca_structure$解释方差比例)-fit$sdev^2/sum(fit$sdev^2))))else list(emitted=FALSE))")
    assert p["rows"]==178 and p["method"]==method and p["coord_error"]<1e-12
    assert p["source"]==[str(i) for i in range(1,181) if i not in (3,57)]
    tables={a.name:a for a in result.tables}
    assert "scientific_result_table" in tables
    if method=="pca":
        assert "pca_structure_table" in tables and p["structure"]["rows"]==9
        assert p["structure"]["load_error"]<1e-12 and p["structure"]["variance_error"]<1e-12
    else:
        assert "pca_structure_table" not in tables and not p["structure"]["emitted"]
        with Path(tables["scientific_result_table"].path).open(encoding="utf-8-sig") as handle:
            assert not any("方差" in column or "载荷" in column for column in next(csv.reader(handle)))


def test_binary_mondrian_weighted_conformal_sets(backend,tmp_path):
    _,p=run(backend,tmp_path,"ml_conformal_prediction",SCORES+";dataset$ratio<-1",{"outcome":"y","prediction":"p","calibration_split":"cal","subgroup":"g","weight":"ratio","alpha":.1},"list(n=nrow(predictions),size=predictions$集合大小,q=q[1],ref=sort(s[i[groups[i]==groups[j[1]]]])[ceiling((sum(groups[i]==groups[j[1]])+1)*.9)])")
    assert p["n"]==60 and all(0<=v<=2 for v in p["size"])
    assert p["q"]==pytest.approx(p["ref"])


def test_multilabel_is_multiple_binary_tasks(backend,tmp_path):
    data=CLASS+";dataset$l2<-factor(ifelse(dataset$z>0,'yes','no'),levels=c('no','yes'))"
    _,p=run(backend,tmp_path,"ml_multiclass_multilabel_ordinal",data,{"outcome":"y","features":["x","z"],"label_set":["l2"],"data_split":"split","weight":"w"},"list(tasks=sort(unique(summary_table$任务)),counts=tapply(confusion$Freq,confusion$任务,sum))")
    assert p["tasks"]==["l2","y"] and p["counts"]==[60,60]


def test_ordinal_labels_use_ordered_prediction(backend,tmp_path):
    data=REG+";dataset$y<-ordered(cut(dataset$y,quantile(dataset$y,c(0,1/3,2/3,1)),include.lowest=TRUE),labels=c('low','middle','high'))"
    _,p=run(backend,tmp_path,"ml_multiclass_multilabel_ordinal",data,{"outcome":"y","features":["x","z"],"data_split":"split"},"list(class=class(fit),n=sum(confusion$Freq))")
    assert "polr" in p["class"] and p["n"]==45


def test_nested_cluster_folds_never_split_same_unit(backend,tmp_path):
    data=REG+";dataset$cluster<-rep(1:60,each=3);dataset$split<-ifelse(dataset$cluster<=45,'train','test')"
    _,p=run(backend,tmp_path,"ml_nested_cv_leakage",data,{"outcome":"y","features":["x","z"],"cluster_id":"cluster","external_validation_split":"split","lambda_grid":[.01,.1]},"list(overlap=max(sapply(audit,function(a)length(intersect(unique(train$cluster[a$train]),unique(train$cluster[a$test]))))))")
    assert p["overlap"]==0


def test_character_outcome_and_named_positive_class_are_preserved(backend,tmp_path):
    _,p=run(backend,tmp_path,"tree_boosting",CLASS+";dataset$y<-as.character(dataset$y)",{"outcome":"y","features":["x","z"],"test_split":"split","analysis_method":"ranger","positive_class":"no","tuning_grid":[{"trees":40}]},"list(event=unique(performance$事件类),levels=levels(train$y),pred_ref=max(abs(pred-predict(fit$model,data=as.data.frame(design$test))$predictions[,'no'])))")
    assert p["event"]=="no" and p["levels"]==["yes","no"] and p["pred_ref"]<1e-12


def test_real_regression_tree_has_conditional_classification_outputs(backend,tmp_path):
    _,p=run(backend,tmp_path,"tree_boosting",REG,{"outcome":"y","features":["x","z"],"test_split":"split","analysis_method":"ranger","analysis_task":"regression","tuning_grid":[{"trees":40}]},"list(binary=fit$binary,rmse=performance$结果[1],ref=sqrt(mean((test$y-pred)^2)))")
    assert not p["binary"] and p["rmse"]==pytest.approx(p["ref"])


def test_time_nested_validation_is_strictly_forward(backend,tmp_path):
    _,p=run(backend,tmp_path,"ml_nested_cv_leakage",REG+";dataset$t<-1:180",{"outcome":"y","features":["x","z"],"time":"t","external_validation_split":"split","lambda_grid":[.01,.1]},"list(forward=all(sapply(audit,function(a)max(train$t[a$train])<min(train$t[a$test]))),external=max(train$t)<min(external$t))")
    assert p["forward"] and p["external"]


def test_feature_role_leakage_is_rejected(backend,tmp_path):
    with pytest.raises(AssertionError,match="保护角色重叠"):
        run(backend,tmp_path,"regularized_models",REG,{"outcome":"y","features":["x","y"],"validation_split":"split"},"list(dummy=1)")


def test_shared_cluster_in_external_split_is_rejected(backend,tmp_path):
    with pytest.raises(AssertionError,match="同一聚类同时出现训练和测试"):
        run(backend,tmp_path,"ml_nested_cv_leakage",REG+";dataset$cluster<-rep(1:45,4)",{"outcome":"y","features":["x","z"],"cluster_id":"cluster","external_validation_split":"split"},"list(dummy=1)")


def test_explicit_classification_converts_numeric_codes_only_when_requested(backend,tmp_path):
    data=CLASS+";dataset$y<-as.numeric(dataset$y=='yes')"
    _,p=run(backend,tmp_path,"regularized_models",data,{"outcome":"y","features":["x","z"],"validation_split":"split","analysis_task":"classification","positive_class":1},"list(binary=fit$binary,event=unique(performance$事件类),levels=levels(train$y))")
    assert p["binary"] and p["event"]=="1" and p["levels"]==["0","1"]


def test_automatic_task_preserves_numeric_zero_one_role(backend,tmp_path):
    data=CLASS+";dataset$y<-as.numeric(dataset$y=='yes')"
    _,p=run(backend,tmp_path,"regularized_models",data,{"outcome":"y","features":["x","z"],"validation_split":"split","analysis_task":"auto"},"list(binary=fit$binary,numeric=is.numeric(train$y),metrics=performance$指标)")
    assert not p["binary"] and p["numeric"] and p["metrics"]==["RMSE","MAE","R2"]


def test_joint_time_group_validation_purges_shared_training_clusters(backend,tmp_path):
    data="set.seed(5);dataset<-data.frame(id=1:320,x=rnorm(320),z=rnorm(320),split=rep(c('train','test'),c(240,80)));dataset$y<-1+2*dataset$x-.5*dataset$z+rnorm(320,0,.5);dataset$t<-1:320;dataset$cluster<-ifelse(dataset$id%%4==0,1,dataset$id+1)"
    _,p=run(backend,tmp_path,"ml_nested_cv_leakage",data,{"outcome":"y","features":["x","z"],"cluster_id":"cluster","time":"t","external_validation_split":"split","lambda_grid":[.01,.1]},"list(purged=length(attr(sets,'purged_rows')),overlap=max(sapply(audit,function(a)length(intersect(train$cluster[a$train],train$cluster[a$test])))),forward=all(sapply(audit,function(a)max(train$t[a$train])<min(train$t[a$test]))))")
    assert p["purged"]>0 and p["overlap"]==0 and p["forward"]


def test_explainability_classification_uses_stored_model_event(backend,tmp_path):
    data=CLASS+";tr<-dataset[dataset$split=='train',];held<-dataset[dataset$split=='test',];model<-ranger::ranger(y~x+z,data=tr,probability=TRUE,num.trees=40,num.threads=1);dataset<-list(model=model,data=held,training_data=tr);unloadNamespace('ranger')"
    _,p=run(backend,tmp_path,"ml_explainability",data,{"trained_model":"fixture","features":["x","z"],"outcome":"y","subject_id":"id","positive_class":"no","resamples":20},"list(event=event,class=class(model),n=length(pred),ref=max(abs(pred-predict(model,data=x)$predictions[,'no'])))")
    assert p["event"]=="no" and 'ranger' in p["class"] and p["n"]==60 and p["ref"]<1e-12


PRODUCTION_CASES = [
    ("tree_boosting", CLASS, {"outcome":"y", "features":["x","z"], "test_split":"split",
                              "analysis_method":"ranger", "analysis_task":"classification", "positive_class":"yes",
                              "tuning_grid":[{"trees":40,"mtry":1},{"trees":40,"mtry":2}]},
     "list(train=nrow(train),held=nrow(test),event=unique(performance$事件类))"),
    ("stacking_ensemble_ml", REG, {"outcome":"y", "features":["x","z"], "data_split":"split"},
     "list(train=nrow(oof),held=nrow(held),missing=anyNA(oof),coef=as.numeric(coef(meta))[-1])"),
    ("ml_nested_cv_leakage", REG+";dataset$t<-1:180", {"outcome":"y", "features":["x","z"],
                "time":"t", "external_validation_split":"split", "lambda_grid":[.01,.1]},
     "list(folds=length(audit),forward=all(sapply(audit,function(a)max(train$t[a$train])<min(train$t[a$test]))),held=nrow(external))"),
    ("dimensionality_reduction_ml", REG, {"features":["x","z","y"], "analysis_method":"pca"},
     "list(rows=nrow(coords),cols=ncol(coords),variance=sum(fit$sdev^2/sum(fit$sdev^2)))"),
]
PRODUCTION_CASES.extend((template_id,data,inputs,probe) for template_id,data,inputs,probe,_ in CASES
                        if template_id in {"imbalanced_classification","survival_ml","feature_selection",
                                           "unsupervised_ml","probability_calibration_ml","cost_sensitive_threshold"})


@pytest.mark.parametrize("template_id,data,inputs,probe", PRODUCTION_CASES,
                         ids=[case[0] for case in PRODUCTION_CASES])
def test_actual_csv_preparer_render_and_result_contract(backend, tmp_path, template_id, data, inputs, probe):
    """Exercise the registered product path, including its unchanged portfolio."""
    dummy = tmp_path / "dummy.csv"
    dummy.write_text("dummy\n1\n", encoding="utf-8")
    dataset_id = "ml-production-synthetic"
    materialized = backend.run_code(RRunRequest(
        job_id="ml-materialize", dataset=DatasetRef(dataset_id, str(dummy), "csv"),
        code=data+'\nbaa_table("source",dataset)', artifacts_dir=str(tmp_path / "source"), timeout_seconds=120,
    ))
    assert materialized.ok, materialized.error+'\n'+materialized.stderr
    source = next(a for a in materialized.tables if a.name == "source")
    inspected = backend.inspect_dataset(DatasetRef(dataset_id, source.path, "csv"), timeout_seconds=120)
    assert inspected.ok, inspected.error+'\n'+inspected.stderr
    profile = inspection_summary(inspected)
    profile.update(dataset_id=dataset_id, format="csv", columns=profile["variables"])
    packages = backend.list_packages()
    assert packages["ok"]
    installed = [p.get("name", p.get("Package", p.get("package", ""))) for p in packages["packages"]]
    prepared = AnalysisPreparer(AnalysisCatalog(), runtime_packages=installed).prepare(
        template_id, profile, provided_inputs=inputs,
    )
    assert prepared.status == "ready", prepared
    plan = prepared.plan
    assert plan is not None and set(inputs) <= set(plan.resolved_inputs)
    if template_id == "tree_boosting":
        assert isinstance(plan.resolved_inputs["tuning_grid"].value, str)
        assert json.loads(plan.resolved_inputs["tuning_grid"].value) == inputs["tuning_grid"]
    script = render_script(plan)
    assert script is not None
    checked = "\n".join((RToolsMixin._frozen_input_prelude(plan),
                          RToolsMixin._frozen_user_code_block(script, plan),
                          RToolsMixin._frozen_input_epilogue(plan),
                          'if(!identical(res,"OK"))stop(res)',
                          'baa_text("production_probe",jsonlite::toJSON('+probe+',auto_unbox=TRUE,digits=15,na="null"))'))
    artifacts_root = tmp_path / "prepared"
    executed = backend.run_code(RRunRequest(
        job_id="ml-prepared", dataset=DatasetRef(dataset_id, source.path, "csv"),
        code=checked, artifacts_dir=str(artifacts_root), timeout_seconds=240,
    ))
    assert executed.ok, executed.error+'\n'+executed.stderr
    diagnostic_status = RToolsMixin._attach_analysis_plan_metadata(
        SimpleNamespace(_analysis_plan=plan), executed, dataset_id,
        job_id="ml-prepared", artifacts_root=str(artifacts_root),
    )
    artifacts = [dict(a.to_dict(), source_tool="run_r_code", job_id="ml-prepared")
                 for a in (*executed.tables, *executed.figures, *executed.texts, *executed.models)]
    execution = dict(dataset_id=dataset_id, plan_id=plan.plan_id, status="succeeded", diagnostic_status=diagnostic_status)
    assessment = ResultValidator().validate(plan, execution, artifacts)
    note = next(a for a in executed.texts if a.name == "production_probe")
    numerical = json.loads(Path(note.path).read_text(encoding="utf-8"))
    receipt = {
        "template_id":template_id, "preparation_status":prepared.status, "execution_status":execution["status"],
        "portfolio":[dict(role=o.role, kind=o.kind, required=o.required, semantic_key=o.semantic_key,
                          conditions=o.conditions, acceptance=o.acceptance, legacy_roles=o.legacy_roles)
                     for o in plan.output_portfolio],
        "assessment":assessment.__dict__, "numerical":numerical,
        "artifacts":[dict(name=a["name"], kind=a["kind"], role=a["metadata"].get("role"),
                          semantic_key=a["metadata"].get("semantic_key"),
                          receipt=a["metadata"].get("artifact_validation_receipt")) for a in artifacts],
    }
    (tmp_path / "production-validation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    assert assessment.complete and assessment.next_action == "complete", repr(assessment)
    if template_id == "tree_boosting":
        assert numerical == {"train":180, "held":60, "event":"yes"}
        resampling = [a for a in artifacts if a["metadata"].get("role")=="ml_resampling_metrics"]
        assert resampling
        for artifact in resampling:
            with Path(artifact["path"]).open(encoding="utf-8-sig") as handle:
                fields=next(csv.reader(handle))
            assert "候选" in fields and "内层MSE" in fields
        thresholds = next(a for a in artifacts if a["name"]=="threshold_performance_table")
        assert thresholds["metadata"]["role"]=="threshold_performance_table"
    elif template_id == "stacking_ensemble_ml":
        assert numerical["train"] == 135 and numerical["held"] == 45 and not numerical["missing"]
        assert all(v >= 0 for v in numerical["coef"])
    elif template_id == "ml_nested_cv_leakage":
        assert numerical == {"folds":3, "forward":True, "held":45}
    elif template_id == "dimensionality_reduction_ml":
        assert numerical["rows"] == 180 and numerical["cols"] == 2 and numerical["variance"] == pytest.approx(1)
        assert any(a["metadata"].get("role")=="scientific_result_table" for a in artifacts)
        assert any(a["metadata"].get("role")=="pca_structure_table" for a in artifacts)
    else:
        reference = next(case for case in CASES if case[0]==template_id)
        if reference[4]:
            assert reference[4](numerical), numerical
        else:
            actual = next(numerical[k] for k in ("coef","value","sens","q") if k in numerical)
            assert actual == pytest.approx(numerical["ref"],abs=1e-7)
    canonical_role = {"imbalanced_classification":"prediction_performance_table",
                      "survival_ml":"survival_time_dependent_performance",
                      "probability_calibration_ml":"prediction_performance_table",
                      "cost_sensitive_threshold":"prediction_performance_table"}.get(template_id)
    if canonical_role:
        complete_tables = [a for a in artifacts if a["metadata"].get("role")==canonical_role]
        assert complete_tables
        for artifact in complete_tables:
            with Path(artifact["path"]).open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            measures = {row["指标"] for row in rows}
            if template_id=="survival_ml":
                assert measures=={"C-index","IPCW动态AUC"}
            elif template_id=="imbalanced_classification":
                assert {"ROC_AUC","AveragePrecision","Brier","TP","FP","TN","FN"} <= measures
            elif template_id=="cost_sensitive_threshold":
                assert {"ROC_AUC","Brier","MeanCost"} <= measures
                assert {row["分组"] for row in rows if row["指标"]=="MeanCost"}=={"总体","A","B"}
            else:
                assert {"ROC_AUC","Brier","Sensitivity","Specificity"} <= measures
                assert {row["模型"] for row in rows}=={"原始","训练内Platt校准"}
        auxiliary_only = [a for a in artifacts if a["metadata"].get("role")!=canonical_role]
        incomplete = ResultValidator().validate(plan,execution,auxiliary_only)
        receipt["auxiliary_only_assessment"] = incomplete.__dict__
        (tmp_path / "production-validation.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding="utf-8")
        assert not incomplete.complete and canonical_role in incomplete.outstanding_roles
    if template_id=="survival_ml":
        effects = next(a for a in artifacts if a["metadata"].get("role")=="survival_effect_estimates")
        with Path(effects["path"]).open(encoding="utf-8-sig") as handle:
            estimates = list(csv.DictReader(handle))
        assert all(row["模型"]=="仅训练Cox对照" and 0<float(row["lower"])<=float(row["HR"])<=float(row["upper"]) for row in estimates)
