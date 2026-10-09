"""Focused numerical/design references for the common fixed methods."""

from __future__ import annotations

import math
import statistics

import pytest

from tests.science.test_method_numerics import backend, run_method  # noqa: F401


def test_linear_model_coefficients_and_small_sample_t_interval(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "regression_glm", '''
i <- seq_len(20)
dataset <- data.frame(x=i,y=.5+1.4*i+sin(i*1.7))
''', {"outcome": "y", "exposure": "x"}, "list(coefficients=unname(coef(fit)),table=mt)")
    x = list(range(1, 21))
    y = [.5 + 1.4*i + math.sin(i*1.7) for i in x]
    mx, my = statistics.mean(x), statistics.mean(y)
    ssx = sum((i-mx)**2 for i in x)
    slope = sum((i-mx)*(j-my) for i, j in zip(x, y)) / ssx
    intercept = my-slope*mx
    residual_var = sum((j-intercept-slope*i)**2 for i, j in zip(x, y))/18
    se = math.sqrt(residual_var/ssx)
    # t(.975, df=18), instead of the large-sample normal quantile.
    critical = 2.10092204024096
    assert probe["coefficients"] == pytest.approx([intercept, slope], abs=1e-10)
    row = next(item for item in probe["table"] if item["变量"] == "x")
    assert row["CI下限"] == pytest.approx(slope-critical*se, abs=5.1e-5)
    assert row["CI上限"] == pytest.approx(slope+critical*se, abs=5.1e-5)


def test_logistic_odds_ratio_matches_analytic_two_by_two(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "regression_glm", '''
dataset <- data.frame(x=rep(c(0,1),each=50), y=c(rep(1,20),rep(0,30),rep(1,30),rep(0,20)))
''', {"outcome": "y", "exposure": "x"}, "list(coefficients=unname(coef(fit)),table=mt)")
    assert probe["coefficients"] == pytest.approx([math.log(2/3), math.log(2.25)], abs=1e-8)
    row = next(item for item in probe["table"] if item["变量"] == "x")
    assert row["效应"] == pytest.approx(2.25)


def test_singular_regression_does_not_publish_valid_inference(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "regression_glm", '''
i <- seq_len(30); dataset <- data.frame(x=i,z=2*i,y=i+sin(i))
''', {"outcome": "y", "exposure": "x", "covariates": ["z"]}, "list(message=res)", expect_success=False)
    assert probe["message"] != "OK"
    assert "秩" in probe["message"] or "不可估计" in probe["message"]


def test_table_one_respects_explicit_continuous_role(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "publication_table", '''
dataset <- data.frame(age=rep(c(40,50,60),4))
''', {"continuous_variables": ["age"]}, "list(continuous=.cont_vars,n_cont=.n_cont,table=.t1)")
    assert probe["continuous"] == "age"
    assert probe["n_cont"] == 1


def test_table_one_reports_missingness_and_observed_group_percentages(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "publication_table", '''
dataset <- data.frame(arm=rep(c("A","B"),each=3),age=c(42,43,NA,55,56,57),sex=c("F",NA,"M","F","M",NA))
''', {"group": "arm", "continuous_variables": ["age"], "categorical_variables": ["sex"]}, "list(table=.t1)")
    table = probe["table"]
    assert next(row for row in table if row["变量"] == "age")["总体"].startswith("50.6")
    assert next(row for row in table if row["变量"] == "sex")["缺失"] == 2
    for row in table:
        if row.get("水平") in {"F", "M"}:
            assert row["组_A"] == "1 (50.0%)"
            assert row["组_B"] == "1 (50.0%)"


def test_group_test_matches_selected_equal_variance_t_method(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "group_tests", '''
q <- qnorm(seq(.05,.95,length.out=20))
dataset <- data.frame(arm=rep(c("A","B"),each=20),y=c(q,.5+1.2*q))
ref <- t.test(y~arm,data=dataset,var.equal=TRUE)
''', {"group": "arm", "continuous_outcomes": ["y"]},
        "list(test=.sk_test_continuous(dataset$y,dataset$arm), reference=ref$p.value)")
    assert probe["test"]["method"] == "t检验"
    assert probe["test"]["p"] == pytest.approx(probe["reference"], abs=1e-12)


