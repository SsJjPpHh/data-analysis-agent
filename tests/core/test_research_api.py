from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
import threading
import time
import types

from r_runtime.protocol import RArtifact, RRunResult


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _install_optional_import_stubs(monkeypatch):
    duckdb = types.ModuleType("duckdb")
    duckdb.DuckDBPyConnection = object
    duckdb.connect = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)


def test_research_upload_list_get(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()
    sid = "research-session"

    response = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"id,value\n1,42\n"), "clinical.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["dataset"]["name"] == "clinical.csv"
    assert payload["dataset"]["format"] == "csv"
    assert payload["datasets"][0]["dataset_id"] == payload["dataset"]["dataset_id"]
    assert payload["errors"] == []

    dataset_id = payload["dataset"]["dataset_id"]
    list_response = client.get(f"/api/session/{sid}/research/datasets")
    assert list_response.status_code == 200
    assert list_response.get_json()["datasets"][0]["dataset_id"] == dataset_id

    get_response = client.get(f"/api/session/{sid}/research/datasets/{dataset_id}")
    assert get_response.status_code == 200
    assert get_response.get_json()["dataset"]["name"] == "clinical.csv"


def test_research_upload_rejects_insufficient_final_storage_before_streaming(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()
    monkeypatch.setattr(
        "api.research.disk_usage",
        lambda _path: types.SimpleNamespace(total=1024, used=1024, free=0),
    )

    response = client.post(
        "/api/session/low-disk-session/research/upload",
        data={"file": (io.BytesIO(b"id,value\n1,42\n"), "clinical.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "存储空间不足" in response.get_json()["error"]
    listed = client.get("/api/session/low-disk-session/research/datasets").get_json()
    assert listed["datasets"] == []


def test_desktop_local_import_copies_native_selection_and_keeps_source_path_private(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("BAA_DESKTOP_MODE", "1")
    _install_optional_import_stubs(monkeypatch)
    from api import create_app
    from desktop.local_import import default_local_import_grants

    source = tmp_path / "GenKey账号" / "大型表达矩阵.mtx"
    source.parent.mkdir()
    source.write_text("%%MatrixMarket matrix coordinate integer general\n1 1 1\n1 1 7\n", encoding="utf-8")
    selection = default_local_import_grants().issue([source])
    client = create_app().test_client()

    response = client.post(
        "/api/session/local-import-session/research/import-local",
        json={"grant_token": selection.token},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["import_mode"] == "local_copy"
    assert payload["dataset"]["format"] == "mtx"
    assert payload["dataset"]["assets"][0]["access_mode"] == "copy"
    assert str(source) not in json.dumps(payload, ensure_ascii=False)

    from data.research_workspace import default_research_workspace

    active = default_research_workspace().current("local-import-session")
    assert active is not None
    copied_path = Path(active.assets[0].path)
    assert copied_path != source
    source.unlink()
    assert copied_path.read_text(encoding="utf-8").endswith("1 1 7\n")

    reused = client.post(
        "/api/session/local-import-session/research/import-local",
        json={"grant_token": selection.token},
    )
    assert reused.status_code == 400


def test_local_import_endpoint_is_disabled_outside_desktop_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_MODE", raising=False)
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    response = create_app().test_client().post(
        "/api/session/browser-session/research/import-local",
        json={"grant_token": "not-a-capability"},
    )

    assert response.status_code == 403


def test_research_upload_replaces_active_dataset_and_rejects_multiple_files(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()
    sid = "replace-session"
    first = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"id,value\n1,42\n"), "first.csv")},
        content_type="multipart/form-data",
    ).get_json()["dataset"]

    multiple = client.post(
        f"/api/session/{sid}/research/upload",
        data={
            "file": [
                (io.BytesIO(b"id,value\n1,1\n"), "one.csv"),
                (io.BytesIO(b"id,value\n2,2\n"), "two.csv"),
            ]
        },
        content_type="multipart/form-data",
    )
    assert multiple.status_code == 400

    second_response = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"id,value\n2,84\n"), "second.csv")},
        content_type="multipart/form-data",
    )
    second = second_response.get_json()["dataset"]
    listed = client.get(f"/api/session/{sid}/research/datasets").get_json()

    assert second_response.status_code == 200
    assert second["dataset_id"] != first["dataset_id"]
    assert [item["dataset_id"] for item in listed["datasets"]] == [second["dataset_id"]]
    assert client.get(f"/api/session/{sid}/research/datasets/{first['dataset_id']}").status_code == 404


