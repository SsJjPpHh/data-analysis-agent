"""Uploaded fitted models retain their identity while observation columns bind."""

import json
import subprocess

from tests.science.test_method_numerics import backend  # noqa: F401
from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.preparation import AnalysisPreparer
from r_runtime.inspection import inspection_summary
from r_runtime.protocol import DatasetRef


def test_uploaded_model_bundle_profiles_observations_and_freezes_original_object(backend, tmp_path):
    path = tmp_path / "trained-model.rds"
    script = tmp_path / "make-model.R"
    script.write_text(
        'set.seed(19);d<-data.frame(id=1:80,x=rnorm(80),y=rnorm(80));'
        'm<-stats::lm(y~x,d);'
        'bundle<-list(model=m,data=d[1:20,],training_data=d[21:80,]);'
        f'saveRDS(bundle,{json.dumps(str(path))})', encoding="utf-8",
    )
    created = subprocess.run([backend.rscript_bin, "--vanilla", str(script)], env=backend.runtime_env,
                             capture_output=True, text=True, check=False, timeout=30)
    assert created.returncode == 0, created.stderr
    result = backend.inspect_dataset(DatasetRef("trained-fixture", str(path), "rds", path.name))
    assert result.ok, result.error
    summary = inspection_summary(result)
    assert summary["rows"] == 20
    assert {c["name"] for c in summary["variables"]} == {"id", "x", "y"}
    assert summary["scientific"]["representation"] == "model_bundle"
    assert summary["scientific"]["model_classes"] == "lm"
    profile = dict(summary, dataset_id="trained-fixture", format="rds")
    profile["columns"] = summary["variables"]
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "ml_explainability", profile,
        provided_inputs={"trained_model":"trained-fixture", "features":["x"], "outcome":"y", "subject_id":"id"},
    )
    assert prepared.plan is not None, prepared.questions
    assert prepared.plan.resolved_inputs["trained_model"].value == "trained-fixture"
    assert prepared.plan.resolved_inputs["features"].value == ["x"]


def test_plain_table_cannot_impersonate_a_trained_model_bundle():
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare("ml_explainability", {
        "dataset_id":"ordinary", "format":"rds", "object_kind":"data.frame", "rows":40,
        "columns":[{"name":"x", "semantic_type":"numeric"}],
    })
    assert prepared.plan is None
