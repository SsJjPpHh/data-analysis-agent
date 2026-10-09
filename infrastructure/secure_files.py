"""Small filesystem primitives for runtime secrets and one-time credentials."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_private_text(path: str | Path, value: str) -> None:
    """Atomically replace a private text file with owner-only permissions.

    A same-directory temporary file is written and synced before replacement,
    so interruption cannot leave the target half-written.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1  # the file object now owns and closes the descriptor
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)


def write_private_bytes(path: str | Path, value: bytes) -> None:
    """Atomically replace a private binary file with owner-only permissions."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        temporary.unlink(missing_ok=True)
