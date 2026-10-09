import pytest

from r_runtime.diagnostics import diagnose_r_failure
from r_runtime.protocol import (
    DatasetAssetRef,
    DatasetRef,
    RArtifact,
    RRunRequest,
    RRunResult,
    promote_checkpoint_artifacts,
    validated_checkpoint_dir,
)


def test_run_request_serializes():
    request = RRunRequest(
        job_id="job1",
        dataset=DatasetRef(dataset_id="ds1", path="/tmp/data.csv", format="csv"),
        code="summary(dataset)",
        artifacts_dir="/tmp/out",
        timeout_seconds=30,
        checkpoint_dir="/internal/checkpoints",
    )

    data = request.to_dict()
    assert data["dataset"]["dataset_id"] == "ds1"
    assert data["code"] == "summary(dataset)"
    assert data["resource_class"] == "standard"
    assert "checkpoint_dir" not in data


def test_run_request_rejects_unknown_resource_class():
    with pytest.raises(ValueError, match="resource_class"):
        RRunRequest(
            job_id="job1",
            dataset=DatasetRef(dataset_id="ds1", path="/tmp/data.csv", format="csv"),
            code="summary(dataset)",
            artifacts_dir="/tmp/out",
            resource_class="unbounded",
        )


def test_checkpoint_dir_is_absolute_below_research_outputs_and_rejects_symlinks(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    root = app_data / "outputs" / "research"
    valid = root / "session-one" / "checkpoints"
    valid.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "session-one" / "linked").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))

    assert validated_checkpoint_dir(str(valid)) == valid.resolve()
    with pytest.raises(ValueError, match="absolute"):
        validated_checkpoint_dir("relative/checkpoints")
    with pytest.raises(ValueError, match="outputs/research"):
        validated_checkpoint_dir(str(outside))
    with pytest.raises(ValueError, match="symbolic links"):
        validated_checkpoint_dir(str(root / "session-one" / "linked" / "checkpoints"))
    with pytest.raises(ValueError, match="path traversal"):
        validated_checkpoint_dir(str(root / "session-one" / ".." / "checkpoints"))


def test_checkpoint_promotion_is_atomic_and_keeps_the_run_output(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    output = app_data / "outputs" / "research" / "session-one" / "run-one"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    output.mkdir(parents=True)
    checkpoint_dir.mkdir()
    source = output / "checkpoint_model.rds"
    source.write_bytes(b"new-checkpoint")
    destination = checkpoint_dir / source.name
    destination.write_bytes(b"old-checkpoint")
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))

    warnings = promote_checkpoint_artifacts(
        {
            "artifacts": [
                {
                    "name": "checkpoint_model",
                    "path": str(source),
                    "kind": "checkpoint",
                }
            ]
        },
        output_dir=output,
        checkpoint_dir=validated_checkpoint_dir(str(checkpoint_dir)),
        max_files=10,
        max_bytes=1024,
    )

    assert warnings == []
    assert source.read_bytes() == b"new-checkpoint"
    assert destination.read_bytes() == b"new-checkpoint"
    assert not list(checkpoint_dir.glob(".baa-checkpoint-*.tmp"))


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        (
            {"path": "OUTSIDE", "kind": "checkpoint"},
            "outside the run output",
        ),
        (
            {"path": "SYMLINK", "kind": "checkpoint"},
            "symbolic links",
        ),
        (
            {"path": "NOT_RDS", "role": "checkpoint"},
            "ordinary .rds",
        ),
    ],
)
def test_checkpoint_promotion_rejects_untrusted_manifest_paths(artifact, message, monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    output = app_data / "outputs" / "research" / "session-one" / "run-one"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    output.mkdir(parents=True)
    outside = tmp_path / "outside.rds"
    outside.write_bytes(b"outside")
    symlink = output / "linked.rds"
    symlink.symlink_to(outside)
    not_rds = output / "checkpoint.txt"
    not_rds.write_bytes(b"not-rds")
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))
    mapped = dict(artifact)
    mapped["path"] = {
        "OUTSIDE": str(outside),
        "SYMLINK": str(symlink),
        "NOT_RDS": str(not_rds),
    }[mapped["path"]]

    warnings = promote_checkpoint_artifacts(
        {"artifacts": [mapped]},
        output_dir=output,
        checkpoint_dir=validated_checkpoint_dir(str(checkpoint_dir)),
        max_files=10,
        max_bytes=1024,
    )

    assert len(warnings) == 1
    assert message in warnings[0]
    assert not checkpoint_dir.exists()


def test_unregistered_rds_output_is_not_promoted(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    output = app_data / "outputs" / "research" / "session-one" / "run-one"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    output.mkdir(parents=True)
    source = output / "model.rds"
    source.write_bytes(b"ordinary-model")
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))

    warnings = promote_checkpoint_artifacts(
        {
            "artifacts": [
                {"path": str(source), "kind": "scientific_object"},
                {"path": str(source), "type": "checkpoint"},
            ]
        },
        output_dir=output,
        checkpoint_dir=validated_checkpoint_dir(str(checkpoint_dir)),
        max_files=10,
        max_bytes=1024,
    )
    stale_warnings = promote_checkpoint_artifacts(
        {"artifacts": [{"path": str(source), "kind": "checkpoint"}]},
        output_dir=output,
        checkpoint_dir=validated_checkpoint_dir(str(checkpoint_dir)),
        max_files=10,
        max_bytes=1024,
        excluded_sources={source.resolve()},
    )

    assert warnings == []
    assert "predates the current run" in stale_warnings[0]
    assert not checkpoint_dir.exists()


