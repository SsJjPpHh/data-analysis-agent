from __future__ import annotations

import json
import sys
import types

from api import create_app


def _install_optional_import_stubs(monkeypatch):
    duckdb = types.ModuleType("duckdb")
    duckdb.DuckDBPyConnection = object
    duckdb.connect = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)


def test_system_mode_endpoint(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    _install_optional_import_stubs(monkeypatch)
    app = create_app()
    client = app.test_client()
    resp = client.get("/api/system/mode")
    assert resp.status_code == 200
    assert resp.get_json()["mode"] == "research"


def test_upload_size_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("BAA_MAX_UPLOAD_BYTES", "12345")
    _install_optional_import_stubs(monkeypatch)
    app = create_app()

    assert app.config["MAX_CONTENT_LENGTH"] == 12345


def test_loopback_product_has_no_flask_request_size_ceiling_by_default(monkeypatch):
    monkeypatch.delenv("BAA_MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("BAA_DESKTOP_MODE", raising=False)
    _install_optional_import_stubs(monkeypatch)

    app = create_app()

    assert app.config["MAX_CONTENT_LENGTH"] is None


def test_removed_bi_entrypoints_are_not_registered(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    _install_optional_import_stubs(monkeypatch)
    app = create_app()
    rules = {rule.rule for rule in app.url_map.iter_rules()}

    for endpoint in (
        "/api/session/<sid>/connect-db",
        "/api/session/<sid>/connect-gsheets",
        "/api/session/<sid>/connect-api",
        "/api/session/<sid>/upload",
    ):
        assert endpoint not in rules


def test_research_mode_saved_session_load_does_not_restore_bi_source(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "research")
    _install_optional_import_stubs(monkeypatch)

    from api import saved_sessions
    from api.state import session_manager

    save_dir = tmp_path / "sessions"
    save_dir.mkdir()
    monkeypatch.setattr(saved_sessions, "SAVE_DIR", save_dir)

    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text("id,value\n1,42\n", encoding="utf-8")
    (save_dir / "legacy_bi.json").write_text(
        json.dumps(
            {
                "name": "legacy BI",
                "saved_at": "2026-06-30T00:00:00",
                "model_provider": "deepseek",
                "history": [{"role": "user", "content": "hello"}],
                "data_source": {
                    "display_name": "legacy.csv",
                    "ds_type": "CSVDataSource",
                    "file_path": str(csv_path),
                },
            }
        ),
        encoding="utf-8",
    )

    app = create_app()
    client = app.test_client()
    sid = "research-load-session"

    resp = client.post(f"/api/session/{sid}/load", json={"filename": "legacy_bi.json"})

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["history"] == [{"role": "user", "content": "hello"}]
    assert payload["model_provider"] == "deepseek"
    assert "ds_connected" not in payload
    assert "workspace_restored" not in payload
    assert session_manager.get(sid).data_source is None
