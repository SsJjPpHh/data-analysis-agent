"""Exercise real Waitress workers during desktop shutdown."""

import threading
import time
from urllib.request import ProxyHandler, build_opener

from desktop.runtime import LocalWebRuntime


def test_shutdown_drains_inflight_request_before_closing_trigger_and_connections(monkeypatch, caplog):
    from flask import Flask

    application = Flask("local-web-shutdown")
    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    @application.get("/delayed")
    def delayed():
        entered.set()
        assert release.wait(5), "Test request was never released"
        completed.set()
        return "request completed"

    monkeypatch.setattr("api.create_app", lambda: application)
    runtime = LocalWebRuntime()
    runtime.start()
    server, server_thread = runtime.server, runtime.thread
    outcomes = []

    def request():
        try:
            with build_opener(ProxyHandler({})).open(
                f"http://127.0.0.1:{runtime.port}/delayed", timeout=5,
            ) as response:
                outcomes.append(response.read().decode("utf-8"))
        except BaseException as exc:
            outcomes.append(exc)

    client = threading.Thread(target=request)
    client.start()
    stopped = threading.Event()
    errors = []

    def stop():
        try:
            runtime.stop()
        except BaseException as exc:
            errors.append(exc)
        finally:
            stopped.set()

    stopper = threading.Thread(target=stop)
    try:
        assert entered.wait(3)
        stopper.start()
        time.sleep(0.1)
        assert not stopped.is_set(), "Shutdown closed the server while its request was running"
        assert not server.trigger._closed
        release.set()
        stopper.join(5)
        client.join(5)
        assert stopped.is_set() and not stopper.is_alive()
        assert not client.is_alive()
        assert completed.is_set()
        assert errors == []
        assert outcomes == ["request completed"]
        assert not server_thread.is_alive()
        assert server._map == {}
        assert server.trigger._closed
        with server.task_dispatcher.lock:
            assert not server.task_dispatcher.threads
        assert not any(record.levelno >= 40 for record in caplog.records)
        runtime.stop()  # Repeated Qt/aboutToQuit/finally shutdown is harmless.
    finally:
        release.set()
        if stopper.ident is not None:
            stopper.join(5)
        runtime.stop()
        client.join(5)


def test_shutdown_budget_keeps_trigger_alive_until_a_slow_worker_finishes(monkeypatch, caplog):
    from flask import Flask

    application = Flask("bounded-web-shutdown")
    entered = threading.Event()
    release = threading.Event()

    @application.get("/delayed")
    def delayed():
        entered.set()
        assert release.wait(12)
        return "slow request completed"

    monkeypatch.setattr("api.create_app", lambda: application)
    runtime = LocalWebRuntime()
    runtime.start()
    server, server_thread = runtime.server, runtime.thread
    port = runtime.port
    outcomes = []

    def request():
        try:
            with build_opener(ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/delayed", timeout=12,
            ) as response:
                outcomes.append(response.read().decode("utf-8"))
        except BaseException as exc:
            outcomes.append(exc)

    client = threading.Thread(target=request)
    client.start()
    try:
        assert entered.wait(3)
        started = time.monotonic()
        runtime.stop()
        assert time.monotonic() - started < 7
        assert server_thread.is_alive()
        assert not server.trigger._closed
        release.set()
        client.join(3)
        server_thread.join(3)
        assert outcomes == ["slow request completed"]
        assert not client.is_alive() and not server_thread.is_alive()
        assert server._map == {}
        assert server.trigger._closed
        assert not any(record.levelno >= 40 for record in caplog.records)
    finally:
        release.set()
        runtime.stop()
        client.join(3)
        server_thread.join(3)
