from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace



def _reset_singletons() -> None:
    import LLM.llm_config_manager as llm_config_manager

    llm_config_manager._config_manager = None
    import api.state as state

    state.config_manager = llm_config_manager.get_config_manager()


def test_application_exposes_only_local_product_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.delenv("BAA_ENV", raising=False)
    _reset_singletons()

    from api import create_app

    rules = {rule.rule for rule in create_app().url_map.iter_rules()}
    assert {"/", "/api/local/session", "/api/system/runtime", "/api/system/r-library"} <= rules
    assert not any(rule.startswith(("/api/auth/", "/api/admin/", "/api/engine/")) for rule in rules)
    assert "/admin" not in rules and "/login" not in rules


def test_system_runtime_exposes_host_r_provenance_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAA_RSCRIPT_BIN", str(tmp_path / "Rscript"))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.delenv("BAA_ENV", raising=False)
    monkeypatch.setenv("BAA_APP_VERSION", "obsolete-app-version")
    _reset_singletons()

    class FakeEnvironment:
        def __init__(self, rscript):
            self.rscript = Path(rscript)

        def prepare(self):
            return SimpleNamespace(
                rscript=self.rscript,
                install_library=tmp_path / "r-library" / "4.6",
                user_library=tmp_path / "user-library" / "4.6",
                library_mode="managed",
                package_count=2320,
                runtime_id="a" * 64,
                r_version="4.6.1",
            )

        def process_environment(self, **_kwargs):
            return {"PATH": ""}

    monkeypatch.setattr("api.HostREnvironment", FakeEnvironment)
    from api import create_app
    from desktop.version import APPLICATION_VERSION

    payload = create_app().test_client().get("/api/system/runtime").get_json()
    assert payload["app"]["version"] == APPLICATION_VERSION
    assert payload["app"]["version"] == "v1.0"
    assert payload["app"]["developer"] == "Z-Joy"
    assert payload["dependencies"]["source"] == "current-application-environment"
    assert payload["dependencies"]["python_version"]
    r_runtime = dict(payload["r"])
    isolation = r_runtime.pop("isolation")
    available = r_runtime.pop("available")
    assert available == isolation["available"]
    assert isolation["verification"] == "launcher_only"
    external_readiness = r_runtime.pop("external_readiness")
    local_resources = {
        key: r_runtime.pop(key)
        for key in (
            "worker_count",
            "native_thread_count",
            "memory_budget_bytes",
            "in_memory_threshold_bytes",
        )
    }
    assert r_runtime == {
        "backend": "local",
        "source": "user-installed",
        "rscript_bin": str(tmp_path / "Rscript"),
        "package_library": str(tmp_path / "r-library" / "4.6"),
        "library_mode": "managed",
        "user_library": str(tmp_path / "user-library" / "4.6"),
        "managed_library_root": str(tmp_path / "r-library"),
        "custom_library_root": "",
        "library_settings_warning": "",
        "installed_packages": 2320,
        "runtime_id": "a" * 64,
        "r_version": "4.6.1",
    }
    assert local_resources["worker_count"] >= 1
    assert local_resources["native_thread_count"] >= 1
    assert local_resources["memory_budget_bytes"] >= 0
    assert local_resources["in_memory_threshold_bytes"] >= 256 * 1024**2
    assert external_readiness["counts"] == {
        "ready": 0,
        "missing": 7,
        "needs_setup": 0,
        "warning": 0,
    }
    assert external_readiness["policy"]["mutates_host"] is False
    assert {item["dependency_id"] for item in external_readiness["states"]} == {
        "c_cpp_toolchain",
        "fortran_toolchain",
        "cmake",
        "pkg_config",
        "git",
        "java",
        "python_ml",
    }
    assert "license" not in payload
    serialized = json.dumps(payload).lower()
    assert "license-key" not in serialized and "api_key" not in serialized


