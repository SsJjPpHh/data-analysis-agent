from pathlib import Path
import sys

from r_runtime.config import RRuntimeConfig, get_product_mode, is_research_mode
from r_runtime.resource_policy import GIB, HostResources, compute_local_resource_policy


def test_product_mode_is_always_research(monkeypatch):
    monkeypatch.setenv("BAA_PRODUCT_MODE", "bi")
    assert get_product_mode() == "research"
    assert is_research_mode() is True


def test_runtime_config_uses_configured_host_r(monkeypatch, tmp_path):
    rscript = tmp_path / "bin" / "Rscript"
    monkeypatch.setenv("BAA_R_BACKEND", "remote")
    monkeypatch.setenv("BAA_RSCRIPT_BIN", str(rscript))
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))

    cfg = RRuntimeConfig.from_env()

    assert cfg.backend == "local"
    assert cfg.rscript_bin == str(rscript)
    assert cfg.package_library == "R_LIBS_USER"


def test_native_resource_policy_assigns_adaptive_cpu_and_memory_budgets():
    host = HostResources(cpu_count=10, memory_bytes=16 * GIB, free_disk_bytes=100 * GIB)
    policy = compute_local_resource_policy(host, environment={}, resource_class="standard")
    assert policy.output_size == "20g"
    assert policy.timeout_seconds == 0
    assert policy.worker_count == (1 if sys.platform == "win32" else 4)
    assert policy.memory_budget_bytes == 12 * GIB
    assert policy.in_memory_threshold_bytes == GIB

    extended = compute_local_resource_policy(
        host,
        environment={},
        resource_class="extended",
    )
    assert extended.worker_count == (1 if sys.platform == "win32" else 6)
    assert extended.memory_budget_bytes == 12 * GIB


def test_native_resource_policy_honors_bounded_local_overrides():
    host = HostResources(cpu_count=10, memory_bytes=16 * GIB, free_disk_bytes=100 * GIB)
    policy = compute_local_resource_policy(
        host,
        environment={
            "BAA_R_WORKERS": "8",
            "BAA_R_MEMORY_BUDGET": "8g",
            "BAA_LAZY_DATASET_THRESHOLD_BYTES": str(512 * 1024**2),
        },
        resource_class="extended",
    )

    assert policy.worker_count == (1 if sys.platform == "win32" else 4)
    assert policy.memory_budget_bytes == 8 * GIB
    assert policy.in_memory_threshold_bytes == 512 * 1024**2


def test_native_resource_policy_accepts_output_and_timeout_overrides():
    host = HostResources(cpu_count=8, memory_bytes=16 * GIB, free_disk_bytes=100 * GIB)
    policy = compute_local_resource_policy(
        host,
        environment={"BAA_R_OUTPUT_SIZE": "50g", "BAA_R_TIMEOUT_SECONDS": "180"},
    )
    assert policy.output_size == "50g"
    assert policy.timeout_seconds == 180
