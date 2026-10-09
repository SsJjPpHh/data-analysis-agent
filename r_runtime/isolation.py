"""OS-enforced file access for analysis R processes, inherited by their workers.

Package installation/readiness is a separate trusted host operation. Analysis
never falls back to an unrestricted process if an isolation backend is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path
from collections.abc import Sequence


class RIsolationError(RuntimeError):
    pass


def _isolation_launcher() -> tuple[str, str]:
    if sys.platform == "darwin":
        launcher = "/usr/bin/sandbox-exec"
        if Path(launcher).is_file():
            return "macos_seatbelt", launcher
        raise RIsolationError("macOS R isolation is unavailable (sandbox-exec missing)")
    if sys.platform.startswith("linux"):
        launcher = shutil.which("bwrap")
        if launcher:
            return "linux_bubblewrap", launcher
        raise RIsolationError("Linux R isolation requires bubblewrap (bwrap); install bubblewrap before running analyses.")
    if sys.platform == "win32":
        from r_runtime.windows_isolation import check_windows_isolation_api
        try:
            check_windows_isolation_api()
        except (OSError, AttributeError) as exc:
            raise RIsolationError(f"Windows R isolation unavailable: {exc}") from exc
        return "windows_appcontainer", "CreateProcessW"
    raise RIsolationError("当前平台尚无受支持的 R 文件隔离后端；分析已停止，未启动未隔离的 R 进程。")


def isolation_availability() -> dict[str, str | bool | list[str]]:
    """Expose backend presence; this is not platform execution acceptance."""
    try:
        backend, _launcher = _isolation_launcher()
        return {"available": True, "backend": backend, "verification": "launcher_only", "supported_platforms": ["macOS", "Linux", "Windows"], "error": ""}
    except RIsolationError as exc:
        return {"available": False, "backend": "", "verification": "launcher_only", "supported_platforms": ["macOS", "Linux", "Windows"], "error": str(exc)}


def isolated_r_command(
    command: Sequence[str], *, read_paths: Sequence[Path], runtime_roots: Sequence[Path],
    output_dir: Path, checkpoint_dir: Path | None = None,
    scratch_dir: Path | None = None,
) -> list[str]:
    output = output_dir.resolve(strict=True)
    runtime = tuple(path.resolve(strict=True) for path in runtime_roots)
    inputs = tuple(path.resolve(strict=True) for path in read_paths)
    if any(path == Path(path.anchor) for path in runtime):
        raise RIsolationError("R runtime read roots must not expose the whole filesystem")
    if any(path.is_relative_to(output) for path in inputs + runtime):
        raise RIsolationError("R inputs and runtime must be outside the writable output directory")
    writes = [output]
    if scratch_dir is not None:
        if scratch_dir.is_symlink():
            raise RIsolationError("R temporary directory must not be a symbolic link")
        scratch = scratch_dir.resolve(strict=True)
        if not scratch.is_dir() or (sys.platform != "win32" and any(char.isspace() for char in str(scratch))):
            raise RIsolationError("R temporary directory must be a space-free directory")
        info = scratch.stat()
        if not scratch.name.startswith("rsw-r-") or (sys.platform != "win32" and stat.S_IMODE(info.st_mode) != 0o700):
            raise RIsolationError("R temporary directory must be a private operation directory")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise RIsolationError("R temporary directory must belong to the application user")
        if scratch == Path(scratch.anchor) or scratch == output:
            raise RIsolationError("R temporary directory must be a separate private directory")
        if any(path.is_relative_to(scratch) or scratch.is_relative_to(path) for path in inputs + runtime):
            raise RIsolationError("R temporary directory must be separate from inputs and runtime")
        writes.append(scratch)
    reads = list(dict.fromkeys((*runtime, *inputs)))
    if checkpoint_dir is not None and checkpoint_dir.is_dir():
        reads.append(checkpoint_dir.resolve(strict=True))

    backend, launcher = _isolation_launcher()
    if backend == "windows_appcontainer":
        from r_runtime.windows_isolation import WindowsRCommand
        return WindowsRCommand(command, read_paths=reads, write_paths=writes)
    if backend == "macos_seatbelt":
        # Runtime/system resources are read-only. User home and application
        # state are not granted as roots. Namespace-qualified R calls cannot
        # bypass the kernel's restrictions, nor can output symlinks.
        system = [Path(p) for p in (
            "/System/Library", "/System/Volumes/Preboot/Cryptexes", "/System/Cryptexes",
            "/usr", "/bin", "/sbin", "/dev", "/private/etc",
            "/Library/Fonts", "/Library/ColorSync", "/Library/Preferences/Logging",
            "/private/var/db/timezone", "/private/var/db/dyld",
            "/opt/homebrew/lib", "/opt/homebrew/opt",
        ) if Path(p).exists()]
        reads.extend(path.resolve(strict=True) for path in system)
        def rule(path: Path) -> str:
            return f'({"subpath" if path.is_dir() else "literal"} {json.dumps(str(path), ensure_ascii=False)})'

        profile = "\n".join((
            "(version 1)", "(allow default)",
            "(deny file-read-data)", "(deny file-write*)", "(deny network*)",
            "(deny appleevent-send)",
            # An explicit file-read-data denial needs the same concrete
            # operation for its exceptions; a file-read* rule does not win.
            "(allow file-read-data " + " ".join(rule(path) for path in dict.fromkeys((*reads, *writes))) + ' (literal "/"))',
            "(allow file-write* " + " ".join(rule(path) for path in writes) + ' (literal "/dev/null"))',
        ))
        # A previous analysis may still have a worker in the output directory.
        # Pass the trusted profile by value so it cannot replace a profile file
        # between Python writing it and sandbox-exec reading it.
        return [launcher, "-p", profile, *command]

    if backend == "linux_bubblewrap":
        args = [launcher, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL", "--tmpfs", "/", "--proc", "/proc", "--dev", "/dev"]
        system = [Path(p) for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc") if Path(p).exists()]
        # Keep conventional symlink paths (/bin, /lib) usable in the empty root.
        for path in system:
            if path.is_symlink():
                args.extend(("--symlink", str(path.resolve()), str(path)))
            else:
                args.extend(("--ro-bind", str(path), str(path)))
        for path in reads:
            if not any(path.is_relative_to(root.resolve()) for root in system):
                args.extend(("--ro-bind", str(path), str(path)))
        for path in writes:
            args.extend(("--bind", str(path), str(path)))
        args.extend(("--chdir", str(output), "--", *command))
        return args

    raise RIsolationError("Unsupported R isolation backend")
