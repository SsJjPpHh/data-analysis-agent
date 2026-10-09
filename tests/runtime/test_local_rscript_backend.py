import json
import os
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from r_runtime.protocol import DatasetRef, RRunRequest


@pytest.fixture(autouse=True)
def _fake_r_process_has_no_os_sandbox(monkeypatch):
    # These tests use Python executables as R process doubles. OS isolation is
    # exercised with actual R in test_r_job_isolation and research smoke tests.
    monkeypatch.setattr(LocalRscriptBackend, "_analysis_command", lambda self, request, **kw: [
        self.rscript_bin, "--vanilla", str(kw["output_dir"] / "analysis.R"),
    ])


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _dataset_ref(tmp_path):
    dataset_path = tmp_path / "data.csv"
    dataset_path.write_text("x\n1\n", encoding="utf-8")
    return DatasetRef(dataset_id="ds1", path=str(dataset_path), format="csv")


def _run_request(tmp_path, code="print(1)", timeout_seconds=10):
    return RRunRequest(
        job_id="job1",
        dataset=_dataset_ref(tmp_path),
        code=code,
        artifacts_dir=str(tmp_path / "out"),
        timeout_seconds=timeout_seconds,
    )


def test_local_backend_invokes_rscript_and_reads_manifest(tmp_path):
    fake = _write_executable(
        tmp_path / "fake-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "table_path = out / 'table.csv'\n"
        "table_path.write_text('x\\n1\\n', encoding='utf-8')\n"
        "manifest = {\n"
        "    'tables': [{\n"
        "        'name': 'table',\n"
        "        'path': str(table_path),\n"
        "        'kind': 'table',\n"
        "        'mime_type': 'text/csv',\n"
        "    }],\n"
        "    'figures': [],\n"
        "    'texts': [],\n"
        "    'artifacts': [],\n"
        "    'warnings': [],\n"
        "}\n"
        "(out / 'artifacts.json').write_text(json.dumps(manifest), encoding='utf-8')\n"
        "print('ok from fake rscript')\n",
    )
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path, code="print(summary(dataset))"))

    assert result.ok is True
    assert "ok from fake rscript" in result.stdout
    assert result.tables[0].name == "table"
    assert result.tables[0].kind == "table"
    assert result.tables[0].mime_type == "text/csv"


def test_local_backend_always_uses_vanilla_and_controlled_environment(monkeypatch, tmp_path):
    args_path = tmp_path / "args.json"
    env_path = tmp_path / "env.json"
    fake = _write_executable(
        tmp_path / "private-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        f"pathlib.Path({str(env_path)!r}).write_text(json.dumps({{'R_HOME': os.environ.get('R_HOME'), 'R_LIBS_USER': os.environ.get('R_LIBS_USER'), 'R_PROFILE_USER': os.environ.get('R_PROFILE_USER')}}))\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'artifacts.json').write_text(json.dumps({'tables': [], 'figures': [], 'texts': [], 'artifacts': [], 'warnings': []}))\n",
    )
    backend = LocalRscriptBackend(
        rscript_bin=str(fake),
        helper_path=tmp_path / "missing.R",
        runtime_env={
            "PATH": os.environ["PATH"],
            "R_HOME": "/private/R",
            "R_LIBS_USER": "/private/library",
        },
    )
    monkeypatch.setenv("R_PROFILE_USER", "/host/.Rprofile")

    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is True
    assert json.loads(args_path.read_text(encoding="utf-8"))[0] == "--vanilla"
    assert json.loads(env_path.read_text(encoding="utf-8")) == {
        "R_HOME": "/private/R",
        "R_LIBS_USER": "/private/library",
        "R_PROFILE_USER": None,
    }


