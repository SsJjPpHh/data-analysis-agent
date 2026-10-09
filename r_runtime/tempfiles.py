"""Private, space-free temporary storage for each R process.

R rejects a temporary directory containing spaces before running any script.
The application data root may contain spaces, and must remain independent.
"""

from __future__ import annotations

import tempfile
import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path


class RTemporaryDirectoryError(OSError):
    pass


def _windows_short_path(path: Path) -> Path:
    """Keep R's temporary path space-free when a Windows 8.3 alias exists.

    The returned alias must not be resolved back to its long name before it
    is passed to R. On volumes without short names the API returns the long
    name, which the caller detects and rejects.
    """
    import ctypes
    from ctypes import wintypes

    function = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    function.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
    function.restype = wintypes.DWORD
    size = function(str(path), None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size)
    written = function(str(path), buffer, size)
    if not written or written >= size:
        raise OSError("Windows temporary path alias could not be read")
    alias = Path(buffer.value)
    if not alias.is_absolute() or not alias.samefile(path):
        raise OSError("Windows temporary path alias did not identify the same directory")
    return alias


def _temporary_parents() -> tuple[str, ...]:
    configured = str(os.environ.get("BAA_R_TEMP_ROOT") or "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise RTemporaryDirectoryError("BAA_R_TEMP_ROOT 必须是已存在、可写的绝对目录。")
        return (str(root),)
    if sys.platform == "win32":
        candidates = [tempfile.gettempdir()]
        if os.environ.get("SystemRoot"):
            candidates.append(str(Path(os.environ["SystemRoot"]) / "Temp"))
        drive = str(os.environ.get("SystemDrive") or "C:").rstrip("/\\")
        candidates.append(str(Path(drive + "\\") / "Temp"))
        return tuple(dict.fromkeys(candidates))
    return tuple(dict.fromkeys((tempfile.gettempdir(), "/tmp", "/var/tmp")))


@contextmanager
def private_r_temp_directory() -> Iterator[Path]:
    temporary = None
    runtime_path = None
    for candidate in _temporary_parents():
        try:
            parent = Path(candidate).resolve(strict=True)
            if not parent.is_dir():
                continue
            if sys.platform != "win32" and any(char.isspace() for char in str(parent)):
                continue
            temporary = tempfile.TemporaryDirectory(prefix="rsw-r-", dir=parent)
            runtime_path = Path(temporary.name).resolve(strict=True)
            if os.name == "nt":
                from r_runtime.windows_isolation import secure_windows_temp_directory

                secure_windows_temp_directory(runtime_path)
            if sys.platform == "win32" and any(char.isspace() for char in str(runtime_path)):
                runtime_path = _windows_short_path(runtime_path)
            if any(char.isspace() for char in str(runtime_path)):
                temporary.cleanup()
                temporary = None
                continue
            break
        except OSError:
            if temporary is not None:
                temporary.cleanup()
                temporary = None
            continue
    if temporary is None:
        raise RTemporaryDirectoryError(
            "无法创建不含空格的 R 专用临时目录。"
            "可设置 BAA_R_TEMP_ROOT 为已存在、可写且路径不含空格的目录后重试。"
        )
    try:
        yield runtime_path
    finally:
        temporary.cleanup()


@contextmanager
def r_temporary_environment(environment: Mapping[str, str]) -> Iterator[dict[str, str]]:
    with private_r_temp_directory() as directory:
        result = dict(environment)
        result.update(TMPDIR=str(directory), TMP=str(directory), TEMP=str(directory))
        yield result
