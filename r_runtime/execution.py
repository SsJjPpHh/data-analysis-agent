"""Session-scoped R execution helpers for the local desktop product."""

from __future__ import annotations

import uuid
from hashlib import sha256

from data.session import validate_session_id
from data.research_results import generation_root
from infrastructure.paths import data_path
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from r_runtime.protocol import DatasetRef, RRunRequest, RRunResult


def dataset_inspection_request(
    dataset: DatasetRef,
    *,
    session_id: str,
    workspace_generation: str = "",
    conversation_job_id: str = "",
    timeout_seconds: int = 0,
) -> RRunRequest:
    safe_session_id = validate_session_id(session_id)
    if not safe_session_id:
        raise ValueError("session_id is required for dataset inspection")
    dataset_marker = sha256(str(dataset.dataset_id).encode("utf-8")).hexdigest()[:12]
    job_id = f"inspect-{dataset_marker}-{uuid.uuid4().hex[:12]}"
    artifacts_root = (
        generation_root(safe_session_id, workspace_generation) / "artifacts"
        if workspace_generation
        else data_path("outputs", "research", safe_session_id)
    )
    return RRunRequest(
        job_id=job_id,
        dataset=dataset,
        code=DATASET_PROFILE_R_CODE,
        artifacts_dir=str(artifacts_root / job_id),
        timeout_seconds=timeout_seconds,
        conversation_job_id=str(conversation_job_id or ""),
    )


def run_r_request(backend, request: RRunRequest, *, progress_callback=None) -> RRunResult:
    """Execute through the configured local R adapter.

    The callback is forwarded only when set, so alternative backends that
    predate the progress seam keep working unchanged.
    """
    if progress_callback is None:
        return backend.run_code(request)
    return backend.run_code(request, progress_callback=progress_callback)