def test_checkpoint_paths_and_promotion_budgets_are_enforced(monkeypatch, tmp_path):
    app_data = tmp_path / "app-data"
    output = app_data / "outputs" / "research" / "session-one" / "run-one"
    checkpoint_dir = app_data / "outputs" / "research" / "session-one" / "checkpoints"
    output.mkdir(parents=True)
    source = output / "checkpoint_large.rds"
    source.write_bytes(b"12345")
    second_source = output / "checkpoint_second.rds"
    second_source.write_bytes(b"6")
    monkeypatch.setenv("BAA_DATA_DIR", str(app_data))
    trusted_dir = validated_checkpoint_dir(str(checkpoint_dir))

    traversal = promote_checkpoint_artifacts(
        {"artifacts": [{"path": "/baa-output/../escape.rds", "kind": "checkpoint"}]},
        output_dir=output,
        checkpoint_dir=trusted_dir,
        max_files=10,
        max_bytes=100,
        manifest_output_root="/baa-output",
    )
    oversized = promote_checkpoint_artifacts(
        {"artifacts": [{"path": "/baa-output/checkpoint_large.rds", "kind": "checkpoint"}]},
        output_dir=output,
        checkpoint_dir=trusted_dir,
        max_files=10,
        max_bytes=4,
        manifest_output_root="/baa-output",
    )
    too_many = promote_checkpoint_artifacts(
        {
            "artifacts": [
                {"path": "/baa-output/checkpoint_large.rds", "kind": "checkpoint"},
                {"path": "/baa-output/checkpoint_second.rds", "role": "checkpoint"},
            ]
        },
        output_dir=output,
        checkpoint_dir=trusted_dir,
        max_files=1,
        max_bytes=100,
        manifest_output_root="/baa-output",
    )

    assert "traversal" in traversal[0]
    assert "byte budget" in oversized[0]
    assert "file-count budget" in too_many[0]
    assert not checkpoint_dir.exists()


def test_dataset_ref_serializes_a_multi_asset_scientific_bundle():
    reference = DatasetRef(
        dataset_id="omics",
        path="/host/matrix.mtx.gz",
        format="mtx",
        name="10x bundle",
        assets=(
            DatasetAssetRef(
                path="/host/matrix.mtx.gz",
                format="mtx",
                role="primary",
                relative_mount_name="matrix.mtx.gz",
            ),
            DatasetAssetRef(
                path="/host/features.tsv.gz",
                format="tsv",
                role="features",
                relative_mount_name="features.tsv.gz",
            ),
        ),
    )

    payload = reference.to_dict()

    assert payload["assets"][0]["role"] == "primary"
    assert payload["assets"][1]["relative_mount_name"] == "features.tsv.gz"


def test_run_result_from_worker_payload():
    payload = {
        "ok": True,
        "stdout": "done",
        "stderr": "",
        "tables": [{"name": "table1", "path": "/tmp/out/table1.csv", "kind": "table"}],
        "figures": [{"name": "plot1", "path": "/tmp/out/plot1.png", "type": "figure"}],
        "texts": [{"name": "summary", "path": "/tmp/out/summary.txt", "kind": "text"}],
        "models": [{"name": "fit", "path": "/tmp/out/fit.txt", "kind": "model"}],
        "artifacts": [{"name": "session", "path": "/tmp/out/session.json", "kind": "artifact"}],
        "warnings": ["small sample"],
        "error": "",
        "code": "summary(dataset)",
        "execution_stage": "complete",
    }

    result = RRunResult.from_dict(payload)
    assert result.ok is True
    assert result.tables[0].name == "table1"
    assert result.models[0].kind == "model"
    assert result.warnings == ["small sample"]
    assert result.execution_stage == "complete"


def test_run_result_null_list_fields_are_empty():
    result = RRunResult.from_dict(
        {
            "ok": True,
            "tables": None,
            "figures": None,
            "texts": None,
            "models": None,
            "artifacts": None,
            "warnings": None,
        }
    )

    assert result.tables == []
    assert result.figures == []
    assert result.texts == []
    assert result.models == []
    assert result.artifacts == []
    assert result.warnings == []


def test_run_result_null_string_fields_are_empty_strings():
    result = RRunResult.from_dict(
        {
            "ok": False,
            "stdout": None,
            "stderr": None,
            "error": None,
            "code": None,
        }
    )

    assert result.stdout == ""
    assert result.stderr == ""
    assert result.error == ""
    assert result.code == ""


def test_artifact_null_attributes_fall_back_to_empty_strings():
    artifact = RArtifact.from_dict(
        {"name": None, "path": None, "kind": None, "type": None, "mime_type": None}
    )

    assert artifact.name == ""
    assert artifact.path == ""
    assert artifact.kind == "artifact"
    assert artifact.mime_type == ""


