"""Keep test runtime state outside the source and real user directories."""

from __future__ import annotations

import os
import tempfile
import atexit

import pytest


_test_data = tempfile.TemporaryDirectory(prefix="daa-tests-")
os.environ["BAA_DATA_DIR"] = _test_data.name
atexit.register(_test_data.cleanup)
if os.getenv("BAA_TEST_R_LIBS"):
    os.environ["R_LIBS_USER"] = os.environ["BAA_TEST_R_LIBS"]


@pytest.fixture(autouse=True)
def _enable_source_development_mode(monkeypatch):
    tracked = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("BAA_") or key == "LOG_DIR"
    }
    yield

    # DesktopRuntime intentionally configures its process through os.environ.
    # Keep that production behaviour while preventing one constructor test from
    # turning every later Flask client into a token-protected desktop session.
    for key in tuple(os.environ):
        if (key.startswith("BAA_") or key == "LOG_DIR") and key not in tracked:
            os.environ.pop(key, None)
    os.environ.update(tracked)
