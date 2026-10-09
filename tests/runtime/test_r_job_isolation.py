"""Real local-R checks of the OS file boundary, including namespace calls."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.protocol import DatasetRef, RRunRequest


@pytest.fixture(scope="module")
def backend():
    if sys.platform != "darwin":
        pytest.skip("macOS OS-isolation acceptance")
    try:
        host = HostREnvironment.locate()
        verified = host.prepare()
    except REnvironmentError as exc:
        if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
            pytest.fail(str(exc))
        pytest.skip(str(exc))
    return LocalRscriptBackend(rscript_bin=str(verified.rscript), runtime_env=host.process_environment(verified=verified))


@pytest.mark.parametrize("operation", ["write", "connection", "read", "overwrite_input", "symlink", "volume_alias", "hardlink"])
def test_r_cannot_access_undeclared_files_or_modify_inputs(backend, tmp_path, operation):
    root = tmp_path.resolve()
    data = root / "input.csv"
    data.write_text("x\n1\n2\n", encoding="utf-8")
    original = data.read_bytes()
    outside = root / "private-marker.txt"
    outside.write_text("synthetic private content", encoding="utf-8")
    output = root / "output"
    output.mkdir()
    if operation == "symlink":
        (output / "escape.txt").symlink_to(outside)
    target = json.dumps(str(data if operation == "overwrite_input" else output / "escape.txt" if operation == "symlink" else outside))
    if operation == "volume_alias":
        alias = Path("/System/Volumes/Data") / outside.relative_to("/")
        if not alias.exists():
            pytest.skip("Data-volume alias not available")
        target = json.dumps(str(alias))
    expression = {
        "write": f'base::writeLines("changed", {target})',
        "connection": f'con <- base::file({target}, "w"); writeLines("changed", con); close(con)',
        "read": f'base::readLines({target})',
        "overwrite_input": f'base::writeLines("changed", {target})',
        "symlink": f'base::writeLines("changed", {target})',
        "volume_alias": f'base::readLines({target})',
        "hardlink": f'stopifnot(file.link({target}, "linked-private.txt")); base::readLines("linked-private.txt")',
    }[operation]
    result = backend.run_code(RRunRequest(
        job_id=f"isolation-{operation}", dataset=DatasetRef("synthetic", str(data), "csv"),
        code=expression, artifacts_dir=str(output), timeout_seconds=30,
    ))
    assert result.ok is False, "R must be denied at the OS boundary"
    assert result.reproducibility.get("status") == "failed", "R must start and deny this operation, not fail during startup"
    assert data.read_bytes() == original
    assert outside.read_text(encoding="utf-8") == "synthetic private content"


def test_r_can_write_output_and_job_temporary_files(backend, tmp_path):
    data = tmp_path / "中文输入.csv"
    data.write_text("x\n1\n2\n", encoding="utf-8")
    result = backend.run_code(RRunRequest(
        job_id="isolation-allowed", dataset=DatasetRef("synthetic", str(data), "csv"),
        code='p <- tempfile(); saveRDS(dataset, p); baa_table("summary", data.frame(mean=mean(readRDS(p)$x)))',
        artifacts_dir=str(tmp_path / "output"), timeout_seconds=30,
    ))
    assert result.ok, result.error + "\n" + result.stderr
    assert "1.5" in Path(result.tables[0].path).read_text(encoding="utf-8")


def test_reused_output_cannot_redirect_python_script_write(backend, tmp_path):
    data = tmp_path / "input.csv"
    data.write_text("x\n1\n2\n", encoding="utf-8")
    outside = tmp_path / "outside-marker.txt"
    outside.write_text("keep this synthetic file", encoding="utf-8")
    request = RRunRequest(
        job_id="isolation-reused", dataset=DatasetRef("synthetic", str(data), "csv"),
        code=f'unlink("analysis.R"); stopifnot(file.symlink({json.dumps(str(outside.resolve()))}, "analysis.R")); baa_table("first", dataset)',
        artifacts_dir=str(tmp_path / "output"), timeout_seconds=30,
    )
    first = backend.run_code(request)
    assert first.ok, first.stderr
    second = backend.run_code(replace(request, code='baa_table("second", dataset)'))
    assert outside.read_text(encoding="utf-8") == "keep this synthetic file"
    assert second.ok, second.stderr
