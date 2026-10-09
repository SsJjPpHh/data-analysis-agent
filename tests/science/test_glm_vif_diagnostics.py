"""Native R checks for design-column VIF and truthful GLM diagnostics."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from agent.analysis.skeleton_cores import _HELPERS
from r_runtime.protocol import DatasetRef, RRunRequest
from tests.science.test_additional_input_consumption import run_checked
from tests.science.test_method_numerics import backend, run_method  # noqa: F401


DATA = r"""
set.seed(17);n<-120;x<-rnorm(n);z<-.4*x+rnorm(n)
arm<-factor(rep(c("A","B","C"),length.out=n))
y<-1+2*x+.3*z+as.numeric(arm)/2+rnorm(n)
dataset<-data.frame(y=y,x=x,z=z,arm=arm)
"""


def parse_vifs(values):
    if values == []:
        return {}
    return {key: None if value == "NA" else float(value) for key, value in values.items()}


def test_factor_regression_vif_uses_design_columns_and_agrees_with_main_fit(backend, tmp_path):
    result, probe = run_checked(
        backend,
        tmp_path,
        "regression_glm",
        DATA,
        {"outcome": "y", "exposure": "x", "covariates": ["z", "arm"]},
        r"""list(vif=as.list(vifv),max_vif=max_vif,status=.diag_status[["共线性检查"]],note=results_txt,diagnostics=diag_table,coef=unname(coef(fit)),reference_coef=unname(coef(glm(y~x+z+arm,family=gaussian(),data=dataset))))""",
    )
    assert set(probe["vif"]) == {"x", "z", "armB", "armC"}
    assert all(isinstance(value, (int, float)) and value >= 1 for value in probe["vif"].values())
    assert probe["max_vif"] == pytest.approx(max(probe["vif"].values()))
    assert probe["coef"] == pytest.approx(probe["reference_coef"], rel=1e-12)
    assert probe["status"] == "passed"
    assert "设计列VIF" in probe["note"] and "不等同于" in probe["note"]
    artifact = next(item for item in result.tables if item.name == "diagnostic_output_2")
    with Path(artifact.path).open(encoding="utf8", newline="") as source:
        rows = list(csv.DictReader(source))
    value = next(row["结果"] for row in rows if row["指标"] == "最大设计列VIF")
    assert value == f"{probe['max_vif']:.2f}"
    assert f"最大设计列VIF={value}" in probe["note"]


def run_helper(backend, tmp_path, expression):
    # Independent reference: fit an ordinary auxiliary lm to each numeric
    # model.matrix column, using the same fitted rows and an intercept.
    code = (
        DATA
        + _HELPERS
        + expression
        + r"""
