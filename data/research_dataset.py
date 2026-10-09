"""Manifest-backed research dataset uploads."""

from __future__ import annotations

import hashlib
import json
import errno
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from data.scientific_dataset import (
    COPY_ACCESS,
    REFERENCE_ACCESS,
    SCIENTIFIC_FORMAT_SUFFIXES,
    ScientificDataAsset,
    detect_scientific_format,
    infer_scientific_asset_specs,
    inspect_local_asset_path,
    register_scientific_format,
    validate_relative_mount_name,
    validate_scientific_data_asset,
)
from infrastructure.public_projection import public_projection
from werkzeug.utils import secure_filename


# Backward-compatible name retained for callers that inspect the original map.
SUPPORTED_RESEARCH_EXTS = SCIENTIFIC_FORMAT_SUFFIXES
detect_research_format = detect_scientific_format
register_research_format = register_scientific_format

_DATASET_ID_RE = re.compile(r"^[a-f0-9]{12}$")


def _atomic_replace(source: Path, destination: Path) -> None:
    """Move one staged upload without a copy fallback."""
    os.replace(source, destination)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_part(value: str, fallback: str) -> str:
    safe = secure_filename(value)
    return safe or fallback


def _session_part(session_id: str) -> str:
    return hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:24]


def _validate_dataset_id(dataset_id: str) -> str:
    dataset_id = str(dataset_id)
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise ValueError("Invalid dataset_id")
    return dataset_id