def test_research_get_missing_dataset_returns_json_404(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()

    response = client.get("/api/session/research-session/research/datasets/missing")

    assert response.status_code == 404
    assert response.get_json() == {"error": "数据集不存在"}


def test_research_mode_index_renders_research_workbench(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "科研统计工作台" in response.get_data(as_text=True)
    assert "research_workbench.js" in response.get_data(as_text=True)
    assert "<dt>开发者</dt><dd>Z-Joy</dd>" in response.get_data(as_text=True)
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


def test_research_readiness_reports_base_capabilities(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    monkeypatch.setattr(
        research,
        "_r_runtime_readiness",
        lambda: {
            "backend": "local",
            "label": "本地 Rscript",
            "detail": "Rscript",
            "available": True,
            "rscript_bin": "Rscript",
            "timeout_seconds": 180,
        },
    )
    monkeypatch.setattr(
        research,
        "_llm_readiness",
        lambda: {
            "configured": False,
            "default_provider": "",
            "enabled_count": 0,
            "providers": [],
        },
    )

    client = create_app().test_client()
    response = client.get("/api/session/research-session/research/readiness")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["mode"] == "research"
    assert payload["research_mode"] is True
    assert payload["r_runtime"]["available"] is True
    assert payload["llm"]["configured"] is False
    assert payload["session"]["datasets"] == 0
    assert payload["session"]["active_jobs"] == 0
    statuses = {item["id"]: item["status"] for item in payload["capabilities"]}
    assert statuses["upload"] == "ready"
    assert statuses["r_execute"] == "ready"
    assert statuses["ai"] == "needs_setup"
    assert statuses["connectors"] == "disabled"
    assert payload["base_modules"]["job_runner"] is True
    assert payload["base_modules"]["external_connectors_enabled"] is False


def test_chat_template_context_sanitizer_bounds_contract_values():
    from api.chat import _sanitize_template_context

    payload = _sanitize_template_context(
        {
            "id": "x" * 200,
            "title": "Table 1 基线表",
            "category": "论文交付",
            "workflow": "论文交付",
            "variable_bindings": {"group": "treatment", "outcome": "response"},
            "package_names": ["ggplot2", "ggplot2", "tableone"] + [f"pkg{i}" for i in range(40)],
            "template_contract": {
                "applicability": {"variable_roles": ["group", "outcome"]},
                "methodology": {"assumptions": ["independent observations"]},
                "output_contract": {
                    "min_tables": "not-a-number",
                    "min_figures": 99,
                    "required_outputs": ["table_csv", "figure_png"],
                    "requires_diagnostics": 1,
                },
                "execution": {"cost": "low", "label": "fast"},
                "fallback_packages": ["stats", "graphics"],
            },
        }
    )

    assert payload["id"] == "x" * 120
    assert payload["package_names"][:3] == ["ggplot2", "tableone", "pkg0"]
    assert len(payload["package_names"]) == 16
    assert payload["variable_bindings"] == {"group": "treatment", "outcome": "response"}
    output_contract = payload["template_contract"]["output_contract"]
    assert output_contract["min_tables"] == 0
    assert output_contract["min_figures"] == 12
    assert output_contract["requires_diagnostics"] is True


def test_chat_template_context_accepts_only_a_bounded_frozen_plan_identifier():
    from api.chat import _sanitize_template_context

    payload = _sanitize_template_context(
        {
            "id": "regression_glm",
            "title": "线性与广义线性模型",
            "plan_id": "plan_1234567890abcdef" + "x" * 500,
        }
    )

    assert payload["plan_id"] == ("plan_1234567890abcdef" + "x" * 500)[:80]


def test_chat_rejects_a_template_plan_when_no_dataset_is_active(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    response = (
        create_app()
        .test_client()
        .post(
            "/api/session/no-dataset-session/chat",
            json={
                "message": "执行数据质量检查。",
                "template_context": {
                    "id": "data_quality",
                    "title": "数据质量与变量字典",
                    "plan_id": "untrusted-browser-plan",
                },
            },
        )
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "当前没有可用于该分析计划的数据，请重新上传数据并重新准备分析。",
        "code": "invalid_analysis_plan",
    }


def test_chat_restores_a_freeform_frozen_plan_without_relabeling_it_as_a_template(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from agent.analysis.models import AnalysisPlan, RequiredOutput
    from api import create_app
    from api import chat as chat_module
    from data.analysis_plan_store import AnalysisPlanStore
    from data.research_workspace import default_research_workspace

    client = create_app().test_client()
    sid = "freeform-frozen-continuation"
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"arm,score\nA,1\nB,2\n"), "trial.csv")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    dataset_id = upload.get_json()["dataset"]["dataset_id"]
    workspace = default_research_workspace()
    dataset = workspace.current(sid)
    record = workspace.current_record(sid) or {}
    summary = (dataset.profile.get("summary") or {}) if dataset is not None else {}
    plan = AnalysisPlan(
        plan_id="freeform-plan-to-continue",
        source="conversation",
        dataset_id=dataset_id,
        intent="自由对话的两组比较",
        category="基础统计",
        method_family="group_comparison",
        scenario="继续补齐科研产物",
        variable_bindings={"group": "arm", "outcome": "score"},
        packages=("stats",),
        prerequisites=(),
        assumptions=(),
        diagnostics=(),
        required_outputs=(RequiredOutput("comparison", "table"),),
        resource_class="standard",
        workspace_generation=str(record.get("workspace_generation") or ""),
        profile_revision=str(summary.get("profile_revision") or ""),
    )
    AnalysisPlanStore().save(sid, plan)
    captured = {}

    class FakeAgent:
        def run(self, *args, **kwargs):
            yield {"type": "text", "content": "已沿用自由分析计划。"}

    def build_agent(*args, **kwargs):
        captured["plan"] = kwargs.get("analysis_plan")
        return FakeAgent()

    monkeypatch.setattr(chat_module, "_build_agent", build_agent)
    response = client.post(
        f"/api/session/{sid}/chat",
        json={
            "message": "继续当前分析",
            "dataset_id": dataset_id,
            "template_context": {
                "id": "",
                "plan_id": plan.plan_id,
                "title": plan.intent,
            },
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]
    turn = next(item for item in events if item.get("type") == "analysis_turn")
    assert turn["source"] == "natural_language"
    assert turn["template_id"] == ""
    assert turn["analysis_plan"]["source"] == "conversation"
    assert captured["plan"].plan_id == plan.plan_id


def test_r_runtime_readiness_reports_local_r_version(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_RSCRIPT_BIN", str(tmp_path / "Rscript"))
    from api import research

    monkeypatch.setattr(
        research.HostREnvironment,
        "prepare",
        lambda self: types.SimpleNamespace(
            rscript=tmp_path / "Rscript",
            install_library=tmp_path / "r-library" / "4.6",
            library_mode="managed",
            package_count=2320,
            runtime_id="d" * 64,
            r_version="4.6.1",
        ),
    )

    payload = research._r_runtime_readiness()

    assert payload["backend"] == "local"
    assert payload["available"] is True
    assert payload["r_version"] == "4.6.1"
    assert payload["runtime_id"] == "d" * 64
    assert payload["package_count"] == 2320
    assert payload["library_mode"] == "managed"
    assert "4.6.1" in payload["version_text"]


def test_research_inspect_dataset_returns_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    table_path = tmp_path / "dataset_profile.csv"
    table_path.write_text(
        "column,type,missing,missing_pct,example\n"
        "age,numeric,0,0,40 | 50\n"
        "treatment_group,character,1,50,A | B\n",
        encoding="utf-8",
    )
    text_path = tmp_path / "dataset_overview.txt"
    text_path.write_text("Rows: 2\nColumns: 2", encoding="utf-8")

    class FakeBackend:
        request = None

        def run_code(self, request):
            self.request = request
            return RRunResult(
                ok=True,
                tables=[
                    RArtifact(
                        name="dataset_profile",
                        path=str(table_path),
                        kind="table",
                        mime_type="text/csv",
                    )
                ],
                texts=[
                    RArtifact(
                        name="dataset_overview",
                        path=str(text_path),
                        kind="text",
                        mime_type="text/plain",
                    )
                ],
                code="inspect code",
            )

    backend = FakeBackend()
    monkeypatch.setattr(research, "get_r_backend", lambda: backend)

    client = create_app().test_client()
    sid = "research-session"
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"age,treatment_group\n40,A\n50,\n"), "clinical.csv")},
        content_type="multipart/form-data",
    )
    dataset_id = upload.get_json()["dataset"]["dataset_id"]

    response = client.post(f"/api/session/{sid}/research/datasets/{dataset_id}/inspect")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["summary"]["rows"] == 2
    assert "local_semantics" not in payload["summary"]
    assert payload["summary"]["columns"] == 2
    assert payload["summary"]["overall_missing_pct"] == 25.0
    assert payload["summary"]["variables"][1]["name"] == "treatment_group"
    assert str(table_path) not in json.dumps(payload, ensure_ascii=False)
    assert not ({"code", "stdout", "stderr"} & payload["result"].keys())
    assert all("path" not in item for item in payload["result"]["tables"])
    state_response = client.get(f"/api/session/{sid}/research/result-state")
    assert state_response.status_code == 200
    result_state = state_response.get_json()["state"]
    assert result_state["schema_version"] == 2
    assert result_state["dataset"]["name"] == "clinical.csv"
    assert result_state["dataset"]["summary"]["rows"] == 2
    assert result_state["dataset"]["summary"]["columns"] == 2
    assert result_state["dataset"]["summary"]["variables"][0]["name"] == "age"
    assert str(tmp_path / "outputs" / "research" / sid) in backend.request.artifacts_dir
    assert ".baa_inspect" not in backend.request.artifacts_dir
    assert backend.request.conversation_job_id
    assert payload["job_id"] == backend.request.conversation_job_id
    runtime_asset = backend.request.dataset.assets[0]
    assert runtime_asset.size_bytes > 0
    assert runtime_asset.device is not None
    assert runtime_asset.inode is not None
    assert runtime_asset.object_type is not None
    assert runtime_asset.mtime_ns is not None
    assert "inode" not in json.dumps(payload, ensure_ascii=False)


def test_dataset_inspection_is_registered_and_can_be_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from agent.analysis.run_control import research_run_controller
    from api import create_app
    from api import research

    started = threading.Event()

    class BlockingBackend:
        request = None

        def run_code(self, request):
            self.request = request
            started.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if research_run_controller.is_cancelled(request.conversation_job_id):
                    return RRunResult(ok=False, error="R execution canceled")
                time.sleep(0.01)
            raise AssertionError("inspection was not canceled")

    backend = BlockingBackend()
    monkeypatch.setattr(research, "get_r_backend", lambda: backend)
    app = create_app()
    sid = "cancel-inspection-session"
    with app.test_client() as client:
        upload = client.post(
            f"/api/session/{sid}/research/upload",
            data={"file": (io.BytesIO(b"age\n40\n"), "clinical.csv")},
            content_type="multipart/form-data",
        )
    dataset_id = upload.get_json()["dataset"]["dataset_id"]
    response_holder = {}

    def inspect_in_background():
        with app.test_client() as client:
            response_holder["response"] = client.post(
                f"/api/session/{sid}/research/datasets/{dataset_id}/inspect"
            )

    thread = threading.Thread(target=inspect_in_background, daemon=True)
    thread.start()
    assert started.wait(timeout=2)
    assert backend.request.conversation_job_id
    with app.test_client() as client:
        stopped = client.post(f"/api/session/{sid}/stop")
    thread.join(timeout=2)

    assert stopped.status_code == 200
    assert thread.is_alive() is False
    response = response_holder["response"]
    assert response.status_code == 409
    assert response.get_json()["code"] == "analysis_canceled"


def test_template_prepare_endpoint_returns_readiness_contract_and_frozen_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    table_path = tmp_path / "dataset_profile.csv"
    table_path.write_text(
        "column,type,missing,missing_pct,non_missing,unique_count,example\n"
        "measure_a,numeric,0,0,4,4,\n"
        "measure_b,numeric,0,0,4,4,\n"
        "measure_c,numeric,0,0,4,4,\n"
        "site,factor,0,0,4,2,\n",
        encoding="utf-8",
    )
    text_path = tmp_path / "dataset_overview.txt"
    text_path.write_text("Rows: 4\nColumns: 3\nObject kind: data.frame", encoding="utf-8")

    class FakeBackend:
        def run_code(self, request):
            return RRunResult(
                ok=True,
                tables=[RArtifact("dataset_profile", str(table_path), "table", "text/csv")],
                texts=[RArtifact("dataset_overview", str(text_path), "text", "text/plain")],
            )

    monkeypatch.setattr(research, "get_r_backend", lambda: FakeBackend())
    client = create_app().test_client()
    sid = "prepare-session"
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"measure_a,measure_b,measure_c,site\n1,2,3,A\n"), "study.csv")},
        content_type="multipart/form-data",
    )
    dataset_id = upload.get_json()["dataset"]["dataset_id"]
    assert client.post(f"/api/session/{sid}/research/datasets/{dataset_id}/inspect").status_code == 200

    direct = client.post(
        f"/api/session/{sid}/research/templates/data_quality/prepare",
        json={"dataset_id": dataset_id, "inputs": {}},
    )
    assert direct.status_code == 200
    direct_payload = direct.get_json()
    assert direct_payload["status"] == "ready"
    assert direct_payload["status_label"] == "可直接使用"
    assert direct_payload["plan"]["plan_id"]
    assert direct_payload["input_controls"]

    unresolved = client.post(
        f"/api/session/{sid}/research/templates/regression_glm/prepare",
        json={"dataset_id": dataset_id, "inputs": {}},
    ).get_json()
    assert unresolved["status"] == "needs_input"
    assert unresolved["status_label"] == "需确认关键项"
    assert {item["input_id"] for item in unresolved["questions"]} == {"outcome", "exposure"}
    assert {item["input_id"] for item in unresolved["input_controls"]} >= {
        "outcome",
        "exposure",
    }

    ready = client.post(
        f"/api/session/{sid}/research/templates/regression_glm/prepare",
        json={
            "dataset_id": dataset_id,
            "inputs": {"outcome": "measure_a", "exposure": "measure_b"},
        },
    )
    assert ready.status_code == 200
    ready_payload = ready.get_json()
    assert ready_payload["status"] == "ready"
    assert ready_payload["plan"]["template_id"] == "regression_glm"
    assert ready_payload["plan"]["resolved_inputs"]["outcome"]["provenance"] == "user"

    ambiguous_matrix = client.post(
        f"/api/session/{sid}/research/templates/bulk_rnaseq/prepare",
        json={"dataset_id": dataset_id, "inputs": {}},
    )
    assert ambiguous_matrix.status_code == 200
    ambiguous_payload = ambiguous_matrix.get_json()
    assert ambiguous_payload["status"] == "needs_input"
    assert {item["input_id"] for item in ambiguous_payload["questions"]} == {
        "__matrix_orientation__"
    }
    assert ambiguous_payload["plan"] is None

    confirmed_matrix = client.post(
        f"/api/session/{sid}/research/templates/bulk_rnaseq/prepare",
        json={
            "dataset_id": dataset_id,
            "inputs": {
                "__matrix_orientation__": "samples_by_features", "group": "site",
                "feature_columns": ["measure_a", "measure_b", "measure_c"],
                "ref_group": "A", "comparison_group": "B",
            },
        },
    ).get_json()
    assert confirmed_matrix["status"] == "ready"
    assert confirmed_matrix["plan"]["resolved_inputs"]["__matrix_orientation__"]["value"] == (
        "samples_by_features"
    )

    rejected_matrix = client.post(
        f"/api/session/{sid}/research/templates/bulk_rnaseq/prepare",
        json={
            "dataset_id": dataset_id,
            "inputs": {"__matrix_orientation__": "not_target_matrix"},
        },
    ).get_json()
    assert rejected_matrix["status"] == "not_applicable"
    assert rejected_matrix["compatibility_guidance"]["alternative_templates"]

    unrelated = client.post(
        f"/api/session/{sid}/research/templates/single_cell/prepare",
        json={"dataset_id": dataset_id, "inputs": {}},
    )
    assert unrelated.status_code == 200
    unrelated_payload = unrelated.get_json()
    # The declared cell matrix adapter is reachable, but an ordinary numeric
    # CSV still cannot run before explicit scientific orientation confirmation.
    assert unrelated_payload["status"] == "needs_input", unrelated_payload
    assert unrelated_payload["status_label"] == "需确认关键项"
    assert "__matrix_orientation__" in {q["input_id"] for q in unrelated_payload["questions"]}
    assert unrelated_payload["plan"] is None

    invalid_inputs = client.post(
        f"/api/session/{sid}/research/templates/data_quality/prepare",
        json={"dataset_id": dataset_id, "inputs": []},
    )
    assert invalid_inputs.status_code == 400

    oversized = client.post(
        f"/api/session/{sid}/research/templates/data_quality/prepare",
        data=json.dumps({"padding": "x" * (65 * 1024)}),
        content_type="application/json",
    )
    assert oversized.status_code == 413


