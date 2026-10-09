import json
from types import SimpleNamespace

from agent.analysis.model_projection import ModelResultProjector
from agent.tools.results import make_tool_result, truncate_tool_result_preserving_refs
from agent.analysis.models import AnalysisPlan, RequiredOutput
from agent.analysis.result_validation import ResultValidator, build_artifact_validation_receipt


def _trusted_artifact(
    tmp_path,
    *,
    kind: str,
    role: str,
    dataset_id: str = "abcdef123456",
    plan_id: str = "plan-1",
    semantic_key: str = "",
    acceptance_evidence: tuple[str, ...] = (),
    portfolio_status: str = "",
    portfolio_reason: str = "",
    table_content: str = "term,estimate\nx,1.2\n",
):
    if kind == "table":
        path = tmp_path / f"{role}.csv"
        path.write_text(table_content, encoding="utf-8")
    elif kind == "figure":
        path = tmp_path / f"{role}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\nfigure")
    else:
        path = tmp_path / f"{role}.txt"
        path.write_text("Scientifically justified output.\n", encoding="utf-8")
    metadata = {
        "dataset_id": dataset_id,
        "plan_id": plan_id,
        "job_id": "job-1",
        "source_execution_status": "succeeded",
        "role": role,
        "semantic_key": semantic_key,
        "acceptance_evidence": list(acceptance_evidence),
        "portfolio_status": portfolio_status,
        "portfolio_reason": portfolio_reason,
        "source_tool": "run_r_code",
    }
    receipt = build_artifact_validation_receipt(
        path,
        kind=kind,
        metadata=metadata,
        job_id="job-1",
        artifacts_root=tmp_path,
    )
    metadata.update(
        {
            "artifact_size_bytes": receipt["size_bytes"],
            "artifact_sha256": receipt["sha256"],
            "artifact_validation_receipt": receipt,
        }
    )
    return {
        "type": kind,
        "kind": kind,
        "path": str(path),
        "source_tool": "run_r_code",
        "job_id": "job-1",
        "metadata": metadata,
    }


def test_model_projection_removes_private_r_fields_and_raw_sentinel():
    payload = {
        "result": {
            "code": "print(dataset)",
            "stdout": "RAW_SENTINEL_987654",
            "stderr": "/Users/test/private.csv",
            "tables": [{"path": "/tmp/result.csv", "name": "model"}],
        },
        "artifact_previews": {"tables": [{"preview_csv": "term,estimate\nx,1.2"}]},
    }

    projected = ModelResultProjector().project(payload)
    text = json.dumps(projected, ensure_ascii=False)

    assert "RAW_SENTINEL_987654" not in text
    assert "/Users/test" not in text
    assert "print(dataset)" not in text
    assert "estimate" in text
    assert len(text.encode("utf-8")) <= 32 * 1024


def test_model_projection_redacts_paths_embedded_in_prioritized_errors():
    projected = ModelResultProjector().project(
        {
            "result": {
                "ok": False,
                "error": "cannot open /Users/researcher/Documents/private cohort.csv",
            }
        }
    )

    text = json.dumps(projected, ensure_ascii=False)
    assert "/Users/researcher" not in text
    assert "[已隐藏路径]" in text


def test_model_projection_prioritizes_numeric_artifact_previews_before_large_dataset_profile(tmp_path):
    payload = {
        "dataset": {
            "dataset_id": "abcdef123456",
            "name": "clinical.csv",
            "profile": {
                "summary": {
                    "variables": [
                        {"name": f"variable_{index}", "description": "x" * 256}
                        for index in range(80)
                    ]
                }
            },
        },
        "job_id": "job-priority",
        "artifact_previews": {
            "tables": [
                {
                    "name": "effect_estimates",
                    "preview_csv": "term,estimate,p_value\nage,1.2345,0.0042\n",
                }
            ]
        },
        "result": {"ok": True, "warnings": []},
        "execution": {"status": "succeeded", "plan_id": "plan-1"},
    }

    projected = ModelResultProjector().project(payload)
    envelope = make_tool_result(
        "run_r_code",
        projected,
        session_id="projection-session",
        runtime=SimpleNamespace(cache_dir=tmp_path),
        result_char_budget=max(4500, len(json.dumps(projected, ensure_ascii=False, indent=2))),
    )

    assert list(projected)[:3] == ["job_id", "artifact_previews", "result"]
    assert "1.2345" in str(envelope.data)
    assert "0.0042" in str(envelope.data)
    replayed = truncate_tool_result_preserving_refs(envelope.to_model_text(), 800)
    assert "1.2345" in replayed
    assert "0.0042" in replayed


