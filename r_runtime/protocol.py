from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from data.scientific_dataset import inspect_local_asset_path
from infrastructure.paths import data_path


CHECKPOINT_MAX_FILES = 1024
_ARTIFACT_GROUPS = ("tables", "figures", "texts", "models", "artifacts")


def _string_or_empty(value: Any) -> str:
    return "" if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _bool_from_payload(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"Invalid ok value: {value!r}")


def validated_checkpoint_dir(value: str, *, create: bool = False) -> Path | None:
    """Resolve an internal checkpoint directory below the app research-output root."""
    raw_text = str(value or "").strip()
    if not raw_text:
        return None
    raw = Path(raw_text).expanduser()
    if not raw.is_absolute():
        raise ValueError("checkpoint_dir must be an absolute path")
    if ".." in raw.parts:
        raise ValueError("checkpoint_dir must not contain path traversal")

    configured_root = Path(data_path("outputs", "research")).expanduser()
    lexical_root = Path(os.path.abspath(configured_root))
    lexical_candidate = Path(os.path.abspath(raw))
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError as exc:
        raise ValueError("checkpoint_dir must be below the app outputs/research root") from exc
    if relative == Path("."):
        raise ValueError("checkpoint_dir must be below the app outputs/research root")

    current = lexical_root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("checkpoint_dir must not contain symbolic links")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("checkpoint_dir parent must be a directory")

    resolved_root = lexical_root.resolve(strict=False)
    resolved_candidate = lexical_candidate.resolve(strict=False)
    try:
        resolved_relative = resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("checkpoint_dir must be below the app outputs/research root") from exc
    if resolved_relative == Path("."):
        raise ValueError("checkpoint_dir must be below the app outputs/research root")
    if lexical_candidate.exists() or lexical_candidate.is_symlink():
        metadata = lexical_candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("checkpoint_dir must be an ordinary directory")
    if create:
        lexical_candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        return validated_checkpoint_dir(str(lexical_candidate), create=False)
    return resolved_candidate


def validate_checkpoint_inventory(
    checkpoint_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> dict[str, int]:
    """Validate the persisted checkpoint set before mounting or updating it."""
    if max_files < 1 or max_bytes < 1:
        raise ValueError("checkpoint budget must be positive")
    count = 0
    total_bytes = 0
    inventory: dict[str, int] = {}
    for entry in checkpoint_dir.iterdir():
        metadata = entry.lstat()
        if not stat.S_ISREG(metadata.st_mode) or entry.suffix.lower() != ".rds":
            raise ValueError("checkpoint_dir may contain only ordinary .rds files")
        count += 1
        total_bytes += max(0, int(metadata.st_size))
        inventory[entry.name] = max(0, int(metadata.st_size))
        if count > max_files:
            raise ValueError("checkpoint_dir exceeds its file-count budget")
        if total_bytes > max_bytes:
            raise ValueError("checkpoint_dir exceeds its byte budget")
    return inventory


def _checkpoint_manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for group in _ARTIFACT_GROUPS:
        items = manifest.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            kind = str(item.get("kind") or "").strip().lower()
            roles = {
                str(item.get("role") or "").strip().lower(),
                str(metadata.get("role") or "").strip().lower(),
            }
            if kind == "checkpoint" or "checkpoint" in roles:
                selected.append(item)
    return selected


def _checkpoint_source_path(
    raw_path: object,
    *,
    output_dir: Path,
    manifest_output_root: str,
) -> Path:
    text = str(raw_path or "")
    if not text or "\x00" in text:
        raise ValueError("checkpoint manifest path is invalid")
    output_root = output_dir.resolve(strict=True)
    if manifest_output_root:
        if "\\" in text:
            raise ValueError("checkpoint manifest path is invalid")
        manifest_path = PurePosixPath(text)
        manifest_root = PurePosixPath(manifest_output_root)
        if not manifest_path.is_absolute() or ".." in manifest_path.parts:
            raise ValueError("checkpoint manifest path contains traversal")
        try:
            relative = manifest_path.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError("checkpoint manifest path is outside the run output") from exc
        if relative == PurePosixPath("."):
            raise ValueError("checkpoint manifest path must name a file")
        candidate = output_root.joinpath(*relative.parts)
    else:
        candidate = Path(text).expanduser()
        if not candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("checkpoint manifest path must be an absolute output path")
        candidate = Path(os.path.abspath(candidate))

    try:
        relative = candidate.relative_to(output_root)
    except ValueError as exc:
        raise ValueError("checkpoint manifest path is outside the run output") from exc
    current = output_root
    for part in relative.parts:
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("checkpoint output must not contain symbolic links")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(output_root)
    except ValueError as exc:
        raise ValueError("checkpoint manifest path is outside the run output") from exc
    metadata = candidate.lstat()
    if not stat.S_ISREG(metadata.st_mode) or candidate.suffix.lower() != ".rds":
        raise ValueError("checkpoint output must be an ordinary .rds file")
    return candidate


def _stage_checkpoint_copy(source: Path, checkpoint_dir: Path, expected_size: int) -> Path:
    flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
    source_fd = os.open(source, flags)
    temporary_path: Path | None = None
    try:
        with os.fdopen(source_fd, "rb") as source_handle:
            source_fd = -1
            metadata = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(metadata.st_mode) or int(metadata.st_size) != expected_size:
                raise OSError("checkpoint output changed during promotion")
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".baa-checkpoint-",
                suffix=".tmp",
                dir=str(checkpoint_dir),
                delete=False,
            ) as destination_handle:
                temporary_path = Path(destination_handle.name)
                remaining = expected_size
                while remaining:
                    chunk = source_handle.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("checkpoint output ended during promotion")
                    destination_handle.write(chunk)
                    remaining -= len(chunk)
                if source_handle.read(1):
                    raise OSError("checkpoint output grew during promotion")
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        os.chmod(temporary_path, 0o600)
        return temporary_path
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)


