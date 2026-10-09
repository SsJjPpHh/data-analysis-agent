"""Actual scientific workflows, frozen R AST/access audit and artifact validator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.contract_conditions import conditions_match
from agent.analysis.input_validation import normalize_parameter_value, normalize_setting_value
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.script_library import render_script
from agent.analysis.time_space_text_workflows import CORES, INPUTS, ROLE_MAPS
from agent.tools.research.r_tools import RToolsMixin
from r_runtime.protocol import DatasetRef, RRunRequest
from r_runtime.inspection import inspection_summary
from r_runtime.package_references import _scrub_r_code, referenced_r_packages


def normalized_inputs(template, inputs):
    specs = {s.id:s for s in template.input_specs}
    result = {}
    for key, value in inputs.items():
        if isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        spec = specs[key]
        if spec.kind == "parameter":
            value = normalize_parameter_value(input_id=spec.id, label=spec.label,
                                              semantic_role=spec.semantic_role, value=value)
        elif spec.kind == "specification":
            value = normalize_setting_value(value, json_document="json" in spec.accepts)
        result[key] = value
    return result


def run_tst(backend, tmp_path, template_id, data, inputs, probe, *, failure=False):
    template = AnalysisCatalog().get(template_id)
    inputs = normalized_inputs(template, inputs)
    specs = {s.id: s for s in template.input_specs}
    outputs = tuple(o for o in template.required_outputs if conditions_match(o.conditions,inputs))
    plan = AnalysisPlan(
        plan_id="time-space-text-reference", source="template", dataset_id="tst-synthetic",
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
        job_id="tst-reference", dataset=DatasetRef(plan.dataset_id, str(dummy), "csv", "input.csv"),
        code=code, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=180,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    if not failure:
        artifacts = []
        for a in (*result.tables, *result.figures, *result.texts):
            metadata = dict(a.metadata, dataset_id=plan.dataset_id, plan_id=plan.plan_id,
                            source_tool="run_r_code", source_execution_status="succeeded", job_id="tst-reference")
            receipt = build_artifact_validation_receipt(a.path, kind=a.kind, metadata=metadata,
                                                       job_id="tst-reference", artifacts_root=tmp_path / "artifacts")
            metadata.update(artifact_size_bytes=receipt["size_bytes"], artifact_sha256=receipt["sha256"],
                            artifact_validation_receipt=receipt)
            artifacts.append(dict(a.to_dict(), metadata=metadata, source_tool="run_r_code", job_id="tst-reference"))
        assessment = ResultValidator().validate(plan, dict(dataset_id=plan.dataset_id,
                    plan_id=plan.plan_id, status="succeeded", diagnostic_status={d:"not_assessed" for d in plan.diagnostics}), artifacts)
        assert assessment.complete, repr(assessment)
    return result, json.loads(Path(note.path).read_text(encoding="utf-8"))


SPACE = r'''
set.seed(818);dataset<-expand.grid(x=0:4,y=0:4);dataset$x<-dataset$x*1000;dataset$y<-dataset$y*1000
dataset$id<-paste0("R",seq_len(25));dataset$coord<-paste(dataset$x,dataset$y,sep="|")
D<-as.matrix(dist(dataset[,c("x","y")]));dataset$value<-as.numeric(MASS::mvrnorm(1,rep(10,25),4*exp(-D/1500)+diag(.3,25)))
dataset$exposure<-rnorm(25);dataset$adj<-vapply(seq_len(25),function(i) paste(dataset$id[which(D[i,]==1000)],collapse=","),character(1))
dataset$grid<-paste(dataset$x+100,dataset$y+100,sep="|")
'''
TEXT = r'''
set.seed(819);dataset<-data.frame(id=paste0("doc",1:60),group=rep(c("A","B"),30),time=1:60,class=rep(c("negative","positive"),30))
dataset$text<-vapply(seq_len(60),function(i) paste(if(i%%2==0) paste("good excellent happy treatment improved health",sample(c("cardiac","metabolic","clinical","recovery","safe","positive"),1)) else paste("bad terrible unhappy disease severe pain",sample(c("adverse","toxic","infection","symptoms","negative","failure"),1)),paste0("context",i)),character(1))
'''
TIME = r'''
set.seed(820);dataset<-data.frame(time=1:72,y=10+.05*(1:72)+sin(2*pi*(1:72)/12)+rnorm(72,sd=.3),x=rnorm(72))
'''
PANEL = TIME + '\ndataset<-rbind(transform(dataset,panel="A"),transform(dataset,panel="B",y=y+2))'

CASES = {
 "bayesian_state_space": (TIME,{"time":"time","observed_measure":"y","state_trend_specification":{"order":1,"observation_shape":2,"observation_rate":1,"state_shape":2,"state_rate":1,"samples":200,"burnin":100,"horizon":3,"seed":820}},
  "list(n=nrow(states),h=nrow(prediction),lower=prediction$lower,mean=prediction$mean,upper=prediction$upper,draws=length(draws$dV))"),
 "gam_splines_time": (TIME,{"exposure":"time","outcome":"y","covariates":["x"],"basis_dimension":8},
  "list(beta=as.numeric(coef(fit)),reference=as.numeric(coef(mgcv::gam(y~s(time,k=8)+x,data=dataset,method='REML'))),n=nrow(curve))"),
 "gis_mapping": (SPACE,{"region_id":"id","coordinates":"coord","measure":"value","crs":3857},"list(n=nrow(connection),CRS=sf::st_crs(points)$epsg,valid=all(sf::st_is_valid(points)))"),
 "spatial_autocorrelation": (SPACE,{"region_id":"id","measure":"value","adjacency":"adj","coordinates":"coord","crs":3857},
  "list(I=global_table$I,reference=unname(spdep::moran.test(dataset$value,listw)$estimate[1]),local=nrow(local_table),adjusted=local_table$p_BH)"),
 "geostatistics": (SPACE,{"coordinates":"coord","measure":"value","prediction_grid":"grid","crs":3857},
  "list(n=nrow(prediction),variance=prediction$var1.var,mean=prediction$var1.pred,reference=as.numeric(gstat::krige(.value~1,points,grid,model=model,debug.level=0)$var1.pred))"),
 "network_graphs": ('dataset<-data.frame(node=c("A","A","B","B","C","D"),edge=c("B","C","C","D","D","E"),kind=c("k1","k1","k2","k2","k3","k4"),w=c(1,2,3,1,2,1))',
  {"node_id":"node","edge":"edge","node_attributes":["kind"],"weight":"w"},"list(degree=as.numeric(central$degree),reference=as.numeric(igraph::degree(g)),communities=central$community,n=metrics$vertices)"),
 "text_mining_nlp": (TEXT,{"text":"text","document_id":"id","group":"group","time":"time"},
  "list(total=sum(frequency$count),reference=sum(Matrix::colSums(m)),similarity_n=nrow(similarity),docs=nrow(features),feature=features$top_tfidf_term)"),
 "panel_forecast": (PANEL,{"time":"time","panel_id":"panel","measure":"y","frequency":12,"horizon":3},
  "list(n=nrow(predictions),panels=nrow(errors),mean=tail(predictions$mean,3),reference=as.numeric(forecast::forecast(forecast::auto.arima(ts(dataset$y[dataset$panel=='B'],frequency=12)),h=3)$mean))"),
 "functional_data_analysis": (r'''set.seed(821);dataset<-expand.grid(time=seq(0,1,length.out=12),id=1:12);dataset$y<-sin(2*pi*dataset$time)+rep(rnorm(12,sd=.3),each=12)*dataset$time+rnorm(144,sd=.03);dataset$group<-ifelse(dataset$id<=6,"A","B")''',
  {"subject_id":"id","time":"time","measure":"y","group":"group","components":2,"clusters":2},
  "list(n=nrow(scores),variance=pca$varprop[1:2],reference=fda::pca.fd(smooth$fd,nharm=2)$varprop[1:2],clusters=length(unique(scores$cluster)))"),
 "spatiotemporal_panel": (SPACE+'\ndataset<-do.call(rbind,lapply(1:4,function(t) transform(dataset,time=t,value=value+.5*t+exposure*.1*t,exposure=exposure*t)))',
  {"region_id":"id","time":"time","outcome":"value","coordinates":"coord","covariates":["exposure"],"crs":3857},
  "list(time_beta=model$estimate[model$term=='.time'],reference=unname(coef(lm(value~factor(id)+time+exposure,data=dataset))['time']),n=nrow(df))"),
 "raster_exposure_linkage": ('dataset<-data.frame(coord=c(".5|1.5","1.5|1.5",".5|.5","1.5|.5"),exposure=1:4)',
  {"coordinates":"coord","exposure":"exposure","crs":3857,"raster_spec":{"nrows":2,"ncols":2,"extent":[0,2,0,2],"crs":3857,"values":[1,2,3,4]}},
  "list(extracted=primary$raster_extracted_exposure,reference=dataset$exposure,difference=primary$difference)"),
 "distance_accessibility": ('dataset<-data.frame(lon=c(0,.01,.02),lat=0,flon=c(0,0,.03),flat=0,group=c("A","A","B"))',
  {"origin_longitude":"lon","origin_latitude":"lat","facility_longitude":"flon","facility_latitude":"flat","group":"group","coverage_distance_m":1500},
  "list(distance=primary$distance_m,reference=apply(geosphere::distm(cbind(dataset$lon,dataset$lat),unique(cbind(dataset$flon,dataset$flat)),fun=geosphere::distGeo),1,min),coverage=access$coverage)"),
 "spatial_regression_models": (SPACE+'\ndataset$outcome<-1+.8*dataset$exposure+.4*dataset$value+rnorm(25)',
  {"region_id":"id","outcome":"outcome","exposure":"exposure","adjacency":"adj","coordinates":"coord","crs":3857},
  "list(rho=sar$rho,reference=spatialreg::lagsarlm(outcome~exposure,data=dataset,listw=listw)$rho,models=comparison$model)"),
 "topic_embedding_text": (TEXT,{"text":"text","document_id":"id","group":"group","time":"time","dimensions":2,"clusters":2},
  "list(n=nrow(documents),dimensions=ncol(embedding),groups=length(unique(documents$cluster)),terms=nrow(topics))"),
 "knowledge_graph": ('dataset<-data.frame(source=c("A","B","A","C"),target=c("B","C","D","D"),relation=c("activates","inhibits","binds","activates"),weight=c(1,2,1,3))',
  {"source_entity":"source","target_entity":"target","relation":"relation","weight":"weight"},
  "list(n=nrow(edges),directed=igraph::is_directed(g),relations=sort(unique(edges$relation)))"),
 "event_sequence_patterns": ('dataset<-data.frame(id=rep(1:8,each=3),time=rep(1:3,8),event=rep(c("start","treatment","recovery"),8),group=rep(c("A","B"),each=12))',
  {"subject_id":"id","event_time":"time","event_type":"event","group":"group"},
  "list(subjects=sum(frequency$subjects),prob=counts$conditional_probability,n=nrow(paths))"),
 "time_var_vecm_cointegration": (r'''set.seed(822);Y<-matrix(0,100,2);for(i in 2:100) Y[i,]<-c(.4*Y[i-1,1]+.2*Y[i-1,2],.3*Y[i-1,2])+rnorm(2);dataset<-data.frame(time=1:100,a=Y[,1],b=Y[,2])''',
  {"time":"time","multiple_continuous_series":["a","b"],"analysis_method":"var","lag_order":2,"horizon":4},
  "list(beta=primary$estimate,reference=as.numeric(unlist(lapply(vars::VAR(as.matrix(dataset[,c('a','b')]),p=2,type='const')$varresult,coef))),irf=sum(curves$type=='orthogonal IRF'),FEVD=sum(curves$type=='FEVD'))"),
 "time_hierarchical_grouped_forecast": (PANEL,{"time":"time","measure":"y","group":"panel","frequency":12,"horizon":3},
  "list(n=nrow(primary),coherence=max(primary$coherence_error),total=primary$forecast[primary$node=='total'],bottom_sum=rowSums(final))"),
 "time_seasonal_anomaly_changepoint": (TIME+'\ndataset$y[45]<-dataset$y[45]+5;dataset$intervention<-as.integer(dataset$time>=48)',
  {"time":"time","measure":"y","seasonal_period":12,"intervention_indicator":"intervention"},
  "list(anomaly_time=primary$time[primary$anomaly],n=nrow(curves),residual=curves$residual,reference=as.numeric(stl(ts(dataset$y,frequency=12),s.window='periodic',robust=TRUE)$time.series[,'remainder']))"),
 "spatial_point_pattern": ('set.seed(823);dataset<-data.frame(x=runif(50,0,100),y=runif(50,0,100),mark=rep(c("A","B"),25))',
  {"coordinates":"x","coordinates_2":"y","study_window":{"xrange":[0,100],"yrange":[0,100],"units":"metre"},"point_indicator":"mark"},
  "list(intensity=primary$intensity[1],reference=50/10000,k_n=nrow(curve))"),
 "spatial_interpolation_validation": (SPACE,{"coordinates":"coord","continuous_measure":"value","crs":3857},
  "list(RMSE=metrics$RMSE,reference=sqrt(mean(gstat::krige.cv(.value~1,points,model=model,nfold=folds_vector,verbose=FALSE)$residual^2)),n=nrow(prediction))"),
 "text_sentiment_analysis": (TEXT,{"text":"text","document_id":"id","group":"group","time":"time","manual_label":"class"},
  "list(score=primary$score,reference=syuzhet::get_sentiment(dataset$text,method='bing'),covered=primary$matched_tokens,correct=primary$correct)"),
 "text_supervised_classification": (TEXT,{"text":"text","class_label":"class","document_id":"id","seed":824},
  "list(AUC=auc,train=length(train),test=length(test),accuracy=mean(pred==truth),vocabulary=ncol(X),not_leaked=all(colnames(Xt)==colnames(X)))"),
 "text_entity_relation_extraction": ('dataset<-data.frame(id=c("d1","d2"),text=c("aspirin treats pain","aspirin treats fever"),time=1:2)',
  {"text":"text","document_id":"id","time":"time","entity_dictionary":{"entities":{"drug":["aspirin"],"symptom":["pain","fever"]},"relations":{"treats":"(?<source>aspirin) treats (?<target>pain|fever)"}}},
  "list(entities=sum(primary$record=='entity'),relations=sum(primary$record=='relation'),targets=sort(primary$target[primary$record=='relation']))"),
 "text_topic_stability": (TEXT,{"text":"text","document_id":"id","group":"group","time":"time","topics":2,"runs":2,"iterations":100,"seed":824},
  "list(stability=primary$value[primary$record=='topic stability'],topics=nrow(phi),documents=nrow(proportions[[1]]),metadata=names(topic_docs))"),
}


@pytest.mark.parametrize("template_id", list(CASES))
def test_real_workflow_and_frozen_artifact_portfolio(backend, tmp_path, template_id):
    data, inputs, probe = CASES[template_id]
    _, p = run_tst(backend, tmp_path, template_id, data, inputs, probe)
    if "reference" in p:
        field = next(k for k in ("beta", "I", "mean", "degree", "total", "variance", "time_beta", "extracted", "distance", "rho", "residual", "intensity", "RMSE", "score") if k in p)
        assert p[field] == pytest.approx(p["reference"], rel=1e-6, abs=1e-7)
    if template_id == "bayesian_state_space":
        assert p["n"] == 72 and p["h"] == 3 and p["draws"] == 200
        assert all(l < e < u for l, e, u in zip(p["lower"], p["mean"], p["upper"]))
    elif template_id == "gis_mapping":
        assert p["n"] == 25 and p["CRS"] == 3857 and p["valid"] is True
    elif template_id == "text_mining_nlp":
        assert p["docs"] == 60 and p["similarity_n"] == 1770
    elif template_id == "panel_forecast":
        assert p["n"] == 6 and p["panels"] == 2
    elif template_id == "functional_data_analysis":
        assert p["n"] == 12 and p["clusters"] == 2
    elif template_id == "topic_embedding_text":
        assert p["n"] == 60 and p["dimensions"] == 2 and p["groups"] == 2
    elif template_id == "knowledge_graph":
        assert p["directed"] is True and p["n"] == 4
    elif template_id == "event_sequence_patterns":
        assert p["subjects"] == p["n"] == 8 and all(x == 1 for x in p["prob"])
    elif template_id == "time_hierarchical_grouped_forecast":
        assert p["n"] == 9 and p["coherence"] == pytest.approx(0, abs=1e-8)
        assert p["total"] == pytest.approx(p["bottom_sum"])
    elif template_id == "time_seasonal_anomaly_changepoint":
        assert 45 in ([p["anomaly_time"]] if isinstance(p["anomaly_time"], (int, float)) else p["anomaly_time"]) and p["n"] == 72
    elif template_id == "text_supervised_classification":
        assert p["train"] == 42 and p["test"] == 18 and p["not_leaked"] is True
        assert p["AUC"] >= .9 and p["accuracy"] >= .9
    elif template_id == "text_entity_relation_extraction":
        assert p["entities"] == 4 and p["relations"] == 2 and p["targets"] == ["fever", "pain"]
    elif template_id == "text_topic_stability":
        assert p["topics"] == 2 and p["documents"] == 60
        assert all(0 <= x <= 1.000001 for x in p["stability"])


def test_maps_cover_declared_inputs_and_outputs():
    assert len(CORES) == 25
    for k in CORES:
        t = AnalysisCatalog().get(k)
        assert {s.id for s in t.input_specs} <= set(INPUTS[k])
        assert {s.role for s in t.required_outputs} <= set(ROLE_MAPS[k])


def test_registered_scripts_keep_scientific_helper_dependency_closures():
    """A method-only environment must not need unrelated scientific packages."""
    from tests.science.test_script_library import _plan

    spatial = {"sf", "spdep", "gstat", "terra", "spatialreg", "spatstat.explore",
               "spatstat.geom", "spatstat.model", "geosphere"}
    text = {"quanteda", "text2vec", "syuzhet", "glmnet", "clue"}
    spatial_methods = {"gis_mapping", "spatial_autocorrelation", "geostatistics", "spatiotemporal_panel",
                       "raster_exposure_linkage", "distance_accessibility", "spatial_regression_models",
                       "spatial_point_pattern", "spatial_interpolation_validation"}
    text_methods = {name for name in CASES if name.startswith("text_")} | {"topic_embedding_text"}
    required = {
        "bayesian_state_space":{"dlm"}, "gam_splines_time":{"mgcv"}, "panel_forecast":{"forecast"},
        "gis_mapping":{"sf"}, "spatial_autocorrelation":{"sf","spdep"},
        "geostatistics":{"gstat","sf"}, "spatial_interpolation_validation":{"gstat","sf"},
        "text_mining_nlp":{"quanteda","Matrix","text2vec"},
        "text_sentiment_analysis":{"quanteda","syuzhet"},
        "text_entity_relation_extraction":{"quanteda","igraph","jsonlite"},
    }
    for template_id,(_, inputs,_) in CASES.items():
        template = AnalysisCatalog().get(template_id)
        specs = {spec.id:spec for spec in template.input_specs}
        resolved = {key:ResolvedInput(key,specs[key].label,specs[key].kind,value,"user")
                    for key,value in normalized_inputs(template,inputs).items()}
        script = render_script(_plan(template_id,resolved))
        assert script is not None
        packages = set(referenced_r_packages(script))
        assert required.get(template_id,set()) <= packages, (template_id,packages)
        executable = _scrub_r_code(script,keep_strings=False)
        helper_calls = set(re.findall(r"(\.(?:mt|te|ts)_[A-Za-z0-9_]+)\s*\(",executable))
        helper_definitions = set(re.findall(r"(\.(?:mt|te|ts)_[A-Za-z0-9_]+)\s*<-\s*function\b",executable))
        assert helper_calls <= helper_definitions, (template_id,helper_calls-helper_definitions)
        if template_id in {"bayesian_state_space","gam_splines_time","panel_forecast",
                           "network_graphs","knowledge_graph","event_sequence_patterns"}:
            assert not packages & (spatial|text|{"Matrix"}), (template_id,packages)
        if template_id in spatial_methods:
            assert not packages & text, (template_id,packages)
        if template_id in text_methods:
            assert not packages & spatial, (template_id,packages)
        if template_id in {"text_sentiment_analysis","text_entity_relation_extraction","gis_mapping"}:
            assert not {"Matrix","spdep"} & packages, (template_id,packages)
        if template_id=="spatial_point_pattern":
            assert not {"sf","spdep"} & packages


@pytest.mark.parametrize("template_id", list(CASES))
def test_production_preparer_accepts_actual_fixture_profile_and_settings(backend, tmp_path, template_id):
    data, inputs, _ = CASES[template_id]
    template = AnalysisCatalog().get(template_id)
    inputs = normalized_inputs(template, inputs)
    dummy = tmp_path / "dummy.csv"
    dummy.write_text("dummy\n1\n", encoding="utf-8")
    materialized = backend.run_code(RRunRequest(job_id="materialize-tst", dataset=DatasetRef("tst-synthetic", str(dummy), "csv"),
                         code=data+'\nbaa_table("source",dataset)', artifacts_dir=str(tmp_path / "source"), timeout_seconds=120))
    assert materialized.ok, materialized.stderr
    source = next(a for a in materialized.tables if a.name == "source")
    inspected = backend.inspect_dataset(DatasetRef("tst-synthetic", source.path, "csv"), timeout_seconds=120)
    assert inspected.ok, inspected.stderr
    profile = inspection_summary(inspected)
    profile.update(dataset_id="tst-synthetic", format="csv", columns=profile["variables"])
    packages = backend.list_packages()
    assert packages["ok"]
    installed = [p.get("name", p.get("Package", p.get("package", ""))) for p in packages["packages"]]
    prepared = AnalysisPreparer(AnalysisCatalog(), runtime_packages=installed).prepare(template_id, profile, provided_inputs=inputs)
    assert prepared.status == "ready", prepared
    assert prepared.plan is not None
    assert set(inputs) <= set(prepared.plan.resolved_inputs)
    # Production defaults and chosen settings are subjected to frozen access and AST too.
    code = render_script(prepared.plan)
    assert code is not None
    checked = "\n".join((RToolsMixin._frozen_input_prelude(prepared.plan), RToolsMixin._frozen_user_code_block(code, prepared.plan),
                         RToolsMixin._frozen_input_epilogue(prepared.plan)))
    executed = backend.run_code(RRunRequest(job_id="prepared-tst", dataset=DatasetRef("tst-synthetic", source.path, "csv"),
                              code=checked, artifacts_dir=str(tmp_path / "prepared"), timeout_seconds=180))
    assert executed.ok, executed.stderr
    # Use production metadata attachment and its diagnostic declarations. Do not
    # replace the prepared portfolio or rewrite its canonical semantic keys.
    diagnostic_status = RToolsMixin._attach_analysis_plan_metadata(
        SimpleNamespace(_analysis_plan=prepared.plan), executed, prepared.plan.dataset_id,
        job_id="prepared-tst", artifacts_root=str(tmp_path / "prepared"),
    )
    artifacts = [dict(a.to_dict(), source_tool="run_r_code", job_id="prepared-tst")
                 for a in (*executed.tables, *executed.figures, *executed.texts, *executed.models)]
    execution = dict(dataset_id=prepared.plan.dataset_id, plan_id=prepared.plan.plan_id,
                     status="succeeded", diagnostic_status=diagnostic_status)
    assessment = ResultValidator().validate(prepared.plan, execution, artifacts)
    # Save reviewable production receipts even when this catches a mapping error.
    receipt = {
        "template_id": template_id,
        "plan_id": prepared.plan.plan_id,
        "preparation_status": prepared.status,
        "execution_status": execution["status"],
        "portfolio": [dict(role=o.role, kind=o.kind, required=o.required,
                           semantic_key=o.semantic_key, conditions=o.conditions,
                           acceptance=o.acceptance, legacy_roles=o.legacy_roles)
                      for o in prepared.plan.output_portfolio],
        "assessment": assessment.__dict__,
        "artifacts": [dict(name=a["name"], kind=a["kind"], role=a["metadata"].get("role"),
                           semantic_key=a["metadata"].get("semantic_key"),
                           receipt=a["metadata"].get("artifact_validation_receipt"))
                      for a in artifacts],
    }
    (tmp_path / "production-validation.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    assert assessment.complete and assessment.next_action == "complete", repr(assessment)


def test_vecm_branch_uses_johansen_rank_and_true_irf(backend, tmp_path):
    _, p = run_tst(backend, tmp_path, "time_var_vecm_cointegration", r'''
set.seed(825);a<-cumsum(rnorm(120));b<-a+rnorm(120,sd=.4);dataset<-data.frame(time=1:120,a=a,b=b)
''', {"time":"time","multiple_continuous_series":["a","b"],"analysis_method":"vecm","lag_order":2,"cointegration_rank":1,"horizon":3},
       "list(stat=primary$statistic,reference=urca::ca.jo(as.matrix(dataset[,c('a','b')]),type='trace',ecdet='const',K=2)@teststat,rank=primary$selected_rank,rows=nrow(curves))")
    assert p["stat"] == pytest.approx(p["reference"]) and all(x == 1 for x in p["rank"])
    assert p["rows"] > 0


def test_panel_external_future_values_are_used_and_holdout_is_temporal(backend, tmp_path):
    future = {"A":[[0],[1],[2]], "B":[[2],[1],[0]]}
    _, p = run_tst(backend, tmp_path, "panel_forecast", PANEL,
       {"time":"time","panel_id":"panel","measure":"y","exogenous_variables":["x"],"future_exogenous":future,"frequency":12,"horizon":3},
       "list(pred=tail(predictions$mean,3),reference=as.numeric(forecast::forecast(forecast::auto.arima(ts(dataset$y[dataset$panel=='B'],frequency=12),xreg=matrix(dataset$x[dataset$panel=='B'],ncol=1)),h=3,xreg=matrix(c(2,1,0),ncol=1))$mean),holdout=errors$holdout_n)")
    assert p["pred"] == pytest.approx(p["reference"]) and all(x == 3 for x in p["holdout"])


def test_hierarchy_honors_frozen_common_validation_period(backend, tmp_path):
    _, p = run_tst(backend, tmp_path, "time_hierarchical_grouped_forecast", PANEL+'\ndataset$validation<-as.integer(dataset$time>65)',
       {"time":"time","measure":"y","group":"panel","validation_split":"validation","horizon":3,"frequency":12},
       "list(train=max(train),test=length(test),coherence=max(primary$coherence_error))")
    assert p["train"] == 65 and p["test"] == 7 and p["coherence"] == pytest.approx(0)


def test_spatiotemporal_coordinate_free_branch_has_no_fake_map(backend, tmp_path):
    data, inputs, _ = CASES["spatiotemporal_panel"]
    inputs = {k:v for k,v in inputs.items() if k not in ("coordinates", "crs")}
    result, p = run_tst(backend, tmp_path, "spatiotemporal_panel", data, inputs, "list(no_map=is.null(.out[['map']]))")
    assert p["no_map"] is True and all(a.name != "figure_2" for a in result.figures)


def test_sentiment_uncovered_document_is_na_not_neutral(backend, tmp_path):
    _, p = run_tst(backend, tmp_path, "text_sentiment_analysis",
       'dataset<-data.frame(text=c("excellent good happy","unlistedtoken xqzalpha zqzbeta"))', {"text":"text"},
       "list(score=primary$score,coverage=primary$matched_tokens,sentiment=primary$sentiment)")
    assert p["score"][0] > 0 and p["score"][1] in (None, "NA")
    assert p["coverage"][1] == 0 and p["sentiment"][1] == "uncovered"


def test_chinese_requires_confirmed_whitespace_tokenization(backend, tmp_path):
    _, p = run_tst(backend, tmp_path, "text_mining_nlp",
       'dataset<-data.frame(text=c("疾病 治疗 疗效", "治疗 研究 病人", "疾病 病人 健康"))',
       {"text":"text", "tokenization":"whitespace"}, "list(terms=frequency$term,language=docs$language)")
    assert "治疗" in p["terms"] and p["language"] == "whitespace"


@pytest.mark.parametrize("template_id,data,inputs,message", [
    ("geostatistics", SPACE, {"coordinates":"coord","measure":"value","prediction_grid":"grid","crs":4326}, "投影CRS"),
    ("spatial_autocorrelation", SPACE+'\ndataset$adj[1]<-"unknown"', {"region_id":"id","measure":"value","adjacency":"adj","coordinates":"coord","crs":3857}, "未知 ID"),
    ("panel_forecast", PANEL, {"time":"time","panel_id":"panel","measure":"y","exogenous_variables":["x"]}, "JSON"),
    ("text_mining_nlp", 'dataset<-data.frame(text=c("疾病治疗","健康研究"))', {"text":"text"}, "未分词中文"),
    ("text_sentiment_analysis", 'dataset<-data.frame(text=c("xqzalpha zqzbeta","abcxyz defxyz"))', {"text":"text"}, "没有任何情感词典覆盖"),
    ("text_entity_relation_extraction", 'dataset<-data.frame(text="aspirin cooccurs pain")', {"text":"text","entity_dictionary":{"entities":{"drug":["aspirin"],"symptom":["pain"]},"relations":{"treats":"(?<source>aspirin) treats (?<target>pain)"}}}, "未命中关系"),
])
def test_invalid_spatial_or_text_branches_fail_explicitly(backend, tmp_path, template_id, data, inputs, message):
    _, p = run_tst(backend, tmp_path, template_id, data, inputs, "list(message=res)", failure=True)
    assert message in p["message"] and p["message"] != "OK"
