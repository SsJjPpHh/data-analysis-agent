"""A discovered R runtime is not analysis-ready without file isolation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from r_runtime import isolation


def test_windows_api_missing_never_returns_unrestricted_command(monkeypatch, tmp_path):
    from r_runtime import windows_isolation

    def unavailable():
        raise OSError("AppContainer API absent")

    monkeypatch.setattr(isolation.sys, "platform", "win32")
    monkeypatch.setattr(windows_isolation, "check_windows_isolation_api", unavailable)
    status = isolation.isolation_availability()
    assert status["available"] is False
    assert status["verification"] == "launcher_only"
    assert status["supported_platforms"] == ["macOS", "Linux", "Windows"]
    assert "AppContainer API absent" in status["error"]
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    output.mkdir()
    runtime.mkdir()
    with pytest.raises(isolation.RIsolationError, match="Windows"):
        isolation.isolated_r_command(
            ["Rscript.exe", "analysis.R"], read_paths=[], runtime_roots=[runtime], output_dir=output,
        )


def test_missing_linux_launcher_never_returns_an_unrestricted_command(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation.sys, "platform", "linux")
    monkeypatch.setattr(isolation.shutil, "which", lambda _name: None)
    output = tmp_path / "output"
    runtime = tmp_path / "runtime"
    output.mkdir()
    runtime.mkdir()
    with pytest.raises(isolation.RIsolationError, match="bubblewrap"):
        isolation.isolated_r_command(["Rscript", "analysis.R"], read_paths=[], runtime_roots=[runtime], output_dir=output)


def test_readiness_requires_the_platform_isolation_launcher(monkeypatch):
    from api import research

    verified = SimpleNamespace(r_version="4.6.0", runtime_id="synthetic", package_count=5, install_library=Path("/synthetic-library"), library_mode="personal")
    class Host:
        def __init__(self, *_args):
            pass

        def prepare(self):
            return verified

    original_is_file = Path.is_file
    monkeypatch.setattr(research, "HostREnvironment", Host)
    monkeypatch.setattr(isolation.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_file", lambda path: False if str(path) == "/usr/bin/sandbox-exec" else original_is_file(path))
    status = research._r_runtime_readiness()
    assert status["available"] is False, "Finding R must not advertise an unavailable analysis backend as ready"
    assert status["isolation"]["available"] is False
    assert status["error"]