def test_local_backend_reads_prior_checkpoint_and_promotes_current_output(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "checkpoint_prior.rds").write_bytes(b"prior")
    fake = _write_executable(
        tmp_path / "checkpoint-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "checkpoints = pathlib.Path(os.environ['BAA_CHECKPOINT_DIR'])\n"
        "assert (checkpoints / 'checkpoint_prior.rds').read_bytes() == b'prior'\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "current = out / 'checkpoint_current.rds'\n"
        "current.write_bytes(b'current')\n"
        "manifest = {'tables': [], 'figures': [], 'texts': [], 'models': [], 'artifacts': [{\n"
        "    'name': 'checkpoint_current', 'path': str(current), 'kind': 'checkpoint',\n"
        "    'mime_type': 'application/x-r-rds', 'metadata': {'role': 'workflow_checkpoint'}\n"
        "}], 'warnings': []}\n"
        "(out / 'artifacts.json').write_text(json.dumps(manifest), encoding='utf-8')\n",
    )
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    request = RRunRequest(
        job_id="checkpoint-success",
        dataset=_dataset_ref(tmp_path),
        code="checkpoint",
        artifacts_dir=str(app_data / "outputs" / "research" / "session-one" / "run-one"),
        timeout_seconds=10,
        checkpoint_dir=str(checkpoint_dir),
    )

    result = backend.run_code(request)

    source = Path(request.artifacts_dir) / "checkpoint_current.rds"
    assert result.ok is True
    assert source.read_bytes() == b"current"
    assert (checkpoint_dir / source.name).read_bytes() == b"current"


def test_local_backend_promotes_explicit_role_checkpoint_from_partial_run(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    fake = _write_executable(
        tmp_path / "partial-checkpoint-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "current = out / 'checkpoint_partial.rds'\n"
        "current.write_bytes(b'partial')\n"
        "manifest = {'tables': [], 'figures': [], 'texts': [], 'models': [], 'artifacts': [{\n"
        "    'name': 'checkpoint_partial', 'path': str(current),\n"
        "    'kind': 'scientific_object', 'role': 'checkpoint'\n"
        "}], 'warnings': []}\n"
        "(out / 'artifacts.json').write_text(json.dumps(manifest), encoding='utf-8')\n"
        "raise SystemExit(7)\n",
    )
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    request = RRunRequest(
        job_id="checkpoint-partial",
        dataset=_dataset_ref(tmp_path),
        code="checkpoint then fail",
        artifacts_dir=str(app_data / "outputs" / "research" / "session-one" / "run-partial"),
        timeout_seconds=10,
        checkpoint_dir=str(checkpoint_dir),
    )

    result = backend.run_code(request)

    assert result.ok is False
    assert result.artifacts
    assert (checkpoint_dir / "checkpoint_partial.rds").read_bytes() == b"partial"


def test_local_backend_discovers_unregistered_output_artifacts(tmp_path):
    fake = _write_executable(
        tmp_path / "manual-artifacts-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'manual_table.csv').write_text('x\\n1\\n', encoding='utf-8')\n"
        "(out / 'manual_plot.png').write_bytes(b'png-bytes')\n"
        "(out / 'manual_note.txt').write_text('model note', encoding='utf-8')\n"
        "(out / 'artifacts.json').write_text(json.dumps({\n"
        "    'tables': [], 'figures': [], 'texts': [], 'artifacts': [], 'warnings': []\n"
        "}), encoding='utf-8')\n",
    )
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")

    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is True
    assert [item.name for item in result.tables] == ["manual_table"]
    assert [item.name for item in result.figures] == ["manual_plot"]
    assert [item.name for item in result.texts] == ["manual_note"]
    assert any("Discovered 3 output artifact" in item for item in result.warnings)


def test_local_backend_does_not_rediscover_registered_plot_companions(tmp_path):
    fake = _write_executable(
        tmp_path / "registered-plot-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "png = out / 'model_plot.png'\n"
        "png.write_bytes(b'png-bytes')\n"
        "(out / 'model_plot.pdf').write_bytes(b'pdf-bytes')\n"
        "(out / 'model_plot.tiff').write_bytes(b'tiff-bytes')\n"
        "(out / 'artifacts.json').write_text(json.dumps({\n"
        "    'tables': [],\n"
        "    'figures': [{\n"
        "        'name': 'model_plot',\n"
        "        'path': str(png),\n"
        "        'kind': 'figure',\n"
        "        'mime_type': 'image/png',\n"
        "    }],\n"
        "    'texts': [], 'artifacts': [], 'warnings': []\n"
        "}), encoding='utf-8')\n",
    )
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")

    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is True
    assert [(item.name, item.mime_type) for item in result.figures] == [("model_plot", "image/png")]
    assert not any("Discovered" in warning for warning in result.warnings)


def test_local_backend_resolves_relative_artifacts_dir(tmp_path, monkeypatch):
    fake = _write_executable(
        tmp_path / "fake-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "assert out.is_absolute()\n"
        "(out / 'artifacts.json').write_text(json.dumps({\n"
        "    'tables': [], 'figures': [], 'texts': [], 'artifacts': [], 'warnings': []\n"
        "}), encoding='utf-8')\n",
    )
    monkeypatch.chdir(tmp_path)
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    request = RRunRequest(
        job_id="job1",
        dataset=_dataset_ref(tmp_path),
        code="print(1)",
        artifacts_dir="relative-out",
        timeout_seconds=10,
    )

    result = backend.run_code(request)

    assert result.ok is True
    assert (tmp_path / "relative-out" / "analysis.R").is_file()


def test_local_backend_returns_error_on_timeout(tmp_path):
    fake = _write_executable(
        tmp_path / "slow-rscript",
        "#!/usr/bin/env python3\nimport time\ntime.sleep(2)\n",
    )
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path, timeout_seconds=1))

    assert result.ok is False
    assert "timed out" in result.error.lower()
    assert result.code == "print(1)"


