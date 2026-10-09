import sys
import threading
import time

from agent.analysis.run_control import (
    ResearchRunCancelled,
    ResearchRunController,
    run_cancellable_process,
)


class FakeHandle:
    def __init__(self, cancel):
        self._cancel = cancel

    def cancel(self) -> None:
        self._cancel()


def test_cancel_invokes_only_registered_handles():
    calls = []
    controller = ResearchRunController()
    controller.begin("conversation-1")
    controller.register("conversation-1", FakeHandle(lambda: calls.append("r")))
    controller.register("conversation-1", FakeHandle(lambda: calls.append("llm")))

    assert controller.cancel("conversation-1") is True
    assert calls == ["llm", "r"]
    assert controller.cancel("conversation-1") is False


def test_handle_registered_after_cancel_is_canceled_immediately():
    calls = []
    controller = ResearchRunController()
    controller.begin("conversation-1")
    controller.cancel("conversation-1")

    controller.register("conversation-1", FakeHandle(lambda: calls.append("late")))

    assert calls == ["late"]


def test_cancel_terminates_registered_local_process():
    controller = ResearchRunController()
    controller.begin("conversation-1")
    outcome = []

    def run():
        try:
            run_cancellable_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                timeout_seconds=60,
                conversation_job_id="conversation-1",
                controller=controller,
            )
        except ResearchRunCancelled:
            outcome.append("canceled")

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    controller.cancel("conversation-1")
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert outcome == ["canceled"]


def test_process_reports_progress_before_completion_and_shutdown_cancels_it():
    controller = ResearchRunController()
    controller.begin("installation-1")
    output_ready = threading.Event()
    outcome = []

    def run():
        try:
            run_cancellable_process(
                [sys.executable, "-u", "-c", "import time; print('installing package', flush=True); time.sleep(30)"],
                timeout_seconds=60, conversation_job_id="installation-1", controller=controller,
                on_output=lambda text: output_ready.set() if "installing package" in text else None,
            )
        except ResearchRunCancelled:
            outcome.append("canceled")

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert output_ready.wait(3), "No progress delivered while the child was running"
    finally:
        controller.cancel_all()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert outcome == ["canceled"]
