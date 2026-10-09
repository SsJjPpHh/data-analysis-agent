from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from flask import Flask
from PIL import Image
from werkzeug.exceptions import RequestEntityTooLarge


def test_public_projection_redacts_arbitrary_absolute_paths_but_not_urls():
    from infrastructure.public_projection import redact_filesystem_paths

    value = (
        "path:/volume/custom/private.csv /dev/shm/runtime.txt "
        r"\\server\share\secret.csv https://example.com/public.csv"
    )
    projected = redact_filesystem_paths(value)

    assert "/volume/custom" not in projected
    assert "/dev/shm" not in projected
    assert r"\\server\share" not in projected
    assert "https://example.com/public.csv" in projected


def test_spreadsheet_exports_neutralize_formula_like_text():
    from api.research import _safe_spreadsheet_value

    assert (
        _safe_spreadsheet_value('=HYPERLINK("https://example.invalid")')
        == '\'=HYPERLINK("https://example.invalid")'
    )
    assert _safe_spreadsheet_value(" +SUM(A1:A2)") == "' +SUM(A1:A2)"
    assert _safe_spreadsheet_value("-1.5") == "-1.5"
    assert _safe_spreadsheet_value("+2.0e3") == "+2.0e3"
    assert _safe_spreadsheet_value("ordinary text") == "ordinary text"


def test_table_export_default_row_budget_is_bounded(tmp_path, monkeypatch):
    from api.research import _read_table_rows

    monkeypatch.delenv("BAA_RESEARCH_EXPORT_MAX_TABLE_ROWS", raising=False)
    path = tmp_path / "large.csv"
    path.write_text("value\n" + "1\n" * 10_001, encoding="utf-8")
    app = Flask(__name__)

    with app.test_request_context("/"), pytest.raises(RequestEntityTooLarge):
        _read_table_rows(path)


def test_export_budget_caps_cells_across_all_included_tables(tmp_path, monkeypatch):
    from api.research import _validate_export_budget

    monkeypatch.setenv("BAA_RESEARCH_EXPORT_MAX_TOTAL_ROWS", "4")
    monkeypatch.setenv("BAA_RESEARCH_EXPORT_MAX_TOTAL_CELLS", "20")
    first = tmp_path / "first.csv"
    first.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    state = {
        "analysis_turns": [
            {
                "artifacts": [
                    {"type": "table", "path": str(first), "included_in_paper": True},
                    {"type": "table", "path": str(first), "included_in_paper": True},
                ]
            }
        ]
    }
    app = Flask(__name__)

    with app.test_request_context("/"), pytest.raises(RequestEntityTooLarge):
        _validate_export_budget(state, [tmp_path])


def test_image_conversion_pixel_budget_is_bounded(monkeypatch):
    from api.research import _validate_image_pixel_budget

    monkeypatch.setenv("BAA_RESEARCH_EXPORT_MAX_IMAGE_PIXELS", "4")
    with Image.new("RGB", (3, 2)) as image, pytest.raises(ValueError, match="pixel"):
        _validate_image_pixel_budget(image)


def test_export_budget_caps_pixels_across_repeated_figures(tmp_path, monkeypatch):
    from api.research import _validate_export_budget

    monkeypatch.setenv("BAA_RESEARCH_EXPORT_MAX_TOTAL_FIGURE_PIXELS", "10")
    path = tmp_path / "figure.png"
    Image.new("RGB", (3, 2), color=(255, 255, 255)).save(path)
    state = {
        "analysis_turns": [
            {
                "artifacts": [
                    {"kind": "figure", "path": str(path), "included_in_paper": True},
                    {"kind": "figure", "path": str(path), "included_in_paper": True},
                ]
            }
        ]
    }
    app = Flask(__name__)

    with app.test_request_context("/"), pytest.raises(RequestEntityTooLarge):
        _validate_export_budget(state, [tmp_path])