def test_source_server_is_available_without_desktop_token(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAA_CSRF_FORCE", "1")
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    _reset_singletons()

    from api import create_app

    client = create_app().test_client()
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 200
    local_session = client.get("/api/local/session").get_json()
    assert local_session["mode"] == "browser"
    assert local_session["csrf_token"]
    assert client.post("/api/session/new").status_code == 403
    response = client.post(
        "/api/session/new", headers={"X-CSRF-Token": local_session["csrf_token"]}
    )
    assert response.status_code == 200


def test_runtime_settings_remain_available_when_r_is_missing(monkeypatch, tmp_path):
    from r_runtime.environment import REnvironmentError
    from api import create_app

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.setenv("BAA_ENV", "production")
    _reset_singletons()
    def missing_r(_self):
        raise REnvironmentError("Rscript not found")
    monkeypatch.setattr("api.HostREnvironment.prepare", missing_r)
    response = create_app().test_client().get("/api/system/runtime")
    assert response.status_code == 200
    assert response.get_json()["r"]["available"] is False
    assert "Rscript not found" in response.get_json()["r"]["error"]
    assert response.get_json()["dependencies"]["ready"] is True
    assert response.get_json()["dependencies"]["qt_version"]


def test_desktop_token_bootstraps_same_origin_session(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAA_DESKTOP_TOKEN", "one-launch-secret")
    monkeypatch.delenv("BAA_ENV", raising=False)
    _reset_singletons()

    from api import create_app

    client = create_app().test_client()
    assert client.get("/").status_code == 401
    assert client.get("/desktop/bootstrap?token=wrong").status_code == 401
    response = client.get("/desktop/bootstrap?token=one-launch-secret")
    assert response.status_code == 302 and response.headers["Location"].endswith("/")
    local_session = client.get("/api/local/session")
    csrf = local_session.get_json()["csrf_token"]
    assert csrf
    assert client.post("/api/session/new").status_code == 403
    assert client.post("/api/session/new", headers={"X-CSRF-Token": csrf}).status_code == 200


def test_non_loopback_client_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    monkeypatch.delenv("BAA_ENV", raising=False)
    _reset_singletons()

    from api import create_app

    response = create_app().test_client().get("/api/health", environ_base={"REMOTE_ADDR": "192.0.2.10"})
    assert response.status_code == 403


def test_source_flask_shell_serves_frontend_from_files():
    from api import _flask_shell

    client = _flask_shell().test_client()
    assert client.get("/static/css/research_workbench.css").status_code == 200
    assert client.get("/static/js/research_workbench.js").status_code == 200
    assert client.get("/static/../agent/templates/analysis_catalog.json").status_code == 404


def test_desktop_runtime_prepares_source_environment(monkeypatch, tmp_path):
    from desktop.runtime import DesktopRuntime

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    calls = []
    runtime = DesktopRuntime()
    monkeypatch.setattr(runtime.r_runtime, "prepare", lambda: calls.append("r"))
    status = runtime.prepare()
    assert calls == ["r"]
    assert status.phase == "prepared"
    assert os.environ["BAA_DESKTOP_TOKEN"] == runtime.launch_token
    assert os.environ["BAA_DESKTOP_MODE"] == "1"


def test_desktop_runtime_configures_host_r_and_preserves_timeout(monkeypatch, tmp_path):
    from desktop.runtime import DesktopRuntime

    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BAA_R_TIMEOUT_SECONDS", "900")
    runtime = DesktopRuntime()
    assert os.environ["BAA_R_BACKEND"] == "local"
    assert os.environ["BAA_R_TIMEOUT_SECONDS"] == "900"
    assert runtime.r_runtime.environment.__class__.__name__ == "HostREnvironment"


def test_host_r_runtime_prepares_exact_environment_and_stop_is_local(monkeypatch, tmp_path):
    from desktop.runtime import HostRRuntime

    calls = []
    monkeypatch.setenv("PATH", "/host/tools")

    class Environment:
        def prepare(self, **options):
            calls.append(("prepare", options))
            return SimpleNamespace(r_version="4.6.1")

        def process_environment(self, **_kwargs):
            return {
                "PATH": str(tmp_path / "R" / "bin"),
                "BAA_R_LIBRARY_DIR": str(tmp_path / "r-library"),
                "BAA_RSCRIPT_BIN": str(tmp_path / "Rscript"),
            }

    runtime = HostRRuntime(Environment())
    runtime.prepare()
    runtime.stop()
    assert calls == [("prepare", {})]
    assert "BAA_R_LIBRARY_DIR" not in os.environ
    assert os.environ["BAA_RSCRIPT_BIN"] == str(tmp_path / "Rscript")
    assert os.environ["PATH"] == "/host/tools"
    assert runtime.verified is None


def test_ping_restores_local_autosave_after_process_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    _reset_singletons()

    from api import create_app, saved_sessions
    from api.state import session_manager

    monkeypatch.setattr(saved_sessions, "SAVE_DIR", tmp_path / "outputs" / "Session")
    saved_sessions.SAVE_DIR.mkdir(parents=True, exist_ok=True)
    client = create_app().test_client()
    sid = client.post("/api/session/new").get_json()["session_id"]
    sess = session_manager.get(sid)
    sess.add_user("恢复问题")
    sess.add_assistant("恢复回答")
    saved_sessions.persist_local_autosave(sess)
    session_manager.remove(sid)
    assert client.post(f"/api/session/{sid}/resume").get_json()["restored"] is True
    current = client.get(f"/api/session/{sid}/load-current").get_json()
    assert [item["content"] for item in current["history"]] == ["恢复问题", "恢复回答"]


def test_local_web_worker_count_keeps_long_analysis_streams_responsive(monkeypatch):
    from infrastructure.local_web import LOCAL_MAX_REQUEST_BODY_SIZE, local_web_threads

    monkeypatch.delenv("BAA_WEB_THREADS", raising=False)
    assert local_web_threads() == 8
    monkeypatch.setenv("BAA_WEB_THREADS", "2")
    assert local_web_threads() == 4
    monkeypatch.setenv("BAA_WEB_THREADS", "99")
    assert local_web_threads() == 32
    assert LOCAL_MAX_REQUEST_BODY_SIZE > 50 * 1024**3