def test_local_backend_returns_error_on_invalid_manifest_shape(tmp_path):
    fake = _write_executable(
        tmp_path / "bad-manifest-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'artifacts.json').write_text(json.dumps({'tables': {'name': 'bad'}}), encoding='utf-8')\n",
    )
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is False
    assert "invalid artifact manifest" in result.error.lower()


def test_local_backend_warns_when_manifest_is_missing(tmp_path):
    fake = _write_executable(
        tmp_path / "no-manifest-rscript",
        "#!/usr/bin/env python3\nprint('completed without manifest')\n",
    )

    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is True
    assert "completed without manifest" in result.stdout
    assert result.warnings[0] == "artifact manifest was not created"
    assert any("没有生成任何" in warning for warning in result.warnings)


def test_local_backend_does_not_reuse_stale_manifest(tmp_path):
    fake = _write_executable(
        tmp_path / "no-current-manifest-rscript",
        "#!/usr/bin/env python3\nprint('completed without current manifest')\n",
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    stale_table = out_dir / "old.csv"
    stale_table.write_text("old\n1\n", encoding="utf-8")
    (out_dir / "artifacts.json").write_text(
        "{"
        '"tables": [{"name": "old", "path": "%s", "kind": "table"}], '
        '"figures": [], '
        '"texts": [], '
        '"artifacts": [], '
        '"warnings": ["old warning"]'
        "}" % stale_table,
        encoding="utf-8",
    )

    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is True
    assert result.tables == []
    assert result.warnings[0] == "artifact manifest was not created"
    assert any("没有生成任何" in warning for warning in result.warnings)
    assert "completed without current manifest" in result.stdout


def test_local_backend_returns_error_on_malformed_json_manifest(tmp_path):
    fake = _write_executable(
        tmp_path / "malformed-json-rscript",
        "#!/usr/bin/env python3\n"
        "import os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'artifacts.json').write_text('{bad json', encoding='utf-8')\n",
    )

    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    result = backend.run_code(_run_request(tmp_path))

    assert result.ok is False
    assert "artifact manifest could not be read" in result.error.lower()
    assert result.warnings == ["artifact manifest could not be read"]


def test_generated_analysis_script_uses_conditional_namespaces_and_wrapper(tmp_path):
    fake = _write_executable(
        tmp_path / "fake-rscript",
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['BAA_OUTPUT_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'artifacts.json').write_text(json.dumps({\n"
        "    'tables': [], 'figures': [], 'texts': [], 'artifacts': [], 'warnings': []\n"
        "}), encoding='utf-8')\n",
    )

    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "helpers.R")
    result = backend.run_code(_run_request(tmp_path, code="print(1)"))
    script = (tmp_path / "out" / "analysis.R").read_text(encoding="utf-8")

    assert result.ok is True
    assert "library(" not in script
    assert 'options(baa.dataset_load_profile = "analysis")' in script
    assert ".baa_capture_warning <- function(w)" in script
    assert "warning = .baa_capture_warning" in script
    assert script.index("tryCatch({") < script.index("withCallingHandlers({")
    assert script.index("withCallingHandlers({") < script.index("source(")
    assert script.index("source(") < script.index("dataset_bundle <- baa_read_scientific_bundle")
    assert script.index("dataset_bundle <- baa_read_scientific_bundle") < script.index("print(1)")


def test_generated_local_inspection_script_uses_bounded_load_profile(tmp_path):
    backend = LocalRscriptBackend(helper_path=tmp_path / "helpers.R")
    request = _run_request(tmp_path, code=DATASET_PROFILE_R_CODE)

    script = backend._build_script(request)

    assert 'options(baa.dataset_load_profile = "inspection")' in script
    assert "library(" not in script


def test_local_backend_projects_adaptive_resources_into_r_process_environment(monkeypatch):
    monkeypatch.setenv("BAA_R_WORKERS", "3")
    backend = LocalRscriptBackend(runtime_env={"BAA_R_WORKERS": "3"})

    environment = backend._process_environment(resource_class="extended")

    assert environment["BAA_R_WORKERS"] == "3"
    assert environment["OMP_NUM_THREADS"] == "3"
    assert environment["RCPP_PARALLEL_NUM_THREADS"] == "3"
    assert environment["ARROW_NUM_THREADS"] == "3"
    assert int(environment["BAA_R_MEMORY_BUDGET_BYTES"]) >= 0


def test_generated_local_script_activates_shared_parallel_runtime(tmp_path):
    backend = LocalRscriptBackend(helper_path=tmp_path / "helpers.R")

    script = backend._build_script(_run_request(tmp_path, code="print(baa_workers())"))

    assert 'options(mc.cores = .baa_configured_workers)' in script
    assert 'options(baa.memory_budget_bytes = .baa_memory_budget_bytes)' in script
    assert "BAA_JOB_PATH" in script
    assert "download.file <- function" in script
    assert "system <- function" in script


def test_local_backend_routes_referenced_packages_to_installer():
    backend = LocalRscriptBackend()

    packages = backend._referenced_packages(
        "library(ggplot2); require('survival'); readxl::read_excel('x.xlsx'); "
        'requireNamespace(package = "arrow")'
    )

    assert packages == ("arrow", "ggplot2", "readxl", "survival")


def test_local_backend_package_extraction_ignores_comments_and_narrative_strings():
    backend = LocalRscriptBackend()

    packages = backend._referenced_packages(
        '# fake::call(); library("fake")\n'
        'baa_text("mention other::function without running it")\n'
        'loadNamespace("arrow"); packageVersion("survival"); stats::lm(y ~ x)'
    )

    assert packages == ("arrow", "stats", "survival")


def test_local_backend_reuses_successful_package_preparation():
    prepared = []
    backend = LocalRscriptBackend(
        package_installer=lambda packages: prepared.append(tuple(packages))
    )

    assert backend._prepare_packages(("jsonlite", "readr")) == ""
    assert backend._prepare_packages(("jsonlite", "readr")) == ""
    assert backend._prepare_packages(("jsonlite", "ggplot2")) == ""

    assert prepared == [("jsonlite", "readr"), ("ggplot2",)]


def test_local_backend_reuses_ready_package_evidence_without_starting_r(monkeypatch):
    backend = LocalRscriptBackend(package_installer=lambda packages: None)
    backend._readiness_verified_packages.update(("jsonlite", "readr"))
    monkeypatch.setattr(
        backend,
        "_run_script_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected R start")),
    )

    payload = backend.package_readiness(("jsonlite", "readr"))

    assert payload["ok"] is True
    assert payload["cached"] is True
    assert [item["package"] for item in payload["packages"]] == ["jsonlite", "readr"]


