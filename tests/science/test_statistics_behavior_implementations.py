"""Real-R input, method selection, numerical and artifact semantics checks."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.skeleton_cores import _make_engine
from agent.analysis.statistics_behavior_cores import CORES, INPUTS, ROLE_MAPS
from r_runtime.protocol import DatasetRef, RRunRequest
from agent.tools.research.r_binding import BindingHelpersMixin


def run(backend, tmp_path, template_id, data, inputs, probe, *, fail=False):
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(
        plan_id="statistics-behavior-reference", source="template", dataset_id="fixture",
        intent=template.title, category=template.category, method_family=template.family,
        scenario=template.scenario, variable_bindings={}, packages=(), prerequisites=(),
        assumptions=(), diagnostics=template.diagnostics, required_outputs=template.required_outputs,
        resource_class="standard", template_id=template_id,
        resolved_inputs={k: ResolvedInput(k, k, specs[k].kind if k in specs else "specification", v, "user") for k, v in inputs.items()},
    )
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    script = _make_engine(CORES[template_id], INPUTS[template_id], ())(plan, ROLE_MAPS[template_id])
    if fail:
        script = "tryCatch({\n" + script + "\n},error=function(e) invisible(NULL))"
    code = data + "\n" + BindingHelpersMixin._frozen_input_prelude(plan) + "\n" + BindingHelpersMixin._frozen_user_code_block(script, plan)
    if not fail:
        code += '\nif(!identical(res,"OK")) stop(res)'
        code += '\n' + BindingHelpersMixin._frozen_input_epilogue(plan)
    code += '\nbaa_text("probe",jsonlite::toJSON(' + probe + ',auto_unbox=TRUE,digits=15,na="null"))'
    result = backend.run_code(RRunRequest(job_id="stats-behavior", dataset=DatasetRef("fixture", str(dummy), "csv", "input.csv"), code=code, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=120))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "probe")
    return result, json.loads(Path(note.path).read_text(encoding="utf-8"))


def test_correlation_preserves_numeric_levels_and_matches_all_three(backend, tmp_path):
    _, p = run(backend, tmp_path, "correlation_agreement", '''
dataset<-data.frame(a=rep(c(1,2,3),10),b=rep(c(2,4,3),10)+sin(seq_len(30)))
ref<-sapply(c("pearson","spearman","kendall"),function(m) cor(dataset$a,dataset$b,method=m))
''', {"continuous_variables": ["a", "b"], "analysis_method": "correlation"}, "list(values=corr_table$相关系数,reference=unname(ref),slots=names(.out))")
    assert p["values"] == pytest.approx(p["reference"], abs=1e-12)
    assert "agreement_stats" not in p["slots"]


def test_agreement_icc_uses_factor_subject_design_and_valid_limits(backend, tmp_path):
    _, p = run(backend, tmp_path, "correlation_agreement", '''
i<-seq_len(40);dataset<-data.frame(a=i*.3+sin(i),b=i*.3+cos(i)+.4)
ref<-irr::icc(dataset,model="twoway",type="agreement",unit="single")
''', {"continuous_variables": ["a", "b"], "analysis_method": "agreement"}, "list(icc=agreement_table$估计,reference=ref$value,limits=comparison_table[,c('一致性下限','一致性上限')],ref_limits=mean(dataset$a-dataset$b)+c(-1,1)*qnorm(.975)*sd(dataset$a-dataset$b))")
    assert p["icc"] == pytest.approx(p["reference"])
    assert p["limits"][0]["一致性下限"] == pytest.approx(p["ref_limits"][0])
    assert p["limits"][0]["一致性上限"] == pytest.approx(p["ref_limits"][1])


def test_agreement_categorical_does_not_construct_bland_altman(backend, tmp_path):
    _, p = run(backend, tmp_path, "correlation_agreement", '''
dataset<-data.frame(a=factor(rep(c("yes","yes","no","no"),10)),b=factor(rep(c("yes","no","no","no"),10)))
''', {"continuous_variables": ["a", "b"], "analysis_method": "agreement"}, "list(value=agreement_table$估计,reference=irr::kappa2(dataset)$value,columns=names(comparison_table))")
    assert p["value"] == pytest.approx(p["reference"])
    assert "均值差" not in p["columns"]


def test_ancova_adjusted_means_and_every_group_contrast(backend, tmp_path):
    _, p = run(backend, tmp_path, "ancova_adjusted_groups", '''
i<-seq_len(90);dataset<-data.frame(arm=factor(rep(c("A","B","C"),each=30)),x=sin(i*.4),sex=factor(rep(c("M","F"),45)))
dataset$y<-as.integer(dataset$arm)*2+dataset$x*1.4+(dataset$sex=="M")*.8+cos(i)
reference<-lm(y~arm+x+sex,data=dataset)
emref<-as.data.frame(emmeans::emmeans(reference,"arm",weights="equal"))
''', {"outcome": "y", "group": "arm", "covariates": ["x", "sex"]}, "list(means=adjusted_table$估计[adjusted_table$类型=='调整均值'],reference=emref$emmean,n_contrasts=sum(adjusted_table$类型=='组间对比 Tukey'),slope_p=diagnostic_table$斜率交互P,reference_slope=anova(reference,lm(y~arm*(x+sex),data=dataset))$`Pr(>F)`[2])")
    assert p["means"] == pytest.approx(p["reference"], abs=1e-10)
    assert p["n_contrasts"] == 3
    assert p["slope_p"] == pytest.approx(p["reference_slope"])


@pytest.mark.parametrize("method", ["multinomial", "ordinal", "count"])
def test_selected_categorical_branch_and_continuous_exposure_scale(backend, tmp_path, method):
    ycode = {"count": "dataset$y<-rpois(160,exp(.3+.2*dataset$x+log(dataset$t)))", "multinomial": "dataset$y<-factor(sample(c('A','B','C'),160,TRUE))", "ordinal": "dataset$y<-ordered(cut(dataset$x+rlogis(160),breaks=c(-Inf,-.7,.7,Inf)),levels=c('(-Inf,-0.7]','(-0.7,0.7]','(0.7, Inf]'))"}[method]
    inputs = {"outcome": "y", "exposure": "x", "analysis_method": method}
    if method == "count":
        inputs["exposure_2"] = "t"
    _, p = run(backend, tmp_path, "categorical_ordinal_models", "set.seed(42);dataset<-data.frame(x=rnorm(160),t=runif(160,1,4))\n" + ycode, inputs, "list(class=class(fit),x_numeric=is.numeric(df$x),pred_n=nrow(pred_table),marginal_n=nrow(marginal_table),coef=unname(coef(fit)),ref=unname(if(method=='count') coef(glm(y~x+offset(log(t)),data=dataset,family=poisson())) else if(method=='ordinal') coef(MASS::polr(y~x,data=dataset,Hess=TRUE)) else coef(nnet::multinom(y~x,data=dataset,trace=FALSE))))")
    assert p["x_numeric"] is True
    assert p["pred_n"] == 160
    expected = {"multinomial": "multinom", "ordinal": "polr", "count": "glm"}[method]
    assert expected in p["class"]
    assert p["coef"] == pytest.approx(p["ref"], abs=1e-8) if method != "multinomial" else p["coef"] == p["ref"]


def test_ordinal_requires_explicit_level_order(backend, tmp_path):
    _, p = run(backend, tmp_path, "categorical_ordinal_models", "dataset<-data.frame(x=1:30,y=factor(rep(c('low','middle','high'),10)))", {"outcome": "y", "exposure": "x", "analysis_method": "ordinal"}, "list(message=res)", fail=True)
    assert "ordered factor" in p["message"]


def test_effect_numeric_low_cardinality_stays_continuous(backend, tmp_path):
    _, p = run(backend, tmp_path, "effect_size_precision", "dataset<-data.frame(arm=rep(c('A','B'),each=20),y=c(rep(c(0,1),10),rep(c(1,2),10)))", {"outcome": "y", "group": "arm"}, "list(table=eff_table,reference=unname(t.test(dataset$y[dataset$arm=='B'],dataset$y[dataset$arm=='A'])$conf.int))")
    row = p["table"][0]
    assert row["指标"] == "Welch 均值差"
    assert row["效应"] == pytest.approx(1)
    assert [row["CI下限"], row["CI上限"]] == pytest.approx(p["reference"])


def test_paired_effect_size_uses_subject_matches(backend, tmp_path):
    _, p = run(backend, tmp_path, "effect_size_precision", '''
dataset<-data.frame(id=rep(1:20,2),arm=rep(c("A","B"),each=20),y=c(1:20,1:20+2+sin(1:20)))
''', {"outcome": "y", "group": "arm", "pair_id": "id"}, "list(estimate=eff_table$效应[1],reference=mean(2+sin(1:20)),paired=precision_table$配对[1])")
    assert p["estimate"] == pytest.approx(p["reference"])
    assert p["paired"] is True


def test_binary_effect_zero_cell_uses_exact_reference(backend, tmp_path):
    _, p = run(backend, tmp_path, "effect_size_precision", "dataset<-data.frame(arm=rep(c('A','B'),each=20),y=factor(c(rep('no',20),rep('no',10),rep('yes',10))))", {"outcome": "y", "group": "arm", "positive_class": "yes"}, "list(value=eff_table$效应,low=eff_table$CI下限,high=eff_table$CI上限,infinite=is.infinite(eff_table$效应))")
    assert p["infinite"] is True
    assert p["low"] > 1


@pytest.mark.parametrize("design", ["single", "independent", "paired", "cluster"])
def test_bootstrap_respects_sampling_unit(backend, tmp_path, design):
    inputs = {"outcome": "y", "resamples": 199, "seed": 31}
    data = "dataset<-data.frame(id=rep(1:20,2),arm=rep(c('A','B'),each=20),y=c(1:20,1:20+2+sin(1:20)))"
    if design != "single":
        inputs["group"] = "arm"
    if design == "paired":
        inputs["pair_id"] = "id"
    if design == "cluster":
        data = "dataset<-data.frame(cluster=rep(1:12,each=3),arm=rep(c('A','B'),each=18),y=1:36+sin(1:36))"
        inputs["cluster_id"] = "cluster"
    _, p = run(backend, tmp_path, "bootstrap_permutation_inference", data, inputs, "list(estimate=obs,table=boot_table,reference=if(is.null(group)) mean(dataset$y) else mean(dataset$y[dataset$arm=='B'])-mean(dataset$y[dataset$arm=='A']))")
    assert p["estimate"] == pytest.approx(p["reference"])
    table = p["table"][0]
    if design == "single":
        assert table["置换P"] is None
        assert table["统计量"] == "单总体均值"
    else:
        assert table["置换P"] >= 1 / 200
    if design == "paired":
        assert table["重采样单位"] == "完整配对 ID"
    if design == "cluster":
        assert table["重采样单位"] == "独立聚类 ID"


def test_bootstrap_rejects_incomplete_pairs_instead_of_independent_analysis(backend, tmp_path):
    _, p = run(backend, tmp_path, "bootstrap_permutation_inference", "dataset<-data.frame(id=c(1:10,2:11),arm=rep(c('A','B'),each=10),y=1:20)", {"outcome": "y", "group": "arm", "pair_id": "id"}, "list(message=res)", fail=True)
    assert "完整一一配对" in p["message"]


def test_huber_and_ols_are_real_distinct_comparisons(backend, tmp_path):
    _, p = run(backend, tmp_path, "robust_outlier_analysis", "i<-1:50;dataset<-data.frame(y=2+3*i+sin(i),x=i);dataset$y[50]<-700", {"continuous_variables": ["y", "x"]}, "list(huber=sens_table$Huber,ols=sens_table$OLS,reference=unname(coef(MASS::rlm(y~x,data=dataset,maxit=100))))")
    assert p["huber"] == pytest.approx(p["reference"])
    assert abs(p["huber"][1] - p["ols"][1]) > 1


def test_subgroup_preserves_numeric_outcome_and_joint_interaction(backend, tmp_path):
    _, p = run(backend, tmp_path, "subgroup_sensitivity_basic", '''
i<-1:120;dataset<-data.frame(arm=factor(rep(c("A","B"),60)),sg=factor(rep(c("L","H"),each=60)),z=sin(i))
dataset$y<-1+2*(dataset$arm=="B")+3*(dataset$arm=="B")*(dataset$sg=="H")+dataset$z+cos(i)
ref0<-glm(y~arm+sg+z,data=dataset);ref1<-glm(y~arm*sg+z,data=dataset)
''', {"outcome": "y", "exposure": "arm", "subgroup": "sg", "covariates": ["z"]}, "list(p=interaction_p,reference=anova(ref0,ref1,test='F')[2,6],binary=binary,n_rows=nrow(sens_table),ci=as.numeric(sens_table[1,c('CI下限','CI上限')]),ref_ci=unname(confint(lm(y~arm+z,data=dataset))['armB',]))")
    assert p["binary"] is False
    assert p["p"] == pytest.approx(p["reference"])
    assert p["n_rows"] == 4
    assert p["ci"] == pytest.approx(p["ref_ci"])


def test_scale_reverse_items_dimensions_and_two_item_drop(backend, tmp_path):
    _, p = run(backend, tmp_path, "scale_reliability", '''
set.seed(22);z<-rnorm(100);dataset<-data.frame(a=z+rnorm(100,.0,.3),b=z+rnorm(100,0,.3),c=-z+rnorm(100,0,.3),d=z+rnorm(100,0,.3))
''', {"categorical_variables": ["a", "b", "c", "d"], "reverse_item_indicator": ["c"], "dimension_group": {"two": ["a", "b"]}}, "list(alpha=rel_table$Cronbach_alpha[1],reference=psych::alpha(data.frame(a=dataset$a,b=dataset$b,c=-dataset$c,d=dataset$d),check.keys=FALSE,warnings=FALSE)$total$raw_alpha,drop_two=del_table$删除后alpha[del_table$维度=='two'],omega=rel_table$单因子omega_total[1],corr_columns=names(cor_table))")
    assert p["alpha"] == pytest.approx(p["reference"])
    assert p["drop_two"] == [None, None]
    assert math.isfinite(p["omega"])
    assert p["corr_columns"] == ["条目1", "条目2", "Pearson相关"]


def test_efa_reports_pa_and_full_subject_scores(backend, tmp_path):
    _, p = run(backend, tmp_path, "efa_factor", '''
set.seed(29);z<-rnorm(150);dataset<-as.data.frame(replicate(5,z+rnorm(150,0,.4)));names(dataset)<-paste0("i",1:5);dataset$id<-paste0("P",1:150)
''', {"categorical_variables": ["i1", "i2", "i3", "i4", "i5"], "dimension": 1, "sample_id": "id"}, "list(kmo=kmo,reference=psych::KMO(cor(dataset[,1:5]))$MSA,n_scores=nrow(score_table),ids=head(score_table$样本ID),loads=unname(as.numeric(fa_res$loadings)),ref_loads=unname(as.numeric(psych::fa(dataset[,1:5],nfactors=1,rotate='none',fm='pa')$loadings)))")
    assert p["kmo"] == pytest.approx(p["reference"])
    assert p["loads"] == pytest.approx(p["ref_loads"])
    assert p["n_scores"] == 150
    assert p["ids"][0] == "P1"


@pytest.mark.parametrize("method", ["cfa", "sem"])
def test_cfa_and_sem_execute_selected_method_syntax(backend, tmp_path, method):
    inputs = {"categorical_variables": [f"i{i}" for i in range(1, 7)], "latent_variables": "F1 =~ i1+i2+i3\nF2 =~ i4+i5+i6", "analysis_method": method}
    if method == "sem":
        inputs["path_specification"] = "F2 ~ F1"
    _, p = run(backend, tmp_path, "cfa_sem", '''
set.seed(56);z<-rnorm(180);w<-.6*z+rnorm(180);dataset<-data.frame(i1=z+rnorm(180,0,.5),i2=z+rnorm(180,0,.5),i3=z+rnorm(180,0,.5),i4=w+rnorm(180,0,.5),i5=w+rnorm(180,0,.5),i6=w+rnorm(180,0,.5))
''', inputs, "list(estimator=lavaan::lavInspect(fit,'options')$estimator,model=full_model,paths=nrow(struct_table),loads=load_table$est,reference=lavaan::parameterEstimates(do.call(if(method=='sem') lavaan::sem else lavaan::cfa,list(model=full_model,data=dataset,std.lv=TRUE)))$est[1:6],syntax_kind=.out[['syntax_table']]$kind)")
    assert p["estimator"] == "ML"
    assert p["loads"] == pytest.approx(p["reference"], abs=1e-10)
    assert p["syntax_kind"] == "text"
    assert p["paths"] == (1 if method == "sem" else 0)


def test_cfa_multi_group_is_not_ignored(backend, tmp_path):
    _, p = run(backend, tmp_path, "cfa_sem", '''
set.seed(79);z<-rnorm(200);dataset<-data.frame(i1=z+rnorm(200,0,.5),i2=z+rnorm(200,0,.5),i3=z+rnorm(200,0,.5),i4=z+rnorm(200,0,.5),group=rep(c("A","B"),each=100))
''', {"categorical_variables": ["i1", "i2", "i3", "i4"], "latent_variables": "F =~ i1+i2+i3+i4", "analysis_method": "cfa", "group": "group"}, "list(groups=unique(load_table$group),n_groups=lavaan::lavInspect(fit,'ngroups'))")
    assert p["groups"] == [1, 2]
    assert p["n_groups"] == 2


@pytest.mark.parametrize("method", ["mediation", "moderation", "moderated_mediation"])
def test_mediation_and_moderator_are_mapped_to_selected_model(backend, tmp_path, method):
    inputs = {"predictor": "x", "outcome": "y", "analysis_method": method, "resamples": 199}
    if method != "moderation":
        inputs["mediator"] = "m"
    if method != "mediation":
        inputs["moderator"] = "w"
    _, p = run(backend, tmp_path, "mediation_moderation", '''
set.seed(9);dataset<-data.frame(x=rnorm(100),w=rnorm(100));dataset$m<-.6*dataset$x+.4*dataset$x*dataset$w+rnorm(100);dataset$y<-.2*dataset$x+.8*dataset$m+.2*dataset$w+rnorm(100)
''', inputs, "list(rows=nrow(decomp_table),n_parameters=nrow(paths_table),indirect=if(method=='moderation') NULL else decomp_table$间接效应,reference=if(method=='mediation') unname(coef(lm(m~x,data=dataset))['x']*coef(lm(y~x+m,data=dataset))['m']) else if(method=='moderated_mediation') unname((coef(lm(m~x*w,data=dataset))['x']+coef(lm(m~x*w,data=dataset))['x:w']*decomp_table$调节值)*coef(lm(y~x+m+w,data=dataset))['m']) else NULL)")
    assert p["rows"] == (1 if method == "mediation" else 3)
    if method != "moderation":
        assert p["indirect"] == pytest.approx(p["reference"])


def test_interrater_icc_numeric_scores_match_all_raters_reference(backend, tmp_path):
    _, p = run(backend, tmp_path, "interrater_agreement", '''
i<-1:30;wide_ref<-data.frame(A=i+sin(i),B=i+cos(i),C=i+sin(i*2));dataset<-data.frame(id=rep(i,3),rater=rep(c("A","B","C"),each=30),score=unlist(wide_ref,use.names=FALSE))
''', {"data_object": "id", "rater_id": "rater", "measure": "score"}, "list(value=kappa_table$估计,reference=irr::icc(wide_ref,model='twoway',type='agreement',unit='single')$value,method=method)")
    assert p["value"] == pytest.approx(p["reference"])
    assert p["method"] == "icc"


def test_interrater_kappa_includes_nonadjacent_pair_and_bootstrap_interval(backend, tmp_path):
    _, p = run(backend, tmp_path, "interrater_agreement", '''
a<-rep(c("N","Y"),30);b<-a;b[seq(1,60,7)]<-ifelse(b[seq(1,60,7)]=="N","Y","N");cc<-a;cc[seq(2,60,11)]<-ifelse(cc[seq(2,60,11)]=="N","Y","N")
dataset<-data.frame(id=rep(1:60,3),rater=rep(c("A","B","C"),each=60),score=factor(c(a,b,cc)))
''', {"data_object": "id", "rater_id": "rater", "measure": "score", "resamples": 199}, "list(pairs=kappa_table$评分者对,value=kappa_table$估计[1],reference=irr::kappa2(data.frame(a,b))$value,intervals=kappa_table[,c('CI下限','CI上限')])")
    assert p["pairs"] == ["A vs B", "A vs C", "B vs C"]
    assert p["value"] == pytest.approx(p["reference"])
    assert all(math.isfinite(row["CI下限"]) for row in p["intervals"])


def test_interrater_duplicate_subject_rater_rejected(backend, tmp_path):
    _, p = run(backend, tmp_path, "interrater_agreement", "dataset<-data.frame(id=c(1,1,1,2,2,3,3),rater=c('A','A','B','A','B','A','B'),score=1:7)", {"data_object": "id", "rater_id": "rater", "measure": "score"}, "list(message=res)", fail=True)
    assert "只能有一次评分" in p["message"]


def test_interrater_weighted_kappa_preserves_confirmed_order(backend, tmp_path):
    _, p = run(backend, tmp_path, "interrater_agreement", '''
a<-rep(c("low","middle","high"),20);b<-a;b[c(2,5,8)]<-"high";ratings<-data.frame(a=ordered(a,levels=c("low","middle","high")),b=ordered(b,levels=c("low","middle","high")))
dataset<-data.frame(id=rep(1:60,2),rater=rep(c("A","B"),each=60),score=ordered(c(a,b),levels=c("low","middle","high")))
''', {"data_object": "id", "rater_id": "rater", "measure": "score", "agreement_method": "weighted_kappa", "resamples": 199}, "list(value=kappa_table$估计,reference=irr::kappa2(ratings,weight='equal',sort.levels=FALSE)$value)")
    assert p["value"] == pytest.approx(p["reference"])


def test_count_negative_binomial_uses_selected_family_and_offset(backend, tmp_path):
    _, p = run(backend, tmp_path, "categorical_ordinal_models", "set.seed(100);dataset<-data.frame(x=rnorm(150),t=runif(150,1,5));dataset$y<-rnbinom(150,mu=exp(.3+.2*dataset$x+log(dataset$t)),size=2)", {"outcome": "y", "exposure": "x", "exposure_2": "t", "analysis_method": "count", "count_family": "negative_binomial"}, "list(theta=fit$theta,reference=MASS::glm.nb(y~x+offset(log(t)),data=dataset)$theta)")
    assert p["theta"] == pytest.approx(p["reference"])


def test_cfa_ordered_items_use_wlsmv(backend, tmp_path):
    _, p = run(backend, tmp_path, "cfa_sem", '''
set.seed(80);z<-rnorm(220);dataset<-as.data.frame(lapply(seq_len(5),function(i) ordered(cut(z+rnorm(220,0,.6),breaks=c(-Inf,-.6,.6,Inf)))));names(dataset)<-paste0("i",1:5)
''', {"categorical_variables": ["i1", "i2", "i3", "i4", "i5"], "latent_variables": "F =~ i1+i2+i3+i4+i5", "analysis_method": "cfa"}, "list(estimator=lavaan::lavInspect(fit,'options')$estimator,requested=args$estimator,converged=lavaan::lavInspect(fit,'converged'))")
    assert p["requested"] == "WLSMV"
    assert p["estimator"] == "DWLS"
    assert p["converged"] is True


def test_mediation_supports_confirmed_column_names_with_spaces(backend, tmp_path):
    _, p = run(backend, tmp_path, "mediation_moderation", '''
set.seed(72);dataset<-data.frame(x=rnorm(60));dataset$m<-.7*dataset$x+rnorm(60);dataset$y<-.5*dataset$x+.8*dataset$m+rnorm(60);names(dataset)<-c("predictor score","mediator score","outcome score")
''', {"predictor": "predictor score", "mediator": "mediator score", "outcome": "outcome score", "analysis_method": "mediation", "resamples": 199}, "list(indirect=decomp_table$间接效应,reference=unname(coef(lm(`mediator score`~`predictor score`,data=dataset))[2]*coef(lm(`outcome score`~`predictor score`+`mediator score`,data=dataset))[3]))")
    assert p["indirect"] == pytest.approx(p["reference"])
