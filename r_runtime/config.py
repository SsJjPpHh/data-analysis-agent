from __future__ import annotations

import os
from dataclasses import dataclass

from r_runtime.environment import HostREnvironment
from r_runtime.library_settings import load_r_library_settings
from r_runtime.resource_policy import compute_local_resource_policy


def get_product_mode() -> str:
    return "research"


def is_production_runtime() -> bool:
    return (os.getenv("BAA_ENV") or "").strip().lower() in {"prod", "production"}


def is_research_mode() -> bool:
    return True


@dataclass(frozen=True)
class RRuntimeConfig:
    backend: str
    rscript_bin: str
    package_library: str
    output_size: str
    timeout_seconds: int
    worker_count: int = 1
    native_thread_count: int = 1
    memory_budget_bytes: int = 0
    in_memory_threshold_bytes: int = 256 * 1024**2

    @classmethod
    def from_env(cls) -> "RRuntimeConfig":
        environment = HostREnvironment.locate()
        library_settings = load_r_library_settings()
        policy = compute_local_resource_policy(environment=os.environ)
        return cls(
            backend="local",
            rscript_bin=str(environment.rscript or ""),
            package_library=(
                str(library_settings.managed_root)
                if library_settings.mode == "managed"
                else "R_LIBS_USER"
            ),
            output_size=policy.output_size,
            timeout_seconds=policy.timeout_seconds,
            worker_count=policy.worker_count,
            native_thread_count=policy.native_thread_count,
            memory_budget_bytes=policy.memory_budget_bytes,
            in_memory_threshold_bytes=policy.in_memory_threshold_bytes,
        )