def test_package_readiness_uses_local_r_adapter(tmp_path):
    fake = _write_executable(
        tmp_path / "package-readiness-rscript",
        "#!/usr/bin/env python3\n"
        "print('{\"ok\": true, \"package_count\": 1, \"packages\": [{\"package\": \"mzR\", \"status\": \"warning\"}]}')\n",
    )
    prepared = []
    backend = LocalRscriptBackend(
        rscript_bin=str(fake),
        package_installer=lambda packages: prepared.append(tuple(packages)),
    )

    payload = backend.package_readiness(("mzR",))

    assert payload["ok"] is True
    assert payload["packages"][0]["status"] == "warning"
    assert prepared == []


def test_package_inventory_prepares_jsonlite(tmp_path):
    fake = _write_executable(
        tmp_path / "package-list-rscript",
        "#!/usr/bin/env python3\n"
        "print('{\"ok\": true, \"r_version\": \"4.6.0\", \"packages\": []}')\n",
    )
    prepared = []
    backend = LocalRscriptBackend(
        rscript_bin=str(fake),
        package_installer=lambda packages: prepared.append(tuple(packages)),
    )

    payload = backend.list_packages()

    assert payload["ok"] is True
    assert prepared == [("jsonlite",)]


def test_dataset_profile_prepares_only_the_matching_loader_packages(tmp_path):
    backend = LocalRscriptBackend()
    request = _run_request(tmp_path, code=DATASET_PROFILE_R_CODE)

    packages = backend._required_packages(request, Path(request.dataset.path))

    assert packages == ("jsonlite", "readr")
    assert "SingleCellExperiment" not in packages


