import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from r_runtime.environment import HostREnvironment, REnvironmentError, _sanitized_host_environment
from r_runtime.library_settings import (
    MANAGED_LIBRARY_MODE,
    USER_LIBRARY_MODE,
    RLibrarySettings,
    load_r_library_settings,
    save_r_library_settings,
    settings_path,
)


def test_host_environment_preserves_r_libraries_but_removes_secrets_and_profiles():
    environment = _sanitized_host_environment(
        {
            "R_LIBS_USER": "/research/packages",
            "R_LIBS_SITE": "/shared/packages",
            "R_PROFILE_USER": "/tmp/unsafe-profile",
            "R_MAKEVARS_USER": "/research/Makevars",
            "VIRTUAL_ENV": "/research/app/.venv",
            "CONDA_PREFIX": "/research/conda",
            "PYTHONHOME": "/research/python",
            "PYTHONPATH": "/research/python/site-packages",
            "RETICULATE_PYTHON": "/scientist/python",
            "PATH": "/research/app/.venv/bin:/research/conda/bin:/usr/bin",
            "DEEPSEEK_API_KEY": "secret",
        }
    )

    assert environment["R_LIBS_USER"] == "/research/packages"
    assert environment["R_LIBS_SITE"] == "/shared/packages"
    assert environment["R_MAKEVARS_USER"] == "/research/Makevars"
    assert environment["RETICULATE_PYTHON"] == "/scientist/python"
    assert environment["RETICULATE_USE_MANAGED_VENV"] == "no"
    assert environment["PATH"] == "/usr/bin"
    assert "R_PROFILE_USER" not in environment
    assert "VIRTUAL_ENV" not in environment
    assert "CONDA_PREFIX" not in environment
    assert "PYTHONHOME" not in environment
    assert "PYTHONPATH" not in environment
    assert "DEEPSEEK_API_KEY" not in environment


def test_host_environment_replaces_non_utf8_locale_for_r_subprocesses():
    environment = _sanitized_host_environment(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "LC_CTYPE": "POSIX",
            "PATH": "/usr/bin",
        }
    )

    expected_locales = {"English_United States.UTF-8"} if sys.platform == "win32" else {"C.UTF-8", "en_US.UTF-8"}
    assert environment["LANG"] in expected_locales
    assert environment["LC_ALL"] == environment["LANG"]
    assert environment["LC_CTYPE"] == environment["LANG"]


@pytest.mark.parametrize("invalid_utf8_name", ["UTF-8", "xx_XX.UTF-8"])
def test_host_environment_does_not_trust_unavailable_utf8_locale_names(invalid_utf8_name):
    environment = _sanitized_host_environment(
        {
            "LANG": invalid_utf8_name,
            "LC_ALL": invalid_utf8_name,
            "LC_CTYPE": invalid_utf8_name,
            "PATH": "/usr/bin",
        }
    )

    expected_locale = "English_United States.UTF-8" if sys.platform == "win32" else "C.UTF-8"
    assert environment["LANG"] == expected_locale
    assert environment["LC_ALL"] == expected_locale
    assert environment["LC_CTYPE"] == expected_locale


def test_package_installer_allows_large_stacks_and_passes_plain_rscript_arguments(
    monkeypatch, tmp_path
):
    environment = HostREnvironment(tmp_path / "Rscript")
    verified = SimpleNamespace(rscript=Path(tmp_path / "Rscript"))
    observed = {}

    monkeypatch.setattr(environment, "prepare", lambda: verified)
    monkeypatch.setattr(environment, "process_environment", lambda **_kwargs: {})

    def fake_run(command, **_kwargs):
        observed["command"] = command
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("agent.analysis.run_control.run_cancellable_process", fake_run)

    installed = environment.ensure_packages(("brms", "ggplot2"))

    assert installed == ("brms", "ggplot2")
    assert observed["command"][-1] == "ggplot2"
    assert "--args" not in observed["command"]
    assert "brms" in observed["command"]
    assert "https://inla.r-inla-download.org/R/stable" in observed["command"][3]
    assert "https://stan-dev.r-universe.dev" in observed["command"][3]
    assert "GreenleafLab/ArchR" in observed["command"][3]


def test_r_library_choice_persists_user_or_project_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_R_LIBRARY_MODE", raising=False)
    monkeypatch.delenv("BAA_R_LIBRARY_DIR", raising=False)
    assert load_r_library_settings().mode == USER_LIBRARY_MODE

    project_library = tmp_path / "project" / "r-library"
    save_r_library_settings(
        RLibrarySettings(
            mode=MANAGED_LIBRARY_MODE,
            custom_directory=str(project_library),
        )
    )
    assert settings_path().is_file()
    selected = load_r_library_settings()
    assert selected.mode == MANAGED_LIBRARY_MODE
    assert selected.managed_root == project_library.resolve(strict=False)

    save_r_library_settings(RLibrarySettings(mode=USER_LIBRARY_MODE))
    assert load_r_library_settings().mode == USER_LIBRARY_MODE
    assert "BAA_R_LIBRARY_DIR" not in os.environ


def test_managed_library_cannot_target_r_system_library(monkeypatch, tmp_path):
    r_home = tmp_path / "R"
    system_library = r_home / "library"
    system_library.mkdir(parents=True)
    environment = HostREnvironment(
        tmp_path / "Rscript",
        library_settings=RLibrarySettings(
            mode=MANAGED_LIBRARY_MODE,
            custom_directory=str(system_library),
        ),
    )
    monkeypatch.setattr(
        environment,
        "_probe",
        lambda _environment: (
            "4.6.1",
            r_home,
            (system_library,),
            (tmp_path / "user-library",),
            10,
        ),
    )

    with pytest.raises(REnvironmentError, match="系统/站点包目录"):
        environment.prepare()


def test_invalid_saved_library_settings_surface_warning_and_remain_repairable(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_R_LIBRARY_MODE", raising=False)
    monkeypatch.delenv("BAA_R_LIBRARY_DIR", raising=False)
    path = settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    settings = load_r_library_settings()
    assert settings.mode == USER_LIBRARY_MODE
    assert "损坏或不可读" in settings.load_warning