def test_result_store_coalesces_same_plot_export_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    from data.research_results import ResearchResultStore

    sid = "plot-export-variants"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    paths = {
        "png": artifact_dir / "model_plot.png",
        "pdf": artifact_dir / "model_plot.pdf",
        "tiff": artifact_dir / "model_plot.tiff",
    }
    Image.new("RGB", (20, 10), color=(255, 255, 255)).save(paths["png"])
    paths["pdf"].write_bytes(b"%PDF-1.4\n")
    paths["tiff"].write_bytes(b"tiff")

    store = ResearchResultStore(sid)
    store.reset()
    state = store.append_r_payload(
        turn_id="turn-1",
        question="绘制模型图",
        template_context={},
        payload={
            "result": {
                "ok": True,
                "figures": [
                    {"name": "model_plot", "path": str(paths["png"]), "mime_type": "image/png"},
                    {"name": "model_plot", "path": str(paths["pdf"]), "mime_type": "application/pdf"},
                    {"name": "model_plot", "path": str(paths["tiff"]), "mime_type": "image/tiff"},
                ],
            }
        },
    )

    figures = [item for item in state["analysis_turns"][0]["artifacts"] if item["kind"] == "figure"]
    assert len(figures) == 1
    assert figures[0]["path"] == str(paths["png"])
    assert figures[0]["mime_type"] == "image/png"
    assert figures[0]["export_formats"] == ["png", "pdf", "tiff"]


def test_result_store_keeps_latest_same_named_artifact_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    from data.research_results import ResearchResultStore

    sid = "artifact-revisions"
    store = ResearchResultStore(sid)
    store.reset()
    paths = []
    for job_name, color in (("job-1", (255, 0, 0)), ("job-2", (0, 255, 0))):
        artifact_dir = tmp_path / "outputs" / "research" / sid / job_name
        artifact_dir.mkdir(parents=True)
        path = artifact_dir / "anomaly_plot.png"
        Image.new("RGB", (20, 10), color=color).save(path)
        paths.append(path)
        state = store.append_r_payload(
            turn_id="turn-1",
            question="检查异常值",
            template_context={},
            payload={
                "job_id": job_name,
                "result": {
                    "ok": True,
                    "figures": [
                        {
                            "name": "anomaly_plot",
                            "path": str(path),
                            "mime_type": "image/png",
                            "metadata": {"title": f"{job_name} result"},
                        }
                    ],
                },
            },
        )

    figures = [item for item in state["analysis_turns"][0]["artifacts"] if item["kind"] == "figure"]
    assert len(figures) == 1
    assert figures[0]["path"] == str(paths[-1])
    assert figures[0]["title"] == "job-2 result"
    assert figures[0]["core_selected"] is True


def test_figure_zip_caps_total_generated_output_bytes(tmp_path, monkeypatch):
    from api import research

    monkeypatch.setenv("BAA_RESEARCH_EXPORT_MAX_TOTAL_FIGURE_OUTPUT_BYTES", "5")
    path = tmp_path / "figure.png"
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(path)
    state = {
        "analysis_turns": [
            {
                "artifacts": [
                    {"kind": "figure", "path": str(path), "included_in_paper": True},
                ]
            }
        ]
    }
    monkeypatch.setattr(
        research,
        "_figure_bytes_for_format",
        lambda _path, fmt: (b"123", f".{fmt}"),
    )
    app = Flask(__name__)

    with app.test_request_context("/"), pytest.raises(RequestEntityTooLarge):
        research._build_figures_zip(state, [tmp_path])


def test_result_store_rejects_payload_from_a_different_frozen_dataset(tmp_path, monkeypatch):
    from data.research_results import ResearchResultStore

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    store = ResearchResultStore("dataset-boundary-session")
    state = store.reset()
    state["dataset"] = {"dataset_id": "dataset-a", "name": "a.csv"}
    store.save(state)

    with pytest.raises(ValueError, match="dataset boundary"):
        store.append_r_payload(
            turn_id="turn-1",
            question="分析",
            template_context={},
            payload={"result": {"ok": True}},
            dataset_id="dataset-b",
        )


def test_result_store_rejects_frozen_payload_when_state_has_no_dataset(tmp_path, monkeypatch):
    from data.research_results import ResearchResultStore

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    store = ResearchResultStore("empty-dataset-boundary-session")
    store.reset()

    with pytest.raises(ValueError, match="dataset boundary"):
        store.append_r_payload(
            turn_id="turn-1",
            question="分析",
            template_context={},
            payload={"result": {"ok": True}},
            dataset_id="dataset-b",
        )