def test_spreadsheet_analysis_prepares_loader_and_explicit_code_packages(tmp_path):
    dataset_path = tmp_path / "data.xlsx"
    dataset_path.write_bytes(b"spreadsheet")
    dataset = DatasetRef(dataset_id="ds1", path=str(dataset_path), format="xlsx")
    request = RRunRequest(
        job_id="job1",
        dataset=dataset,
        code="survival::coxph(formula, data = dataset)",
        artifacts_dir=str(tmp_path / "out"),
    )
    backend = LocalRscriptBackend()

    packages = backend._required_packages(request, dataset_path)

    assert packages == ("jsonlite", "readxl", "survival")


def test_large_csv_prepares_lazy_loader_packages(monkeypatch, tmp_path):
    dataset_path = tmp_path / "large.csv"
    dataset_path.write_text("x\n1\n", encoding="utf-8")
    dataset = DatasetRef(dataset_id="ds1", path=str(dataset_path), format="csv")
    request = RRunRequest(
        job_id="job1",
        dataset=dataset,
        code="print(names(dataset))",
        artifacts_dir=str(tmp_path / "out"),
    )
    monkeypatch.setenv("BAA_LAZY_DATASET_THRESHOLD_BYTES", "1")
    backend = LocalRscriptBackend()

    packages = backend._required_packages(request, dataset_path)

    assert {"DBI", "dbplyr", "dplyr", "duckdb", "jsonlite", "readr"} <= set(packages)


def test_generated_local_script_preserves_every_scientific_bundle_asset(tmp_path):
    from r_runtime.protocol import DatasetAssetRef, DatasetRef, RRunRequest

    matrix = tmp_path / "matrix.mtx"
    features = tmp_path / "features.tsv"
    barcodes = tmp_path / "barcodes.tsv"
    matrix.write_text("%%MatrixMarket matrix coordinate integer general\n", encoding="utf-8")
    features.write_text("gene-1\n", encoding="utf-8")
    barcodes.write_text("cell-1\n", encoding="utf-8")
    dataset = DatasetRef(
        dataset_id="bundle",
        path=str(matrix),
        format="mtx",
        name="10x bundle",
        assets=(
            DatasetAssetRef(str(matrix), "mtx", "primary", "matrix.mtx", "file", "matrix.mtx"),
            DatasetAssetRef(str(features), "tsv", "features", "features.tsv", "file", "features.tsv"),
            DatasetAssetRef(str(barcodes), "tsv", "barcodes", "barcodes.tsv", "file", "barcodes.tsv"),
        ),
    )
    request = RRunRequest(
        job_id="bundle-script",
        dataset=dataset,
        code="print(dataset_assets)",
        artifacts_dir=str(tmp_path / "out"),
    )

    script = LocalRscriptBackend(helper_path=tmp_path / "helpers.R")._build_script(request)

    assert "baa_read_scientific_bundle(dataset_manifest)" in script
    assert str(matrix) in script
    assert str(features) in script
    assert str(barcodes) in script
    assert "dataset_assets <- dataset_bundle$assets" in script


def test_generated_local_script_keeps_unicode_paths_as_utf8_literals(tmp_path):
    dataset_path = tmp_path / "中文资料" / "样本数据.csv"
    dataset_path.parent.mkdir()
    dataset_path.write_text("分类变量,数值\n1,42\n", encoding="utf-8")
    dataset = DatasetRef(
        dataset_id="unicode-path",
        path=str(dataset_path),
        format="csv",
        name="样本数据.csv",
    )
    request = RRunRequest(
        job_id="unicode-script",
        dataset=dataset,
        code='print(names(dataset))',
        artifacts_dir=str(tmp_path / "out"),
    )

    script = LocalRscriptBackend(helper_path=tmp_path / "helpers.R")._build_script(request)

    assert "中文资料" in script
    assert "样本数据.csv" in script
    assert "\\u4e2d" not in script
    assert "\\u6837" not in script
