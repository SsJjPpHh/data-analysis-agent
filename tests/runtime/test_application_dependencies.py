"""The settings report must describe this interpreter, independently of R."""

import json
import platform

import pytest

from infrastructure import runtime_dependencies


def _state(report, name):
    return next(item for item in report["states"] if item["dependency_id"] == name)


def test_runtime_inspection_uses_active_environment_without_path_commands(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("BAA_RSCRIPT_BIN", "missing-r-does-not-affect-python-qt")
    report = runtime_dependencies.inspect_runtime_dependencies()
    assert report["python_version"] == platform.python_version()
    assert report["qt_version"]
    assert _state(report, "PySide6")["status"] == "ready"
    assert report["ready"] is True


def test_missing_runtime_package_is_not_confused_with_missing_r_packages(monkeypatch):
    actual_version = runtime_dependencies.metadata.version

    def missing_word_package(name):
        if name == "python-docx":
            raise runtime_dependencies.metadata.PackageNotFoundError(name)
        return actual_version(name)

    monkeypatch.setattr(runtime_dependencies.metadata, "version", missing_word_package)
    report = runtime_dependencies.inspect_runtime_dependencies()
    assert _state(report, "python-docx")["status"] == "missing"
    assert _state(report, "PySide6")["status"] == "ready"
    assert report["ready"] is False
    assert report["counts"]["missing"] == 1


def test_installed_qt_with_unloadable_system_library_is_reported(monkeypatch):
    actual_import = runtime_dependencies.importlib.import_module

    def broken_webengine(name):
        if name == "PySide6.QtWebEngineWidgets":
            raise OSError("DLL cannot load: private-host-path")
        return actual_import(name)

    monkeypatch.setattr(runtime_dependencies.importlib, "import_module", broken_webengine)
    report = runtime_dependencies.inspect_runtime_dependencies()
    assert _state(report, "PySide6")["status"] == "unavailable"
    assert report["ready"] is False
    assert report["counts"]["unavailable"] == 1
    assert "private-host-path" not in json.dumps(report)


def test_wrong_dependency_version_is_reported_separately(monkeypatch):
    actual_version = runtime_dependencies.metadata.version
    monkeypatch.setattr(runtime_dependencies.metadata, "version",
                        lambda name: "999.0" if name == "httpx" else actual_version(name))
    report = runtime_dependencies.inspect_runtime_dependencies()
    assert _state(report, "httpx")["status"] == "version_mismatch"
    assert _state(report, "httpx")["installed_version"] == "999.0"
    assert report["ready"] is False
    assert report["counts"]["missing"] == 0


@pytest.mark.parametrize("host,expected", [("Windows", "Windows"), ("Linux", "Linux"), ("Darwin", "macOS")])
def test_platform_information_follows_the_host(monkeypatch, host, expected):
    monkeypatch.setattr(runtime_dependencies.platform, "system", lambda: host)
    monkeypatch.setattr(runtime_dependencies.platform, "release", lambda: "test-release")
    monkeypatch.setattr(runtime_dependencies.platform, "mac_ver", lambda: ("test-mac", (), ""))
    monkeypatch.setattr(runtime_dependencies.platform, "machine", lambda: "test-architecture")
    report = runtime_dependencies.inspect_runtime_dependencies()
    assert report["platform"] == dict(system=expected,
                                     version="test-mac" if host == "Darwin" else "test-release",
                                     architecture="test-architecture")
