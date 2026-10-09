"""Atomic single-dataset research workspace generations."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from data.research_dataset import ResearchDataset, ResearchDatasetStore
from data.research_results import ResearchResultStore
from data.scientific_dataset import (
    REFERENCE_ACCESS,
    ScientificDataAsset,
    validate_scientific_data_asset,
)
from infrastructure.paths import data_path


log = logging.getLogger(__name__)
RESEARCH_STAGING_MAX_AGE_SECONDS = 24 * 60 * 60


def research_staging_root() -> Path:
    # Browser uploads must be staged on the final dataset filesystem so the
    # store can atomically adopt them without retaining a second full copy.
    root = data_path("uploads", "research", ".staging")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tree_size(root: Path) -> int:
    try:
        if root.is_symlink() or root.is_file():
            return int(root.stat().st_size)
        if not root.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += int(path.stat().st_size)
        except OSError:
            continue
    return total


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat(follow_symlinks=False).st_mtime < cutoff
    except OSError:
        return False


def _remove_path(path: Path) -> bool:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            return False
    except OSError:
        return False
    return True


def _referenced_artifact_paths(state_path: Path) -> set[Path]:
    try:
        record = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return set()
    state = record.get("state") if isinstance(record, dict) else None
    if not isinstance(state, dict):
        return set()
    referenced: set[Path] = set()
    for turn in state.get("analysis_turns") or []:
        if not isinstance(turn, dict):
            continue
        for artifact in turn.get("artifacts") or []:
            if not isinstance(artifact, dict) or not artifact.get("path"):
                continue
            referenced.add(Path(str(artifact["path"])).expanduser().resolve(strict=False))
    return referenced


def cleanup_research_storage(
    *,
    now_timestamp: float | None = None,
    max_age_seconds: int = RESEARCH_STAGING_MAX_AGE_SECONDS,
) -> dict[str, int]:
    """Remove only expired staging data and unreferenced generation artifacts."""
    cutoff = float(now_timestamp if now_timestamp is not None else time.time()) - max(1, int(max_age_seconds))
    removed_staging = 0
    removed_temporary = 0
    removed_generations = 0
    removed_artifact_jobs = 0

    staging = research_staging_root()
    for child in list(staging.iterdir()):
        if _older_than(child, cutoff) and _remove_path(child):
            removed_staging += 1

    active_root = data_path("workspaces", "research")
    outputs_root = data_path("outputs", "research")
    uploads_root = data_path("uploads", "research")
    for root in (active_root, outputs_root, uploads_root):
        if not root.is_dir():
            continue
        for path in list(root.rglob("*.tmp")):
            if _older_than(path, cutoff) and _remove_path(path):
                removed_temporary += 1

    referenced_generations: set[str] = set()
    if active_root.is_dir():
        for manifest in active_root.glob("*/workspace.json"):
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
                generation = str(payload.get("workspace_generation") or "")
                if len(generation) == 32:
                    int(generation, 16)
                    referenced_generations.add(generation)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue

    if outputs_root.is_dir():
        for generation_dir in list(outputs_root.glob("*/generations/*")):
            generation = generation_dir.name
            if generation not in referenced_generations:
                if _older_than(generation_dir, cutoff) and _remove_path(generation_dir):
                    removed_generations += 1
                continue
            artifacts_root = generation_dir / "artifacts"
            if not artifacts_root.is_dir():
                continue
            references = _referenced_artifact_paths(generation_dir / "analysis_state.json")
            for job_dir in list(artifacts_root.iterdir()):
                resolved_job = job_dir.resolve(strict=False)
                is_referenced = any(
                    reference == resolved_job or resolved_job in reference.parents for reference in references
                )
                if not is_referenced and _older_than(job_dir, cutoff) and _remove_path(job_dir):
                    removed_artifact_jobs += 1

    return {
        "removed_staging": removed_staging,
        "removed_temporary": removed_temporary,
        "removed_generations": removed_generations,
        "removed_artifact_jobs": removed_artifact_jobs,
    }


class ResearchWorkspace:
    def __init__(
        self,
        datasets: ResearchDatasetStore,
        results_for_session: Callable[[str], ResearchResultStore],
        active_root: Path,
        validator: Callable[[ResearchDataset], None],
    ) -> None:
        self._datasets = datasets
        self._results_for_session = results_for_session
        self._active_root = Path(active_root)
        self.validator = validator

    @staticmethod
    def _session_key(session_id: str) -> str:
        return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:24]

    def _manifest_path(self, session_id: str) -> Path:
        return self._active_root / self._session_key(session_id) / "workspace.json"

    def _read_active_manifest(self, session_id: str) -> dict[str, str] | None:
        path = self._manifest_path(session_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        dataset_id = str(payload.get("dataset_id") or "")
        generation = str(payload.get("workspace_generation") or "")
        if not dataset_id or not generation:
            return None
        try:
            int(dataset_id, 16)
            int(generation, 16)
        except ValueError:
            return None
        if len(dataset_id) != 12 or len(generation) != 32:
            return None
        return {"dataset_id": dataset_id, "workspace_generation": generation}

    def _commit_active_manifest(
        self,
        session_id: str,
        dataset_id: str,
        generation: str,
    ) -> None:
        path = self._manifest_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "dataset_id": dataset_id,
                    "workspace_generation": generation,
                    "updated_at": time.time(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _delete_previous_generation(
        self,
        session_id: str,
        dataset_id: str,
        generation: str,
    ) -> None:
        self._datasets.delete(session_id, dataset_id)
        self._results_for_session(session_id).discard_generation(generation)

    def current_record(self, session_id: str) -> dict[str, str] | None:
        record = self._read_active_manifest(session_id)
        if record is not None:
            return record
        existing = self._datasets.list(session_id)
        if len(existing) == 1:
            return {"dataset_id": existing[0].dataset_id, "workspace_generation": ""}
        return None

    def current(self, session_id: str) -> ResearchDataset | None:
        record = self.current_record(session_id)
        if record is None:
            return None
        return self._datasets.get(session_id, record["dataset_id"])

    def results(self, session_id: str) -> ResearchResultStore:
        base = self._results_for_session(session_id)
        record = self.current_record(session_id)
        generation = str((record or {}).get("workspace_generation") or "")
        return base.for_generation(generation) if generation else base

    def storage_usage(self, session_id: str) -> dict[str, int | str | bool]:
        record = self.current_record(session_id) or {}
        dataset = self.current(session_id)
        dataset_root = (
            self._datasets.dataset_storage_path(session_id, dataset.dataset_id)
            if dataset is not None
            else Path()
        )
        output_root = data_path("outputs", "research").resolve(strict=False)
        session_output = (output_root / str(session_id)).resolve(strict=False)
        result_bytes = _tree_size(session_output) if session_output.parent == output_root else 0
        manifest = self._manifest_path(session_id)
        dataset_bytes = _tree_size(dataset_root) if dataset is not None else 0
        manifest_bytes = _tree_size(manifest)
        return {
            "active": dataset is not None,
            "dataset_id": str(record.get("dataset_id") or ""),
            "workspace_generation": str(record.get("workspace_generation") or ""),
            "dataset_bytes": dataset_bytes,
            "result_bytes": result_bytes,
            "manifest_bytes": manifest_bytes,
            "total_bytes": dataset_bytes + result_bytes + manifest_bytes,
        }

    def delete_current(self, session_id: str) -> dict[str, str] | None:
        record = self.current_record(session_id)
        for dataset in self._datasets.list(session_id):
            self._datasets.delete(session_id, dataset.dataset_id)

        output_root = data_path("outputs", "research").resolve(strict=False)
        session_output = (output_root / str(session_id)).resolve(strict=False)
        if session_output.parent != output_root:
            raise ValueError("Invalid research session output path")
        if session_output.is_dir():
            shutil.rmtree(session_output)

        manifest = self._manifest_path(session_id)
        manifest.unlink(missing_ok=True)
        try:
            manifest.parent.rmdir()
        except OSError:
            pass
        return record

    def replace(self, session_id: str, staged_upload: Path, filename: str) -> ResearchDataset:
        return self._replace_with(
            session_id,
            lambda: self._datasets.add_upload(session_id, staged_upload, filename),
        )

    def browser_upload_staging_root(self) -> Path:
        return self._datasets.upload_staging_root()

    def replace_staged_upload(
        self,
        session_id: str,
        staged_upload: Path,
        filename: str,
    ) -> ResearchDataset:
        """Atomically activate a browser upload from app-owned staging."""
        return self._replace_with(
            session_id,
            lambda: self._datasets.add_staged_upload(session_id, staged_upload, filename),
        )

    def replace_reference(
        self,
        session_id: str,
        source_path: str | Path,
        filename: str | None = None,
        *,
        role: str = "primary",
        relative_mount_name: str | None = None,
        format: str | None = None,
    ) -> ResearchDataset:
        """Atomically activate one read-only local reference."""
        return self._replace_with(
            session_id,
            lambda: self._datasets.add_reference(
                session_id,
                source_path,
                filename,
                role=role,
                relative_mount_name=relative_mount_name,
                format=format,
            ),
        )

    def replace_assets(
        self,
        session_id: str,
        assets: list[ScientificDataAsset | dict[str, Any] | str | Path],
        *,
        name: str | None = None,
        access_mode: str = REFERENCE_ACCESS,
    ) -> ResearchDataset:
        """Atomically activate one multi-asset Scientific Dataset Bundle."""
        return self._replace_with(
            session_id,
            lambda: self._datasets.add_local_assets(
                session_id,
                assets,
                name=name,
                access_mode=access_mode,
            ),
        )

    def _replace_with(
        self,
        session_id: str,
        create_candidate: Callable[[], ResearchDataset],
    ) -> ResearchDataset:
        previous = self._read_active_manifest(session_id)
        existing_before = self._datasets.list(session_id)
        candidate = create_candidate()
        generation = uuid.uuid4().hex
        results = self._results_for_session(session_id)
        try:
            self.validator(candidate)
            results.prepare_generation(generation, candidate.to_dict())
            self._commit_active_manifest(session_id, candidate.dataset_id, generation)
        except Exception:
            results.discard_generation(generation)
            self._datasets.delete(session_id, candidate.dataset_id)
            raise

        if previous and previous["dataset_id"] != candidate.dataset_id:
            try:
                self._delete_previous_generation(
                    session_id,
                    previous["dataset_id"],
                    previous["workspace_generation"],
                )
            except Exception:
                log.exception("[research-workspace] previous generation cleanup deferred")

        # Migrate legacy multi-dataset sessions to the one-active-dataset model.
        for dataset in existing_before:
            if previous and dataset.dataset_id == previous["dataset_id"]:
                continue
            try:
                self._datasets.delete(session_id, dataset.dataset_id)
            except Exception:
                log.exception("[research-workspace] legacy dataset cleanup deferred")
        return candidate


def _validate_staged_dataset(dataset: ResearchDataset) -> None:
    if not dataset.assets:
        raise ValueError("科研数据集不包含可读取的数据资产")
    for asset in dataset.assets:
        inspected = validate_scientific_data_asset(asset)
        if inspected.size_bytes <= 0:
            raise ValueError("上传的数据文件或目录为空或无法读取")


def default_research_workspace() -> ResearchWorkspace:
    return ResearchWorkspace(
        datasets=ResearchDatasetStore(data_path("uploads", "research")),
        results_for_session=ResearchResultStore,
        active_root=data_path("workspaces", "research"),
        validator=_validate_staged_dataset,
    )
