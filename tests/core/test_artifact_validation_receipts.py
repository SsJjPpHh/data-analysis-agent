from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.analysis.artifact_manifest import (
    build_artifact_manifest,
    validation_evidence_from_manifest,
)
from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import (
    ArtifactValidationError,
    ResultValidator,
    artifact_has_trusted_receipt,
    build_artifact_validation_receipt,
)


def _plan(*, acceptance: tuple[str, ...] = ()) -> AnalysisPlan:
    return AnalysisPlan(
        plan_id="plan-1",
        source="template",
        dataset_id="dataset-1",
        intent="Table 1",
        category="基础统计",
        method_family="publication_tables_and_reporting",
        scenario="baseline comparison",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(
            RequiredOutput(
                "baseline_table",
                "table",
                acceptance=acceptance,
            ),
        ),
        resource_class="standard",
    )


def _trusted_artifact(
    path,
    *,
    acceptance: tuple[str, ...] = (),
    role: str = "baseline_table",
    semantic_key: str = "",
    content: str = "Variable,Overall,Group A,Group B,P value,SMD\nage,42,40,44,0.20,0.10\n",
) -> dict:
    path.write_text(content, encoding="utf-8")
    metadata = {
        "dataset_id": "dataset-1",
        "plan_id": "plan-1",
        "job_id": "job-1",
        "source_execution_status": "succeeded",
        "role": role,
        "semantic_key": semantic_key or role,
        "acceptance_evidence": list(acceptance),
    }
    receipt = build_artifact_validation_receipt(
        path,
        kind="table",
        metadata=metadata,
        source_tool="run_r_code",
        job_id="job-1",
        artifacts_root=path.parent,
    )
    metadata.update(
        {
            "source_tool": "run_r_code",
            "artifact_size_bytes": receipt["size_bytes"],
            "artifact_sha256": receipt["sha256"],
            "artifact_validation_receipt": receipt,
        }
    )
    return {
        "artifact_id": "table-1",
        "turn_id": "turn-1",
        "type": "table",
        "kind": "table",
        "path": str(path),
        "source_tool": "run_r_code",
        "job_id": "job-1",
        "metadata": metadata,
    }


def test_receipt_uses_real_file_size_hash_and_bounded_csv_structure(tmp_path):
    path = tmp_path / "table.tsv"
    payload = "term\testimate\n年龄\t1.25\n".encode()
    path.write_bytes(payload)
    metadata = {
        "dataset_id": "dataset-1",
        "plan_id": "plan-1",
        "role": "baseline_table",
        "semantic_key": "baseline_table",
    }

    receipt = build_artifact_validation_receipt(
        path,
        kind="table",
        metadata=metadata,
        source_tool="run_r_code",
    )

    assert receipt["size_bytes"] == len(payload)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["structure"]["format"] == "tsv"
    assert receipt["structure"]["column_count"] == 2
    assert receipt["structure"]["sampled_data_rows"] == 1
    assert "columns" not in receipt["structure"]


