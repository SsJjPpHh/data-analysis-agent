"""Adaptive resource policy for the user's local R runtime."""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


MIB = 1024 * 1024
GIB = 1024 * MIB
_SIZE_RE = re.compile(r"^(\d+)(b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?$", re.IGNORECASE)
_SIZE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "kib": 1024,
    "m": MIB,
    "mb": MIB,
    "mib": MIB,
    "g": GIB,
    "gb": GIB,
    "gib": GIB,
    "t": 1024 * GIB,
    "tb": 1024 * GIB,
    "tib": 1024 * GIB,
}


@dataclass(frozen=True)
class HostResources:
    cpu_count: int
    memory_bytes: int
    free_disk_bytes: int


@dataclass(frozen=True)
class LocalResourcePolicy:
    host: HostResources
    output_size: str
    timeout_seconds: int
    worker_count: int
    native_thread_count: int
    memory_budget_bytes: int
    in_memory_threshold_bytes: int
    resource_class: str
    parallel_backend: str


def _windows_memory_bytes() -> int:
    import ctypes
    from ctypes import wintypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD), ("load", wintypes.DWORD),
            ("total_physical", ctypes.c_ulonglong), ("available_physical", ctypes.c_ulonglong),
            ("total_page_file", ctypes.c_ulonglong), ("available_page_file", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong), ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(status)
    function = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    function.argtypes = (ctypes.POINTER(MemoryStatus),)
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(status)):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(status.total_physical)


def discover_host_resources(storage_path: str | Path | None = None) -> HostResources:
    probe = Path(storage_path or Path.cwd()).expanduser().resolve(strict=False)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    disk = shutil.disk_usage(probe)
    try:
        memory_bytes = _windows_memory_bytes() if sys.platform == "win32" else int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, TypeError, ValueError):
        memory_bytes = 0
    return HostResources(
        cpu_count=max(1, int(os.cpu_count() or 1)),
        memory_bytes=max(0, memory_bytes),
        free_disk_bytes=max(1, int(disk.free)),
    )


def parse_size_bytes(value: str, *, field: str) -> int:
    raw = str(value or "").strip().lower()
    match = _SIZE_RE.fullmatch(raw)
    if match is None or int(match.group(1)) < 1:
        raise ValueError(f"{field} must be a positive byte count such as 4g")
    return int(match.group(1)) * _SIZE_MULTIPLIERS[str(match.group(2) or "").lower()]


def _format_size(size_bytes: int) -> str:
    if size_bytes % GIB == 0:
        return f"{size_bytes // GIB}g"
    if size_bytes % MIB == 0:
        return f"{size_bytes // MIB}m"
    return str(size_bytes)


def compute_local_resource_policy(
    host: HostResources | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    storage_path: str | Path | None = None,
    resource_class: str = "standard",
    workload_size_bytes: int = 0,
) -> LocalResourcePolicy:
    env = os.environ if environment is None else environment
    measured = host or discover_host_resources(storage_path)
    normalized_class = str(resource_class or "standard").strip().lower()
    if normalized_class not in {"standard", "extended"}:
        raise ValueError("resource_class must be standard or extended")
    raw_output = str(env.get("BAA_R_OUTPUT_SIZE") or "").strip().lower()
    if raw_output:
        parse_size_bytes(raw_output, field="BAA_R_OUTPUT_SIZE")
        output_size = raw_output
    else:
        reserve = max(10 * GIB, measured.free_disk_bytes // 5)
        usable = max(GIB, measured.free_disk_bytes - reserve)
        output_size = _format_size(max(GIB, min(32 * GIB, usable // 4)))
    raw_timeout = str(env.get("BAA_R_TIMEOUT_SECONDS") or "").strip()
    try:
        timeout = int(raw_timeout) if raw_timeout else 0
    except ValueError:
        timeout = 0

    raw_memory = str(env.get("BAA_R_MEMORY_BUDGET") or "").strip().lower()
    if raw_memory:
        requested_memory = parse_size_bytes(raw_memory, field="BAA_R_MEMORY_BUDGET")
        memory_budget = (
            min(requested_memory, measured.memory_bytes)
            if measured.memory_bytes > 0
            else requested_memory
        )
    elif measured.memory_bytes > 0:
        reserve = max(2 * GIB, measured.memory_bytes // 4)
        memory_budget = max(GIB, measured.memory_bytes - reserve)
    else:
        memory_budget = 0

    raw_threshold = str(env.get("BAA_LAZY_DATASET_THRESHOLD_BYTES") or "").strip().lower()
    if raw_threshold:
        in_memory_threshold = parse_size_bytes(
            raw_threshold,
            field="BAA_LAZY_DATASET_THRESHOLD_BYTES",
        )
    elif memory_budget > 0:
        in_memory_threshold = max(256 * MIB, min(4 * GIB, memory_budget // 12))
    else:
        in_memory_threshold = 256 * MIB

    available_cpus = max(1, measured.cpu_count - 1) if measured.cpu_count > 1 else 1
    default_cpu_workers = min(4 if normalized_class == "standard" else 16, available_cpus)
    raw_workers = str(env.get("BAA_R_WORKERS") or "").strip()
    if raw_workers:
        try:
            requested_workers = int(raw_workers)
        except ValueError as exc:
            raise ValueError("BAA_R_WORKERS must be a positive whole number") from exc
        if requested_workers < 1:
            raise ValueError("BAA_R_WORKERS must be a positive whole number")
        cpu_workers = min(requested_workers, available_cpus)
    else:
        cpu_workers = default_cpu_workers

    minimum_worker_memory = GIB if normalized_class == "standard" else 2 * GIB
    workload_bytes = max(0, int(workload_size_bytes or 0))
    eager_workload_bytes = (
        workload_bytes if 0 < workload_bytes <= in_memory_threshold else 0
    )
    estimated_worker_memory = max(
        minimum_worker_memory,
        min(memory_budget or minimum_worker_memory, eager_workload_bytes * 3),
    )
    memory_workers = (
        max(1, memory_budget // estimated_worker_memory)
        if memory_budget > 0
        else cpu_workers
    )
    worker_count = max(1, min(cpu_workers, memory_workers))

    raw_native_threads = str(env.get("BAA_R_NATIVE_THREADS") or "").strip()
    if raw_native_threads:
        try:
            requested_native_threads = int(raw_native_threads)
        except ValueError as exc:
            raise ValueError("BAA_R_NATIVE_THREADS must be a positive whole number") from exc
        if requested_native_threads < 1:
            raise ValueError("BAA_R_NATIVE_THREADS must be a positive whole number")
        native_thread_count = min(requested_native_threads, available_cpus)
    else:
        native_thread_count = worker_count

    # Windows PSOCK workers require local TCP sockets. Analysis AppContainers
    # deliberately have no network capability, so helper task parallelism must
    # remain in-process. Native BLAS/OpenMP threads still use their own cap.
    windows = sys.platform == "win32"
    return LocalResourcePolicy(
        host=measured,
        output_size=output_size,
        timeout_seconds=max(0, timeout),
        worker_count=1 if windows else worker_count,
        native_thread_count=max(1, native_thread_count),
        memory_budget_bytes=max(0, memory_budget),
        in_memory_threshold_bytes=max(1, in_memory_threshold),
        resource_class=normalized_class,
        parallel_backend="sequential" if windows else "multicore",
    )
