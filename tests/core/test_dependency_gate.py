from __future__ import annotations

from r_runtime.dependency_gate import (
    check_dependency_ticket,
    inspect_dependency_gate,
)
from r_runtime.package_references import (
    referenced_external_dependencies,
    referenced_r_packages,
)


import pytest

import r_runtime.dependency_gate as _dg


@pytest.fixture(autouse=True)
def _isolated_repo_snapshot_cache(monkeypatch):
    """Keep the process-wide repo snapshot out of test ordering."""
    monkeypatch.setattr(
        _dg, "_repo_snapshot_cache", {"at": float("-inf"), "names": None}
    )


class FakeBackend:
    def __init__(self, states):
        self.states = states
        self.runtime_env = {"PATH": ""}

    def package_readiness(self, packages, timeout_seconds=30):
        selected = []
        for package in packages:
            state = dict(
                self.states.get(
                    package,
                    {
                        "package": package,
                        "installed": False,
                        "loadable": False,
                        "status": "missing",
                        "warnings": [],
                        "error": "Package is not installed",
                    },
                )
            )
            selected.append(state)
        return {"ok": True, "packages": selected}


def _state(package: str, status: str = "ready", **extra):
    return {
        "package": package,
        "installed": status != "missing",
        "loadable": status in {"ready", "warning", "needs_setup"},
        "status": status,
        "warnings": [],
        "error": "",
        "runtime_check": "not_required",
        "runtime_detail": "",
        **extra,
    }


def test_candidate_gate_requires_one_usable_implementation_not_every_package():
    backend = FakeBackend(
        {
            "gtsummary": _state("gtsummary"),
            "tableone": _state("tableone", "missing"),
            "compareGroups": _state("compareGroups", "missing"),
        }
    )

    report = inspect_dependency_gate(
        packages=("gtsummary", "tableone", "compareGroups"),
        intent="论文 Table 1",
        mode="candidate",
        backend=backend,
        session_id="session-one",
        plan_id="plan-one",
    )

    assert report["status"] == "ready"
    assert report["selected_packages"] == ["gtsummary"]
    assert report["install_packages"] == []
    assert report["ticket_id"] == ""


def test_gee_readiness_cannot_be_satisfied_by_an_installed_mixed_model_package():
    from agent.analysis.catalog import AnalysisCatalog
    from agent.analysis.preparation import AnalysisPreparer

    data = {"dataset_id": "gee-data", "format": "csv", "rows": 100, "columns": [
        {"name": name, "semantic_type": kind, "unique_count": count}
        for name, kind, count in [("score", "numeric", 80), ("time", "numeric", 5), ("subject", "identifier", 20)]
    ]}
    preparer = AnalysisPreparer(AnalysisCatalog(), runtime_packages=("stats", "lme4", "emmeans"))
    values = {"outcome": "score", "time": "time", "subject_id": "subject", "analysis_method": "gee"}
    gee = preparer.prepare("mixed_repeated", data, values)
    assert gee.status == "ready"
    assert set(gee.plan.packages) == {"geepack", "emmeans"}
    backend = FakeBackend({"lme4": _state("lme4"), "emmeans": _state("emmeans")})
    report = inspect_dependency_gate(packages=gee.plan.packages, intent=gee.plan.intent, mode="exact", backend=backend)
    assert report["status"] == "installable"
    assert report["install_packages"] == ["geepack"]
    mixed = preparer.prepare("mixed_repeated", data, {**values, "analysis_method": "mixed"})
    assert "geepack" not in mixed.plan.packages
    assert inspect_dependency_gate(packages=mixed.plan.packages, intent=mixed.plan.intent, mode="candidate", backend=backend)["status"] == "ready"


def test_robust_branch_keeps_its_dependencies_when_only_bayesian_packages_are_installed():
    from agent.analysis.catalog import AnalysisCatalog
    from agent.analysis.preparation import AnalysisPreparer

    data = {"dataset_id": "robust-data", "format": "csv", "rows": 100, "columns": [
        {"name": "score", "semantic_type": "numeric", "unique_count": 80},
        {"name": "arm", "semantic_type": "categorical", "unique_count": 2},
    ]}
    result = AnalysisPreparer(AnalysisCatalog(), runtime_packages=("stats", "brms")).prepare(
        "bayesian_robust_stats", data, {"outcome": "score", "group": "arm", "analysis_method": "robust"},
    )
    assert result.status == "ready"
    assert not {"brms", "rstanarm"} & set(result.plan.packages)
    assert "stats" in result.plan.packages  # Used by the actual robust R core.
    report = inspect_dependency_gate(
        packages=result.plan.packages, intent=result.plan.intent, mode="exact",
        backend=FakeBackend({"stats": _state("stats"), "brms": _state("brms")}),
    )
    assert report["status"] == "installable"
    assert report["install_packages"] == ["MASS"]