def test_dependency_install_endpoint_confirms_installs_rechecks_and_keeps_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    installed = []
    ticket = types.SimpleNamespace(ticket_id="ticket-one")
    reports = [
        {
            "status": "installable",
            "install_packages": ["DESeq2"],
            "ticket_id": "ticket-one",
            "plan_id": "plan-one",
        },
        {
            "status": "ready",
            "install_packages": [],
            "ticket_id": "",
            "plan_id": "plan-one",
            "can_resume": True,
        },
    ]

    class Environment:
        def prepare(self):
            return "verified-r"

        def ensure_packages(self, packages, *, verified, conversation_job_id, on_output):
            assert conversation_job_id
            on_output("test package installation finished")
            installed.append((tuple(packages), verified))

    monkeypatch.setattr(research, "dependency_ticket", lambda ticket_id, sid: ticket)
    monkeypatch.setattr(
        research,
        "check_dependency_ticket",
        lambda sid, ticket_id: reports.pop(0),
    )
    monkeypatch.setattr(
        research,
        "HostREnvironment",
        types.SimpleNamespace(locate=lambda: Environment()),
    )
    monkeypatch.setattr(research, "invalidate_template_runtime_inventory", lambda: None)

    client = create_app().test_client()
    response = client.post(
        "/api/session/dependency-session/research/dependencies/ticket-one/install"
    )

    assert response.status_code == 202
    import time
    job_id = response.get_json()["job_id"]
    deadline = time.monotonic() + 3
    while True:
        job = client.get(f"/api/session/dependency-session/jobs/{job_id}").get_json()["job"]
        if job["status"] in {"succeeded", "failed", "canceled"} or time.monotonic() > deadline:
            break
        time.sleep(0.01)
    assert job["status"] == "succeeded", job
    assert installed == [(('DESeq2',), "verified-r")]
    assert job["result"]["dependency_gate"]["status"] == "ready"
    assert job["result"]["dependency_gate"]["plan_id"] == "plan-one"