vif_values <- .sk_vif(fit)
assessment <- .sk_vif_assessment(vif_values)
mm_ref <- model.matrix(fit);mm_ref <- mm_ref[,attr(mm_ref,"assign")!=0,drop=FALSE]
ref <- setNames(rep(NA_real_,ncol(mm_ref)),colnames(mm_ref))
for (j in seq_len(ncol(mm_ref))) {
  col_ref <- mm_ref[,j]
  if (length(col_ref)<2 || !all(is.finite(col_ref)) || var(col_ref)==0) next
  if (ncol(mm_ref)==1L) {ref[j]<-1;next}
  other_ref <- mm_ref[,-j,drop=FALSE]
  r2_ref <- suppressWarnings(summary(lm(col_ref~other_ref))$r.squared)
  ref[j] <- if (is.finite(r2_ref)) {if (r2_ref>=1) Inf else 1/(1-r2_ref)} else NA_real_
}
format_vif <- function(v) {if (is.na(v)) "NA" else if (is.infinite(v)) "Inf" else sprintf("%.15g",v)}
baa_text("vif_probe",jsonlite::toJSON(list(values=as.list(vapply(vif_values,format_vif,character(1))),reference=as.list(vapply(ref,format_vif,character(1))),status=assessment$status,max_label=assessment$label,note=assessment$note),auto_unbox=TRUE))
"""
    )
    path = tmp_path / "dummy.csv"
    path.write_text("x\n1\n", encoding="utf8")
    result = backend.run_code(
        RRunRequest(
            job_id="glm-design-vif",
            dataset=DatasetRef("glm-vif-fixture", str(path), "csv", path.name),
            code=code,
            artifacts_dir=str(tmp_path / "artifacts"),
            timeout_seconds=30,
        )
    )
    assert result.ok, result.error + "\n" + result.stderr
    return json.loads(Path(next(item.path for item in result.texts if item.name == "vif_probe")).read_text())


@pytest.mark.parametrize(
    "expression,names",
    [
        ("fit<-glm(y~x+z,family=gaussian(),data=dataset)", {"x", "z"}),
        (
            'dataset$arm<-factor(rep(c("A","B"),length.out=n));fit<-glm(y~x+z+arm,family=gaussian(),data=dataset)',
            {"x", "z", "armB"},
        ),
        ("fit<-glm(y~x+z+arm,family=gaussian(),data=dataset)", {"x", "z", "armB", "armC"}),
        ("fit<-glm(y~arm,family=gaussian(),data=dataset)", {"armB", "armC"}),
        ("fit<-glm(y~x,family=gaussian(),data=dataset)", {"x"}),
        (
            'dataset$arm<-factor(rep(c("A","B"),length.out=n));fit<-glm(y~arm,family=gaussian(),data=dataset)',
            {"armB"},
        ),
        (
            "dataset$y<-factor(rep(c(0,1),length.out=n));fit<-glm(y~x+z+arm,family=binomial(),data=dataset)",
            {"x", "z", "armB", "armC"},
        ),
    ],
)
def test_design_vifs_match_independent_auxiliary_lm(backend, tmp_path, expression, names):
    probe = run_helper(backend, tmp_path, expression)
    values = parse_vifs(probe["values"])
    assert set(values) == names
    assert values == pytest.approx(parse_vifs(probe["reference"]), rel=1e-10, abs=1e-12)
    assert probe["status"] == "passed"
    if len(names) == 1:
        assert next(iter(values.values())) == 1


def test_high_collinearity_is_not_capped_and_warns(backend, tmp_path):
    probe = run_helper(
        backend, tmp_path, "dataset$z<-x+rnorm(n,sd=.001);fit<-glm(y~x+z,family=gaussian(),data=dataset)"
    )
    values = parse_vifs(probe["values"])
    assert values == pytest.approx(parse_vifs(probe["reference"]), rel=1e-6)
    assert min(values.values()) > 10000
    assert probe["status"] == "warning"


def test_perfect_collinearity_preserves_infinite_vif_warning(backend, tmp_path):
    probe = run_helper(backend, tmp_path, "dataset$z<-2*x;fit<-glm(y~x+z,family=gaussian(),data=dataset)")
    assert all(math.isinf(value) for value in parse_vifs(probe["values"]).values())
    assert probe["status"] == "warning"
    assert probe["max_label"] == "Inf"
    assert "完全共线" in probe["note"]


@pytest.mark.parametrize(
    "expression",
    [
        "dataset$z<-1;fit<-glm(y~z,family=gaussian(),data=dataset)",
        "fit<-glm(y~1,family=gaussian(),data=dataset)",
    ],
)
def test_constant_or_absent_design_column_is_not_assessed(backend, tmp_path, expression):
    probe = run_helper(backend, tmp_path, expression)
    assert all(value is None for value in parse_vifs(probe["values"]).values())
    assert probe["status"] == "not_assessed"
    assert probe["max_label"] == "NA"
    assert "未评估" in probe["note"]


def test_rank_deficient_glm_keeps_abort_and_high_collinearity_warning(backend, tmp_path):
    _, probe = run_method(
        backend,
        tmp_path,
        "regression_glm",
        DATA + "\ndataset$z<-2*dataset$x",
        {"outcome": "y", "exposure": "x", "covariates": ["z"]},
        r"""list(result=res,vif_infinite=all(is.infinite(vifv)),status=.diag_status[["共线性检查"]],model_table_present=!is.null(.out[["model_table"]]))""",
        expect_success=False,
    )
    assert "模型矩阵秩不足" in probe["result"]
    assert probe["vif_infinite"]
    assert probe["status"] == "warning"
    assert not probe["model_table_present"]