def test_fixed_method_keeps_missing_required_packages_in_its_frozen_plan():
    from agent.analysis.catalog import AnalysisCatalog
    from agent.analysis.preparation import AnalysisPreparer

    catalog = AnalysisCatalog()
    data = {"dataset_id": "fixed-required", "format": "csv", "rows": 100, "columns": [
        {"name": name, "type": "numeric", "semantic_type": "numeric", "unique_count": 100}
        for name in ("y", "x")
    ]}
    result = AnalysisPreparer(catalog, runtime_packages=("stats",)).prepare(
        "regression_glm", data, {"outcome": "y", "exposure": "x", "covariates": []},
    )
    assert result.status == "ready"
    assert set(result.plan.packages) == set(catalog.get("regression_glm").packages)
    assert set(result.plan.packages) - {"stats"}
    report = inspect_dependency_gate(
        packages=result.plan.packages, intent=result.plan.intent, mode="exact",
        backend=FakeBackend({"stats": _state("stats")}),
    )
    assert report["status"] == "installable"
    assert set(report["install_packages"]) == set(result.plan.packages) - {"stats"}


def test_exact_gate_pauses_for_missing_packages_and_issues_scoped_ticket():
    backend = FakeBackend({"DESeq2": _state("DESeq2", "missing")})

    report = inspect_dependency_gate(
        packages=("DESeq2",),
        intent="差异表达",
        mode="exact",
        backend=backend,
        session_id="session-two",
        plan_id="plan-two",
    )

    assert report["status"] == "installable"
    assert report["install_packages"] == ["DESeq2"]
    assert report["ticket_id"]
    ticket = check_dependency_ticket("session-two", report["ticket_id"], backend=backend)
    assert ticket["status"] == "installable"
    assert ticket["plan_id"] == "plan-two"


def test_exact_gate_checks_every_concrete_reference_beyond_template_candidate_sizes():
    packages = tuple(f"Package{index}" for index in range(25))
    backend = FakeBackend(
        {
            package: _state(package, "missing" if package == packages[-1] else "ready")
            for package in packages
        }
    )

    report = inspect_dependency_gate(
        packages=packages,
        intent="组合分析",
        mode="exact",
        backend=backend,
        session_id="session-many",
        plan_id="plan-many",
    )

    assert len(report["requested_packages"]) == 25
    assert report["install_packages"] == [packages[-1]]


def test_candidate_gate_prefers_confirmable_install_over_broken_runtime_setup():
    backend = FakeBackend(
        {
            "preferred": _state("preferred", "missing"),
            "heavy": _state("heavy", "needs_setup", runtime_check="libtorch"),
        }
    )

    report = inspect_dependency_gate(
        packages=("preferred", "heavy"),
        intent="候选实现",
        mode="candidate",
        backend=backend,
        session_id="session-candidate",
        plan_id="plan-candidate",
    )

    assert report["selected_packages"] == ["preferred"]
    assert report["status"] == "installable"


def test_generated_code_references_static_dynamic_packages_and_scientific_cli_tools():
    code = (
        'pkg <- "DESeq2"; library(pkg, character.only = TRUE)\n'
        'system2("samtools", c("quickcheck", "input.bam"))\n'
        'system("bcftools view input.vcf.gz")\n'
        'baa_text("do not treat fastqc in narrative as a command")'
    )

    assert referenced_r_packages(code) == ("DESeq2",)
    assert referenced_external_dependencies(code) == ("samtools", "bcftools")


def test_exact_gate_uses_concrete_external_references_not_broad_plan_keywords():
    backend = FakeBackend({"ShortRead": _state("ShortRead")})

    report = inspect_dependency_gate(
        packages=("ShortRead",),
        dependency_ids=("fastqc",),
        intent="FASTQ 原始读段完整处理",
        mode="exact",
        backend=backend,
        session_id="session-cli",
        plan_id="plan-cli",
    )

    requested = {item["dependency_id"] for item in report["external_dependencies"]}
    assert requested == {"fastqc"}
    assert report["status"] == "needs_setup"


def test_exact_gate_surfaces_external_runtime_instead_of_reinstalling_r_interface():
    backend = FakeBackend(
        {
            "torch": _state(
                "torch",
                "needs_setup",
                runtime_check="libtorch",
                runtime_detail="Run torch::install_torch()",
            )
        }
    )

    report = inspect_dependency_gate(
        packages=("torch",),
        intent="深度学习",
        mode="exact",
        backend=backend,
        session_id="session-three",
        plan_id="plan-three",
    )

    assert report["status"] == "needs_setup"
    assert report["install_packages"] == []
    assert any(item["dependency_id"] == "libtorch" for item in report["external_dependencies"])


