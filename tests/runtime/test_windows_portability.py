"""Host-independent tests of Windows discovery and native-runtime boundaries."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop import runtime as desktop_runtime
from r_runtime import environment, resource_policy, tempfiles


def test_windows_r_discovery_uses_registry_and_user_installs_in_version_order(monkeypatch, tmp_path):
    registered = tmp_path / "自定义 R"
    user_programs = tmp_path / "用户" / "Programs" / "R"
    for version in ("4.9.0", "4.10.0"):
        (user_programs / f"R-{version}").mkdir(parents=True)
    monkeypatch.setattr(environment.sys, "platform", "win32")
    monkeypatch.setattr(environment.shutil, "which", lambda _name: None)
    monkeypatch.setattr(environment, "_windows_registry_r_roots", lambda: [registered])
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("ProgramW6432", str(tmp_path / "Program Files"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "用户"))
    monkeypatch.delenv("APPDATA", raising=False)

    candidates = environment._rscript_candidates()

    assert candidates[0] == registered / "bin" / "Rscript.exe"
    assert registered / "bin" / "aarch64" / "Rscript.exe" in candidates
    assert candidates.index(user_programs / "R-4.10.0" / "bin" / "Rscript.exe") < candidates.index(
        user_programs / "R-4.9.0" / "bin" / "Rscript.exe"
    )


def test_windows_registry_reads_user_and_machine_custom_installations(monkeypatch, tmp_path):
    class Key:
        def __init__(self, identifier):
            self.identifier = identifier

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    locations = {"user": str(tmp_path / "用户 R"), "machine": str(tmp_path / "Machine R")}

    def open_key(hive, name, *_args):
        if isinstance(hive, Key):
            return Key((hive.identifier[0], name))
        if name != r"Software\R-core\R":
            raise OSError("not installed")
        return Key((hive, "root"))

    def query_value(key, name):
        if key.identifier[1] == "root" and name == "Current Version":
            return "4.10.0", 1
        if key.identifier[1] == "4.10.0" and name == "InstallPath":
            return locations[key.identifier[0]], 1
        raise OSError("missing value")

    def enum_key(_key, _index):
        raise OSError("no more keys")

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="user", HKEY_LOCAL_MACHINE="machine", KEY_READ=1,
        KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512,
        OpenKey=open_key, QueryValueEx=query_value, EnumKey=enum_key,
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)

    assert environment._windows_registry_r_roots() == [Path(locations["user"]), Path(locations["machine"])]


def test_windows_r_environment_uses_ucrt_utf8_locale_without_secret(monkeypatch):
    monkeypatch.setattr(environment.sys, "platform", "win32")
    configured = environment._sanitized_host_environment({"LANG": "C", "DEEPSEEK_API_KEY": "private"})
    assert configured["LANG"] == "English_United States.UTF-8"
    assert configured["LC_CTYPE"] == configured["LANG"] == configured["LC_ALL"]
    assert "DEEPSEEK_API_KEY" not in configured


def test_r_probe_decodes_utf8_even_when_host_default_encoding_is_ansi(monkeypatch, tmp_path):
    rscript = tmp_path / "Rscript.exe"
    rscript.touch()
    r_home = tmp_path / "科研 R"
    library = r_home / "library"
    library.mkdir(parents=True)
    observed = {}

    def run(_argv, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess([], 0, f"VERSION\t4.5.0\nHOME\t{r_home}\nLIB\t{library}\nPACKAGES\t2\n", "")

    monkeypatch.setattr(environment.subprocess, "run", run)
    result = environment.HostREnvironment(rscript)._probe({})
    assert observed["encoding"] == "utf-8"
    assert observed["errors"] == "replace"
    assert result[1] == r_home


def test_windows_install_diagnosis_suggests_rtools(monkeypatch):
    monkeypatch.setattr(environment.sys, "platform", "win32")
    message = environment.diagnose_install_failure(["pkg"], "compilation failed for package 'pkg'")
    assert "Rtools" in message
    assert "Homebrew" not in message
    assert "xcode-select" not in message


def test_windows_space_temporary_root_uses_alias_without_resolving_it_back(monkeypatch, tmp_path):
    parent = tmp_path / "用户 空格" / "Temp"
    parent.mkdir(parents=True)
    alias = tmp_path / "RSWTMP~1"
    actual = []

    class ShortAlias(type(tmp_path)):
        def resolve(self, *_args, **_kwargs):
            return actual[0]

        def is_dir(self):
            return actual[0].is_dir()

    def short_path(path):
        actual.append(path)
        return ShortAlias(alias)

    monkeypatch.setattr(tempfiles.sys, "platform", "win32")
    monkeypatch.setattr(tempfiles, "_temporary_parents", lambda: (str(parent),))
    monkeypatch.setattr(tempfiles, "_windows_short_path", short_path)
    with tempfiles.private_r_temp_directory() as scratch:
        assert scratch == alias
        assert scratch.is_dir()
        assert " " in str(scratch.resolve())
        assert scratch.resolve() == actual[0]
    assert not actual[0].exists()


def test_windows_no_short_names_falls_back_and_cleans_rejected_directory(monkeypatch, tmp_path):
    spaced = tmp_path / "用户 空格"
    spaced.mkdir()
    fallback = tmp_path / "Temp"
    fallback.mkdir()
    monkeypatch.setattr(tempfiles.sys, "platform", "win32")
    monkeypatch.setattr(tempfiles, "_temporary_parents", lambda: (str(spaced), str(fallback)))
    monkeypatch.setattr(tempfiles, "_windows_short_path", lambda path: path)
    with tempfiles.private_r_temp_directory() as scratch:
        assert scratch.parent == fallback
        assert not list(spaced.iterdir())
    assert not list(fallback.iterdir())


def test_explicit_r_temporary_root_is_absolute_and_overrides_fallbacks(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_R_TEMP_ROOT", "relative")
    with pytest.raises(tempfiles.RTemporaryDirectoryError, match="绝对"):
        tempfiles._temporary_parents()
    monkeypatch.setenv("BAA_R_TEMP_ROOT", str(tmp_path))
    assert tempfiles._temporary_parents() == (str(tmp_path),)


def test_windows_memory_probe_drives_adaptive_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(resource_policy.sys, "platform", "win32")
    monkeypatch.setattr(resource_policy, "_windows_memory_bytes", lambda: 16 * resource_policy.GIB)
    resources = resource_policy.discover_host_resources(tmp_path)
    assert resources.memory_bytes == 16 * resource_policy.GIB
    policy = resource_policy.compute_local_resource_policy(resources, environment={})
    assert 0 < policy.memory_budget_bytes < resources.memory_bytes


def test_windows_network_isolation_keeps_helpers_serial_and_native_threads_available(monkeypatch):
    monkeypatch.setattr(resource_policy.sys, "platform", "win32")
    resources = resource_policy.HostResources(8, 16 * resource_policy.GIB, 100 * resource_policy.GIB)
    policy = resource_policy.compute_local_resource_policy(
        resources, environment={"BAA_R_WORKERS": "7", "BAA_R_NATIVE_THREADS": "3"},
    )
    assert policy.worker_count == 1
    assert policy.parallel_backend == "sequential"
    assert policy.native_thread_count == 3


def test_loopback_readiness_bypasses_proxy_configuration(monkeypatch):
    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def build_opener(handler):
        observed["proxies"] = handler.proxies
        return SimpleNamespace(open=lambda request, timeout: Response())

    monkeypatch.setattr(desktop_runtime, "build_opener", build_opener)
    assert desktop_runtime._request_ready("http://127.0.0.1:6001/api/health")
    assert observed["proxies"] == {}