def test_dependency_install_cancel_releases_session_for_followup(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from agent.analysis.run_control import ResearchRunCancelled, research_run_controller
    from api import create_app, research
    from api.state import session_manager

    started = threading.Event()

    class Environment:
        def prepare(self):
            return "verified-r"

        def ensure_packages(self, packages, *, verified, conversation_job_id, on_output):
            on_output("installing test package")
            started.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if research_run_controller.is_cancelled(conversation_job_id):
                    raise ResearchRunCancelled("canceled")
                time.sleep(0.01)
            raise AssertionError("installation did not observe cancellation")

    monkeypatch.setattr(research, "dependency_ticket", lambda *args: types.SimpleNamespace(ticket_id="cancel-ticket"))
    monkeypatch.setattr(research, "check_dependency_ticket", lambda *args: {"install_packages": ["DESeq2"]})
    monkeypatch.setattr(research, "HostREnvironment", types.SimpleNamespace(locate=lambda: Environment()))
    monkeypatch.setattr(research, "invalidate_template_runtime_inventory", lambda: None)
    client = create_app().test_client()
    sid = "cancel-dependency-session"
    response = client.post(f"/api/session/{sid}/research/dependencies/cancel-ticket/install")
    assert response.status_code == 202
    job_id = response.get_json()["job_id"]
    assert started.wait(timeout=2)
    assert client.post(f"/api/session/{sid}/jobs/{job_id}/cancel").status_code == 200
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = client.get(f"/api/session/{sid}/jobs/{job_id}").get_json()["job"]
        if job["status"] == "canceled":
            break
        time.sleep(0.01)
    assert job["status"] == "canceled", job
    session = session_manager.get(sid)
    while not session.begin_session_mutation():
        assert time.monotonic() < deadline, "session remained locked after cancellation"
        time.sleep(0.01)
    session.end_session_mutation()


def test_dataset_inspection_pauses_for_loader_packages_before_running_r(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    class Backend:
        run_calls = 0
        runtime_env = {"PATH": ""}

        def package_readiness(self, packages, timeout_seconds=30, *, stop_after_usable=False):
            return {
                "ok": True,
                "packages": [
                    {
                        "package": package,
                        "installed": package != "readr",
                        "loadable": package != "readr",
                        "status": "missing" if package == "readr" else "ready",
                        "warnings": [],
                        "error": "Package is not installed" if package == "readr" else "",
                    }
                    for package in packages
                ],
            }

        def run_code(self, request):
            self.run_calls += 1
            return RRunResult(ok=True)

    backend = Backend()
    monkeypatch.setattr(research, "get_r_backend", lambda: backend)
    client = create_app().test_client()
    sid = "inspection-dependency-session"
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"id,value\n1,42\n"), "clinical.csv")},
        content_type="multipart/form-data",
    ).get_json()

    response = client.post(
        f"/api/session/{sid}/research/datasets/{upload['dataset']['dataset_id']}/inspect"
    )

    assert response.status_code == 409
    assert response.get_json()["code"] == "dependency_preflight"
    assert response.get_json()["dependency_gate"]["install_packages"] == ["readr"]
    assert backend.run_calls == 0