def test_abi_warning_requires_repair_but_ordinary_namespace_warning_is_nonblocking():
    backend = FakeBackend(
        {
            "mzR": _state(
                "mzR",
                "warning",
                warnings=["package was built against a different Rcpp version"],
            ),
            "sf": _state("sf", "warning", warnings=["startup note"]),
        }
    )

    mzr = inspect_dependency_gate(
        packages=("mzR",),
        intent="质谱",
        mode="exact",
        backend=backend,
        session_id="session-four",
        plan_id="plan-four",
    )
    sf = inspect_dependency_gate(
        packages=("sf",),
        intent="空间分析",
        mode="exact",
        backend=backend,
        session_id="session-five",
        plan_id="plan-five",
    )

    assert mzr["status"] == "repair_required"
    assert mzr["repair_packages"] == ["mzR"]
    assert sf["status"] == "ready"
    assert sf["warnings"]


# ── Repo-existence resolution (2026-09-23) ──────────────────────────────────
# Plans can freeze invented package ids (e.g. "pk" for pharmacokinetics);
# the gate must never send those to the installer, and case variants must
# resolve under their canonical CRAN spelling instead of dead-ending.


class _RepoBackend(FakeBackend):
    def __init__(self, states, repo_names, canonical_installed=()):
        super().__init__({**states, **{name: _state(name) for name in canonical_installed}})
        self._repo_names = tuple(repo_names)

    def available_package_names(self, timeout_seconds=90):
        return self._repo_names


def _reset_repo_cache(monkeypatch):
    import r_runtime.dependency_gate as dg

    monkeypatch.setattr(dg, "_repo_snapshot_cache", {"at": float("-inf"), "names": None})


def test_repo_snapshot_cache_isolated_per_test(monkeypatch):
    """Guard: the module-level repo cache must never leak between tests."""
    _reset_repo_cache(monkeypatch)
    import r_runtime.dependency_gate as dg

    assert dg._repo_snapshot_cache["names"] is None


def test_gate_installs_canonical_name_for_case_variant(monkeypatch):
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {
            "pk": _state("pk", "missing"),
            "jsonlite": _state("jsonlite"),
            "readr": _state("readr"),
        },
        repo_names=("PK", "jsonlite", "readr"),
    )

    report = inspect_dependency_gate(
        packages=("jsonlite", "pk", "readr"),
        mode="exact",
        backend=backend,
        session_id="repo-session",
    )

    assert report["status"] == "installable"
    assert report["install_packages"] == ["PK"]
    pk_item = next(item for item in report["package_readiness"] if item["package"] == "pk")
    assert pk_item["resolved_as"] == "PK"
    assert any(w.get("status") == "resolved_as_canonical" for w in report["warnings"])


def test_gate_satisfies_case_variant_when_canonical_installed(monkeypatch):
    """Candidate mode: plan metadata naming "pk" is satisfied by installed
    "PK" (one usable implementation).  Exact mode (literal code references)
    must NOT fake ready — R's library() is case-sensitive."""
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {"pk": _state("pk", "missing")},
        repo_names=("PK",),
        canonical_installed=("PK",),
    )

    report = inspect_dependency_gate(
        packages=("pk",),
        mode="candidate",
        backend=backend,
        session_id="repo-session",
    )

    assert report["status"] == "ready"
    assert report["install_packages"] == []
    assert report["can_resume"] is True
    pk_item = next(item for item in report["package_readiness"] if item["package"] == "pk")
    assert pk_item["status"] == "ready"
    assert pk_item["resolved_as"] == "PK"


def test_exact_mode_case_variant_stays_installable_with_canonical(monkeypatch):
    """Code literally referencing library(pk) cannot run against PK: exact
    mode keeps the block and installs the canonical name, with an explicit
    correction note for the model."""
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {"pk": _state("pk", "missing")},
        repo_names=("PK",),
        canonical_installed=("PK",),
    )

    report = inspect_dependency_gate(
        packages=("pk",),
        mode="exact",
        backend=backend,
        session_id="exact-case-session",
    )

    assert report["status"] == "installable"
    assert report["install_packages"] == ["PK"]
    assert any("区分大小写" in warning.get("message", "") for warning in report["warnings"])


def test_gate_reports_unknown_package_without_installing(monkeypatch):
    _reset_repo_cache(monkeypatch)
    backend = _RepoBackend(
        {"zzqq": _state("zzqq", "missing"), "jsonlite": _state("jsonlite")},
        repo_names=("jsonlite", "ggplot2"),
    )

    report = inspect_dependency_gate(
        packages=("jsonlite", "zzqq"),
        mode="exact",
        backend=backend,
        session_id="repo-session",
    )

    assert report["status"] == "package_names_unresolved"
    assert report["install_packages"] == []
    assert report["can_install"] is False
    item = next(i for i in report["package_readiness"] if i["package"] == "zzqq")
    assert item["status"] == "unknown_package"
    assert "不存在" in (item.get("error") or "")


def test_gate_fails_open_without_repo_seam():
    # No available_package_names on the backend and no cached snapshot:
    # the gate keeps its previous behaviour instead of blocking.
    backend = FakeBackend({"pk": _state("pk", "missing")})

    report = inspect_dependency_gate(
        packages=("pk",),
        mode="exact",
        backend=backend,
        session_id="repo-session",
    )

    assert report["status"] == "installable"
    assert report["install_packages"] == ["pk"]
