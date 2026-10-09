"""Policy tests everywhere; actual AppContainer/R tests require native Windows.

Native tests run on a Windows host with a local R >=4.3 installation.
Skipped native tests on macOS/Linux are explicitly not Windows acceptance.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

from agent.analysis.run_control import ResearchRunCancelled, ResearchRunController, run_cancellable_process
from r_runtime import isolation, windows_isolation
from r_runtime.tempfiles import private_r_temp_directory


def test_windows_command_carries_policy_without_an_unrestricted_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(isolation.sys, "platform", "win32")
    monkeypatch.setattr(windows_isolation, "check_windows_isolation_api", lambda: None)
    output, runtime, scratch = (tmp_path / name for name in ("结果 output", "R runtime", "rsw-r-空间"))
    for path in (output, runtime, scratch):
        path.mkdir()
    command = isolation.isolated_r_command(["Rscript.exe", "analysis.R"],
                                           runtime_roots=[runtime], read_paths=[],
                                           output_dir=output, scratch_dir=scratch)
    assert isinstance(command, windows_isolation.WindowsRCommand)
    assert command.isolation_backend == "windows_appcontainer"
    assert command.read_paths == (runtime,)
    assert command.write_paths == (output, scratch)
    assert isolation.isolation_availability()["backend"] == "windows_appcontainer"


@pytest.mark.skipif(os.name == "nt", reason="non-Windows fail-closed policy")
def test_windows_policy_never_launches_plain_popen_on_other_platforms(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: called.append(True))
    policy = windows_isolation.WindowsRCommand(["Rscript.exe"], read_paths=[], write_paths=[tmp_path])
    with pytest.raises(OSError, match="unrestricted"):
        run_cancellable_process(policy, timeout_seconds=1)
    assert called == []


@pytest.mark.skipif(os.name == "nt", reason="native APIs are tested separately")
def test_api_availability_cannot_be_claimed_on_a_non_windows_host():
    with pytest.raises(OSError, match="Windows 10"):
        windows_isolation.check_windows_isolation_api()


def test_partial_acl_grant_failure_rolls_back_attempted_root(monkeypatch, tmp_path):
    """A failure during child ACL propagation must not leak the root grant."""
    calls = []

    class API:
        def CreateProfile(self, _name, _display, _description, _capabilities, _count, sid):
            sid._obj.value = 1234
            return 0

        def ConvertSidToStringSid(self, _sid, output):
            output._obj.value = "S-1-15-2-1234"
            return True

        def LocalFree(self, _pointer):
            pass

        def FreeSid(self, _pointer):
            pass

        def DeleteProfile(self, name):
            calls.append(("profile-deleted", name))
            return 0

    def grant(path, _sid, *, revoke=False, writable=False):
        calls.append(("revoke" if revoke else "grant", path))
        if not revoke:
            raise OSError("root changed but a child ACL could not be propagated")

    monkeypatch.setattr(windows_isolation, "_api", API)
    monkeypatch.setattr(windows_isolation, "_reject_reparse_tree", lambda _path: None)
    monkeypatch.setattr(windows_isolation, "_change_acl", grant)
    command = windows_isolation.WindowsRCommand(["Rscript"], read_paths=[tmp_path], write_paths=[])
    with pytest.raises(OSError, match="child ACL"):
        windows_isolation._PermissionLease(command)
    assert calls[:2] == [("grant", tmp_path), ("revoke", tmp_path)]
    assert calls[2][0] == "profile-deleted"


native_windows = pytest.mark.skipif(sys.platform != "win32", reason="requires native Windows AppContainer")


@pytest.fixture
def native_r(tmp_path):
    rscript = shutil.which("Rscript")
    assert rscript, "Native Windows acceptance requires Rscript on PATH; do not silently skip it"
    probe = subprocess.run([rscript, "--vanilla", "-e",
                            'cat(normalizePath(R.home(), winslash="/"), "\\n"); cat(.libPaths(), sep="\\n")'],
                           capture_output=True, text=True, encoding="utf-8", check=True)
    roots = tuple(dict.fromkeys(Path(line.strip()).resolve() for line in probe.stdout.splitlines()
                                if line.strip() and Path(line.strip()).is_dir()))
    assert roots
    output = tmp_path / "中文 结果 output"
    inputs = tmp_path / "研究输入 data"
    secret = tmp_path / "私有文件 secret.txt"
    output.mkdir()
    inputs.mkdir()
    dataset = inputs / "测试数据.csv"
    dataset.write_text("x,y\n1,3\n2,5\n3,7\n4,9\n", encoding="utf-8")
    secret.write_text("private-record-not-an-analysis-input", encoding="utf-8")
    return rscript, roots, output, dataset, secret


def _r_path(path):
    return json.dumps(str(Path(path).as_posix()), ensure_ascii=False)


def _native_command(native_r, scratch, script, *, extra_reads=()):
    rscript, roots, output, dataset, _secret = native_r
    script_file = output / "analysis.R"
    script_file.write_text(script, encoding="utf-8")
    command = isolation.isolated_r_command([rscript, "--vanilla", str(script_file)],
                                           runtime_roots=roots, read_paths=[dataset, *extra_reads],
                                           output_dir=output, scratch_dir=scratch)
    env = dict(os.environ, TMPDIR=str(scratch), TMP=str(scratch), TEMP=str(scratch),
               BAA_OUTPUT_DIR=str(output), BAA_R_WORKERS="1")
    return command, env


@native_windows
def test_native_r_calculation_unicode_helpers_and_file_network_boundaries(native_r):
    _rscript, _roots, output, dataset, secret = native_r
    helper = Path(__file__).resolve().parents[2] / "r_runtime/helpers/baa_helpers.R"
    original = hashlib.sha256(dataset.read_bytes()).hexdigest()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        with private_r_temp_directory() as scratch:
            script = f"""
            source({_r_path(helper)}, encoding="UTF-8")
            d <- read.csv({_r_path(dataset)})
            fit <- lm(y ~ x, data=d)
            stopifnot(abs(coef(fit)[[2]] - 2) < 1e-10)
            stopifnot(identical(unlist(baa_parallel_lapply(1:3, function(x) x * x, workers=2)), c(1L,4L,9L)))
            denied <- function(expr) inherits(tryCatch(force(expr), error=function(e)e), "error")
            stopifnot(denied(readLines({_r_path(secret)})))
            stopifnot(denied(writeLines("tampered", {_r_path(dataset)})))
            stopifnot(denied(writeLines("leak", {_r_path(secret.parent / 'outside.txt')})))
            stopifnot(denied(socketConnection(host="127.0.0.1", port={port}, open="r+", timeout=1)))
            write.csv(data.frame(slope=coef(fit)[[2]]), file="result.csv", row.names=FALSE)
            writeLines("临时目录可写", file.path(tempdir(), "验证.txt"), useBytes=TRUE)
            png("figure.png"); plot(d$x,d$y); dev.off()
            cat("中文模型计算成功\\n")
            """
            command, env = _native_command(native_r, scratch, script, extra_reads=[helper])
            completed = run_cancellable_process(command, timeout_seconds=45, env=env, cwd=output)
            assert completed.returncode == 0, completed.stdout + completed.stderr
            assert "中文模型计算成功" in completed.stdout
            assert (output / "result.csv").exists()
            assert (output / "figure.png").stat().st_size > 0
    finally:
        listener.close()
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == original
    assert not (secret.parent / "outside.txt").exists()


@native_windows
def test_native_operation_acl_is_removed_after_success(native_r):
    _rscript, _roots, output, dataset, _secret = native_r
    with private_r_temp_directory() as scratch:
        command, env = _native_command(native_r, scratch, 'cat("ok\\n")')
        process = windows_isolation.WindowsJobProcess(command, cwd=output, env=env,
                                                      stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                      stderr=subprocess.PIPE, text=True, encoding="utf-8")
        sid = process.isolation_sid
        try:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stdout + stderr
        finally:
            process.close_execution_resources()
        assert sid.startswith("S-1-15-2-")
        for path in (output, output / "analysis.R", dataset, scratch):
            acl = subprocess.run(["icacls", str(path)], capture_output=True, check=True)
            assert sid.encode("ascii") not in acl.stdout


@native_windows
def test_native_worker_denials_are_inherited_and_timeout_kills_tree(native_r):
    rscript, _roots, output, _dataset, secret = native_r
    with private_r_temp_directory() as scratch:
        child = output / "worker.R"
        child.write_text(f"""
            stopifnot(inherits(tryCatch(readLines({_r_path(secret)}), error=function(e)e), "error"))
            writeLines(as.character(Sys.getpid()), "worker.pid")
            repeat {{ writeLines(as.character(Sys.time()), "worker.alive"); Sys.sleep(0.1) }}
            """, encoding="utf-8")
        script = f"""
            system2({_r_path(rscript)}, c("--vanilla", shQuote({_r_path(child)})),
                    stdout="worker.stdout", stderr="worker.stderr", wait=FALSE)
            Sys.sleep(30)
            """
        command, env = _native_command(native_r, scratch, script)
        with pytest.raises(subprocess.TimeoutExpired):
            run_cancellable_process(command, timeout_seconds=4, env=env, cwd=output)
        assert (output / "worker.pid").exists(), (output / "worker.stderr").read_text(errors="replace")
        heartbeat = (output / "worker.alive").stat().st_mtime_ns
        time.sleep(0.5)
        assert (output / "worker.alive").stat().st_mtime_ns == heartbeat


@native_windows
def test_native_cancel_stops_r_and_its_worker(native_r):
    rscript, _roots, output, _dataset, _secret = native_r
    controller = ResearchRunController()
    controller.begin("native-windows-cancel")
    errors = []
    with private_r_temp_directory() as scratch:
        child = output / "worker.R"
        child.write_text('repeat {writeLines(as.character(Sys.time()), "worker.alive"); Sys.sleep(0.1)}',
                         encoding="utf-8")
        script = f'system2({_r_path(rscript)}, c("--vanilla", shQuote({_r_path(child)})), stdout="worker.stdout", stderr="worker.stderr", wait=FALSE); Sys.sleep(30)'
        command, env = _native_command(native_r, scratch, script)

        def run():
            try:
                run_cancellable_process(command, timeout_seconds=45, env=env, cwd=output,
                                        controller=controller, conversation_job_id="native-windows-cancel")
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 20
        try:
            while not (output / "worker.alive").exists() and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert (output / "worker.alive").exists(), str(errors)
        finally:
            controller.cancel("native-windows-cancel")
            thread.join(timeout=10)
        assert not thread.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], ResearchRunCancelled), errors
        heartbeat = (output / "worker.alive").stat().st_mtime_ns
        time.sleep(0.5)
        assert (output / "worker.alive").stat().st_mtime_ns == heartbeat