def test_research_inspect_dataset_returns_backend_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app, research

    class FakeBackend:
        def run_code(self, request):
            return RRunResult(
                ok=False,
                error="Unable to start Rscript at /volume/private/Rscript",
                stderr=r"failed at \\server\share\private.csv",
                code="source('/volume/private/script.R')",
            )

    monkeypatch.setattr(research, "get_r_backend", lambda: FakeBackend())

    client = create_app().test_client()
    sid = "research-session"
    upload = client.post(
        f"/api/session/{sid}/research/upload",
        data={"file": (io.BytesIO(b"age,cvd\n40,0\n50,1\n"), "clinical.csv")},
        content_type="multipart/form-data",
    )
    dataset_id = upload.get_json()["dataset"]["dataset_id"]

    response = client.post(f"/api/session/{sid}/research/datasets/{dataset_id}/inspect")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"].startswith("数据理解失败（错误编号：")
    response_text = response.get_data(as_text=True)
    assert "/volume/private" not in response_text
    assert r"\\server\share" not in response_text
    assert "Rscript" not in response_text
    assert not ({"code", "stdout", "stderr"} & payload["result"].keys())


def test_research_artifact_serves_session_scoped_csv_json(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "table.csv"
    artifact_path.write_text("term,estimate\nage,1.25\n", encoding="utf-8")

    client = create_app().test_client()

    response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "json"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kind"] == "table"
    assert payload["columns"] == ["term", "estimate"]
    assert payload["rows"] == [{"term": "age", "estimate": "1.25"}]
    assert payload["truncated"] is False
    assert payload["total_rows"] == 1
    assert payload["total_columns"] == 2


def test_research_artifact_preview_enforces_rows_columns_fields_and_response_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-preview-budget"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "wide.csv"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"column_{index}" for index in range(55)])
    for row_index in range(105):
        writer.writerow(
            [
                ("界" * 1000) if column_index == 0 else f"{row_index}-{column_index}"
                for column_index in range(55)
            ]
        )
    artifact_path.write_text(buffer.getvalue(), encoding="utf-8")

    response = (
        create_app()
        .test_client()
        .get(
            f"/api/session/{sid}/research/artifact",
            query_string={"path": str(artifact_path), "format": "json"},
        )
    )

    assert response.status_code == 200
    assert len(response.get_data()) <= 512 * 1024
    payload = response.get_json()
    assert payload["truncated"] is True
    assert payload["total_rows"] == 105
    assert payload["total_columns"] == 55
    assert len(payload["rows"]) <= 100
    assert len(payload["columns"]) == 50
    assert all(
        len(str(value).encode("utf-8")) <= 2 * 1024 for row in payload["rows"] for value in row.values()
    )


def test_research_artifact_downloads_csv_docx_and_xlsx_table(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "table.csv"
    artifact_path.write_text(
        "term,estimate\nage,1.25\n=2+2,@command\n-12.5,+1e3\n-not-number,plain\n",
        encoding="utf-8",
    )

    client = create_app().test_client()

    csv_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "csv"},
    )
    assert csv_response.status_code == 200
    assert "attachment" in csv_response.headers["Content-Disposition"]
    assert csv_response.get_data(as_text=True).startswith("term,estimate")
    downloaded_rows = list(csv.reader(io.StringIO(csv_response.get_data(as_text=True))))
    assert downloaded_rows[2] == ["'=2+2", "'@command"]
    assert downloaded_rows[3] == ["-12.5", "+1e3"]
    assert downloaded_rows[4] == ["'-not-number", "plain"]

    docx_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "docx"},
    )
    assert docx_response.status_code == 200
    assert "wordprocessingml.document" in docx_response.headers["Content-Type"]
    assert "table.docx" in docx_response.headers["Content-Disposition"]
    assert docx_response.get_data().startswith(b"PK")

    legacy_word_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "word"},
    )
    assert legacy_word_response.status_code == 200
    assert "wordprocessingml.document" in legacy_word_response.headers["Content-Type"]
    assert "table.docx" in legacy_word_response.headers["Content-Disposition"]

    xlsx_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "xlsx"},
    )
    assert xlsx_response.status_code == 200
    assert "spreadsheetml.sheet" in xlsx_response.headers["Content-Type"]
    assert "table.xlsx" in xlsx_response.headers["Content-Disposition"]
    assert xlsx_response.get_data().startswith(b"PK")


