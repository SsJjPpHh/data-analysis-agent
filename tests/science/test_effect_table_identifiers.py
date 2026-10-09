"""Real-file acceptance for labeled effect comparisons, without metadata claims."""

from __future__ import annotations

import csv
import io

import pytest

from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import (
    ResultValidator,
    artifact_has_trusted_receipt,
    artifact_satisfies_output_contract,
    build_artifact_validation_receipt,
)


# Exact CSV from the final real effsize run in flow-release, artifact job
# 3c8d75faccdf (2026-10-09). Keep the fixture here so tests do not depend on
# mutable acceptance/runtime files, and do not imply a new model or R run.
REAL_EFFECT_CSV = (
    "outcome,Comparison,group_intervention,group_control,n_intervention,n_control,"
    "mean_sd_intervention,mean_sd_control,effect_measure,effect_estimate,SMD,"
    "standardized_mean_difference,standard_error,CI_lower_95,CI_upper_95,"
    "SMD_CI_lower_95,SMD_CI_upper_95,CI_level,p_value\n"
    "score,intervention vs control,intervention,control,40,40,14.38 (1.86),12.91 (1.87),"
    '"Cohen\'s d (standardized mean difference, pooled SD)",0.792,0.792,0.792,0.232,'
    "0.33,1.254,0.33,1.254,95,0.0006757\n"
)


def receipted_table(tmp_path, content, *, role="effect_estimate_table", claims=None):
    path = tmp_path / "effect.csv"
    path.write_text(content, encoding="utf-8")
    metadata = {
        "dataset_id": "effect-fixture",
        "plan_id": "effect-plan",
        "job_id": "effect-fixture-job",
        "source_execution_status": "succeeded",
        "role": role,
        "semantic_key": role,
        "variable_bindings": {"outcome": "score", "group": "group"},
        **(claims or {}),
    }
    receipt = build_artifact_validation_receipt(
        path,
        kind="table",
        metadata=metadata,
        source_tool="run_r_code",
        job_id="effect-fixture-job",
        artifacts_root=tmp_path,
    )
    metadata.update(
        source_tool="run_r_code",
        artifact_size_bytes=receipt["size_bytes"],
        artifact_sha256=receipt["sha256"],
        artifact_validation_receipt=receipt,
    )
    return {
        "artifact_id": "effect-fixture-artifact",
        "type": "table",
        "kind": "table",
        "path": str(path),
        "source_tool": "run_r_code",
        "job_id": "effect-fixture-job",
        "metadata": metadata,
    }


def accepted_effect(artifact):
    return artifact_satisfies_output_contract(
        artifact,
        kind="table",
        semantic_key="effect_estimate_table",
        acceptance=("效应量与不确定性",),
    )


def test_real_effsize_csv_receipt_and_result_contract_accept_comparison_labels(tmp_path):
    rows = list(csv.DictReader(io.StringIO(REAL_EFFECT_CSV)))
    assert len(rows) == 1 and len(rows[0]) == 19
    assert float(rows[0]["effect_estimate"]) == pytest.approx(0.792)
    assert (float(rows[0]["CI_lower_95"]), float(rows[0]["CI_upper_95"])) == pytest.approx((0.33, 1.254))
    artifact = receipted_table(tmp_path, REAL_EFFECT_CSV)
    assert artifact_has_trusted_receipt(artifact, verify_file=True)
    checks = artifact["metadata"]["artifact_validation_receipt"]["structure"]["semantic_checks"]
    assert all(
        checks[key]
        for key in ("has_data_rows", "has_identifier", "has_effect_estimate", "has_uncertainty", "has_smd")
    )
    assert accepted_effect(artifact)
    plan = AnalysisPlan(
        plan_id="effect-plan",
        source="conversation",
        dataset_id="effect-fixture",
        intent="Cohen's d with 95% CI",
        category="基础统计",
        method_family="standardized_mean_difference",
        scenario="control vs intervention",
        variable_bindings={"outcome": "score", "group": "group"},
        packages=("effsize",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("effect_estimate_table", "table"),),
        resource_class="standard",
    )
    assessment = ResultValidator().validate(
        plan,
        {"dataset_id": plan.dataset_id, "plan_id": plan.plan_id, "status": "succeeded"},
        [artifact],
    )
    assert assessment.complete
    assert not assessment.invalid_roles


@pytest.mark.parametrize(
    "header",
    [
        "Comparison",
        "contrast",
        "outcome",
        "endpoint",
        "effect_measure",
        "比较",
        "对比",
        "结局",
        "终点",
        "效应指标",
        "效应度量",
        "效应量类型",
    ],
)
def test_statistical_label_header_is_supported_in_real_file_receipt(tmp_path, header):
    artifact = receipted_table(
        tmp_path, f"{header},effect_estimate,CI_lower_95,CI_upper_95\nscore,0.792,0.33,1.254\n"
    )
    assert artifact_has_trusted_receipt(artifact, verify_file=True)
    assert accepted_effect(artifact)


@pytest.mark.parametrize("header", ["", "subject_id,", "participant_id,", "title,", "comparison_notes,"])
def test_estimate_and_ci_without_statistical_label_remain_rejected(tmp_path, header):
    prefix = "unrelated," if header else ""
    artifact = receipted_table(
        tmp_path,
        f"{header}effect_estimate,CI_lower_95,CI_upper_95\n{prefix}0.792,0.33,1.254\n",
        claims={
            "has_identifier": True,
            "columns": ["Comparison", "effect_estimate", "CI_lower_95", "CI_upper_95"],
            "acceptance_evidence": ["效应量与不确定性"],
        },
    )
    assert artifact_has_trusted_receipt(artifact, verify_file=True)
    checks = artifact["metadata"]["artifact_validation_receipt"]["structure"]["semantic_checks"]
    assert not checks["has_identifier"]
    assert checks["has_effect_estimate"] and checks["has_uncertainty"]
    assert not accepted_effect(artifact)


@pytest.mark.parametrize(
    "content",
    [
        "Comparison,CI_lower_95,CI_upper_95\nA vs B,0.33,1.254\n",
        "Comparison,effect_estimate\nA vs B,0.792\n",
        "Comparison,effect_estimate,CI_lower_95,CI_upper_95\n",
    ],
)
def test_label_alone_does_not_replace_estimate_uncertainty_or_data_rows(tmp_path, content):
    assert not accepted_effect(receipted_table(tmp_path, content))


def test_model_claim_without_server_receipt_still_rejected(tmp_path):
    artifact = receipted_table(tmp_path, REAL_EFFECT_CSV)
    artifact["metadata"].pop("artifact_validation_receipt")
    assert not accepted_effect(artifact)


def test_other_role_contracts_remain_required(tmp_path):
    artifact = receipted_table(
        tmp_path,
        "Comparison,effect_estimate,CI_lower_95,CI_upper_95\nA vs B,0.792,0.33,1.254\n",
        role="baseline_table",
    )
    assert not artifact_satisfies_output_contract(artifact, kind="table", semantic_key="baseline_table")
