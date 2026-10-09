"""Runtime module for the source-run desktop application.

Callers only prepare/start/stop this module. The user's local R runtime,
launch secret, loopback Flask port, and process cleanup remain implementation
details behind that interface. AI inference uses the configured HTTPS API.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

from infrastructure.local_web import LOCAL_MAX_REQUEST_BODY_SIZE, local_web_threads
from infrastructure.paths import data_path, default_data_root
from r_runtime.environment import HostREnvironment, REnvironmentError


log = logging.getLogger(__name__)


class DesktopRuntimeError(RuntimeError):
    """The desktop runtime could not become ready."""


def _request_ready(url: str, timeout: float = 1.0) -> bool:
    try:
        request = Request(url, headers={"Accept": "application/json"})
        # Readiness is always loopback. A desktop user's HTTPS/HTTP proxy must
        # neither receive this request nor make local startup depend on it.
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except HTTPError:
        return False
    except (OSError, URLError, ValueError):
        return False


class HostRRuntime:
    """Verify and expose the user's local R installation."""

    def __init__(self, environment: HostREnvironment | None = None):
        self.environment = environment or HostREnvironment.locate()
        self.verified = None

    def prepare(self) -> None:
        try:
            self.verified = self.environment.prepare()
            runtime_environment = self.environment.process_environment(verified=self.verified)
            for name in ("BAA_RSCRIPT_BIN",):
                value = str(runtime_environment.get(name) or "").strip()
                if value:
                    os.environ[name] = value
            os.environ["BAA_R_BACKEND"] = "local"
        except REnvironmentError as exc:
            raise DesktopRuntimeError(str(exc)) from exc

    def stop(self) -> None:
        self.verified = None


class LocalWebRuntime:
    def __init__(self):
        self.server = None
        self.thread: threading.Thread | None = None
        self.port = 0
        self._stop_lock = threading.Lock()

    def start(self) -> None:
        from api import create_app
        from waitress import create_server

        self.server = create_server(
            create_app(),
            host="127.0.0.1",
            port=0,
            send_bytes=1,
            inbuf_overflow=1024 * 1024,
            max_request_body_size=LOCAL_MAX_REQUEST_BODY_SIZE,
            connection_limit=100,
            channel_timeout=300,
            threads=local_web_threads(),
        )
        self.port = int(self.server.effective_port)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="desktop-web")
        self.thread.start()

    def wait_until_ready(self) -> None:
        deadline = time.monotonic() + 20
        url = f"http://127.0.0.1:{self.port}/api/health"
        while time.monotonic() < deadline:
            if _request_ready(url):
                return
            time.sleep(0.05)
        raise DesktopRuntimeError("Local web module did not become ready")

    def stop(self) -> None:
        with self._stop_lock:
            server, thread = self.server, self.thread
            self.server = None
            self.thread = None
            self.port = 0
        if server is None:
            return

        deadline = time.monotonic() + 5.0
        # BaseWSGIServer.close() closes the trigger before its WSGI workers.
        # An in-flight request then writes to a closed pipe when it finishes.
        # Keep the event loop/trigger alive while retiring the dispatcher.
        server.accepting = False
        dispatcher = server.task_dispatcher
        dispatcher.shutdown(cancel_pending=True, timeout=5.0)

        def close_channels() -> None:
            # Close the listener, trigger and every active HTTP channel in the
            # event-loop thread, after no worker can pull the trigger again.
            def close_map() -> None:
                server.asyncore.close_all(map=server._map)

            if thread is not None and thread.is_alive():
                server.trigger.pull_trigger(close_map)
            else:
                close_map()

        with dispatcher.lock:
            workers_pending = bool(dispatcher.threads)
        if workers_pending:
            # A route that ignores cancellation must not block app exit or
            # race a closed trigger. Finish cleanup after its workers return.
            def finish_shutdown() -> None:
                with dispatcher.lock:
                    while dispatcher.threads:
                        dispatcher.thread_exit_cv.wait()
                dispatcher.shutdown(cancel_pending=True, timeout=0)
                close_channels()

            threading.Thread(
                target=finish_shutdown, daemon=True, name="desktop-web-cleanup",
            ).start()
        else:
            close_channels()
            if thread is not None and thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))


@dataclass(frozen=True)
class RuntimeStatus:
    phase: str
    web_url: str = ""
    data_dir: str = ""
    r_error: str = ""


class DesktopRuntime:
    """Own every process needed by one local application instance."""

    def __init__(self):
        os.environ.setdefault("BAA_DATA_DIR", str(default_data_root()))
        os.environ.setdefault("LOG_DIR", str(data_path("logs")))
        os.environ.setdefault("BAA_DESKTOP_MODE", "1")
        os.environ["BAA_PRODUCT_MODE"] = "research"
        os.environ["BAA_R_BACKEND"] = "local"
        os.environ.setdefault("BAA_R_TIMEOUT_SECONDS", "0")
        self.launch_token = secrets.token_urlsafe(32)
        os.environ["BAA_DESKTOP_TOKEN"] = self.launch_token
        self.r_runtime = HostRRuntime()
        self.web = LocalWebRuntime()
        self._status = RuntimeStatus("created", data_dir=str(data_path()))

    @property
    def status(self) -> RuntimeStatus:
        return self._status

    @property
    def bootstrap_url(self) -> str:
        if not self.web.port:
            return ""
        return f"http://127.0.0.1:{self.web.port}/desktop/bootstrap?token={quote(self.launch_token, safe='')}"

    def prepare(self) -> RuntimeStatus:
        for name in ("config", "logs", "projects", "runtime", "exports", "uploads", "outputs"):
            data_path(name).mkdir(parents=True, exist_ok=True)
        r_error = ""
        try:
            self.r_runtime.prepare()
        except DesktopRuntimeError as exc:
            r_error = str(exc)
            log.warning("R is unavailable; opening the workbench for configuration: %s", exc)
        self._status = RuntimeStatus("prepared", data_dir=str(data_path()), r_error=r_error)
        return self._status

    def start(self) -> RuntimeStatus:
        try:
            self.prepare()
            self.web.start()
            self.web.wait_until_ready()
            self._status = RuntimeStatus(
                "ready",
                web_url=f"http://127.0.0.1:{self.web.port}",
                data_dir=str(data_path()),
                r_error=self._status.r_error,
            )
            return self._status
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        from agent.analysis.run_control import research_run_controller

        research_run_controller.cancel_all()
        self.web.stop()
        self.r_runtime.stop()
        self._status = RuntimeStatus("stopped", data_dir=str(data_path()))