@dataclass
class ResearchDataset:
    dataset_id: str
    session_id: str
    name: str
    format: str
    stored_path: str
    created_at: str
    profile: dict[str, Any] = field(default_factory=dict)
    assets: list[ScientificDataAsset] = field(default_factory=list)

    def __post_init__(self) -> None:
        normalized_assets: list[ScientificDataAsset] = []
        for asset in self.assets:
            normalized_assets.append(
                asset if isinstance(asset, ScientificDataAsset) else ScientificDataAsset.from_dict(asset)
            )
        if not normalized_assets:
            path = Path(self.stored_path)
            try:
                size_bytes = int(path.stat().st_size) if path.is_file() and not path.is_symlink() else 0
            except OSError:
                size_bytes = 0
            normalized_assets = [
                ScientificDataAsset(
                    path=self.stored_path,
                    format=self.format,
                    role="primary",
                    size_bytes=size_bytes,
                    access_mode=COPY_ACCESS,
                    relative_mount_name=_safe_part(path.name, f"dataset.{self.format}"),
                    kind="directory" if path.is_dir() and not path.is_symlink() else "file",
                    name=self.name,
                )
            ]
        mount_names = [asset.relative_mount_name for asset in normalized_assets]
        if len(set(mount_names)) != len(mount_names):
            raise ValueError("Scientific data asset mount names must be unique")
        self.assets = normalized_assets

    @property
    def primary_asset(self) -> ScientificDataAsset:
        return next((asset for asset in self.assets if asset.role == "primary"), self.assets[0])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchDataset":
        raw_assets = data.get("assets") or []
        assets = [ScientificDataAsset.from_dict(asset) for asset in raw_assets]
        primary = next((asset for asset in assets if asset.role == "primary"), assets[0] if assets else None)
        return cls(
            dataset_id=str(data["dataset_id"]),
            session_id=str(data["session_id"]),
            name=str(data["name"]),
            format=str(data.get("format") or (primary.format if primary else "")),
            stored_path=str(data.get("stored_path") or (primary.path if primary else "")),
            created_at=str(data["created_at"]),
            profile=dict(data.get("profile") or {}),
            assets=assets,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the browser/model-safe dataset descriptor."""
        return {
            "dataset_id": self.dataset_id,
            "session_id": self.session_id,
            "name": self.name,
            "format": self.format,
            "support": self.primary_asset.support,
            "created_at": self.created_at,
            "assets": [asset.to_dict() for asset in self.assets],
            "profile": public_projection(
                self.profile,
                drop_keys={
                    "path",
                    "stored_path",
                    "artifacts_dir",
                    "file_path",
                    "source_path",
                    "output_path",
                    "workdir",
                    "cwd",
                    "code",
                    "stdout",
                    "stderr",
                    "error",
                    "analysis_context",
                    "local_semantics",
                    "role_memory",
                },
            ),
        }

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            **self.to_dict(),
            "profile": self.profile,
            "stored_path": self.stored_path,
            "assets": [asset.to_storage_dict() for asset in self.assets],
        }


class ResearchDatasetStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        return self.root / _session_part(session_id)

    def _manifest_path(self, session_id: str, dataset_id: str) -> Path:
        dataset_id = _validate_dataset_id(dataset_id)
        return self._session_dir(session_id) / dataset_id / "manifest.json"

    def dataset_storage_path(self, session_id: str, dataset_id: str) -> Path:
        """Return the app-owned directory for a validated dataset identifier."""
        return self._manifest_path(session_id, dataset_id).parent

    def upload_staging_root(self) -> Path:
        """Return a private staging root on the dataset-storage filesystem."""
        root = self.root / ".staging"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root

    @staticmethod
    def _display_name(filename: str | None, source: Path) -> str:
        raw = str(filename or source.name).replace("\\", "/")
        return raw.rsplit("/", 1)[-1] or source.name

    def add_upload(
        self,
        session_id: str,
        source_path: str | Path,
        filename: str | None = None,
    ) -> ResearchDataset:
        inspected = inspect_local_asset_path(source_path, require_absolute=False)
        source = inspected.path
        display_name = self._display_name(filename, source)
        fmt = detect_scientific_format(source, filename=display_name)

        dataset_id = uuid.uuid4().hex[:12]
        safe_name = _safe_part(display_name, f"dataset.{fmt}")
        dataset_dir = self._session_dir(session_id) / dataset_id
        stored_path = dataset_dir / safe_name
        created_dataset_dir = False
        try:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            created_dataset_dir = True
            if inspected.kind == "directory":
                # Preserve a concurrently introduced symlink as a link so the
                # post-copy validator rejects it instead of following it.
                shutil.copytree(source, stored_path, symlinks=True)
            else:
                shutil.copy2(source, stored_path)
            copied = inspect_local_asset_path(stored_path, require_absolute=False)
            asset = ScientificDataAsset(
                path=str(copied.path),
                format=fmt,
                role="primary",
                size_bytes=copied.size_bytes,
                access_mode=COPY_ACCESS,
                relative_mount_name=safe_name,
                kind=copied.kind,
                name=display_name,
                device=copied.device,
                inode=copied.inode,
                object_type=copied.object_type,
                mtime_ns=copied.mtime_ns,
                ctime_ns=copied.ctime_ns,
                fingerprint_sha256=copied.fingerprint_sha256,
            )
            dataset = ResearchDataset(
                dataset_id=dataset_id,
                session_id=session_id,
                name=display_name,
                format=fmt,
                stored_path=asset.path,
                created_at=_now_iso(),
                assets=[asset],
            )
            self.save(dataset)
            return dataset
        except (OSError, shutil.Error) as exc:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            raise ValueError("Scientific dataset could not be copied into app storage") from exc
        except Exception:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            raise

    def add_staged_upload(
        self,
        session_id: str,
        staged_path: str | Path,
        filename: str | None = None,
    ) -> ResearchDataset:
        """Atomically adopt one browser upload already staged below ``root``.

        This path deliberately has no copy fallback: a cross-device move would
        recreate the large staging-plus-permanent double write that the browser
        compatibility route is designed to avoid.
        """
        inspected = inspect_local_asset_path(staged_path, require_absolute=False)
        source = inspected.path
        staging_root = self.upload_staging_root().resolve(strict=True)
        try:
            source.relative_to(staging_root)
        except ValueError as exc:
            raise ValueError("Browser upload is outside the app-owned staging area") from exc
        if inspected.kind != "file":
            raise ValueError("Browser upload staging accepts exactly one regular file")

        display_name = self._display_name(filename, source)
        fmt = detect_scientific_format(source, filename=display_name)
        dataset_id = uuid.uuid4().hex[:12]
        safe_name = _safe_part(display_name, f"dataset.{fmt}")
        dataset_dir = self._session_dir(session_id) / dataset_id
        stored_path = dataset_dir / safe_name
        created_dataset_dir = False
        try:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            created_dataset_dir = True
            if source.stat(follow_symlinks=False).st_dev != dataset_dir.stat().st_dev:
                raise OSError(errno.EXDEV, "cross-device browser upload staging")
            _atomic_replace(source, stored_path)
            adopted = inspect_local_asset_path(stored_path, require_absolute=False)
            asset = ScientificDataAsset(
                path=str(adopted.path),
                format=fmt,
                role="primary",
                size_bytes=adopted.size_bytes,
                access_mode=COPY_ACCESS,
                relative_mount_name=safe_name,
                kind=adopted.kind,
                name=display_name,
                device=adopted.device,
                inode=adopted.inode,
                object_type=adopted.object_type,
                mtime_ns=adopted.mtime_ns,
                ctime_ns=adopted.ctime_ns,
                fingerprint_sha256=adopted.fingerprint_sha256,
            )
            dataset = ResearchDataset(
                dataset_id=dataset_id,
                session_id=session_id,
                name=display_name,
                format=fmt,
                stored_path=asset.path,
                created_at=_now_iso(),
                assets=[asset],
            )
            self.save(dataset)
            return dataset
        except OSError as exc:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            if exc.errno == errno.EXDEV:
                raise ValueError(
                    "Browser upload staging and app storage must use the same filesystem"
                ) from exc
            raise ValueError(
                "Scientific dataset could not be atomically adopted into app storage"
            ) from exc
        except Exception:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            raise

    def add_reference(
        self,
        session_id: str,
        source_path: str | Path,
        filename: str | None = None,
        *,
        role: str = "primary",
        relative_mount_name: str | None = None,
        format: str | None = None,
    ) -> ResearchDataset:
        """Register one exact local source without copying its contents."""
        spec: dict[str, Any] = {
            "path": source_path,
            "name": self._display_name(filename, Path(source_path)),
            "role": role,
            "format": format,
        }
        if relative_mount_name is not None:
            spec["relative_mount_name"] = relative_mount_name
        return self.add_local_assets(session_id, [spec], access_mode=REFERENCE_ACCESS)

    def add_local_assets(
        self,
        session_id: str,
        assets: list[ScientificDataAsset | dict[str, Any] | str | Path],
        *,
        name: str | None = None,
        access_mode: str = REFERENCE_ACCESS,
    ) -> ResearchDataset:
        """Create one atomic bundle from one or more local files/directories.

        Each mapping accepts ``path`` plus optional ``name``, ``format``,
        ``role``, ``access_mode`` and ``relative_mount_name``/``mount_name``.
        Reference assets keep their canonical host path; copy assets are placed
        below the app-owned dataset directory.
        """
        if not assets:
            raise ValueError("A scientific dataset bundle requires at least one asset")

        def is_untyped(value: ScientificDataAsset | dict[str, Any] | str | Path) -> bool:
            if isinstance(value, (str, Path)):
                return True
            if isinstance(value, ScientificDataAsset):
                return False
            return not str(value.get("role") or "").strip() and not str(
                value.get("format") or ""
            ).strip()

        # Native multi-selection order is not scientific metadata. Infer one
        # compound manifest before individual assets are validated, but retain
        # explicit caller roles/formats unchanged for programmatic imports.
        if all(is_untyped(item) for item in assets):
            source_paths = [
                item
                if isinstance(item, (str, Path))
                else item.get("path", item.get("source_path"))
                for item in assets
            ]
            assets = infer_scientific_asset_specs(source_paths)

        prepared: list[dict[str, Any]] = []
        mount_paths: list[PurePosixPath] = []
        for index, raw_asset in enumerate(assets):
            if isinstance(raw_asset, (str, Path)):
                raw = {"path": raw_asset}
            elif isinstance(raw_asset, ScientificDataAsset):
                raw = raw_asset.to_storage_dict()
            else:
                raw = dict(raw_asset)
            source_path = raw.get("path", raw.get("source_path"))
            if not source_path:
                raise ValueError("Scientific data asset path is required")
            mode = str(raw.get("access_mode") or access_mode).strip().lower()
            if mode not in {REFERENCE_ACCESS, COPY_ACCESS}:
                raise ValueError("Invalid scientific data asset access mode")
            inspected = inspect_local_asset_path(
                source_path,
                require_absolute=mode == REFERENCE_ACCESS,
            )
            display_name = self._display_name(raw.get("name") or raw.get("filename"), inspected.path)
            data_format = detect_scientific_format(
                inspected.path,
                filename=display_name,
                declared_format=raw.get("format"),
            )
            role = str(raw.get("role") or ("primary" if index == 0 else f"asset_{index + 1}")).strip()
            if not role:
                raise ValueError("Scientific data asset role is required")
            if "relative_mount_name" in raw:
                raw_mount_name = raw["relative_mount_name"]
            elif "mount_name" in raw:
                raw_mount_name = raw["mount_name"]
            else:
                raw_mount_name = _safe_part(display_name, f"asset-{index + 1}.{data_format}")
            mount_name = validate_relative_mount_name(str(raw_mount_name))
            mount_path = PurePosixPath(mount_name)
            if any(
                mount_path == existing or mount_path in existing.parents or existing in mount_path.parents
                for existing in mount_paths
            ):
                raise ValueError("Scientific data asset mount names must be unique and non-overlapping")
            mount_paths.append(mount_path)
            prepared.append(
                {
                    "inspection": inspected,
                    "format": data_format,
                    "role": role,
                    "access_mode": mode,
                    "relative_mount_name": mount_name,
                    "name": display_name,
                    "support": str(raw.get("support") or ""),
                }
            )

        primary_count = sum(item["role"] == "primary" for item in prepared)
        if primary_count > 1:
            raise ValueError("Scientific dataset bundle must declare exactly one primary asset")

        dataset_id = uuid.uuid4().hex[:12]
        dataset_dir = self._session_dir(session_id) / dataset_id
        created_assets: list[ScientificDataAsset] = []
        created_dataset_dir = False
        try:
            dataset_dir.mkdir(parents=True, exist_ok=False)
            created_dataset_dir = True
            copy_root = dataset_dir / "assets"
            for item in prepared:
                inspected = item["inspection"]
                active_inspection = inspected
                if item["access_mode"] == COPY_ACCESS:
                    relative = PurePosixPath(item["relative_mount_name"])
                    destination = copy_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if inspected.kind == "directory":
                        shutil.copytree(inspected.path, destination, symlinks=True)
                    else:
                        shutil.copy2(inspected.path, destination)
                    active_inspection = inspect_local_asset_path(destination, require_absolute=False)
                created_assets.append(
                    ScientificDataAsset(
                        path=str(active_inspection.path),
                        format=item["format"],
                        role=item["role"],
                        size_bytes=active_inspection.size_bytes,
                        access_mode=item["access_mode"],
                        relative_mount_name=item["relative_mount_name"],
                        kind=active_inspection.kind,
                        name=item["name"],
                        support=item["support"],
                        device=active_inspection.device,
                        inode=active_inspection.inode,
                        object_type=active_inspection.object_type,
                        mtime_ns=active_inspection.mtime_ns,
                        ctime_ns=active_inspection.ctime_ns,
                        fingerprint_sha256=active_inspection.fingerprint_sha256,
                    )
                )

            primary = next(
                (asset for asset in created_assets if asset.role == "primary"),
                created_assets[0],
            )
            dataset = ResearchDataset(
                dataset_id=dataset_id,
                session_id=session_id,
                name=str(name or created_assets[0].name),
                format=primary.format,
                stored_path=primary.path,
                created_at=_now_iso(),
                assets=created_assets,
            )
            self.save(dataset)
            return dataset
        except (OSError, shutil.Error) as exc:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            raise ValueError("Scientific dataset assets could not be imported") from exc
        except Exception:
            if created_dataset_dir:
                shutil.rmtree(dataset_dir, ignore_errors=True)
            raise

    def save(self, dataset: ResearchDataset) -> ResearchDataset:
        manifest_path = self._manifest_path(dataset.session_id, dataset.dataset_id)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(dataset.to_storage_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(manifest_path)
        return dataset

    def _upgrade_legacy_asset_identities(self, dataset: ResearchDataset) -> ResearchDataset:
        """Seal legacy path-only assets the first time they become active.

        Old manifests remain readable, but they must not stay permanently on
        the weaker same-path/same-size contract. The one-time validation also
        refuses an unavailable or structurally changed source before recording
        its current filesystem identity.
        """
        upgraded: list[ScientificDataAsset] = []
        changed = False
        for asset in dataset.assets:
            if all(
                value is not None
                for value in (
                    asset.device,
                    asset.inode,
                    asset.object_type,
                    asset.mtime_ns,
                    asset.ctime_ns,
                    asset.fingerprint_sha256,
                )
            ):
                upgraded.append(asset)
                continue
            inspected = validate_scientific_data_asset(asset)
            upgraded.append(
                replace(
                    asset,
                    device=inspected.device,
                    inode=inspected.inode,
                    object_type=inspected.object_type,
                    mtime_ns=inspected.mtime_ns,
                    ctime_ns=inspected.ctime_ns,
                    fingerprint_sha256=inspected.fingerprint_sha256,
                )
            )
            changed = True
        if changed:
            dataset.assets = upgraded
            dataset.stored_path = dataset.primary_asset.path
            self.save(dataset)
        return dataset

    def get(self, session_id: str, dataset_id: str) -> ResearchDataset | None:
        manifest_path = self._manifest_path(session_id, dataset_id)
        try:
            dataset = ResearchDataset.from_dict(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None
        return self._upgrade_legacy_asset_identities(dataset)

    def list(self, session_id: str) -> list[ResearchDataset]:
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return []

        datasets: list[ResearchDataset] = []
        for manifest_path in sorted(session_dir.glob("*/manifest.json")):
            try:
                dataset = ResearchDataset.from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
                _validate_dataset_id(dataset.dataset_id)
                datasets.append(dataset)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return sorted(datasets, key=lambda item: (item.created_at, item.dataset_id), reverse=True)

    def delete(self, session_id: str, dataset_id: str) -> bool:
        """Delete app-owned bundle metadata/copies, never referenced sources."""
        validated_id = _validate_dataset_id(dataset_id)
        session_dir = self._session_dir(session_id).resolve(strict=False)
        dataset_dir = (session_dir / validated_id).resolve(strict=False)
        if dataset_dir.parent != session_dir:
            raise ValueError("Invalid dataset_id")
        if not dataset_dir.exists():
            return False
        if not dataset_dir.is_dir():
            raise ValueError("Dataset storage target is not a directory")
        shutil.rmtree(dataset_dir)
        return True
