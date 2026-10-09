from pathlib import Path

from r_runtime.external_dependencies import (
    dependency_setup_requirements,
    inspect_external_dependencies,
    intent_dependency_ids,
    package_dependency_ids,
)


def _executable(path: Path, text: str = "#!/bin/sh\necho ready\n") -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_external_dependency_report_distinguishes_ready_and_missing(tmp_path):
    _executable(tmp_path / "clang")
    _executable(tmp_path / "clang++")
    _executable(tmp_path / "make")

    report = inspect_external_dependencies(
        dependency_ids=("c_cpp_toolchain", "java"),
        environment={"PATH": str(tmp_path)},
    )

    states = {item["dependency_id"]: item for item in report["states"]}
    assert states["c_cpp_toolchain"]["status"] == "ready"
    assert states["java"]["status"] == "missing"
    assert report["policy"]["installs_system_software"] is False


def test_package_and_workflow_dependencies_share_one_registry():
    assert package_dependency_ids(("cmdstanr", "sf")) == (
        "c_cpp_toolchain",
        "cmdstan",
        "geospatial_libraries",
    )
    assert "fastqc" in intent_dependency_ids("FASTQ 原始读段质控")
    assert "read_aligner" in intent_dependency_ids("FASTQ 原始读段质控")
    assert "cuda" in intent_dependency_ids("使用 CUDA GPU 加速")
    assert package_dependency_ids(("sparklyr", "keras3")) == (
        "java",
        "spark",
        "python_ml",
    )
    requirements = dependency_setup_requirements(("torch",))
    assert len(requirements) == 1
    assert "torch::install_torch()" in requirements[0]


def test_python_command_does_not_prove_tensorflow_backend_is_ready(tmp_path):
    _executable(tmp_path / "python3")

    pending = inspect_external_dependencies(
        packages=("tensorflow",),
        environment={"PATH": str(tmp_path)},
    )
    state = pending["states"][0]
    assert state["dependency_id"] == "python_ml"
    assert state["status"] == "needs_setup"

    verified = inspect_external_dependencies(
        packages=("tensorflow",),
        satisfied_dependency_ids=("python_ml",),
        environment={"PATH": str(tmp_path)},
    )
    assert verified["states"][0]["status"] == "ready"