def test_model_projection_degrades_wide_tables_without_dropping_all_numeric_results():
    header = ",".join(["term", "estimate", "p_value", *(f"extra_{index}" for index in range(27))])
    rows = [
        ",".join(
            [
                f"term_{row}",
                f"{1.2345 + row:.4f}",
                f"0.00{row + 1}",
                *(f"value_{row}_{column}_" + "x" * 80 for column in range(27)),
            ]
        )
        for row in range(20)
    ]
    preview_csv = "\n".join([header, *rows])
    payload = {
        "dataset": {
            "profile": {
                "variables": [
                    {"name": f"variable_{index}", "description": "x" * 256}
                    for index in range(80)
                ]
            }
        },
        "job_id": "wide-table-job",
        "artifact_previews": {
            "tables": [
                {"name": "wide_a", "preview_csv": preview_csv},
                {"name": "wide_b", "preview_csv": preview_csv},
            ]
        },
        "result": {"ok": True},
        "execution": {"status": "succeeded"},
    }

    projected = ModelResultProjector().project(payload)
    text = json.dumps(projected, ensure_ascii=False)

    assert projected.get("truncated") is True
    assert projected["projection"]["bounded_fields"]
    assert "artifact_previews" in projected
    assert "1.2345" in text
    assert "0.001" in text
    assert len(text.encode("utf-8")) <= ModelResultProjector.MAX_PAYLOAD_BYTES


def test_history_summary_withholds_wide_table_instead_of_sending_numbers_without_complete_headers(tmp_path):
    header = ",".join(f"very_long_header_{index}_" + "x" * 40 for index in range(30))
    preview_csv = header + "\n" + ",".join(["1.2345", "0.0042", *("7" for _ in range(28))])
    projected = ModelResultProjector().project(
        {
            "job_id": "long-header-job",
            "artifact_previews": {
                "tables": [{"name": "wide", "preview_csv": preview_csv}]
            },
            "result": {"ok": True},
        }
    )
    envelope = make_tool_result(
        "run_r_code",
        projected,
        session_id="long-header-session",
        runtime=SimpleNamespace(cache_dir=tmp_path),
        result_char_budget=max(4500, len(json.dumps(projected, ensure_ascii=False, indent=2))),
    )

    replayed = truncate_tool_result_preserving_refs(envelope.to_model_text(), 800)

    envelope = json.loads(replayed.split("\n", 1)[1])
    assert envelope["truncated"] is True
    assert "表格预览未发送" in json.dumps(envelope["data"], ensure_ascii=False)
    assert "1.2345" not in replayed and "0.0042" not in replayed


def test_result_validator_checks_plan_ownership_outputs_and_diagnostics(tmp_path):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abcdef123456",
        intent="组间比较",
        category="基础统计",
        method_family="group_comparison",
        scenario="两组连续变量比较",
        variable_bindings={"outcome": "score", "group": "arm"},
        packages=("stats",),
        prerequisites=(),
        assumptions=("独立观测",),
        diagnostics=("分布检查",),
        required_outputs=(RequiredOutput("primary_table", "table"),),
        resource_class="standard",
    )
    artifacts = [
        _trusted_artifact(
            tmp_path,
            kind="table",
            role="primary_table",
            semantic_key="primary_table",
        )
    ]

    assessment = ResultValidator().validate(
        plan,
        {
            "dataset_id": "abcdef123456",
            "plan_id": "plan-1",
            "status": "succeeded",
            "diagnostic_status": {"分布检查": "passed"},
        },
        artifacts,
    )

    assert assessment.complete is True
    assert assessment.warnings == ()

    failed = ResultValidator().validate(plan, {
        "dataset_id": plan.dataset_id, "plan_id": plan.plan_id, "status": "succeeded",
        "diagnostic_status": {"分布检查": "failed"},
    }, artifacts)
    assert failed.complete is True  # Delivery and method quality are separate.
    assert failed.diagnostic_quality == "failed"
    assert failed.failed_diagnostics == ("分布检查",)
    assert failed.warnings


def test_inspection_keeps_all_variable_names_and_discloses_bounded_details():
    variables = [{"name": f"variable_{index}", "semantic_type": "numeric", "unique_count": index + 2} for index in range(70)]
    projected = ModelResultProjector().project({"summary": {"variables": variables, "columns": 70}}, inspection=True)
    assert [item["name"] for item in projected["variable_index"]] == [item["name"] for item in variables]
    assert projected["truncated"] is True
    assert "summary.variables" in projected["projection"]["bounded_fields"]
    assert len(projected["summary"]["variables"]) <= 20


def test_targeted_variable_lookup_reuses_the_local_profile_without_r():
    from agent.tools.research import RToolsMixin

    variables = [{"name": f"variable_{i}", "unique_count": i + 2} for i in range(70)]
    class Agent(RToolsMixin):
        def _resolve_research_dataset(self, dataset_id):
            return SimpleNamespace(dataset_id="data-one", profile={"summary": {"variables": variables, "columns": 70}})

    selected = json.loads(Agent()._tool_inspect_dataset("data-one", column_names=["variable_69"]))
    assert selected["cached"] is True
    assert selected["summary"]["variables"] == [{"name": "variable_69", "unique_count": 71}]
    assert selected["summary"]["columns"] == 70


