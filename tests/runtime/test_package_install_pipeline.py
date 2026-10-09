# -*- coding: utf-8 -*-
"""Package installation fallback, failure diagnosis and repository resolution.

Covers the systemic fixes for "missing R packages that cannot be installed":
  - the multi-round install script gains a binary-capable fallback mirror for
    Apple silicon (source-compile floods of gfortran errors);
  - failed installs are diagnosed by signature (dependency chain, missing
    system library, compilation failure, Java, network) instead of dumping a
    raw log tail;
  - the dependency gate never flags GitHub-only / special-repository packages
    (ArchR, CellChat, monocle3, INLA, cmdstanr) as "unknown names".
"""

from __future__ import annotations

import platform
import sys

import pytest

from r_runtime.dependency_gate import inspect_dependency_gate
from r_runtime.environment import (
    build_install_script,
    default_binary_fallback_repo,
    diagnose_install_failure,
)

from tests.core.test_dependency_gate import FakeBackend, _RepoBackend, _reset_repo_cache, _state


# ── Binary fallback mirror ───────────────────────────────────────────────────


def test_apple_silicon_gets_binary_fallback_by_default(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert default_binary_fallback_repo() == (
        "https://packagemanager.posit.co/cran/__macos__/arm64/latest"
    )


def test_other_platforms_have_no_binary_fallback_default(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert default_binary_fallback_repo() == ""


def test_install_script_contains_all_rounds_in_order():
    script = build_install_script(
        '"INLA" = "https://inla.r-inla-download.org/R/stable"',
        '"monocle3" = "cole-trapnell-lab/monocle3"',
        "https://example.test/binary",
    )
    markers = [
        "BAA_CRAN_MIRROR",
        "BAA_CRAN_FALLBACK",
        "BAA_BIOC_MIRROR",
        "BAA_BIOC_FALLBACK",
        "BAA_CRAN_BINARY_FALLBACK",
        "https://example.test/binary",
        "github_sources",
        "remotes::install_github",
    ]
    positions = [script.index(marker) for marker in markers]
    assert positions == sorted(positions)
    # Env override wins over the baked-in default.
    assert 'Sys.getenv("BAA_CRAN_BINARY_FALLBACK", "https://example.test/binary")' in script
    # No template placeholders survive substitution.
    assert "__" not in script.replace("__macos__", "")


# ── Failure diagnosis ────────────────────────────────────────────────────────


def test_diagnose_reports_dependency_chain_compilation_and_network(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    log = chr(10).join(
        [
            "ERROR: dependency 'xml2' is not available for package 'textshaping'",
            "ERROR: compilation failed for package 'textshaping'",
            "Warning: unable to access index of repository",
            "cannot open URL 'https://cran.rstudio.com/...'",
        ]
    )
    message = diagnose_install_failure(
        ("systemfonts", "textshaping"),
        log,
        r_version="4.5.1",
        platform_label="Darwin arm64",
    )
    assert "R 4.5.1" in message and "Darwin arm64" in message
    assert "依赖包 xml2" in message
    assert "源码编译失败" in message and "textshaping" in message
    assert "网络或镜像超时" in message
    assert "xcode-select --install" in message


@pytest.mark.parametrize("platform_name", ["darwin", "linux", "win32"])
def test_diagnose_flags_java_and_version_mismatch(monkeypatch, platform_name):
    monkeypatch.setattr(sys, "platform", platform_name)
    log = chr(10).join(
        [
            "JAVA_HOME cannot be determined from this Registry",
            "package 'future' is not available (for R version 4.3.1)",
        ]
    )
    message = diagnose_install_failure(("rJava", "future"), log)
    assert "Java" in message and "JAVA_HOME" in message
    assert ("R CMD javareconf" in message) is (platform_name != "win32")
    assert "与当前 R 版本不匹配" in message


def test_diagnose_falls_back_when_no_signature_matches():
    message = diagnose_install_failure(("pkgx", "完全无关的乱码日志"), "nothing recognizable")
    assert "未能识别具体的失败签名" in message


# ── Gate: GitHub-only / special-repo packages stay installable ───────────────


def test_gate_treats_github_only_packages_as_known(monkeypatch):
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {"monocle3": _state("monocle3", "missing")},
        repo_names=("jsonlite",),  # monocle3 is GitHub-only: not in CRAN index
    )

    report = inspect_dependency_gate(
        packages=("monocle3",),
        mode="exact",
        backend=backend,
        session_id="github-session",
    )

    assert report["status"] == "installable"
    assert report["install_packages"] == ["monocle3"]
    item = next(i for i in report["package_readiness"] if i["package"] == "monocle3")
    assert item["status"] == "missing"  # honestly missing, not "unknown"


def test_gate_treats_special_repository_packages_as_known(monkeypatch):
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {"cmdstanr": _state("cmdstanr", "missing"), "INLA": _state("INLA", "missing")},
        repo_names=("jsonlite",),  # cmdstanr/INLA live on dedicated repos
    )

    report = inspect_dependency_gate(
        packages=("cmdstanr", "INLA"),
        mode="exact",
        backend=backend,
        session_id="special-session",
    )

    assert report["status"] == "installable"
    assert set(report["install_packages"]) == {"cmdstanr", "INLA"}


def test_fake_backend_without_seam_still_fails_open(monkeypatch):
    _reset_repo_cache(monkeypatch)
    backend = FakeBackend({"pkgfake": _state("pkgfake", "missing")})

    report = inspect_dependency_gate(
        packages=("pkgfake",),
        mode="exact",
        backend=backend,
        session_id="open-session",
    )

    # FakeBackend has no available_package_names and the cache is fresh:
    # fail-open keeps the historical installable-missing behaviour.
    assert report["status"] == "installable"
    assert report["install_packages"] == ["pkgfake"]