def test_freeform_continuation_with_a_frozen_plan_keeps_natural_language_source(tmp_path, monkeypatch):
    from data.research_results import ResearchResultStore

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    store = ResearchResultStore("freeform-continuation-source")
    store.reset()

    state = store.append_r_payload(
        turn_id="turn-freeform",
        question="继续完成多因素分析",
        template_context={
            "id": "",
            "plan_id": "plan-freeform",
            "title": "自由对话的多因素分析",
        },
        payload={
            "execution": {"plan_id": "plan-freeform"},
            "result": {"ok": True, "tables": [], "figures": [], "texts": [], "models": []},
        },
    )

    turn = state["analysis_turns"][0]
    assert turn["source"] == "natural_language"
    assert turn["template_id"] == ""
    assert turn["title"] == "自由对话的多因素分析"


def _install_optional_import_stubs(monkeypatch):
    import sys
    import types

    duckdb = types.ModuleType("duckdb")
    duckdb.DuckDBPyConnection = object
    duckdb.connect = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)


def test_research_result_v2_turn_paper_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)

    from api import create_app
    from data.research_results import ResearchResultStore

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    table_path = artifact_dir / "model_table.csv"
    table_path.write_text("term,estimate,p.value\nage,1.23,0.041\n", encoding="utf-8")
    figure_path = artifact_dir / "model_plot.png"
    Image.new("RGB", (120, 80), color=(255, 255, 255)).save(figure_path)
    Image.new("RGB", (120, 80), color=(255, 255, 255)).save(artifact_dir / "model_plot.tiff")
    note_path = artifact_dir / "method_note.txt"
    note_path.write_text("采用广义线性模型估计主要效应。", encoding="utf-8")
    model_path = artifact_dir / "fitted_model.txt"
    model_path.write_text("模型摘要：年龄系数 1.23，P=0.041。", encoding="utf-8")

    store = ResearchResultStore(sid)
    state = store.reset()
    state["dataset"] = {"dataset_id": "ds1", "name": "clinical.csv", "summary": {"rows": 2, "columns": 3}}
    store.save(state)
    state = store.append_r_payload(
        turn_id="turn-1",
        question="请做主要模型分析",
        template_context={"id": "logistic_model", "title": "Logistic 回归"},
        payload={
            "job_id": "job-1",
            "execution": {
                "analysis_plan": {"template_id": "logistic_model", "diagnostics": ["model_check"]},
                "diagnostic_status": {"model_check": "failed"},
            },
            "result_assessment": {
                "complete": True,
                "diagnostic_quality": "failed",
                "failed_diagnostics": ["model_check"],
            },
            "result": {
                "ok": True,
                "reproducibility": {
                    "status": "succeeded", "seed": 42, "rng_before": [10403, 1, 2],
                    "packages": {"stats": "4.4.1"},
                    "dataset_manifest": {"path": str(tmp_path / "clinical.csv"), "format": "csv"},
                },
                "tables": [
                    {
                        "name": "model_table",
                        "path": str(table_path),
                        "kind": "table",
                        "mime_type": "text/csv",
                        "metadata": {"role": "primary", "title": "主要模型结果", "paper_ready": True},
                    }
                ],
                "figures": [
                    {
                        "name": "model_plot",
                        "path": str(figure_path),
                        "kind": "figure",
                        "mime_type": "image/png",
                        "metadata": {"role": "primary", "title": "主要模型图", "caption": "模型估计结果。"},
                    }
                ],
                "texts": [
                    {
                        "name": "method_note",
                        "path": str(note_path),
                        "kind": "text",
                        "mime_type": "text/plain",
                        "metadata": {"role": "method_note", "title": "方法说明"},
                    }
                ],
                "models": [
                    {
                        "name": "fitted_model",
                        "path": str(model_path),
                        "kind": "model",
                        "mime_type": "text/plain",
                        "metadata": {"role": "model_summary", "title": "拟合模型摘要"},
                    }
                ],
                "warnings": [],
                "code": "baa_table('model_table', results)",
            },
        },
        conversation_job_id="conversation-1",
    )
    state = store.attach_narrative(
        turn_id="turn-1",
        text="结果显示，年龄与结局显著相关，可作为论文结果段落候选。",
    )

    turn = state["analysis_turns"][0]
    assert turn["turn_id"] == "turn-1"
    assert turn["template_title"] == "Logistic 回归"
    assert turn["summary"]["table_count"] == 1
    assert turn["summary"]["figure_count"] == 1
    assert turn["summary"]["model_count"] == 1
    assert turn["summary"]["method_note_count"] == 1
    assert len([item for item in turn["artifacts"] if item["core_selected"]]) <= 3
    assert "结果显示" in turn["narrative"]

    client = create_app().test_client()
    loaded = client.get(f"/api/session/{sid}/research/result-state")
    assert loaded.status_code == 200
    assert loaded.get_json()["state"]["schema_version"] == 2
    restored_turn = loaded.get_json()["state"]["analysis_turns"][0]
    assert restored_turn["status"] == "succeeded"
    assert restored_turn["diagnostic_quality"] == "failed"
    assert restored_turn["validation_scope"] == "artifact_structure"
    assert restored_turn["scientific_review_status"] == "not_reviewed"
    assert any("model_check" in flag["message"] for flag in restored_turn["quality_flags"])

    table_artifact = next(item for item in turn["artifacts"] if item["kind"] == "table")
    figure_artifact = next(item for item in turn["artifacts"] if item["kind"] == "figure")
    model_artifact = next(item for item in turn["artifacts"] if item["kind"] == "model")
    updated = store.patch_artifact(
        table_artifact["artifact_id"],
        {
            "core_selected": False,
            "included_in_paper": True,
            "title": "用户编辑后的主表标题",
            "footnote": "用户编辑后的主表脚注。",
        },
    )
    assert updated is not None
    state = store.append_r_payload(
        turn_id="turn-1",
        question="请做主要模型分析",
        template_context={"id": "logistic_model", "title": "Logistic 回归"},
        payload={
            "job_id": "job-1-repeat",
            "result": {
                "ok": True,
                "tables": [
                    {
                        "name": "model_table",
                        "path": str(table_path),
                        "kind": "table",
                        "mime_type": "text/csv",
                        "metadata": {"role": "primary", "title": "R 再次生成的标题", "paper_ready": True},
                    }
                ],
            },
        },
        conversation_job_id="conversation-1",
    )
    persisted_table = next(
        item
        for item in state["analysis_turns"][0]["artifacts"]
        if item["artifact_id"] == table_artifact["artifact_id"]
    )
    assert persisted_table["core_selected"] is False
    assert persisted_table["included_in_paper"] is True
    assert persisted_table["title"] == "用户编辑后的主表标题"
    assert persisted_table["footnote"] == "用户编辑后的主表脚注。"

    for artifact in (table_artifact, figure_artifact, model_artifact):
        response = client.patch(
            f"/api/session/{sid}/research/artifacts/{artifact['artifact_id']}/paper",
            json={"included_in_paper": True, "paper_group": "Primary analysis"},
        )
        assert response.status_code == 200

    word = client.post(f"/api/session/{sid}/research/export/word")
    assert word.status_code == 200
    assert word.get_data().startswith(b"PK")
    from docx import Document

    word_text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(word.get_data())).paragraphs)
    assert "模型结果" in word_text
    assert "年龄系数 1.23" in word_text

    excel = client.post(f"/api/session/{sid}/research/export/excel")
    assert excel.status_code == 200
    assert excel.get_data().startswith(b"PK")

    figures = client.post(f"/api/session/{sid}/research/export/figures")
    assert figures.status_code == 200
    figures_zip = tmp_path / "figures.zip"
    figures_zip.write_bytes(figures.get_data())
    with zipfile.ZipFile(figures_zip) as archive:
        names = archive.namelist()
    assert "figures_manifest.json" in names
    assert any(name.endswith(".png") for name in names)
    assert any(name.endswith(".pdf") for name in names)
    assert any(name.endswith(".tiff") for name in names)

    package = client.post(f"/api/session/{sid}/research/export/package")
    assert package.status_code == 200
    package_zip = tmp_path / "package.zip"
    package_zip.write_bytes(package.get_data())
    with zipfile.ZipFile(package_zip) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("analysis_manifest.json"))
        models_zip = archive.read("research_models.zip")
        runtime = json.loads(archive.read("reproducibility/step_001/runtime.json"))
        assert runtime["rng_before"] == [10403, 1, 2]
        assert runtime["packages"] == {"stats": "4.4.1"}
        replay_inputs = json.loads(archive.read("reproducibility/step_001/input_manifest.example.json"))
        assert replay_inputs["path"] == "REPLACE_WITH_LOCAL_PRIMARY_PATH"
        plan = json.loads(archive.read("reproducibility/step_001/analysis_plan.json"))
        assert plan["template_id"] == "logistic_model"
        assert archive.read("reproducibility/step_001/analysis.R").decode() == "baa_table('model_table', results)"
        executions = json.loads(archive.read("reproducibility/executions.json"))
        assert executions[0]["diagnostics"] == {"model_check": "failed"}
    assert {
        "research_results.docx",
        "research_tables.xlsx",
        "research_figures.zip",
        "research_models.zip",
        "delivery_checklist.md",
    } <= names
    with zipfile.ZipFile(io.BytesIO(models_zip)) as archive:
        model_names = archive.namelist()
        exported_model = next(name for name in model_names if name.startswith("models/"))
        assert "年龄系数 1.23" in archive.read(exported_model).decode("utf-8")
    assert manifest["overview"]["included_in_paper_count"] == 3