def test_categorical_pearson_has_no_unlabelled_yates_correction(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "group_tests", '''
dataset <- data.frame(arm=rep(c("A","B"),each=40),y=c(rep(1,11),rep(0,29),rep(1,21),rep(0,19)))
tab <- table(dataset$y,dataset$arm)
ref <- chisq.test(tab,correct=FALSE)
''', {"group": "arm", "categorical_outcomes": ["y"]},
        "list(test=.sk_test_categorical(dataset$y,dataset$arm),reference=ref$p.value, v=.sk_cramers_v(dataset$y,dataset$arm),ref_v=sqrt(unname(ref$statistic)/sum(tab)))")
    assert probe["test"]["p"] == pytest.approx(probe["reference"], abs=1e-12)
    assert probe["v"] == pytest.approx(probe["ref_v"], abs=1e-12)


def test_sparse_test_uses_expected_cell_counts(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "group_tests", '''
dataset <- data.frame(arm=c(rep("A",10),rep("B",100)),y=c(rep(1,5),rep(0,5),rep(1,10),rep(0,90)))
''', {"group": "arm", "categorical_outcomes": ["y"]}, "list(test=.sk_test_categorical(dataset$y,dataset$arm))")
    assert probe["test"]["method"] == "Fisher精确检验"


def test_paired_data_is_not_silently_analysed_as_independent(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "group_tests", '''
dataset <- data.frame(id=rep(seq_len(20),2),arm=rep(c("A","B"),each=20),y=c(seq_len(20),seq_len(20)+1))
''', {"group": "arm", "continuous_outcomes": ["y"], "paired_marker": "id"}, "list(message=res)", expect_success=False)
    assert "配对" in probe["message"] and probe["message"] != "OK"


@pytest.mark.parametrize("reference,expected", [
    ("0", {"rr": 2, "or": 8/3, "risk1": .4, "risk0": .2, "arr": -.2}),
    ("1", {"rr": .5, "or": 3/8, "risk1": .2, "risk0": .4, "arr": .2}),
])
def test_epi_event_and_reference_direction_matches_manual_risks(backend, tmp_path, reference, expected):
    _, probe = run_method(backend, tmp_path, "epi_measures", '''
dataset <- data.frame(exposed=rep(c(0,1),each=50),y=c(rep(1,10),rep(0,40),rep(1,20),rep(0,30)))
''', {"outcome": "y", "exposure": "exposed", "positive_class": "1", "reference_group": reference},
        "list(rr=rr,or=orv,risk1=risk1,risk0=risk0,arr=arr)")
    assert probe == pytest.approx(expected)


def test_epi_zero_cell_does_not_fabricate_wald_interval(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "epi_measures", '''
dataset <- data.frame(exposed=rep(c(0,1),each=30),y=c(rep(1,10),rep(0,20),rep(0,30)))
''', {"outcome": "y", "exposure": "exposed", "positive_class": "1", "reference_group": "0"},
        "list(message=res)", expect_success=False)
    assert "Wald" in probe["message"] and probe["message"] != "OK"


def test_survival_optional_covariates_and_km_risk_sets(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "survival_km_cox", '''
dataset <- data.frame(t=rep(seq_len(20),2),event=1,arm=rep(c("A","B"),each=20))
dataset$event[c(5,10,25,30)] <- 0
''', {"followup_time": "t", "event": "event", "exposure": "arm"},
        "list(coefficients=unname(coef(fit)), first_survival=summary(km,times=1)$surv, first_risk=summary(km,times=1)$n.risk)")
    assert probe["coefficients"] == pytest.approx(0, abs=1e-12)
    assert probe["first_survival"] == pytest.approx([.95, .95])
    assert probe["first_risk"] == [20, 20]


def test_survival_rejects_unconfirmed_event_codes(backend, tmp_path):
    _, probe = run_method(backend, tmp_path, "survival_km_cox", '''
dataset <- data.frame(t=rep(seq_len(30),2),event=rep(c(1,2),30),arm=rep(c("A","B"),each=30))
''', {"followup_time": "t", "event": "event", "exposure": "arm", "covariates": []},
        "list(message=res)", expect_success=False)
    assert "0/1" in probe["message"]
