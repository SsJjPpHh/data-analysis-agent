import csv
import os
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.protocol import DatasetRef, RRunRequest


host_environment = HostREnvironment.locate()
try:
    host_verified = host_environment.prepare()
    rscript_bin = str(host_verified.rscript)
    runtime_env = host_environment.process_environment(verified=host_verified)
except REnvironmentError as exc:
    if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
        pytest.fail(f"Required local R backend could not be prepared: {exc}", pytrace=False)
    rscript_bin = None
    runtime_env = {}

local_rscript_required = pytest.mark.skipif(
    rscript_bin is None,
    reason="Local R is unavailable",
)


@local_rscript_required
def test_local_rscript_research_smoke(tmp_path):
    data_path = tmp_path / "clinical.csv"
    data_path.write_text(
        "age,outcome\n42,0\n55,1\n61,1\n37,0\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    result = backend.run_code(
        RRunRequest(
            job_id="research-r-smoke",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                name="clinical.csv",
            ),
            code="""
summary_table <- data.frame(
  n = nrow(dataset),
  mean_age = mean(dataset$age),
  outcome_rate = mean(dataset$outcome)
)
baa_table("summary_table", summary_table)

fake_flextable <- list(body = list(dataset = data.frame(term = c("age"), estimate = c(0.42))))
class(fake_flextable) <- "flextable"
baa_table("fake_flextable_table", fake_flextable)

p <- ggplot(dataset, aes(x = age, y = outcome)) +
  geom_point() +
  labs(x = "Age", y = "Outcome")
baa_plot("age_outcome", p)
""",
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    result_payload = result.to_dict()

    assert result.tables, result_payload
    table = result.tables[0]
    table_path = Path(table.path).resolve()
    assert table.name == "summary_table"
    assert table.kind == "table"
    assert table.mime_type == "text/csv"
    assert table_path.is_file()
    assert table_path.is_relative_to(out_dir.resolve())
    table_content = table_path.read_text(encoding="utf-8")
    assert "n,mean_age,outcome_rate" in table_content
    assert "4,48.75,0.5" in table_content
    assert any(item.name == "fake_flextable_table" for item in result.tables)

    assert result.figures, result_payload
    figure = result.figures[0]
    figure_path = Path(figure.path).resolve()
    assert figure.name == "age_outcome"
    assert figure.kind == "figure"
    assert figure.mime_type == "image/png"
    assert figure_path.is_file()
    assert figure_path.is_relative_to(out_dir.resolve())
    assert figure_path.stat().st_size > 0
    assert figure_path.with_suffix(".pdf").is_file()
    assert figure_path.with_suffix(".pdf").stat().st_size > 0
    assert figure_path.with_suffix(".tiff").is_file()
    assert figure_path.with_suffix(".tiff").stat().st_size > 0


@local_rscript_required
def test_overall_table_and_exported_rng_state_replay(tmp_path):
    import io
    import json
    import subprocess
    import zipfile
    from types import SimpleNamespace
    from agent.tools.research.r_tools import RToolsMixin
    from data.reproducibility import write_reproducibility_bundle

    data_path = tmp_path / "small.csv"
    data_path.write_text("age,sex\n42,F\n55,M\n61,F\n37,M\n", encoding="utf-8")
    output = tmp_path / "run"
    plan = SimpleNamespace(dataset_id="small", resolved_inputs={
        "variables": {"kind": "columns", "value": ["age", "sex"]},
    })
    analysis_code = 'baa_table_one("overall", by = NULL, vars = baa_plan_input("variables"))\nbaa_table("random_draws", data.frame(value = runif(3)))'
    executed_code = "\n".join((
        RToolsMixin._worker_compatibility_prelude(),
        RToolsMixin._frozen_input_prelude(plan),
        RToolsMixin._frozen_user_code_block(analysis_code, plan),
        RToolsMixin._frozen_input_epilogue(plan),
    ))
    request = RRunRequest(
        job_id="overall-replay", dataset=DatasetRef(dataset_id="small", path=str(data_path), format="csv"),
        code=executed_code,
        artifacts_dir=str(output), timeout_seconds=30,
    )
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(request)
    assert result.ok, result.error + "\n" + result.stderr
    overall_path = Path(next(a.path for a in result.tables if a.name == "overall"))
    header = next(csv.reader(overall_path.open(encoding="utf-8")))
    assert any("Overall" in name for name in header)
    assert not any("SMD" in name or "P value" in name for name in header)
    assert result.reproducibility["status"] == "succeeded"
    assert result.reproducibility["rng_before"]
    assert result.reproducibility["packages"]["jsonlite"]

    state = {"analysis_turns": [{"turn_id": "one", "execution_refs": [{
        "job_id": request.job_id, "status": "succeeded", "code": result.code,
        "reproducibility": result.reproducibility,
        "analysis_plan": {"plan_id": "plan-one", "dataset_id": "small", "resolved_inputs": plan.resolved_inputs},
    }]}]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_reproducibility_bundle(archive, state)
    with zipfile.ZipFile(buffer) as archive:
        archive.extractall(tmp_path / "export")
    root = tmp_path / "export" / "reproducibility"
    manifest = json.loads((root / "step_001" / "input_manifest.example.json").read_text())
    manifest["path"] = str(data_path)
    input_file = tmp_path / "input_manifest.json"
    input_file.write_text(json.dumps(manifest), encoding="utf-8")
    replay = subprocess.run(
        [rscript_bin, "--vanilla", str(root / "replay.R"), str(root / "step_001"), str(input_file)],
        cwd=root, env=runtime_env, text=True, capture_output=True, timeout=30,
    )
    assert replay.returncode == 0, replay.stderr
    assert (root / "step_001" / "replay_output" / "random_draws.csv").read_text() == (output / "random_draws.csv").read_text()


@local_rscript_required
def test_single_group_table_one_exports_descriptive_results_without_comparison_claims(tmp_path):
    data = tmp_path / "single.csv"
    data.write_text("arm,age\nA,40\nA,50\nA,60\n", encoding="utf-8")
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(RRunRequest(
        job_id="single-group", dataset=DatasetRef(dataset_id="single", path=str(data), format="csv"),
        code='baa_table_one("baseline", by="arm", vars="age", continuous="age", continuous_summary="mean_sd")',
        artifacts_dir=str(tmp_path / "output"), timeout_seconds=30,
    ))
    assert result.ok, result.error
    artifact = next(item for item in result.tables if item.name == "baseline")
    with Path(artifact.path).open(encoding="utf-8") as file:
        table = list(csv.DictReader(file))
    assert not any("P value" in name or "SMD" in name for name in table[0])
    overall = next(name for name in table[0] if name.startswith("Overall"))
    assert "50" in next(row[overall] for row in table if row["Variable"] == "age")
    assert "总体描述" in artifact.metadata["acceptance_evidence"]
    note = Path(next(item.path for item in result.texts if item.name == "baseline_note")).read_text()
    assert "Descriptive summaries only" in note
    assert "uses t test/ANOVA" not in note


@local_rscript_required
def test_export_replays_exact_helper_and_prior_checkpoint(tmp_path, monkeypatch):
    import io
    import json
    import subprocess
    import zipfile
    from data.reproducibility import write_reproducibility_bundle
    from infrastructure.resources import resource_text

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path.resolve()))
    checkpoint = tmp_path / "outputs" / "research" / "replay" / "checkpoints"
    dataset = tmp_path / "data.csv"
    dataset.write_text("x\n1\n2\n")
    helper = tmp_path / "historical.R"
    helper.write_text(resource_text("r_runtime/helpers/baa_helpers.R") + "\nhistorical_helper_marker <- 41\n")
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env, helper_path=helper)
    first = backend.run_code(RRunRequest(job_id="save", dataset=DatasetRef("replay", str(dataset), "csv"),
        code='baa_checkpoint("state",list(value=17))', artifacts_dir=str(tmp_path / "first"), checkpoint_dir=str(checkpoint)))
    assert first.ok, first.stderr
    code = 'x <- baa_load_checkpoint("state"); baa_table("answer",data.frame(value=x$value+historical_helper_marker,draw=runif(1)))'
    second = backend.run_code(RRunRequest(job_id="load", dataset=DatasetRef("replay", str(dataset), "csv"),
        code=code, artifacts_dir=str(tmp_path / "second"), checkpoint_dir=str(checkpoint)))
    assert second.ok, second.stderr
    helper.write_text("stop('current helper must not replace execution helper')")
    for file in checkpoint.iterdir():
        file.write_bytes(b"changed after execution")
    buffer = io.BytesIO()
    state = {"analysis_turns": [{"execution_refs": [{"code": code, "reproducibility": second.reproducibility}]}]}
    with zipfile.ZipFile(buffer, "w") as archive:
        write_reproducibility_bundle(archive, state)
    with zipfile.ZipFile(buffer) as archive:
        archive.extractall(tmp_path / "export")
    root = tmp_path / "export" / "reproducibility"
    manifest = json.loads((root / "step_001" / "input_manifest.example.json").read_text())
    manifest["path"] = str(dataset)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps(manifest))
    replay = subprocess.run([rscript_bin, "--vanilla", str(root / "replay.R"), str(root / "step_001"), str(inputs)],
        env=runtime_env, capture_output=True, text=True, timeout=30)
    assert replay.returncode == 0, replay.stderr
    assert (root / "step_001" / "replay_output" / "answer.csv").read_bytes() == (tmp_path / "second" / "answer.csv").read_bytes()