def test_artifact_type_fallback_maps_to_kind():
    artifact = RArtifact.from_dict({"name": "plot", "path": "/tmp/plot.png", "type": "figure"})

    assert artifact.kind == "figure"


def test_run_result_to_dict_round_trips_artifact_lists():
    result = RRunResult(
        ok=True,
        execution_stage="complete",
        tables=[RArtifact(name="table1", path="/tmp/table1.csv", kind="table")],
        figures=[
            RArtifact(
                name="plot1",
                path="/tmp/plot1.png",
                kind="figure",
                mime_type="image/png",
            )
        ],
        texts=[RArtifact(name="summary", path="/tmp/summary.txt", kind="text")],
        models=[RArtifact(name="fit", path="/tmp/fit.txt", kind="model")],
        artifacts=[RArtifact(name="session", path="/tmp/session.json", kind="artifact")],
    )

    data = result.to_dict()
    round_tripped = RRunResult.from_dict(data)

    assert round_tripped.tables[0].name == "table1"
    assert round_tripped.figures[0].mime_type == "image/png"
    assert round_tripped.texts[0].kind == "text"
    assert round_tripped.models[0].kind == "model"
    assert round_tripped.artifacts[0].path == "/tmp/session.json"
    assert round_tripped.execution_stage == "complete"


def test_run_result_mutable_defaults_are_independent():
    first = RRunResult(ok=True)
    second = RRunResult(ok=True)

    first.tables.append(RArtifact(name="table1", path="/tmp/table1.csv", kind="table"))

    assert len(first.tables) == 1
    assert second.tables == []


def test_run_result_string_false_ok_parses_false():
    result = RRunResult.from_dict({"ok": "false"})

    assert result.ok is False


def test_run_result_rejects_scalar_warning_payload():
    with pytest.raises(ValueError):
        RRunResult.from_dict({"ok": True, "warnings": "small sample"})


def test_run_result_rejects_dict_valued_artifact_payload():
    with pytest.raises(ValueError):
        RRunResult.from_dict({"ok": True, "tables": {"name": "table1", "path": "/tmp/table.csv"}})


def test_run_result_rejects_non_dict_artifact_items():
    with pytest.raises(ValueError):
        RRunResult.from_dict({"ok": True, "tables": ["table1"]})


def test_diagnose_r_failure_classifies_common_errors():
    missing_function = diagnose_r_failure(
        stderr='Error: could not find function "coxph"',
        code="coxph(Surv(time, status) ~ age)",
    )
    assert missing_function["category"] == "missing_function"
    assert any("get_r_package_help" in item for item in missing_function["next_actions"])
    assert missing_function["detected"]["missing_function"] == "coxph"
    assert missing_function["recommended_tool_calls"][0]["tool"] == "get_r_package_help"
    assert missing_function["recommended_tool_calls"][0]["arguments"]["package"] == "survival"

    missing_column = diagnose_r_failure(
        stderr="Error in `[.data.frame`(dataset, , c('age2')) : undefined columns selected",
        code="dataset[, c('age2')]",
    )
    assert missing_column["category"] == "missing_column"
    assert any("exact column names" in item for item in missing_column["repair_hints"])
    assert any("available_columns" in item for item in missing_column["minimal_probe_code"])

    many_to_many = diagnose_r_failure(
        stderr=(
            "Detected an unexpected many-to-many relationship between `x` and `y`. "
            "Row 23 of `x` matches multiple rows in `y`."
        ),
        code="x |> dplyr::left_join(y, by = c('Variable', 'Level'))",
    )
    assert many_to_many["category"] == "join_cardinality"
    assert any("baa_table_one()" in item for item in many_to_many["repair_hints"])
    assert any("join keys" in item for item in many_to_many["next_actions"])


def test_diagnose_r_failure_recommends_cross_package_search_for_unknown_function():
    diagnostics = diagnose_r_failure(
        stderr='Error: could not find function "rareSpecialFunction"',
        code="rareSpecialFunction(dataset)",
    )

    assert diagnostics["category"] == "missing_function"
    assert diagnostics["recommended_tool_calls"][0]["arguments"] == {
        "query": "rareSpecialFunction",
        "include_docs": True,
        "doc_limit": 2,
    }
    assert "without a package" in diagnostics["next_actions"][0]


def test_diagnose_r_failure_classifies_external_and_binary_runtime_failures():
    java = diagnose_r_failure(
        stderr="Unable to locate a Java Runtime. JAVA_HOME cannot be determined",
        code="h2o::h2o.init()",
    )
    assert java["category"] == "external_dependency"
    assert java["detected"]["external_dependency_id"] == "java"
    assert java["recommended_tool_calls"][0]["tool"] == "get_r_package_inventory"

    abi = diagnose_r_failure(
        stderr="namespace load failed; package was built against a different Rcpp version",
        code="MSnbase::readMSData('example.mzML')",
    )
    assert abi["category"] == "package_binary_incompatibility"
    assert any("binary-compatible" in item for item in abi["facts"])