def test_chat_stream_archives_run_r_code_payload_to_analysis_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)

    from api import create_app
    from api import chat as chat_module
    from data.research_results import ResearchResultStore

    sid = "research-chat-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-chat"
    artifact_dir.mkdir(parents=True)
    table_path = artifact_dir / "summary.csv"
    table_path.write_text("metric,value\nn,12\n", encoding="utf-8")

    class FakeAgent:
        def run(self, *args, **kwargs):
            yield {
                "type": "tool_audit",
                "tool": "run_r_code",
                "ok": True,
                "content": json.dumps(
                    {
                        "job_id": "job-chat",
                        "result": {
                            "ok": True,
                            "tables": [
                                {
                                    "name": "summary",
                                    "path": str(table_path),
                                    "mime_type": "text/csv",
                                    "metadata": {"role": "primary", "title": "描述性统计"},
                                }
                            ],
                            "figures": [],
                            "texts": [],
                            "warnings": [],
                            "code": "baa_table('summary', summary_df)",
                        },
                    },
                    ensure_ascii=False,
                ),
            }
            yield {"type": "text", "content": "描述性统计已完成，可用于结果部分。"}

    monkeypatch.setattr(chat_module, "_build_agent", lambda *args, **kwargs: FakeAgent())

    client = create_app().test_client()
    response = client.post(
        f"/api/session/{sid}/chat",
        json={
            "message": "请做描述性统计",
            "turn_id": "turn-chat-1",
        },
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '"type": "analysis_turn"' in body
    assert '"analysis_turn_id": "turn-chat-1"' in body
    state, exists, _saved_at = ResearchResultStore(sid).load()
    assert exists is True
    assert state["overview"]["analysis_turn_count"] == 1
    turn = state["analysis_turns"][0]
    assert turn["turn_id"] == "turn-chat-1"
    assert turn["template_title"] == ""
    assert turn["summary"]["table_count"] == 1
    assert turn["artifacts"][0]["title"] == "描述性统计"
    assert "描述性统计已完成" in turn["narrative"]


def test_successful_artifact_run_clears_recovered_rscript_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)

    from data.research_results import ResearchResultStore

    sid = "research-recovered-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-success"
    artifact_dir.mkdir(parents=True)
    table_path = artifact_dir / "summary.csv"
    table_path.write_text("metric,value\nn,12\n", encoding="utf-8")

    store = ResearchResultStore(sid)
    store.append_r_payload(
        turn_id="turn-1",
        question="请做描述性统计",
        template_context={"id": "descriptive", "title": "描述性统计"},
        payload={
            "job_id": "job-failed",
            "result": {
                "ok": False,
                "error": "Rscript exited with code 1",
                "tables": [],
                "figures": [],
                "texts": [],
            },
        },
        conversation_job_id="conversation-1",
    )
    state = store.append_r_payload(
        turn_id="turn-1",
        question="请做描述性统计",
        template_context={"id": "descriptive", "title": "描述性统计"},
        payload={
            "job_id": "job-success",
            "result": {
                "ok": True,
                "tables": [
                    {
                        "name": "summary",
                        "path": str(table_path),
                        "mime_type": "text/csv",
                    }
                ],
                "figures": [],
                "texts": [],
                "warnings": [],
            },
        },
        conversation_job_id="conversation-1",
    )

    turn = state["analysis_turns"][0]
    assert turn["summary"]["table_count"] == 1
    assert turn["summary"]["quality_flag_count"] == 0
    assert [(item["job_id"], item["ok"]) for item in turn["execution_refs"]] == [
        ("job-failed", False),
        ("job-success", True),
    ]


