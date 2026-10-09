"""Numerical and method-selection checks, independent of artifact role counts."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ResolvedInput
from agent.analysis.script_library import render_script
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.protocol import DatasetRef, RRunRequest


@pytest.fixture(scope="module")
def backend():
    try:
        host = HostREnvironment.locate()
        verified = host.prepare()
    except REnvironmentError as exc:
        if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
            pytest.fail(str(exc))
        pytest.skip(str(exc))
    runtime_env = host.process_environment(verified=verified)
    return LocalRscriptBackend(rscript_bin=str(verified.rscript), runtime_env=runtime_env)


def run_method(backend, tmp_path, template_id, data_code, inputs, probe, *, expect_success=True):
    from agent.analysis.method_policy import SUPPORTED_DESIGNS
    from agent.tools.research import RToolsMixin
    inputs = dict(inputs)
    if template_id in SUPPORTED_DESIGNS:
        inputs.setdefault("design_scope", SUPPORTED_DESIGNS[template_id])
    if template_id == "diagnostic_roc":
        inputs.setdefault("positive_class", "1")
        inputs.setdefault("score_direction", "higher_positive")
    template = AnalysisCatalog().get(template_id)
    specs = {s.id: s for s in template.input_specs}
    plan = AnalysisPlan(
        plan_id="numeric-reference-20260929", source="template", dataset_id="numeric-fixture",
        intent=template.title, category=template.category, method_family=template.family,
        scenario=template.scenario, variable_bindings={}, packages=(), prerequisites=(),
        assumptions=(), diagnostics=template.diagnostics, required_outputs=template.required_outputs,
        resource_class="standard", template_id=template_id,
        resolved_inputs={
            k: ResolvedInput(k, k, specs[k].kind if k in specs else "specification", v, "user")
            for k, v in inputs.items()
        },
    )
    dummy = tmp_path / "input.csv"
    dummy.write_text("x\n1\n", encoding="utf-8")
    script = render_script(plan)
    assert script is not None
    if not expect_success:
        script = "tryCatch({\n" + script + "\n}, error=function(e) invisible(NULL))"
    code = (
        data_code + "\n"
        + RToolsMixin._frozen_input_prelude(plan) + "\n"
        + RToolsMixin._frozen_user_code_block(script, plan) + "\n"
        + ((RToolsMixin._frozen_input_epilogue(plan) + "\n") if expect_success else "")
        + ('if (!identical(res,"OK")) stop(res)\n' if expect_success else "")
        + 'baa_text("numeric_probe", jsonlite::toJSON(' + probe + ', auto_unbox=TRUE, digits=15))'
    )
    result = backend.run_code(RRunRequest(
        job_id="numeric-test", dataset=DatasetRef("numeric-fixture", str(dummy), "csv", "input.csv"),
        code=code, artifacts_dir=str(tmp_path / "artifacts"), timeout_seconds=90,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    note = next(a for a in result.texts if a.name == "numeric_probe")
    return result, json.loads(Path(note.path).read_text(encoding="utf-8"))


NI_INPUTS = {
    "outcome": "y", "group": "arm", "noninferiority_margin": 1,
    "analysis_population": "population", "analysis_population_level": "ITT",
    "reference_group": "A", "outcome_direction": "higher_better",
    "analysis_method": "noninferiority", "alpha": 0.025,
}


@pytest.mark.parametrize("shift,direction,method,expected", [
    (2, "higher_better", "noninferiority", "非劣效成立"),
    (-2, "higher_better", "noninferiority", "非劣效未成立"),
    (-2, "lower_better", "noninferiority", "非劣效成立"),
    (2, "lower_better", "noninferiority", "非劣效未成立"),
    (0, "higher_better", "equivalence", "等效成立"),
    (2, "higher_better", "equivalence", "等效未成立"),
])
def test_noninferiority_matches_welch_reference(backend, tmp_path, shift, direction, method, expected):
    _, probe = run_method(backend, tmp_path, "noninferiority_equivalence", f'''
dataset <- data.frame(arm=rep(c("A","B"), each=30), population="ITT",
                      y=c(seq(9,11,length.out=30), seq(9,11,length.out=30)+{shift}))
reference <- stats::t.test(dataset$y[dataset$arm=="B"], dataset$y[dataset$arm=="A"])
''', {**NI_INPUTS, "outcome_direction": direction, "analysis_method": method},
        "list(estimate=diff, interval=unname(ci), reference=unname(reference$conf.int), decision=decision)")
    assert probe["estimate"] == pytest.approx(shift)
    assert probe["interval"] == pytest.approx(probe["reference"], abs=1e-10)
    assert probe["decision"] == expected


def test_noninferiority_uses_selected_population_and_reference(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "noninferiority_equivalence", '''
dataset <- data.frame(arm=rep(c("A","B"), each=30), population="ITT",
                      y=c(seq(9,11,length.out=30), seq(11,13,length.out=30)))
dataset <- rbind(dataset, data.frame(arm="A", population="PP", y=1000))
''', {**NI_INPUTS, "reference_group": "B"}, "list(estimate=diff, n=nrow(df))")
    assert probe["estimate"] == pytest.approx(-2)
    assert probe["n"] == 60


@pytest.mark.parametrize("method", ["mixed", "gee"])
def test_longitudinal_method_matches_reference_fit(backend, tmp_path, method):
    _, probe = run_method(backend, tmp_path, "mixed_repeated", '''
dataset <- expand.grid(id=seq_len(30), visit=seq_len(4))
dataset$id <- factor(dataset$id)
dataset$group <- factor(ifelse(as.integer(dataset$id) %% 2, "A", "B"))
dataset$y <- 20+as.integer(dataset$id)*0.2+dataset$visit*1.3+sin(as.integer(dataset$id)*dataset$visit)
ref_data <- dataset[order(dataset$id, dataset$visit), ]
ref_data$visit <- factor(ref_data$visit)
''', {"outcome": "y", "time": "visit", "subject_id": "id", "fixed_effects": ["group"], "analysis_method": method},
        '''list(model_class=class(fit),
          estimate=unname(if (inherits(fit,"merMod")) lme4::fixef(fit) else coef(fit)),
          reference=unname(if (method_v=="mixed") lme4::fixef(lme4::lmer(y~visit+group+(1|id),data=ref_data))
            else coef(geepack::geeglm(y~visit+group,id=id,data=ref_data,corstr="exchangeable"))))''')
    assert ("lmerMod" if method == "mixed" else "geeglm") in probe["model_class"]
    assert probe["estimate"] == pytest.approx(probe["reference"], rel=1e-7, abs=1e-7)


@pytest.mark.parametrize("template_id", [
    "missing_imputation", "multiple_imputation_pooling", "clinical_target_trial_emulation",
    "trial_simon_two_stage", "staggered_did_panel",
])
def test_complex_methods_have_dedicated_scripts(template_id):
    from tests.science.test_script_library import _plan
    assert render_script(_plan(template_id, {})) is not None


def test_ml_selection_is_independent_of_holdout_outcomes(backend, tmp_path):
    rows = []
    for flip in (False, True):
        folder = tmp_path / str(flip)
        folder.mkdir()
        result, probe = run_method(backend, folder, "tabular_ml_workflow", f'''
i <- seq_len(150)
dataset <- data.frame(x1=sin(i*.7), x2=cos(i*.3), x3=i%%7, test=i>100)
dataset$y <- as.numeric(dataset$x1+cos(i*1.7)*.8 > 0)
if ({str(flip).upper()}) dataset$y[dataset$test] <- 1-dataset$y[dataset$test]
''', {"outcome": "y", "features": ["x1", "x2", "x3"], "test_split": "test"},
            'list(chosen=best_is_rf, diagnostics=.diag_status, n_test=nrow(test))')
        comparison = next(a for a in result.tables if a.name == "table_1")
        with Path(comparison.path).open(encoding="utf-8-sig") as handle:
            rows.append((probe, list(csv.DictReader(handle))))
    assert rows[0][1] == rows[1][1], "Model selection scores must use training CV only"
    assert rows[0][0]["chosen"] == rows[1][0]["chosen"]
    assert rows[0][0]["diagnostics"]["特征泄漏检查"] == "warning"


@pytest.mark.parametrize("method", ["mixed", "gee"])
def test_binary_longitudinal_uses_logit_model(backend, tmp_path, method):
    _, probe = run_method(backend, tmp_path, "mixed_repeated", '''
set.seed(811)
dataset <- expand.grid(id=seq_len(80),visit=seq_len(4))
effects <- rnorm(80,sd=.8)
dataset$y <- rbinom(nrow(dataset),1,plogis(-1+dataset$visit*.3+effects[dataset$id]))
ref_data <- dataset[order(dataset$id,dataset$visit),]
ref_data$id <- factor(ref_data$id); ref_data$visit <- factor(ref_data$visit)
''', {"outcome": "y", "time": "visit", "subject_id": "id", "analysis_method": method},
        '''list(model_class=class(fit),family=family$family,
          estimate=unname(if (inherits(fit,"merMod")) lme4::fixef(fit) else coef(fit)),
          reference=unname(if (method_v=="mixed") lme4::fixef(lme4::glmer(y~visit+(1|id),data=ref_data,family=binomial()))
            else coef(geepack::geeglm(y~visit,id=id,data=ref_data,family=binomial(),corstr="exchangeable"))))''')
    assert ("glmerMod" if method == "mixed" else "geeglm") in probe["model_class"]
    assert probe["family"] == "binomial"
    assert probe["estimate"] == pytest.approx(probe["reference"], rel=1e-7, abs=1e-7)


def test_ml_regression_reports_test_error_for_locked_model(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "tabular_ml_workflow", '''
i <- seq_len(120)
dataset <- data.frame(x1=sin(i*.4),x2=cos(i*.7),test=i>80)
dataset$y <- 2*dataset$x1-dataset$x2+sin(i*1.7)*.2
''', {"outcome": "y", "features": ["x1", "x2"], "test_split": "test"},
        "list(reported=test_perf$RMSE,reference=sqrt(mean((test$y-test_prediction)^2)),n_predictions=length(test_prediction))")
    assert probe["reported"] == pytest.approx(probe["reference"])
    assert probe["n_predictions"] == 40


def test_equivalence_alpha_changes_interval_and_tost_p_value(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "noninferiority_equivalence", '''
dataset <- data.frame(arm=rep(c("A","B"),each=30),population="ITT",
  y=c(seq(9,11,length.out=30),seq(9,11,length.out=30)+.8))
reference <- t.test(dataset$y[dataset$arm=="B"],dataset$y[dataset$arm=="A"],conf.level=.9)
''', {**NI_INPUTS, "alpha": 0.05, "analysis_method": "equivalence"},
        'list(interval=ci,reference=unname(reference$conf.int),p=test_p,accepted=accepted)')
    assert probe["interval"] == pytest.approx(probe["reference"])
    assert probe["accepted"] == (probe["p"] < 0.05)


def test_ml_honors_requested_strata_in_split_and_cv(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "tabular_ml_workflow", '''
i <- seq_len(120)
dataset <- data.frame(x1=sin(i*.4),x2=cos(i*.7),site=rep(c("A","B"),each=60))
dataset$y <- 2*dataset$x1-dataset$x2+sin(i*1.7)*.2
''', {"outcome": "y", "features": ["x1", "x2"], "strata": "site"},
        '''list(test_counts=as.integer(table(dataset$site[is_test])),
          cv_spread=unname(apply(table(dataset$site[!is_test],cv_folds),1,function(v) max(v)-min(v))))''')
    assert probe["test_counts"] == [18, 18]
    assert all(spread <= 1 for spread in probe["cv_spread"])


def test_ml_rejects_too_few_events_instead_of_reporting_performance(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "tabular_ml_workflow", '''
i <- seq_len(120)
dataset <- data.frame(x1=sin(i*.4),x2=cos(i*.7),y=as.integer(i<=8))
''', {"outcome": "y", "features": ["x1", "x2"]},
        'list(status=res,outputs=length(.out))', expect_success=False)
    assert "训练集每类至少需 10 行" in probe["status"]
    assert probe["outputs"] == 0


def test_group_sequential_preserves_information_schedule(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "group_sequential", "dataset <- data.frame(x=1:10)",
        {"information_time": [.3, .6, 1], "alpha": .025, "effect_size": .5,
         "power": .8, "alpha_spending": "OBrienFleming"},
        "list(timing=gs$timing,alpha=sum(gs$upper$prob[,1]),n=n_total,reference=ceiling(gs$n.I[k]))")
    assert p["timing"] == [.3, .6, 1]
    assert p["alpha"] == pytest.approx(.025, abs=1e-6)
    assert p["n"] == p["reference"]


def test_roc_ties_have_a_real_threshold(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "diagnostic_roc",
        "dataset <- data.frame(y=rep(c(0,1),each=10),score=rep(c(0,1),each=10))",
        {"outcome": "y", "diagnostic_score": "score"},
        "list(threshold=best_thr,auc=main$auc,sensitivity=sens,specificity=spec)")
    assert p["threshold"] is not None
    assert p["sensitivity"] == p["specificity"] == p["auc"] == 1


def test_pr_precision_uses_counts_not_rates(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "diagnostic_roc",
        "dataset <- data.frame(y=c(1,1,rep(0,8)),score=9:0)",
        {"outcome": "y", "diagnostic_score": "score"},
        "list(precision=tail(ggplot2::ggplot_build(roc2_fig)$data[[1]]$y,1))")
    assert p["precision"] == pytest.approx(.2)


def test_allocation_ratio_means_control_per_experimental(backend, tmp_path):
    _, p = run_method(backend, tmp_path, "sample_size_power", "dataset <- data.frame(x=1:10)",
        {"effect_size": .5, "alpha": .05, "power": .8, "allocation_ratio": 2},
        "list(n=ss_table$样本量)")
    assert p["n"][:2] == [48, 95]

@pytest.mark.parametrize('tid', ['missing_imputation','multiple_imputation_pooling'])
def test_multiple_imputation_preserves_between_variance_and_rubin_rule(backend,tmp_path,tid):
    _, p = run_method(backend,tmp_path,tid,'''set.seed(204)
dataset <- data.frame(x=rnorm(150),z=rnorm(150))
dataset$y <- 2*dataset$x+dataset$z+rnorm(150)
dataset$x[sample.int(150,40)] <- NA''',
        {'outcome':'y','predictors':['x','z'],'imputations':5,'iterations':5},
        'list(b=variance_table$b,total=variance_table$t,reference=variance_table$ubar+(1+1/m)*variance_table$b,m=imp$m)')
    assert p['m'] == 5
    assert any(x > 0 for x in p['b'])
    assert p['total'] == pytest.approx(p['reference'])

@pytest.mark.parametrize('criterion', ['optimal','minimax'])
def test_simon_exact_operating_characteristics(backend,tmp_path,criterion):
    _,p = run_method(backend,tmp_path,'trial_simon_two_stage','dataset <- data.frame(y=rep(c(0,1),20))',
        {'outcome':'y','null_response_rate':.2,'target_response_rate':.4,'alpha':.05,'power':.8,'design_criterion':criterion},
        'list(alpha=actual_alpha,power=actual_power,n1=n1,n=n,r1=r1,r=r)')
    assert p['alpha'] <= .05
    assert p['power'] >= .8
    assert [p['n1'],p['n'],p['r1'],p['r']] == ([13,43,3,12] if criterion=='optimal' else [18,33,4,10])


def test_staggered_did_estimates_group_time_effects(backend,tmp_path):
    _,p = run_method(backend,tmp_path,'staggered_did_panel','''set.seed(204)
dataset <- expand.grid(id=1:180,time=1:6)
dataset$cohort <- rep(c(rep(0,60),rep(3,60),rep(5,60)),6)
dataset$y <- dataset$id*.01+dataset$time*.5+rnorm(nrow(dataset),sd=.1)+
 ifelse(dataset$cohort>0 & dataset$time>=dataset$cohort,2,0)''',
        {'subject_id':'id','time':'time','treatment_point':'cohort','outcome':'y'},
        'list(att=agg$overall.att,cohorts=unique(att_table$group))')
    assert p['att'] == pytest.approx(2,abs=.08)
    assert p['cohorts'] == [3,5]


def test_target_trial_matches_unconfounded_risk_difference(backend,tmp_path):
    _,p = run_method(backend,tmp_path,'clinical_target_trial_emulation','''set.seed(204)
dataset <- data.frame(id=1:400,baseline=0,a=rep(0:1,each=200),x=rep(seq(-2,2,length.out=200),2))
dataset$y <- c(rep(1,40),rep(0,160),rep(1,80),rep(0,120))''',
        {'subject_id':'id','time_zero':'baseline','treatment_strategy':'a','outcome':'y','covariates_2':['x']},
        'list(effect=est$effect,lower=ci[1],upper=ci[2],ps_range=range(est$ps))')
    assert p['effect'] == pytest.approx(.2,abs=1e-7)
    assert p['lower'] < .2 < p['upper']
    assert p['ps_range'] == pytest.approx([.5,.5])


def test_roc_honors_negative_direction_and_prespecified_threshold(backend,tmp_path):
    _,p = run_method(backend,tmp_path,'diagnostic_roc',
        'dataset <- data.frame(y=rep(c("case","control"),each=10),score=rep(c(0,1),each=10))',
        {'outcome':'y','diagnostic_score':'score','positive_class':'case','score_direction':'lower_positive','threshold':.5},
        'list(threshold=best_thr,sensitivity=sens,specificity=spec,auc=main$auc)')
    assert p == {'threshold':.5,'sensitivity':1,'specificity':1,'auc':1}


def test_group_sequential_pocock_and_string_timing_match_reference(backend,tmp_path):
    _,p = run_method(backend,tmp_path,'group_sequential','dataset <- data.frame(x=1:10)',
        {'information_time':'c(0.5, 1)','alpha_spending':'Pocock','alpha':.025,'effect_size':.5,'power':.8},
        '''list(bounds=gs$upper$bound,reference=gsDesign::gsDesign(k=2,test.type=1,alpha=.025,beta=.2,
          timing=c(.5,1),n.fix=gsDesign::nNormal(delta1=.5,sd=1,alpha=.025,beta=.2),sfu=gsDesign::sfLDPocock)$upper$bound)''')
    assert p['bounds'] == pytest.approx(p['reference'])


def test_fixed_script_rejects_an_unsupported_study_design(backend,tmp_path):
    _,p = run_method(backend,tmp_path,'sample_size_power','dataset <- data.frame(x=1:10)',
        {'design_scope':'binary_endpoint','effect_size':.5},'list(status=res,outputs=length(.out))',expect_success=False)
    assert '研究设计' in p['status']
    assert p['outputs'] == 0