def test_new_receipt_binds_frozen_variables_and_legacy_v1_remains_readable(tmp_path):
    path = tmp_path / "legacy.csv"
    artifact = _trusted_artifact(path)
    metadata = artifact["metadata"]
    receipt = metadata["artifact_validation_receipt"]

    assert receipt["schema_version"] == 3
    assert "variable_bindings_sha256" in receipt["bindings"]
    assert "resolved_inputs_sha256" in receipt["bindings"]

    legacy = json.loads(json.dumps(receipt))
    legacy["schema_version"] = 1
    legacy["bindings"].pop("variable_bindings_sha256")
    legacy["bindings"].pop("resolved_inputs_sha256")
    legacy_body = dict(legacy)
    legacy_body.pop("receipt_sha256")
    legacy["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            legacy_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    metadata["artifact_validation_receipt"] = legacy

    assert artifact_has_trusted_receipt(artifact, verify_file=True) is True

    metadata["variable_bindings"] = {"group": "arm"}
    assert artifact_has_trusted_receipt(artifact, verify_file=True) is False


def test_receipt_streams_a_multi_chunk_file_and_bounds_the_table_sample(tmp_path):
    path = tmp_path / "large.csv"
    payload = b"term,estimate\n" + (b"age,1.25\n" * 150_000)
    path.write_bytes(payload)

    receipt = build_artifact_validation_receipt(path, kind="table", metadata={})

    assert receipt["bytes_hashed"] == len(payload)
    assert receipt["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["structure"]["sampled_data_rows"] == 128
    assert receipt["structure"]["sample_bounded"] is True


def test_receipt_rejects_links_malformed_tables_and_unsupported_figures(tmp_path):
    malformed = tmp_path / "malformed.csv"
    malformed.write_text("a,b\n1\n", encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("not empty", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    fake_png = tmp_path / "figure.png"
    fake_png.write_text("not a png", encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="column"):
        build_artifact_validation_receipt(malformed, kind="table", metadata={})
    with pytest.raises(ArtifactValidationError, match="symbolic link"):
        build_artifact_validation_receipt(link, kind="text", metadata={})
    with pytest.raises(ArtifactValidationError, match="signature"):
        build_artifact_validation_receipt(fake_png, kind="figure", metadata={})


def test_run_receipt_rejects_files_outside_the_job_output_root(tmp_path):
    output_root = tmp_path / "job-output"
    output_root.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_text("term,estimate\nage,1.2\n", encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="outside"):
        build_artifact_validation_receipt(
            outside,
            kind="table",
            metadata={"job_id": "job-1"},
            job_id="job-1",
            artifacts_root=output_root,
        )


@pytest.mark.parametrize(
    ("name", "payload", "expected_format"),
    [
        ("plot.png", b"\x89PNG\r\n\x1a\nrest", "png"),
        ("plot.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
        ("plot.pdf", b"%PDF-1.7\nbody\n%%EOF\n", "pdf"),
    ],
)
def test_receipt_recognizes_supported_figure_structures(tmp_path, name, payload, expected_format):
    path = tmp_path / name
    path.write_bytes(payload)

    receipt = build_artifact_validation_receipt(path, kind="figure", metadata={})

    assert receipt["structure"] == {"valid": True, "format": expected_format}


@pytest.mark.parametrize("kind", ["text", "model"])
def test_receipt_rejects_empty_or_whitespace_only_textual_artifacts(tmp_path, kind):
    path = tmp_path / f"empty-{kind}.txt"
    path.write_text(" \n\t", encoding="utf-8")

    with pytest.raises(ArtifactValidationError, match="non-whitespace"):
        build_artifact_validation_receipt(path, kind=kind, metadata={})


def test_validator_requires_server_receipt_and_binds_declared_acceptance(tmp_path):
    path = tmp_path / "table.csv"
    path.write_text(
        "Variable,Overall,Group A,Group B,P value,SMD\nage,42,40,44,0.2,0.1\n",
        encoding="utf-8",
    )
    plan = _plan(acceptance=("P 值", "SMD"))
    artifact = _trusted_artifact(path, acceptance=("P 值", "SMD"))
    execution = {"dataset_id": "dataset-1", "plan_id": "plan-1", "status": "succeeded"}

    accepted = ResultValidator().validate(plan, execution, [artifact])
    artifact["metadata"]["acceptance_evidence"] = ["P 值", "SMD", "伪造的新声明"]
    mutated = ResultValidator().validate(plan, execution, [artifact])
    no_receipt = {
        **artifact,
        "metadata": {
            **artifact["metadata"],
            "artifact_size_bytes": path.stat().st_size,
            "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }
    no_receipt["metadata"].pop("artifact_validation_receipt")
    self_report_only = ResultValidator().validate(plan, execution, [no_receipt])

    assert accepted.complete is True
    assert mutated.complete is False
    assert self_report_only.complete is False


def test_declared_acceptance_cannot_promote_semantically_unrelated_table(tmp_path):
    path = tmp_path / "unrelated.csv"
    artifact = _trusted_artifact(
        path,
        acceptance=("P 值", "SMD"),
        content="unrelated\n42\n",
    )

    assessment = ResultValidator().validate(
        _plan(acceptance=("P 值", "SMD")),
        {"dataset_id": "dataset-1", "plan_id": "plan-1", "status": "succeeded"},
        [artifact],
    )

    assert assessment.complete is False
    assert assessment.invalid_roles == ("baseline_table",)


def test_table_one_helper_headers_are_verified_without_named_group_prefixes(tmp_path):
    content = (
        "Variable,Level,Overall (N=1941),1 (n=927),2 (n=1014),Missing,"
        "SMD (max pairwise),P value\n"
        "age,Mean (SD),42,40,44,0,0.10,0.20\n"
    )
    assessment = ResultValidator().validate(
        _plan(acceptance=("总体与分组描述", "P 值", "SMD")),
        {"dataset_id": "dataset-1", "plan_id": "plan-1", "status": "succeeded"},
        [_trusted_artifact(tmp_path / "table-one.csv", content=content)],
    )

    assert assessment.complete is True


@pytest.mark.parametrize(
    ("role", "content"),
    [
        (
            "baseline_table",
            "Variable,Overall,Group A,Group B,P value,SMD\nage,42,40,44,0.2,0.1\n",
        ),
        (
            "effect_estimate_table",
            "term,estimate,conf.low,conf.high,p.value\nage,1.2,1.0,1.4,0.02\n",
        ),
        (
            "model_assumption_diagnostics",
            "diagnostic,statistic,p.value,status\nnormality,1.2,0.24,passed\n",
        ),
    ],
)
def test_common_scientific_table_roles_require_server_verified_structure(tmp_path, role, content):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="dataset-1",
        intent="bounded semantic validation",
        category="基础统计",
        method_family="classical_statistics_and_mixed_models",
        scenario="validation",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput(role, "table"),),
        resource_class="standard",
    )
    accepted = ResultValidator().validate(
        plan,
        {"dataset_id": "dataset-1", "plan_id": "plan-1", "status": "succeeded"},
        [_trusted_artifact(tmp_path / f"{role}.csv", role=role, content=content)],
    )
    rejected = ResultValidator().validate(
        plan,
        {"dataset_id": "dataset-1", "plan_id": "plan-1", "status": "succeeded"},
        [
            _trusted_artifact(
                tmp_path / f"{role}-bad.csv",
                role=role,
                content="unrelated\n42\n",
            )
        ],
    )

    assert accepted.complete is True
    assert rejected.complete is False
    assert rejected.invalid_roles == (role,)


def test_manifest_recovery_uses_persisted_actual_receipt_and_rechecks_file(tmp_path):
    path = tmp_path / "table.csv"
    path.write_text("term,estimate\nage,1.2\n", encoding="utf-8")
    artifact = _trusted_artifact(path)
    manifest = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[{"role": "baseline_table", "kind": "table", "required": True}],
        artifacts=[artifact],
        assessment={"complete": True, "r_executed": True},
    )

    persisted = manifest["artifacts"][0]
    evidence = validation_evidence_from_manifest(
        manifest,
        dataset_id="dataset-1",
        plan_id="plan-1",
    )

    assert manifest["complete"] is True
    assert persisted["path"] == str(path)
    assert persisted["metadata"]["artifact_size_bytes"] == path.stat().st_size
    assert persisted["metadata"]["artifact_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(evidence.artifacts) == 1
    assert artifact_has_trusted_receipt(evidence.artifacts[0], verify_file=True)

    path.write_text("term,estimate\nage,999\n", encoding="utf-8")
    tampered = validation_evidence_from_manifest(
        manifest,
        dataset_id="dataset-1",
        plan_id="plan-1",
    )

    assert tampered.artifacts == ()
    assert tampered.r_executed is False


def test_diagnostic_conflicts_survive_continuation_and_require_valid_backing_files(tmp_path):
    path = tmp_path / "diagnostic.csv"
    artifact = _trusted_artifact(path)
    artifact["metadata"]["diagnostic_status"] = {"model_check": "failed"}
    artifact["metadata"]["variable_bindings"] = {"group": "arm"}
    artifact["metadata"]["resolved_inputs"] = {"group": {"kind": "column", "value": "arm"}}
    artifact["metadata"]["artifact_validation_receipt"] = build_artifact_validation_receipt(
        path, kind="table", metadata=artifact["metadata"], source_tool="run_r_code",
        job_id="job-1", artifacts_root=tmp_path,
    )
    kwargs = {
        "plan_id": "plan-1", "portfolio_version": "1.0",
        "portfolio": [{"role": "baseline_table", "kind": "table", "required": True}],
        "artifacts": [artifact], "planned_diagnostics": ["model_check"],
        "assessment": {"complete": True, "r_executed": True},
    }
    first = build_artifact_manifest(**kwargs, diagnostic_status={"model_check": "failed"})
    continued = build_artifact_manifest(**kwargs, previous=first, diagnostic_status={"model_check": "passed"})
    assert continued["diagnostic_status"] == {"model_check": "failed"}
    assert any("诊断声明冲突" in message for message in continued["diagnostic_conflicts"])
    restored = json.loads(json.dumps(continued))
    evidence = validation_evidence_from_manifest(restored, dataset_id="dataset-1", plan_id="plan-1")
    assert evidence.diagnostic_status == {"model_check": "failed"}
    path.unlink()
    missing = validation_evidence_from_manifest(restored, dataset_id="dataset-1", plan_id="plan-1")
    assert missing.diagnostic_status == {}


def test_manifest_does_not_promote_a_receipted_file_with_unverified_content(tmp_path):
    path = tmp_path / "table.csv"
    artifact = _trusted_artifact(path, content="unrelated\n42\n")

    manifest = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[
            {
                "role": "baseline_table",
                "kind": "table",
                "required": True,
                "acceptance": ["P 值", "SMD"],
            }
        ],
        artifacts=[artifact],
        assessment={"complete": False, "r_executed": True},
    )

    assert manifest["complete"] is False
    assert manifest["items"][0]["status"] == "invalid"


def test_failed_execution_cannot_become_complete_after_manifest_recovery(tmp_path):
    path = tmp_path / "table.csv"
    path.write_text("term,estimate\nage,1.2\n", encoding="utf-8")
    artifact = _trusted_artifact(path)
    manifest = build_artifact_manifest(
        plan_id="plan-1",
        portfolio_version="1.0",
        portfolio=[{"role": "baseline_table", "kind": "table", "required": True}],
        artifacts=[artifact],
        assessment={"complete": False, "r_executed": False},
    )

    evidence = validation_evidence_from_manifest(
        manifest,
        dataset_id="dataset-1",
        plan_id="plan-1",
    )

    assert manifest["items"][0]["status"] == "produced"
    assert manifest["complete"] is False
    assert len(evidence.artifacts) == 1
    assert evidence.r_executed is False


def test_legacy_manifest_items_do_not_synthesize_size_or_acceptance_evidence():
    manifest = {
        "plan_id": "plan-1",
        "complete": True,
        "items": [
            {
                "role": "baseline_table",
                "kind": "table",
                "status": "produced",
                "acceptance": ["P 值", "SMD"],
            }
        ],
    }

    evidence = validation_evidence_from_manifest(
        manifest,
        dataset_id="dataset-1",
        plan_id="plan-1",
    )

    assert evidence.artifacts == ()
    assert evidence.r_executed is False