def test_r_artifacts_fill_core_slots_after_existing_narrative(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)

    from data.research_results import ResearchResultStore

    sid = "research-core-fill-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-success"
    artifact_dir.mkdir(parents=True)
    table_path = artifact_dir / "summary.csv"
    table_path.write_text("metric,value\nn,12\n", encoding="utf-8")
    figure_path = artifact_dir / "plot.png"
    Image.new("RGB", (120, 80), color=(255, 255, 255)).save(figure_path)

    store = ResearchResultStore(sid)
    store.attach_narrative(turn_id="turn-1", text="先生成了一段结果解释。", title="结果解释")
    state = store.append_r_payload(
        turn_id="turn-1",
        question="请做描述性统计",
        template_context={"id": "descriptive", "title": "描述性统计"},
        payload={
            "job_id": "job-success",
            "result": {
                "ok": True,
                "tables": [
                    {
                        "name": "summary",
                        "path": str(table_path),
                        "mime_type": "text/csv",
                    }
                ],
                "figures": [
                    {
                        "name": "plot",
                        "path": str(figure_path),
                        "mime_type": "image/png",
                    }
                ],
                "texts": [],
                "warnings": [],
            },
        },
        conversation_job_id="conversation-1",
    )

    turn = state["analysis_turns"][0]
    core = [item for item in turn["artifacts"] if item["core_selected"]]
    assert len(core) == 3
    assert {item["kind"] for item in core} == {"text", "table", "figure"}


