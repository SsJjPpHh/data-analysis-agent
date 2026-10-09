"""Real R startup, scratch isolation and cleanup with local paths containing spaces."""

from __future__ import annotations

import csv
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

import pytest

from agent.analysis.run_control import research_run_controller
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.execution import dataset_inspection_request
from r_runtime.inspection import inspection_summary
from r_runtime.isolation import RIsolationError, isolated_r_command, isolation_availability
from r_runtime.library_settings import RLibrarySettings
from r_runtime.protocol import DatasetRef, RRunRequest


TEMP_REPORT = r'''
baa_table("r_temporary_environment", data.frame(
  variable = c("tempdir", "TMPDIR", "TMP", "TEMP", "scratch_mode"),
  value = c(tempdir(), Sys.getenv(c("TMPDIR", "TMP", "TEMP")),
            as.character(file.info(Sys.getenv("TMPDIR"))$mode))
))
p <- tempfile(); saveRDS(dataset, p)
restored <- readRDS(p)
# readr tables carry parser specification attributes with external pointers;
# serialization preserves the data but does not preserve pointer identity.
stopifnot(identical(names(restored), names(dataset)),
          all(vapply(seq_along(dataset), function(i)
            identical(restored[[i]], dataset[[i]]), logical(1))))
writeLines("actual R reached the analysis body", "scratch-ready.txt")
cat("SCRATCH_READY\n"); flush.console()
'''


@pytest.fixture(scope="module")
def native_backend(tmp_path_factory):
    available = isolation_availability()
    if not available["available"]:
        if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
            pytest.fail(str(available["error"]))
        pytest.skip(str(available["error"]))
    owned = tmp_path_factory.mktemp("private-r-library")
    located = HostREnvironment.locate()
    host = HostREnvironment(
        located.rscript,
        library_settings=RLibrarySettings(mode="managed", custom_directory=str(owned)),
    )
    try:
        verified = host.prepare()
    except REnvironmentError as exc:
        if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
            pytest.fail(str(exc))
        pytest.skip(str(exc))

    backend = LocalRscriptBackend(
        rscript_bin=str(verified.rscript),
        runtime_env=host.process_environment(verified=verified),
    )
    # These checks run actual R and never install packages. A backend installer
    # receives every pending dependency, including preinstalled packages, so a
    # callback that fails on any invocation would reject a healthy runtime.
    readiness = backend.package_readiness(["jsonlite", "readr"])
    missing = [
        str(item.get("package")) for item in readiness.get("packages", [])
        if item.get("status") != "ready"
    ]
    if not readiness.get("ok") or missing:
        message = f"Real-R scratch tests require preinstalled jsonlite/readr: {missing or readiness.get('error')}"
        if os.getenv("BAA_REQUIRE_SCIENCE_R") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return backend


@pytest.fixture
def desktop_paths(tmp_path, monkeypatch):
    owned = tmp_path / "Library" / "Application Support" / "科研 中文 空格"
    data = owned / "data" / "输入 数据.csv"
    data.parent.mkdir(parents=True)
    data.write_text("x,y,组别\n1,3,对照\n2,5,处理\n3,7,对照\n4,9,处理\n", encoding="utf-8")
    config = owned / "config" / "synthetic-config.json"
    config.parent.mkdir()
    config.write_text('{"synthetic_fixture": true}\n', encoding="utf-8")
    monkeypatch.setenv("BAA_DATA_DIR", str(owned))
    return owned, data, config


@pytest.fixture
def observed_private_directories(monkeypatch):
    """Observe the real allocator without replacing R, sandbox or cleanup."""
    import r_runtime.backends.local_rscript as local_module
    import r_runtime.environment as environment_module
    import r_runtime.tempfiles as temp_module

    original = temp_module.private_r_temp_directory
    allocated = []
    lock = Lock()

    @contextmanager
    def observe(*args, **kwargs):
        with original(*args, **kwargs) as directory:
            with lock:
                allocated.append(Path(directory))
            yield directory

    for module in (temp_module, local_module, environment_module):
        if getattr(module, "private_r_temp_directory", None) is original:
            monkeypatch.setattr(module, "private_r_temp_directory", observe)
    return allocated


def _request(data, output, code=TEMP_REPORT, *, timeout=30, conversation_id=""):
    return RRunRequest(
        job_id=f"temporary-path-{uuid.uuid4().hex}",
        dataset=DatasetRef("synthetic-space-csv", str(data), "csv", data.name),
        code=code,
        artifacts_dir=str(output),
        timeout_seconds=timeout,
        conversation_job_id=conversation_id,
    )


