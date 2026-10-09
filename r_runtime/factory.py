from __future__ import annotations

from r_runtime.backends.base import RExecutionBackend
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.config import RRuntimeConfig
from r_runtime.environment import HostREnvironment


def get_r_backend(config: RRuntimeConfig | None = None) -> RExecutionBackend:
    cfg = config or RRuntimeConfig.from_env()
    environment = HostREnvironment(cfg.rscript_bin or None)
    verified = environment.prepare()
    return LocalRscriptBackend(
        rscript_bin=str(verified.rscript),
        runtime_env=environment.process_environment(verified=verified),
        package_installer=lambda packages, conversation_job_id="": environment.ensure_packages(
            packages,
            verified=verified,
            conversation_job_id=conversation_job_id,
        ),
    )