def test_result_validator_rejects_a_role_carried_by_the_wrong_artifact_kind(tmp_path):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abcdef123456",
        intent="评估模型校准",
        category="临床研究",
        method_family="clinical_prediction_and_diagnostic_performance",
        scenario="模型验证",
        variable_bindings={},
        packages=("rms",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("prediction_calibration_plot", "figure"),),
        resource_class="standard",
    )
    artifacts = [
        _trusted_artifact(
            tmp_path,
            kind="table",
            role="prediction_calibration_plot",
            semantic_key="prediction_calibration_plot",
        )
    ]

    assessment = ResultValidator().validate(
        plan,
        {"dataset_id": "abcdef123456", "plan_id": "plan-1", "status": "succeeded"},
        artifacts,
    )

    assert assessment.complete is False
    assert assessment.invalid_roles == ("prediction_calibration_plot",)
    assert assessment.outstanding_roles == ("prediction_calibration_plot",)
    assert assessment.next_action == "repair"


def test_result_validator_accepts_justified_non_core_terminal_dispositions(tmp_path):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abcdef123456",
        intent="评估模型校准",
        category="临床研究",
        method_family="clinical_prediction_and_diagnostic_performance",
        scenario="模型验证",
        variable_bindings={},
        packages=("rms",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(
            RequiredOutput(
                "prediction_calibration_plot",
                "figure",
                tier="conditional",
            ),
        ),
        resource_class="standard",
    )
    for status in ("not_applicable", "limited", "declined"):
        artifacts = [
            _trusted_artifact(
                tmp_path,
                kind="text",
                role="prediction_calibration_plot",
                semantic_key="prediction_calibration_plot",
                portfolio_status=status,
                portfolio_reason="当前数据不支持可靠估计，已记录科学限制。",
            )
        ]

        assessment = ResultValidator().validate(
            plan,
            {"dataset_id": "abcdef123456", "plan_id": "plan-1", "status": "succeeded"},
            artifacts,
        )

        assert assessment.complete is True
        assert assessment.disposition_roles == ("prediction_calibration_plot",)
        assert assessment.next_action == "complete"


def test_result_validator_does_not_let_a_core_output_be_disposed_instead_of_produced(tmp_path):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="conversation",
        dataset_id="abcdef123456",
        intent="主要模型",
        category="基础统计",
        method_family="classical_statistics_and_mixed_models",
        scenario="回归",
        variable_bindings={},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("effect_estimate_table", "table", tier="core"),),
        resource_class="standard",
    )
    artifacts = [
        _trusted_artifact(
            tmp_path,
            kind="text",
            role="effect_estimate_table",
            semantic_key="effect_estimate_table",
            portfolio_status="limited",
            portfolio_reason="本轮不再执行。",
        )
    ]

    assessment = ResultValidator().validate(
        plan,
        {"dataset_id": "abcdef123456", "plan_id": "plan-1", "status": "succeeded"},
        artifacts,
    )

    assert assessment.complete is False
    assert assessment.invalid_roles == ("effect_estimate_table",)


def test_result_validator_requires_declared_acceptance_evidence(tmp_path):
    plan = AnalysisPlan(
        plan_id="plan-1",
        source="template",
        dataset_id="abcdef123456",
        intent="Table 1",
        category="基础统计",
        method_family="publication_tables_and_reporting",
        scenario="基线比较",
        variable_bindings={},
        packages=("tableone",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(
            RequiredOutput(
                "baseline_table",
                "table",
                acceptance=("P 值", "SMD"),
                legacy_roles=("baseline",),
            ),
        ),
        resource_class="standard",
    )
    artifacts = [
        _trusted_artifact(
            tmp_path,
            kind="table",
            role="baseline",
            semantic_key="baseline_table",
        )
    ]

    missing_evidence = ResultValidator().validate(
        plan,
        {"dataset_id": "abcdef123456", "plan_id": "plan-1", "status": "succeeded"},
        artifacts,
    )
    artifacts = [
        _trusted_artifact(
            tmp_path,
            kind="table",
            role="baseline",
            semantic_key="baseline_table",
            acceptance_evidence=("P 值", "SMD"),
            table_content=(
                "Variable,Overall,Group A,Group B,P value,SMD\n"
                "age,42,40,44,0.20,0.10\n"
            ),
        )
    ]
    accepted = ResultValidator().validate(
        plan,
        {"dataset_id": "abcdef123456", "plan_id": "plan-1", "status": "succeeded"},
        artifacts,
    )

    assert missing_evidence.complete is False
    assert missing_evidence.invalid_roles == ("baseline_table",)
    assert accepted.complete is True


def test_inspection_projection_drops_raw_samples_but_keeps_variable_names():
    projected = ModelResultProjector().project(
        {
            "summary": {
                "variables": [{"name": "age", "type": "numeric"}],
                "sample_rows": [{"age": 987654321}],
            }
        },
        inspection=True,
    )

    text = json.dumps(projected, ensure_ascii=False)
    assert "sample_rows" not in text
    assert "987654321" not in text
    assert '"age"' in text