def promote_checkpoint_artifacts(
    manifest: dict[str, Any],
    *,
    output_dir: Path,
    checkpoint_dir: Path | None,
    max_files: int,
    max_bytes: int,
    manifest_output_root: str = "",
    excluded_sources: set[Path] | None = None,
) -> list[str]:
    """Atomically copy explicitly declared checkpoint artifacts into trusted storage."""
    if checkpoint_dir is None or not isinstance(manifest, dict):
        return []
    items = _checkpoint_manifest_items(manifest)
    if not items:
        return []

    staged: list[tuple[Path, Path]] = []
    try:
        if len(items) > max_files:
            raise ValueError("checkpoint manifest exceeds its file-count budget")
        candidates: list[tuple[Path, int]] = []
        names: set[str] = set()
        candidate_bytes = 0
        excluded = {path.resolve(strict=False) for path in (excluded_sources or set())}
        for item in items:
            source = _checkpoint_source_path(
                item.get("path"),
                output_dir=output_dir,
                manifest_output_root=manifest_output_root,
            )
            if source.resolve(strict=True) in excluded:
                raise ValueError("checkpoint output predates the current run")
            if source.name in names:
                raise ValueError("checkpoint manifest contains duplicate destination names")
            names.add(source.name)
            size = max(0, int(source.lstat().st_size))
            candidate_bytes += size
            if size > max_bytes or candidate_bytes > max_bytes:
                raise ValueError("checkpoint manifest exceeds its byte budget")
            candidates.append((source, size))

        destination_root = validated_checkpoint_dir(str(checkpoint_dir), create=True)
        if destination_root is None:  # pragma: no cover - guarded above
            raise ValueError("checkpoint_dir is unavailable")
        existing = validate_checkpoint_inventory(
            destination_root,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        projected_files = len(existing) + sum(source.name not in existing for source, _ in candidates)
        projected_bytes = (
            sum(existing.values())
            - sum(existing.get(source.name, 0) for source, _ in candidates)
            + candidate_bytes
        )
        if projected_files > max_files:
            raise ValueError("checkpoint promotion exceeds its file-count budget")
        if projected_bytes > max_bytes:
            raise ValueError("checkpoint promotion exceeds its byte budget")

        for source, size in candidates:
            temporary = _stage_checkpoint_copy(source, destination_root, size)
            staged.append((temporary, destination_root / source.name))
        validated_checkpoint_dir(str(destination_root), create=False)
        for _temporary, destination in staged:
            if destination.is_symlink():
                raise ValueError("checkpoint destination must not be a symbolic link")
            if destination.exists() and not stat.S_ISREG(destination.lstat().st_mode):
                raise ValueError("checkpoint destination must be an ordinary file")
        for temporary, destination in staged:
            temporary.replace(destination)
        staged.clear()
        try:
            directory_fd = os.open(destination_root, os.O_RDONLY)
        except OSError:
            pass
        else:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except (OSError, RuntimeError, ValueError) as exc:
        return [f"Checkpoint promotion rejected: {exc}"]
    finally:
        for temporary, _destination in staged:
            temporary.unlink(missing_ok=True)
    return []


@dataclass(frozen=True)
class DatasetAssetRef:
    path: str
    format: str
    role: str = "primary"
    relative_mount_name: str = ""
    kind: str = "file"
    name: str = ""
    size_bytes: int | None = None
    device: int | None = None
    inode: int | None = None
    object_type: int | None = None
    mtime_ns: int | None = None
    ctime_ns: int | None = None
    fingerprint_sha256: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DatasetAssetRef":
        identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
        return cls(
            path=_string_or_empty(data.get("path")),
            format=_string_or_empty(data.get("format")),
            role=str(data.get("role") or "primary"),
            relative_mount_name=str(data.get("relative_mount_name") or data.get("mount_name") or ""),
            kind=str(data.get("kind") or "file"),
            name=_string_or_empty(data.get("name")),
            size_bytes=_optional_int(data.get("size_bytes")),
            device=_optional_int(identity.get("device", data.get("device"))),
            inode=_optional_int(identity.get("inode", data.get("inode"))),
            object_type=_optional_int(identity.get("type", data.get("object_type"))),
            mtime_ns=_optional_int(identity.get("mtime_ns", data.get("mtime_ns"))),
            ctime_ns=_optional_int(identity.get("ctime_ns", data.get("ctime_ns"))),
            fingerprint_sha256=(
                str(
                    identity.get(
                        "fingerprint_sha256",
                        data.get("fingerprint_sha256"),
                    )
                    or ""
                ).strip().lower()
                or None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "format": self.format,
            "role": self.role,
            "relative_mount_name": self.relative_mount_name,
            "kind": self.kind,
            "name": self.name,
        }


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(reparse_flag and attributes & reparse_flag)


def validate_dataset_asset_mount(asset: DatasetAssetRef) -> Path:
    """Revalidate one host object immediately before it is read or mounted.

    Strong identities include a bounded file-content fingerprint or a
    deterministic descendant-manifest fingerprint for directory assets.
    Legacy references without those fields retain their older root/size checks.
    """
    source = Path(str(asset.path or "")).expanduser()
    if not str(asset.path or ""):
        raise ValueError("Scientific dataset asset path is required")
    if ".." in source.parts:
        raise ValueError("Scientific dataset asset path must be exact")
    absolute_source = source if source.is_absolute() else Path.cwd() / source
    lexical_source = Path(os.path.abspath(absolute_source))
    current = Path(lexical_source.anchor)
    try:
        for part in lexical_source.parts[1:]:
            current /= part
            if _is_link_or_reparse(current.lstat()):
                raise ValueError(
                    "Scientific dataset asset path cannot traverse a symlink or reparse point"
                )
        metadata = lexical_source.lstat()
        resolved = lexical_source.resolve(strict=True)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("Scientific dataset asset is unavailable") from exc
    if resolved != lexical_source:
        raise ValueError("Scientific dataset asset path is no longer exact")

    object_type = int(stat.S_IFMT(metadata.st_mode))
    if stat.S_ISREG(metadata.st_mode):
        current_kind = "file"
    elif stat.S_ISDIR(metadata.st_mode):
        current_kind = "directory"
    else:
        raise ValueError("Scientific dataset asset must be a regular file or directory")
    declared_kind = str(asset.kind or "").strip().lower()
    if declared_kind and declared_kind not in {"file", "directory"}:
        raise ValueError("Scientific dataset asset kind must be file or directory")
    if declared_kind and declared_kind != current_kind:
        raise ValueError("Scientific dataset asset object type changed after import")

    current_identity = {
        "device": int(getattr(metadata, "st_dev", 0)),
        "inode": int(getattr(metadata, "st_ino", 0)),
        "object_type": object_type,
        "mtime_ns": int(
            getattr(metadata, "st_mtime_ns", int(metadata.st_mtime * 1_000_000_000))
        ),
        "ctime_ns": int(
            getattr(metadata, "st_ctime_ns", int(metadata.st_ctime * 1_000_000_000))
        ),
    }
    for field_name in ("device", "inode", "object_type", "mtime_ns", "ctime_ns"):
        expected = getattr(asset, field_name)
        if expected is not None and int(expected) != current_identity[field_name]:
            raise ValueError("Scientific dataset asset identity changed after import")
    if current_kind == "file" and asset.size_bytes is not None:
        if int(asset.size_bytes) != int(metadata.st_size):
            raise ValueError("Scientific dataset file size changed after import")
    expected_fingerprint = str(asset.fingerprint_sha256 or "").strip().lower()
    if expected_fingerprint:
        if len(expected_fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in expected_fingerprint
        ):
            raise ValueError("Scientific dataset asset fingerprint is invalid")
        inspected = inspect_local_asset_path(resolved)
        if inspected.fingerprint_sha256 != expected_fingerprint:
            raise ValueError("Scientific dataset asset identity changed after import")
    return resolved


@dataclass(frozen=True)
class DatasetRef:
    dataset_id: str
    path: str
    format: str
    name: str = ""
    assets: tuple[DatasetAssetRef, ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(
            item if isinstance(item, DatasetAssetRef) else DatasetAssetRef.from_dict(item)
            for item in self.assets
        )
        object.__setattr__(self, "assets", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "path": self.path,
            "format": self.format,
            "name": self.name,
            "assets": [item.to_dict() for item in self.assets],
        }


@dataclass(frozen=True)
class RArtifact:
    name: str
    path: str
    kind: str
    mime_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RArtifact":
        return cls(
            name=_string_or_empty(data.get("name")),
            path=_string_or_empty(data.get("path")),
            kind=str(data.get("kind") or data.get("type") or "artifact"),
            mime_type=_string_or_empty(data.get("mime_type")),
            metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class RRunRequest:
    job_id: str
    dataset: DatasetRef
    code: str
    artifacts_dir: str
    timeout_seconds: int = 0
    conversation_job_id: str = ""
    checkpoint_dir: str = ""
    resource_class: str = "standard"

    def __post_init__(self) -> None:
        normalized = str(self.resource_class or "standard").strip().lower()
        if normalized not in {"standard", "extended"}:
            raise ValueError("resource_class must be standard or extended")
        object.__setattr__(self, "resource_class", normalized)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "dataset": self.dataset.to_dict(),
            "code": self.code,
            "artifacts_dir": self.artifacts_dir,
            "timeout_seconds": self.timeout_seconds,
            "resource_class": self.resource_class,
        }


def _list_from_payload(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(f"Expected list payload, got {type(value).__name__}")


def _artifacts_from_payload(items: Any) -> list[RArtifact]:
    artifacts: list[RArtifact] = []
    for item in _list_from_payload(items):
        if isinstance(item, dict):
            artifacts.append(RArtifact.from_dict(item))
        else:
            raise ValueError(f"Expected artifact object, got {type(item).__name__}")
    return artifacts


@dataclass
class RRunResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    tables: list[RArtifact] = field(default_factory=list)
    figures: list[RArtifact] = field(default_factory=list)
    texts: list[RArtifact] = field(default_factory=list)
    models: list[RArtifact] = field(default_factory=list)
    artifacts: list[RArtifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    code: str = ""
    execution_stage: str = ""
    reproducibility: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RRunResult":
        return cls(
            ok=_bool_from_payload(data.get("ok", False)),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            tables=_artifacts_from_payload(data.get("tables")),
            figures=_artifacts_from_payload(data.get("figures")),
            texts=_artifacts_from_payload(data.get("texts")),
            models=_artifacts_from_payload(data.get("models")),
            artifacts=_artifacts_from_payload(data.get("artifacts")),
            warnings=[str(item) for item in _list_from_payload(data.get("warnings"))],
            error=str(data.get("error") or ""),
            code=str(data.get("code") or ""),
            execution_stage=str(data.get("execution_stage") or ""),
            reproducibility=dict(data.get("reproducibility") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "tables": [item.to_dict() for item in self.tables],
            "figures": [item.to_dict() for item in self.figures],
            "texts": [item.to_dict() for item in self.texts],
            "models": [item.to_dict() for item in self.models],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "warnings": list(self.warnings),
            "error": self.error,
            "code": self.code,
            "execution_stage": self.execution_stage,
            "reproducibility": self.reproducibility,
        }