@local_rscript_required
def test_local_rscript_runtime_uses_multiple_workers_for_independent_tasks(tmp_path):
    if (os.cpu_count() or 1) < 3:
        pytest.skip("Multicore runtime check requires at least three logical CPUs")

    data_path = tmp_path / "clinical.csv"
    data_path.write_text("value\n1\n2\n3\n4\n", encoding="utf-8")
    out_dir = tmp_path / "parallel-out"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    result = backend.run_code(
        RRunRequest(
            job_id="research-r-parallel-smoke",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                name="clinical.csv",
            ),
            code="""
workers <- baa_workers(tasks = 4L)
pids <- unlist(baa_parallel_lapply(
  seq_len(4L),
  function(index) Sys.getpid(),
  workers = workers,
  seed = 7L
))
baa_table("parallel_runtime", data.frame(
  configured_workers = workers,
  distinct_processes = length(unique(pids))
))
""",
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
            resource_class="standard",
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    with Path(result.tables[0].path).open(encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert int(row["configured_workers"]) >= 2
    assert int(row["distinct_processes"]) >= 2


@local_rscript_required
def test_local_rscript_preserves_model_artifacts_as_first_class_outputs(tmp_path):
    data_path = tmp_path / "model.csv"
    data_path.write_text("x,y\n1,2\n2,4\n3,6\n", encoding="utf-8")
    out_dir = tmp_path / "model-output"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)

    result = backend.run_code(
        RRunRequest(
            job_id="research-model-smoke",
            dataset=DatasetRef(
                dataset_id="model",
                path=str(data_path),
                format="csv",
                name="model.csv",
            ),
            code='fit <- lm(y ~ x, data = dataset); baa_model_summary("fit", fit)',
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    assert len(result.models) == 1
    assert result.models[0].kind == "model"
    assert Path(result.models[0].path).read_text(encoding="utf-8").strip()
    assert all(item.name != "fit" for item in result.texts)


@local_rscript_required
def test_local_rscript_table_one_helper(tmp_path):
    data_path = tmp_path / "clinical.csv"
    data_path.write_text(
        "age,sex,group,outcome\n42,F,A,0\n55,M,B,1\n61,F,B,1\n37,M,A,0\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "table_one"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    result = backend.run_code(
        RRunRequest(
            job_id="table-one-smoke",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                name="clinical.csv",
            ),
            code="""
dataset$group <- factor(dataset$group, levels = c("A", "B"), labels = c("Control", "Treatment"))
dataset$sex <- factor(dataset$sex, levels = c("F", "M"), labels = c("Female", "Male"))
baa_table_one(
  name = "table_one_baseline",
  data = dataset,
  by = "group",
  vars = c("age", "sex", "outcome"),
  categorical = c("sex", "outcome"),
  continuous = c("age"),
  labels = list(age = "Age", sex = "Sex", outcome = "Outcome")
)
""",
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    assert result.tables
    table_path = Path(result.tables[0].path)
    content = table_path.read_text(encoding="utf-8")
    assert "Variable,Level,Overall" in content
    assert "Control (n=2)" in content
    assert "Treatment (n=2)" in content
    assert "P value" in content
    assert "Age,Mean (SD)" in content
    assert result.texts


@local_rscript_required
def test_local_rscript_table_one_reports_max_pairwise_smd_for_multiple_groups(tmp_path):
    data_path = tmp_path / "multigroup.csv"
    data_path.write_text(
        "age,sex,group\n40,F,A\n44,M,A\n52,F,B\n56,M,B\n64,F,C\n68,M,C\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "multigroup-table-one"
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(
        RRunRequest(
            job_id="multigroup-table-one",
            dataset=DatasetRef(
                dataset_id="multigroup",
                path=str(data_path),
                format="csv",
                name="multigroup.csv",
            ),
            code='baa_table_one("table_one", dataset, by = "group", vars = c("age", "sex"))',
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    with Path(result.tables[0].path).open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert "SMD (max pairwise)" in rows[0]
    assert any(row["SMD (max pairwise)"] for row in rows if row["Variable"] != "N")
    assert "SMD" in result.tables[0].metadata["acceptance_evidence"]
    note = Path(result.texts[0].path).read_text(encoding="utf-8")
    assert "Maximum absolute pairwise SMD" in note


@local_rscript_required
def test_local_rscript_table_one_auto_uses_median_iqr_for_skewed_variables(tmp_path):
    data_path = tmp_path / "skewed.csv"
    data_path.write_text(
        "marker,group\n1,A\n1,A\n1,A\n80,A\n2,B\n2,B\n2,B\n120,B\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "skewed-table-one"
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(
        RRunRequest(
            job_id="skewed-table-one",
            dataset=DatasetRef(
                dataset_id="skewed",
                path=str(data_path),
                format="csv",
                name="skewed.csv",
            ),
            code=(
                'baa_table_one("table_one", dataset, by = "group", '
                'vars = "marker", continuous = "marker")'
            ),
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    content = Path(result.tables[0].path).read_text(encoding="utf-8")
    assert "marker,Median (IQR)" in content


@local_rscript_required
def test_local_rscript_table_one_group_counts_do_not_contain_alignment_spaces(tmp_path):
    data_path = tmp_path / "uneven-groups.csv"
    data_path.write_text(
        "value,group\n"
        + "".join(f"{index},A\n" for index in range(1, 1002))
        + "1002,B\n1003,B\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "uneven-table-one"
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(
        RRunRequest(
            job_id="uneven-table-one",
            dataset=DatasetRef(
                dataset_id="uneven",
                path=str(data_path),
                format="csv",
                name="uneven-groups.csv",
            ),
            code='baa_table_one("table_one", dataset, by = "group", vars = "value")',
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    header = Path(result.tables[0].path).read_text(encoding="utf-8").splitlines()[0]
    assert "A (n=1,001)" in header
    assert "B (n=2)" in header


@local_rscript_required
def test_local_rscript_expands_collapsed_table_one_categories(tmp_path):
    data_path = tmp_path / "clinical.csv"
    data_path.write_text("age,group\n42,A\n55,B\n", encoding="utf-8")

    out_dir = tmp_path / "collapsed_table_one"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    result = backend.run_code(
        RRunRequest(
            job_id="collapsed-table-one",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                name="clinical.csv",
            ),
            code="""
table1 <- data.frame(
  Variable = c("age", "gender", "edu"),
  Level = c("", "", ""),
  Overall = c("48.5 ± 9.2", "Male: 1 (50.0%); Female: 1 (50.0%)", "Low: 1 (50.0%); High: 1 (50.0%)"),
  A = c("42.0 ± NA", "Male: 1 (100.0%); Female: 0 (0.0%)", "Low: 1 (100.0%); High: 0 (0.0%)"),
  B = c("55.0 ± NA", "Male: 0 (0.0%); Female: 1 (100.0%)", "Low: 0 (0.0%); High: 1 (100.0%)"),
  P_value = c("0.200", "0.300", "0.400"),
  check.names = FALSE
)
baa_table("table1_by_group", table1, metadata = list(role = "baseline_table", title = "Table 1 by group"))
""",
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    table_path = Path(result.tables[0].path)
    content = table_path.read_text(encoding="utf-8")
    assert "Variable,Level,Overall,A,B,P_value" in content
    assert "gender,Male,1 (50.0%),1 (100.0%),0 (0.0%),0.300" in content
    assert ",Female,1 (50.0%),0 (0.0%),1 (100.0%)," in content
    assert "edu,Low,1 (50.0%),1 (100.0%),0 (0.0%),0.400" in content


@local_rscript_required
def test_local_rscript_uses_artifact_dir_as_working_directory(tmp_path):
    data_path = tmp_path / "clinical.csv"
    data_path.write_text("age,outcome\n42,0\n55,1\n", encoding="utf-8")

    out_dir = tmp_path / "cwd_output"
    backend = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env)
    result = backend.run_code(
        RRunRequest(
            job_id="cwd-smoke",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                name="clinical.csv",
            ),
            code="""
writeLines(normalizePath(getwd(), winslash = "/", mustWork = TRUE), "cwd.txt")
baa_text("cwd_report", getwd())
""",
            artifacts_dir=str(out_dir),
            timeout_seconds=60,
        )
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    cwd_file = out_dir / "cwd.txt"
    assert cwd_file.is_file()
    assert Path(cwd_file.read_text(encoding="utf-8").strip()).resolve() == out_dir.resolve()