def test_chat_stream_archives_persisted_large_run_r_code_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)

    from api import create_app
    from api import chat as chat_module
    from agent.tools import results as results_module
    from data.research_results import ResearchResultStore

    monkeypatch.setattr(
        results_module,
        "_GLOBAL_RESULT_ROOT",
        tmp_path / "outputs" / "tool_results",
    )

    sid = "research-chat-large-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-large"
    artifact_dir.mkdir(parents=True)
    table_path = artifact_dir / "variable_dictionary.csv"
    table_path.write_text("variable,type\nage,numeric\n", encoding="utf-8")
    figure_path = artifact_dir / "correlation_heatmap.png"
    Image.new("RGB", (120, 80), color=(255, 255, 255)).save(figure_path)

    full_payload = {
        "job_id": "job-large",
        "result": {
            "ok": True,
            "tables": [
                {
                    "name": "variable_dictionary",
                    "path": str(table_path),
                    "mime_type": "text/csv",
                    "metadata": {"role": "data_dictionary", "title": "变量字典"},
                }
            ],
            "figures": [
                {
                    "name": "correlation_heatmap",
                    "path": str(figure_path),
                    "mime_type": "image/png",
                    "metadata": {"role": "diagnostic_figure", "title": "相关性热图"},
                }
            ],
            "texts": [],
            "warnings": [],
            "code": "baa_table('variable_dictionary', dictionary)",
        },
    }

    class FakeAgent:
        def run(self, *args, **kwargs):
            preview, artifact, _debug = results_module.persist_large_tool_result(
                sid,
                "run_r_code",
                full_payload,
                threshold=1,
            )
            yield {
                "type": "tool_audit",
                "tool": "run_r_code",
                "ok": True,
                "content": preview,
                "artifacts": [artifact],
            }
            yield {"type": "text", "content": "数据质量分析已完成。"}

    monkeypatch.setattr(chat_module, "_build_agent", lambda *args, **kwargs: FakeAgent())

    client = create_app().test_client()
    response = client.post(
        f"/api/session/{sid}/chat",
        json={
            "message": "请做数据质量分析",
            "turn_id": "turn-large-1",
        },
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert '"type": "analysis_turn"' in body
    state, exists, _saved_at = ResearchResultStore(sid).load()
    assert exists is True
    turn = state["analysis_turns"][0]
    assert turn["summary"]["table_count"] == 1
    assert turn["summary"]["figure_count"] == 1
    assert {item["name"] for item in turn["artifacts"]} >= {
        "variable_dictionary",
        "correlation_heatmap",
    }


def test_generation_mismatch_keeps_conversation_text_but_detaches_old_results(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from api.saved_sessions import _restore_research_workspace_binding
    from data.research_workspace import default_research_workspace

    source = tmp_path / "clinical.csv"
    source.write_text("age,outcome\n42,0\n55,1\n", encoding="utf-8")
    sid = "generation-recovery-session"
    workspace = default_research_workspace()
    dataset = workspace.replace(sid, source, source.name)
    record = workspace.current_record(sid)
    assert record is not None

    result_store = workspace.results(sid)
    persisted = json.loads(result_store.path.read_text(encoding="utf-8"))
    persisted["state"]["workspace_generation"] = "f" * 32
    result_store.path.write_text(json.dumps(persisted), encoding="utf-8")
    _state, exists, _saved_at = result_store.load()
    assert exists is False

    class Session:
        session_id = sid
        history = [
            {"role": "user", "content": "分析这个数据"},
            {"role": "assistant", "content": "调用工具", "tool_calls": [{"id": "tool-1"}]},
            {"role": "tool", "content": "old result"},
            {"role": "assistant", "content": "旧分析文字仍需保留"},
        ]
        research_dataset_id = dataset.dataset_id
        chart_ids = ["old-chart"]
        recent_sql = ["old-query"]
        recent_artifacts = [{"artifact_id": "old-artifact"}]
        turn_activations = [{"turn_id": "old-turn"}]

    session = Session()
    _restore_research_workspace_binding(
        session,
        {
            "research_workspace": {
                "dataset_id": dataset.dataset_id,
                "workspace_generation": record["workspace_generation"],
            }
        },
    )

    assert session.history == [
        {"role": "user", "content": "分析这个数据"},
        {"role": "assistant", "content": "旧分析文字仍需保留"},
    ]
    assert session.research_dataset_id == ""
    assert session.chart_ids == []
    assert session.recent_artifacts == []
    assert session.turn_activations == []


def test_workspace_storage_and_delete_are_scoped_to_research_data(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "workspace-delete-session"
    preferences_path = tmp_path / "config" / "preferences.json"
    preferences_path.parent.mkdir(parents=True)
    preferences_path.write_text("preferences-must-survive", encoding="utf-8")
    client = create_app().test_client()
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"age,outcome\n42,0\n"), "clinical.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200

    usage = client.get(f"/api/session/{sid}/research/storage").get_json()["storage"]
    assert usage["active"] is True
    assert usage["dataset_bytes"] > 0
    assert usage["result_bytes"] > 0
    assert usage["total_bytes"] >= usage["dataset_bytes"] + usage["result_bytes"]

    deleted = client.delete(f"/api/session/{sid}/research/workspace")
    assert deleted.status_code == 200
    assert deleted.get_json()["storage"]["total_bytes"] == 0
    assert client.get(f"/api/session/{sid}/research/datasets").get_json()["datasets"] == []
    assert preferences_path.read_text(encoding="utf-8") == "preferences-must-survive"


def test_startup_cleanup_removes_only_expired_staging_and_unreferenced_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from data.research_workspace import cleanup_research_storage, research_staging_root

    old_time = 1_000_000.0
    staging_old = research_staging_root() / "upload-old"
    staging_old.mkdir()
    (staging_old / "data.csv").write_text("x\n1\n", encoding="utf-8")
    orphan = tmp_path / "outputs" / "research" / "orphan-session" / "generations" / ("e" * 32)
    orphan.mkdir(parents=True)
    (orphan / "temporary.txt").write_text("orphan", encoding="utf-8")
    fresh = research_staging_root() / "upload-fresh"
    fresh.mkdir()
    for path in (staging_old / "data.csv", staging_old, orphan / "temporary.txt", orphan):
        os.utime(path, (old_time, old_time))

    summary = cleanup_research_storage(
        now_timestamp=old_time + (25 * 60 * 60),
        max_age_seconds=24 * 60 * 60,
    )

    assert summary["removed_staging"] == 1
    assert summary["removed_generations"] == 1
    assert not staging_old.exists()
    assert not orphan.exists()
    assert fresh.is_dir()


def test_result_store_builds_one_canonical_portfolio_manifest_across_continuation_turns(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    from agent.analysis.result_validation import build_artifact_validation_receipt
    from data.research_results import ResearchResultStore

    store = ResearchResultStore("portfolio-manifest-session")
    store.reset()
    table_path = tmp_path / "table_one.csv"
    table_path.write_text(
        "Variable,Overall,Group A,Group B,P value,SMD\nage,42,40,44,0.20,0.10\n",
        encoding="utf-8",
    )
    figure_path = tmp_path / "balance_plot.png"
    Image.new("RGB", (4, 4), color=(255, 255, 255)).save(figure_path)

    def verified_metadata(path, *, kind, role, title, job_id):
        metadata = {
            "dataset_id": "",
            "plan_id": "plan-1",
            "job_id": job_id,
            "source_execution_status": "succeeded",
            "role": role,
            "semantic_key": role,
            "title": title,
            "source_tool": "run_r_code",
        }
        receipt = build_artifact_validation_receipt(
            path,
            kind=kind,
            metadata=metadata,
            job_id=job_id,
            artifacts_root=tmp_path,
        )
        metadata.update(
            {
                "artifact_size_bytes": receipt["size_bytes"],
                "artifact_sha256": receipt["sha256"],
                "artifact_validation_receipt": receipt,
            }
        )
        return metadata

    portfolio = [
        {
            "role": "baseline_table",
            "kind": "table",
            "label": "Table 1",
            "tier": "core",
            "source": "template",
            "required": True,
        },
        {
            "role": "baseline_balance_plot",
            "kind": "figure",
            "label": "组间基线平衡图",
            "tier": "recommended",
            "source": "capability",
            "required": True,
        },
    ]
    first = store.append_r_payload(
        turn_id="turn-1",
        question="生成 Table 1",
        template_context={"id": "publication_table", "title": "论文 Table 1 / 三线表"},
        payload={
            "job_id": "job-1",
            "result": {
                "ok": True,
                "tables": [
                        {
                            "name": "table_one",
                            "path": str(table_path),
                            "kind": "table",
                            "metadata": verified_metadata(
                                table_path,
                                kind="table",
                                role="baseline_table",
                                title="Table 1",
                                job_id="job-1",
                            ),
                        }
                ],
            },
            "execution": {
                "plan_id": "plan-1",
                "status": "succeeded",
                "output_portfolio": portfolio,
                "portfolio_version": "1.0",
                "diagnostic_status": {},
            },
            "result_assessment": {
                "complete": False,
                "r_executed": True,
                "missing_roles": ["baseline_balance_plot"],
                "outstanding_roles": ["baseline_balance_plot"],
            },
        },
    )

    first_manifest = first["portfolio_manifests"]["plan-1"]
    assert first_manifest["complete"] is False
    assert {item["role"]: item["status"] for item in first_manifest["items"]} == {
        "baseline_table": "produced",
        "baseline_balance_plot": "missing",
    }
    first_turn = first["analysis_turns"][0]
    assert first_turn["status"] == "incomplete"
    assert first_turn["artifacts"][0]["portfolio_role"] == "baseline_table"

    completed = store.append_r_payload(
        turn_id="turn-2",
        question="继续补齐平衡图",
        template_context={"id": "publication_table", "title": "论文 Table 1 / 三线表"},
        payload={
            "job_id": "job-2",
            "result": {
                "ok": True,
                "figures": [
                        {
                            "name": "balance_plot",
                            "path": str(figure_path),
                            "kind": "figure",
                            "metadata": verified_metadata(
                                figure_path,
                                kind="figure",
                                role="baseline_balance_plot",
                                title="组间基线平衡图",
                                job_id="job-2",
                            ),
                        }
                ],
            },
            "execution": {
                "plan_id": "plan-1",
                "status": "succeeded",
                "output_portfolio": portfolio,
                "portfolio_version": "1.0",
                "diagnostic_status": {},
            },
            "result_assessment": {
                "complete": True,
                "r_executed": True,
                "missing_roles": [],
                "outstanding_roles": [],
            },
        },
    )

    manifest = completed["portfolio_manifests"]["plan-1"]
    assert manifest["complete"] is True
    assert {item["role"]: item["status"] for item in manifest["items"]} == {
        "baseline_table": "produced",
        "baseline_balance_plot": "produced",
    }
    assert {item["turn_id"] for item in manifest["artifacts"]} == {"turn-1", "turn-2"}
    assert {item["source_tool"] for item in manifest["artifacts"]} == {"run_r_code"}
    assert {item["job_id"] for item in manifest["artifacts"]} == {"job-1", "job-2"}