def _table(result, name):
    artifact = next((item for item in result.tables if item.name == name), None)
    assert artifact is not None, result.error + "\n" + result.stderr
    with Path(artifact.path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _temporary_values(result):
    return {row["variable"]: row["value"] for row in _table(result, "r_temporary_environment")}


def _assert_private(values, *, still_running=False):
    for variable in ("tempdir", "TMPDIR", "TMP", "TEMP"):
        assert values[variable] and not any(character.isspace() for character in values[variable])
    assert values["TMPDIR"] == values["TMP"] == values["TEMP"]
    scratch = Path(values["TMPDIR"]).resolve(strict=False)
    assert Path(values["tempdir"]).resolve(strict=False).is_relative_to(scratch)
    assert values["scratch_mode"] == "700"
    if still_running:
        assert scratch.is_dir()
        assert stat.S_IMODE(scratch.stat().st_mode) == 0o700
    else:
        assert not scratch.exists(), "Completed R jobs must remove their private scratch leaf"
    return scratch


def _assert_all_removed(allocated):
    assert allocated, "The actual R execution did not allocate a private scratch directory"
    assert all(not directory.exists() for directory in allocated)


def _wait_for_ready(outputs, futures, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if all((output / "scratch-ready.txt").is_file() for output in outputs):
            return
        if any(future.done() for future in futures):
            completed = [future.result() for future in futures if future.done()]
            pytest.fail("R exited before its ready marker: " + "\n".join(r.error + r.stderr for r in completed))
        time.sleep(0.05)
    pytest.fail("Actual R did not reach its ready marker within the startup bound")


def test_real_profile_and_analysis_keep_desktop_paths_with_spaces(
    native_backend, desktop_paths, observed_private_directories,
):
    owned, data, _config = desktop_paths
    original = data.read_bytes()
    dataset = DatasetRef("space-profile", str(data), "csv", data.name)
    inspect_request = dataset_inspection_request(dataset, session_id="temporary-path-fixture", timeout_seconds=30)
    inspected = native_backend.run_code(inspect_request)
    assert inspected.ok, inspected.error + "\n" + inspected.stderr
    summary = inspection_summary(inspected)
    assert summary["rows"] == 4
    assert {item["name"] for item in summary["variables"]} == {"x", "y", "组别"}
    assert summary["overall_missing_pct"] == 0
    assert Path(inspect_request.artifacts_dir).is_relative_to(owned)
    assert "Application Support" in inspect_request.artifacts_dir

    output = owned / "outputs" / "中文 结果"
    result = native_backend.run_code(_request(data, output, TEMP_REPORT + r'''
baa_table("numerical_result", data.frame(mean_x = mean(dataset$x),
  slope = unname(stats::coef(stats::lm(y ~ x, data = dataset))[2])))
'''))
    assert result.ok, result.error + "\n" + result.stderr
    row = _table(result, "numerical_result")[0]
    assert float(row["mean_x"]) == pytest.approx(2.5)
    assert float(row["slope"]) == pytest.approx(2)
    assert all(Path(item.path).is_relative_to(output.resolve()) for item in result.tables)
    assert " " in str(data) and " " in str(output)
    assert data.read_bytes() == original
    _assert_private(_temporary_values(result))
    _assert_all_removed(observed_private_directories)


@pytest.mark.parametrize("raise_inside", [False, True])
def test_allocator_ignores_whitespace_temp_defaults_and_cleans_up(tmp_path, monkeypatch, raise_inside):
    from r_runtime.tempfiles import private_r_temp_directory

    hostile = tmp_path / "默认 临时 空格"
    hostile.mkdir()
    for variable in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(variable, str(hostile))
    monkeypatch.setattr(tempfile, "tempdir", str(hostile))
    observed = None

    class SyntheticFailure(Exception):
        pass

    try:
        with private_r_temp_directory() as directory:
            observed = Path(directory)
            assert observed.is_dir()
            assert not any(character.isspace() for character in str(observed))
            assert stat.S_IMODE(observed.stat().st_mode) == 0o700
            (observed / "owned-marker").write_text("synthetic", encoding="utf-8")
            if raise_inside:
                raise SyntheticFailure
    except SyntheticFailure:
        assert raise_inside
    assert observed is not None and not observed.exists()
    assert hostile.is_dir(), "Allocator cleanup must retain the caller's existing directory"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt denies writes outside the exact scratch leaf")
def test_real_r_scratch_leaf_does_not_grant_parent_neighbor_config_or_input(native_backend, desktop_paths):
    from r_runtime.tempfiles import private_r_temp_directory

    owned, data, config = desktop_paths
    output = owned / "outputs" / "边界 结果"
    output.mkdir(parents=True)
    originals = {data: data.read_bytes(), config: config.read_bytes()}
    runtime_roots = [Path(native_backend.runtime_env["BAA_R_HOME"])]
    runtime_roots.extend(Path(p) for p in native_backend.runtime_env["R_LIBS_USER"].split(os.pathsep) if p and Path(p).is_dir())
    with private_r_temp_directory() as raw_scratch:
        scratch = Path(raw_scratch).resolve()
        with tempfile.TemporaryDirectory(prefix="baa-test-scratch-neighbor-", dir=scratch.parent) as neighbor_name:
            neighbor = Path(neighbor_name)
            neighbor_file = neighbor / "synthetic-marker"
            neighbor_file.write_bytes(b"keep-neighbor")
            parent_file = scratch.parent / f"baa-test-denied-{uuid.uuid4().hex}"
            global_tmp = Path("/tmp").resolve() / f"baa-test-global-denied-{uuid.uuid4().hex}"
            targets = {
                "scratch": scratch / "allowed.txt", "scratch_child": scratch / "nested" / "allowed.txt",
                "parent": parent_file, "neighbor": neighbor_file, "user_config": config,
                "input": data, "global_tmp": global_tmp,
            }
            code = r'''
safe_write <- function(path) tryCatch({
  con <- file(path, "wb"); on.exit(close(con)); writeBin(charToRaw("changed"), con); TRUE
}, error = function(e) FALSE)
''' + f'dir.create({json.dumps(str(scratch / "nested"), ensure_ascii=False)});\n'
            code += "paths <- c(" + ",".join(json.dumps(str(p), ensure_ascii=False) for p in targets.values()) + ")\n"
            code += "names(paths) <- c(" + ",".join(json.dumps(n) for n in targets) + ")\n"
            code += 'utils::write.csv(data.frame(target=names(paths),allowed=vapply(paths,safe_write,logical(1))), "boundaries.csv", row.names=FALSE)\n'
            command = isolated_r_command(
                [native_backend.rscript_bin, "--vanilla", "-e", code],
                read_paths=[data], runtime_roots=runtime_roots, output_dir=output, scratch_dir=scratch,
            )
            environment = dict(native_backend.runtime_env, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch))
            try:
                completed = subprocess.run(command, cwd=output, env=environment, capture_output=True, text=True, timeout=30)
                assert completed.returncode == 0, completed.stderr
                with (output / "boundaries.csv").open(newline="", encoding="utf-8") as handle:
                    actual = {row["target"]: row["allowed"] for row in csv.DictReader(handle)}
                assert actual == {name: "TRUE" if name in {"scratch", "scratch_child"} else "FALSE" for name in targets}
                assert neighbor_file.read_bytes() == b"keep-neighbor"
                assert not parent_file.exists() and not global_tmp.exists()
                assert all(path.read_bytes() == payload for path, payload in originals.items())
            finally:
                # These two exact random probe names belong to this test only.
                parent_file.unlink(missing_ok=True)
                global_tmp.unlink(missing_ok=True)
    assert not scratch.exists()


@pytest.mark.parametrize("broad_scratch", ["filesystem_root", "input_parent"])
def test_scratch_cannot_convert_an_input_or_filesystem_root_to_writable(native_backend, desktop_paths, broad_scratch):
    owned, data, _config = desktop_paths
    output = owned / "outputs" / broad_scratch
    output.mkdir(parents=True)
    scratch = Path(data.anchor) if broad_scratch == "filesystem_root" else data.parent
    with pytest.raises(RIsolationError):
        isolated_r_command(
            [native_backend.rscript_bin, "--vanilla", "-e", 'cat("must not launch")'],
            read_paths=[data], runtime_roots=[Path(native_backend.runtime_env["BAA_R_HOME"])],
            output_dir=output, scratch_dir=scratch,
        )


def test_trusted_probe_and_host_prepare_also_have_private_r_temps(
    native_backend, desktop_paths, observed_private_directories, monkeypatch,
):
    owned, _data, _config = desktop_paths
    for variable in ("TMPDIR", "TMP", "TEMP"):
        monkeypatch.setenv(variable, str(owned))
    host = HostREnvironment(
        native_backend.rscript_bin,
        library_settings=RLibrarySettings(mode="managed", custom_directory=str(owned / "owned-r-library")),
    )
    verified = host.prepare()
    assert verified.rscript == Path(native_backend.rscript_bin)
    probed = native_backend._run_script_text(
        'cat(paste(c(tempdir(),Sys.getenv(c("TMPDIR","TMP","TEMP"))),collapse="\\n"))',
        timeout_seconds=30,
    )
    assert probed.returncode == 0, probed.stderr
    values = probed.stdout.strip().splitlines()
    assert len(values) == 4 and all(values)
    assert all(not any(c.isspace() for c in value) for value in values)
    assert values[1] == values[2] == values[3]
    assert Path(values[0]).resolve(strict=False).is_relative_to(Path(values[1]).resolve(strict=False))
    _assert_all_removed(observed_private_directories)


@pytest.mark.parametrize("termination", ["r_error", "timeout"])
def test_real_r_failure_and_timeout_remove_scratch(
    native_backend, desktop_paths, observed_private_directories, termination,
):
    owned, data, _config = desktop_paths
    output = owned / "outputs" / termination
    code = TEMP_REPORT + ('stop("synthetic R failure")' if termination == "r_error" else "Sys.sleep(30)")
    result = native_backend.run_code(_request(data, output, code, timeout=4 if termination == "timeout" else 30))
    assert not result.ok
    assert (output / "scratch-ready.txt").is_file(), result.error + "\n" + result.stderr
    if termination == "r_error":
        assert "synthetic R failure" in result.error + result.stderr
    else:
        assert "timed out" in result.error.lower()
    _assert_private(_temporary_values(result))
    _assert_all_removed(observed_private_directories)


def test_real_r_cancel_removes_its_scratch(native_backend, desktop_paths, observed_private_directories):
    owned, data, _config = desktop_paths
    output = owned / "outputs" / "取消 结果"
    conversation_id = f"scratch-cancel-{uuid.uuid4().hex}"
    research_run_controller.begin(conversation_id)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(native_backend.run_code, _request(data, output, TEMP_REPORT + "Sys.sleep(30)", conversation_id=conversation_id))
        try:
            _wait_for_ready([output], [future])
            with (output / "r_temporary_environment.csv").open(newline="", encoding="utf-8-sig") as handle:
                values = {row["variable"]: row["value"] for row in csv.DictReader(handle)}
            _assert_private(values, still_running=True)
            assert research_run_controller.cancel(conversation_id)
            result = future.result(timeout=10)
            assert not result.ok and "canceled" in result.error.lower()
        finally:
            research_run_controller.cancel(conversation_id)
            research_run_controller.finish(conversation_id)
    _assert_private(values)
    _assert_all_removed(observed_private_directories)


def test_concurrent_r_jobs_have_independent_scratch_and_cleanup(native_backend, desktop_paths, observed_private_directories):
    owned, data, _config = desktop_paths
    outputs = [owned / "outputs" / f"并发 结果 {i}" for i in (1, 2)]
    wait_code = TEMP_REPORT + r'''
started <- Sys.time()
while (!file.exists("release.flag")) {
  if (as.numeric(difftime(Sys.time(), started, units="secs")) > 20) stop("synthetic release marker timeout")
  Sys.sleep(0.05)
}
'''
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(native_backend.run_code, _request(data, output, wait_code)) for output in outputs]
        try:
            _wait_for_ready(outputs, futures)
            values = []
            for output in outputs:
                with (output / "r_temporary_environment.csv").open(newline="", encoding="utf-8-sig") as handle:
                    values.append({row["variable"]: row["value"] for row in csv.DictReader(handle)})
            scratch = [_assert_private(value, still_running=True) for value in values]
            assert scratch[0] != scratch[1]
            (outputs[0] / "release.flag").write_text("release owned first job", encoding="utf-8")
            first = futures[0].result(timeout=10)
            assert first.ok, first.error + "\n" + first.stderr
            assert not scratch[0].exists() and scratch[1].exists()
            (outputs[1] / "release.flag").write_text("release owned second job", encoding="utf-8")
            second = futures[1].result(timeout=10)
            assert second.ok, second.error + "\n" + second.stderr
        finally:
            for output in outputs:
                if output.is_dir():
                    (output / "release.flag").write_text("owned test cleanup", encoding="utf-8")
    assert all(not directory.exists() for directory in scratch)
    _assert_all_removed(observed_private_directories)
