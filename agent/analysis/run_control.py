"""Thread-safe cancellation registry for one research conversation run."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Protocol


class CancelHandle(Protocol):
    def cancel(self) -> None: ...


class ResearchRunCancelled(RuntimeError):
    pass


class CallbackCancelHandle:
    def __init__(self, callback: Callable[[], object]) -> None:
        self._callback = callback
        self._lock = threading.Lock()
        self._called = False

    def cancel(self) -> None:
        with self._lock:
            if self._called:
                return
            self._called = True
        self._callback()


class ProcessCancelHandle:
    """Terminate exactly one process group, with a bounded hard-kill fallback."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process
        self._lock = threading.Lock()
        self._called = False

    def cancel(self) -> None:
        with self._lock:
            if self._called or self._process.poll() is not None:
                return
            self._called = True
        if os.name == "nt":
            self._process.terminate()
        else:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                self._process.terminate()
        deadline = time.monotonic() + 2.0
        while self._process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._process.poll() is not None:
            return
        if os.name == "nt":
            self._process.kill()
        else:
            try:
                os.killpg(self._process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                self._process.kill()


class ResearchRunController:
    def __init__(self) -> None:
        self._handles: dict[str, list[CancelHandle]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.RLock()

    def begin(self, conversation_job_id: str) -> None:
        job_id = str(conversation_job_id or "")
        if not job_id:
            raise ValueError("conversation_job_id is required")
        with self._lock:
            self._handles[job_id] = []
            self._cancelled.discard(job_id)

    def register(self, conversation_job_id: str, handle: CancelHandle) -> None:
        job_id = str(conversation_job_id or "")
        cancel_now = False
        with self._lock:
            if job_id in self._cancelled:
                cancel_now = True
            else:
                self._handles.setdefault(job_id, []).append(handle)
        if cancel_now:
            handle.cancel()

    def cancel(self, conversation_job_id: str) -> bool:
        job_id = str(conversation_job_id or "")
        with self._lock:
            known = job_id in self._handles
            handles = self._handles.pop(job_id, [])
            self._cancelled.add(job_id)
        for handle in reversed(handles):
            try:
                handle.cancel()
            except Exception:
                # Cancellation is best effort across independent resources.
                continue
        return known or bool(handles)

    def is_cancelled(self, conversation_job_id: str) -> bool:
        with self._lock:
            return str(conversation_job_id or "") in self._cancelled

    def finish(self, conversation_job_id: str) -> None:
        job_id = str(conversation_job_id or "")
        with self._lock:
            self._handles.pop(job_id, None)
            self._cancelled.discard(job_id)

    def cancel_all(self) -> None:
        with self._lock:
            job_ids = tuple(self._handles)
        for job_id in job_ids:
            self.cancel(job_id)


research_run_controller = ResearchRunController()


def run_cancellable_process(
    command: list[str],
    *,
    timeout_seconds: int,
    conversation_job_id: str = "",
    controller: ResearchRunController = research_run_controller,
    on_output: Callable[[str], None] | None = None,
    **popen_kwargs,
) -> subprocess.CompletedProcess[str]:
    """Run one local child with 200ms cancellation and timeout polling."""
    options = dict(popen_kwargs)
    options.setdefault("stdout", subprocess.PIPE)
    options.setdefault("stderr", subprocess.PIPE)
    options.setdefault("text", True)
    if options.get("text") or options.get("universal_newlines") or options.get("encoding"):
        options.setdefault("encoding", "utf-8")
        options.setdefault("errors", "replace")
    if os.name == "nt":
        options.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
        options.setdefault("stdin", subprocess.DEVNULL)
    else:
        options.setdefault("start_new_session", True)
    if conversation_job_id and controller.is_cancelled(conversation_job_id):
        raise ResearchRunCancelled("Research execution canceled")
    from r_runtime.windows_isolation import WindowsRCommand
    if isinstance(command, WindowsRCommand) and os.name != "nt":
        raise OSError("A Windows AppContainer command cannot run through an unrestricted process")
    if os.name == "nt":
        from r_runtime.windows_isolation import WindowsJobProcess
        process = WindowsJobProcess(command, **options)
    else:
        process = subprocess.Popen(command, **options)
    handle = ProcessCancelHandle(process)
    if conversation_job_id:
        controller.register(conversation_job_id, handle)
    deadline = (
        None
        if int(timeout_seconds) == 0
        else time.monotonic() + max(1, int(timeout_seconds))
    )
    last_output = ""

    def report_output(stdout, stderr) -> None:
        nonlocal last_output
        if on_output is None:
            return
        def as_text(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
        tail = (as_text(stdout) + "\n" + as_text(stderr)).strip()[-2000:]
        if tail and tail != last_output:
            last_output = tail
            try:
                on_output(tail)
            except Exception:
                handle.cancel()
                process.communicate()
                raise

    try:
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                report_output(stdout, stderr)
                if conversation_job_id and controller.is_cancelled(conversation_job_id):
                    raise ResearchRunCancelled("Research execution canceled")
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired as exc:
                # A finished parent can leave a child holding a pipe open.
                # WindowsJobProcess.poll closes the Job and releases those
                # workers before the next communication attempt.
                if os.name == "nt":
                    process.poll()
                report_output(exc.output, exc.stderr)
                if conversation_job_id and controller.is_cancelled(conversation_job_id):
                    handle.cancel()
                    stdout, stderr = process.communicate()
                    raise ResearchRunCancelled("Research execution canceled") from exc
                if deadline is not None and time.monotonic() >= deadline:
                    handle.cancel()
                    stdout, stderr = process.communicate()
                    raise subprocess.TimeoutExpired(
                        command,
                        timeout_seconds,
                        output=stdout,
                        stderr=stderr,
                    ) from exc
    finally:
        if os.name == "nt":
            process.close_execution_resources()