def test_research_artifact_supports_tsv_preview_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "table.tsv"
    artifact_path.write_text("term\testimate\nage\t1.25\n", encoding="utf-8")

    client = create_app().test_client()

    json_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "json"},
    )
    assert json_response.status_code == 200
    assert json_response.get_json()["rows"] == [{"term": "age", "estimate": "1.25"}]

    csv_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "csv"},
    )
    assert csv_response.status_code == 200
    assert csv_response.get_data().decode("utf-8-sig").startswith("term,estimate")

    docx_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "docx"},
    )
    assert docx_response.status_code == 200
    assert docx_response.get_data().startswith(b"PK")

    xlsx_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "xlsx"},
    )
    assert xlsx_response.status_code == 200
    assert xlsx_response.get_data().startswith(b"PK")


def test_research_templates_are_backend_inventory_enriched(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    import api.research as research_api
    from api import create_app

    class Backend:
        def list_packages(self, timeout_seconds=15):
            return {
                "ok": True,
                "r_version": "4.6.1",
                "packages": [
                    {"package": "tidyverse"},
                    {"package": "data.table"},
                    {"package": "janitor"},
                    {"package": "skimr"},
                    {"package": "DataExplorer"},
                    {"package": "ggplot2"},
                ],
            }

    monkeypatch.setattr(research_api, "get_r_backend", lambda: Backend())
    research_api._template_runtime_cache.update(
        {
            "expires_at": 0.0,
            "packages": [],
            "ok": False,
            "error": "",
            "r_version": "",
        }
    )
    client = create_app().test_client()
    response = client.get("/api/session/research-session/research/templates")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["schema_version"] == "4.0.0"
    assert 205 <= len(payload["templates"]) <= 215
    assert payload["inventory"]["declared_research_packages"] >= 250
    assert payload["inventory"]["live_inventory_ok"] is True
    assert payload["inventory"]["live_installed_packages"] == 6
    assert payload["inventory"]["live_r_version"] == "4.6.1"
    assert payload["capability_groups"] >= 20
    assert payload["catalog"]["user_templates"] == len(payload["templates"])
    assert payload["catalog"]["generated_wrappers"] == 0
    assert payload["aliases"]["publication_graphics_export"] == "publication_table"
    assert not any(item["id"].startswith("capability_") for item in payload["templates"])
    first = payload["templates"][0]
    assert first["id"] == "data_quality"
    assert first["source"] == "analysis-catalog-v4-live-inventory"
    assert first["runtime_inventory_ok"] is True
    assert "tidyverse" in first["runtime_installed_packages"]
    assert "package_summary" in first
    assert first["family"] == "data_import_cleaning_profiling"
    assert first["template_contract"]["schema_version"] == "4.0.0"
    assert first["input_contract"]["inputs"]
    assert first["input_contract"]["inputs"][0]["kind"] in {
        "dataset",
        "column",
        "columns",
        "level",
        "parameter",
        "object",
        "specification",
    }
    assert first["template_contract"]["applicability"]["variable_roles"]
    assert first["template_contract"]["output_contract"]["required_outputs"]
    assert first["template_contract"]["output_contract"]["requires_code"] is True
    assert first["output_contract"]["baseline_is_floor"] is True
    assert first["output_contract"]["required"][0]["tier"] == "core"
    assert first["output_contract"]["required"][0]["source"] == "template"
    assert first["template_contract"]["output_contract"]["baseline_is_floor"] is True
    visible_template_text = json.dumps(
        {
            "prompts": [item["analysis_prompt"] for item in payload["templates"]],
            "appendix": [item["template_prompt_appendix"] for item in payload["templates"]],
            "questions": [
                item.get("curated_profile", {}).get("questions", []) for item in payload["templates"]
            ],
            "required_outputs": [
                item["template_contract"]["output_contract"]["required_outputs"]
                for item in payload["templates"]
            ],
        },
        ensure_ascii=False,
    )
    assert "代码依据" not in visible_template_text
    assert "R 方法与代码依据" not in visible_template_text
    assert "quality" not in first
    assert first["template_prompt_appendix"].startswith("模板合同")
    assert "最低产物基线" in first["template_prompt_appendix"]
    publication = next(item for item in payload["templates"] if item["id"] == "publication_table")
    publication_roles = {
        item["role"] for item in publication["output_contract"]["required"]
    }
    assert "baseline_table" in publication_roles
    assert not {
        "table_2",
        "balance_table",
        "table_4",
        "table_5",
        "baseline_table_2",
    } & publication_roles
    prompts = [item["analysis_prompt"] for item in payload["templates"]]
    assert len(prompts) == len(payload["templates"])
    assert all(prompt.strip() for prompt in prompts)
    assert not any("worker" in prompt.lower() or "镜像" in prompt for prompt in prompts)
    assert first["curated_profile"]["priority"] == "catalog_v4"
    assert first["curated_profile"]["label"] == "目录 V4"
    assert first["curated_profile"]["scenario"]


def test_research_result_state_round_trip_and_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    sid = "research-session"
    client = create_app().test_client()

    missing = client.get(f"/api/session/{sid}/research/result-state")
    assert missing.status_code == 200
    assert missing.get_json()["exists"] is False

    state = {
        "active_dataset_id": "ds1",
        "analysisResult": {"payload": {"result": {"ok": True, "tables": []}}},
        "selectedTableIndex": 0,
    }
    saved = client.post(f"/api/session/{sid}/research/result-state", json={"state": state})
    assert saved.status_code == 200

    loaded = client.get(f"/api/session/{sid}/research/result-state")
    assert loaded.status_code == 200
    payload = loaded.get_json()
    assert payload["exists"] is True
    assert payload["state"] == state
    assert payload["saved_at"]

    deleted = client.delete(f"/api/session/{sid}/research/result-state")
    assert deleted.status_code == 200
    assert client.get(f"/api/session/{sid}/research/result-state").get_json()["exists"] is False


def test_research_result_state_rejects_unsafe_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    client = create_app().test_client()
    response = client.post(
        "/api/session/%2E%2E/research/result-state",
        json={"state": {"unsafe": True}},
    )

    assert response.status_code == 400
    assert not (tmp_path / "outputs" / "analysis_state.json").exists()


def test_research_artifact_downloads_png_and_pdf_figure(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from PIL import Image
    from api import create_app

    sid = "research-session"
    artifact_dir = tmp_path / "outputs" / "research" / sid / "job-1"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "plot.png"
    Image.new("RGB", (80, 60), color=(255, 255, 255)).save(artifact_path)

    client = create_app().test_client()

    png_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "png"},
    )
    assert png_response.status_code == 200
    assert "image/png" in png_response.headers["Content-Type"]
    assert "attachment" in png_response.headers["Content-Disposition"]

    pdf_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "pdf"},
    )
    assert pdf_response.status_code == 200
    assert "application/pdf" in pdf_response.headers["Content-Type"]
    assert pdf_response.get_data().startswith(b"%PDF")

    tiff_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "tiff"},
    )
    assert tiff_response.status_code == 200
    assert "image/tiff" in tiff_response.headers["Content-Type"]
    assert "attachment" in tiff_response.headers["Content-Disposition"]
    assert tiff_response.get_data()[:2] in {b"II", b"MM"}

    native_pdf = artifact_dir / "plot.pdf"
    native_pdf.write_bytes(b"%PDF-1.4\n% native companion\n")
    native_response = client.get(
        f"/api/session/{sid}/research/artifact",
        query_string={"path": str(artifact_path), "format": "pdf"},
    )
    assert native_response.status_code == 200
    assert native_response.get_data() == b"%PDF-1.4\n% native companion\n"


def test_research_artifact_rejects_outside_path(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app

    outside = tmp_path / "outside.csv"
    outside.write_text("x\n1\n", encoding="utf-8")

    client = create_app().test_client()
    response = client.get(
        "/api/session/research-session/research/artifact",
        query_string={"path": str(outside), "format": "json"},
    )

    assert response.status_code == 404


def test_chat_stream_defaults_missing_data_context(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app
    from api import chat as chat_module

    seen = {}

    class FakeAgent:
        def run(self, *args, **kwargs):
            seen["data_context"] = kwargs.get("data_context")
            yield {"type": "text", "content": "ok"}

    monkeypatch.setattr(chat_module, "_build_agent", lambda *args, **kwargs: FakeAgent())

    client = create_app().test_client()
    response = client.post(
        "/api/session/research-session/chat",
        json={"message": "以 CVD 作为结局，age 作为协变量，进行分析。"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "name 'data_context' is not defined" not in body
    assert '"type": "text"' in body
    assert seen["data_context"] == {}


def test_chat_stream_surfaces_invalid_deepseek_key_as_actionable_public_error(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    _install_optional_import_stubs(monkeypatch)
    from api import create_app
    from api import chat as chat_module

    class FakeAgent:
        def run(self, *args, **kwargs):
            yield {
                "type": "error",
                "message": (
                    "LLM 调用失败: Error code: 401 - "
                    "{'error': {'message': 'Authentication Fails, "
                    "Your api key: ****f9b3 is invalid', "
                    "'type': 'authentication_error'}}"
                ),
            }

    monkeypatch.setattr(chat_module, "_build_agent", lambda *args, **kwargs: FakeAgent())

    client = create_app().test_client()
    response = client.post(
        "/api/session/research-session/chat",
        json={"message": "执行数据质量与变量字典。"},
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "DeepSeek API Key 无效或已失效，请在系统设置中重新填写有效的 API Key" in body
    assert '"code": "invalid_api_key"' in body
    assert "分析服务暂时异常" not in body
    assert "****f9b3" not in body
    assert "authentication_error" not in body
