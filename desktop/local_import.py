"""Native-path import grants for the loopback desktop workbench.

The web view never submits an arbitrary host path.  A native file dialog
creates a short-lived, one-use capability and the local Flask module consumes
that capability when it creates the Scientific Dataset Bundle.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from data.scientific_dataset import inspect_local_asset_path


@dataclass(frozen=True)
class LocalImportSelection:
    token: str
    name: str
    kind: str
    asset_count: int
    size_bytes: int
    expires_at: float

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass(frozen=True)
class _LocalPathIdentity:
    device: int
    inode: int
    object_type: int
    size_bytes: int
    mtime_ns: int
    ctime_ns: int
    fingerprint_sha256: str


@dataclass(frozen=True)
class LocalImportGrant:
    token: str
    paths: tuple[str, ...]
    created_at: float
    expires_at: float
    identities: tuple[_LocalPathIdentity, ...] = ()


class LocalImportGrantStore:
    """Issue and consume exact native-path capabilities."""

    def __init__(self, *, ttl_seconds: int = 10 * 60, clock=time.time) -> None:
        self._ttl_seconds = max(30, int(ttl_seconds))
        self._clock = clock
        self._grants: dict[str, LocalImportGrant] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validated_paths(paths: Iterable[str | Path]) -> tuple[Path, ...]:
        selected: list[Path] = []
        seen: set[Path] = set()
        for raw in paths:
            source = Path(raw).expanduser()
            if source.is_symlink():
                raise ValueError("不能通过符号链接导入科研数据")
            try:
                resolved = source.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"所选数据不存在或无法访问：{source}") from exc
            if not (resolved.is_file() or resolved.is_dir()):
                raise ValueError(f"所选路径不是文件或目录：{source}")
            if resolved in seen:
                continue
            seen.add(resolved)
            selected.append(resolved)
        if not selected:
            raise ValueError("没有选择数据")
        if len(selected) > 256:
            raise ValueError("一个科学数据包最多登记 256 个顶层数据资产")
        return tuple(selected)

    @staticmethod
    def _path_size(path: Path) -> int:
        if path.is_file():
            try:
                return int(path.stat().st_size)
            except OSError:
                return 0
        # Directory size remains private/pending in the UI. Grant issuance does
        # perform a recursive identity scan so the capability is bound to the
        # exact descendants selected by the user.
        return 0

    @staticmethod
    def _path_identity(path: Path) -> _LocalPathIdentity:
        """Bind a grant to an object and, for directories, its descendants."""
        try:
            inspected = inspect_local_asset_path(path)
        except ValueError:
            raise
        except OSError as exc:
            raise ValueError(f"所选数据不存在或无法访问：{path}") from exc
        return _LocalPathIdentity(
            device=inspected.device,
            inode=inspected.inode,
            object_type=inspected.object_type,
            size_bytes=inspected.size_bytes,
            mtime_ns=inspected.mtime_ns,
            ctime_ns=inspected.ctime_ns,
            fingerprint_sha256=inspected.fingerprint_sha256,
        )

    def issue(self, paths: Iterable[str | Path]) -> LocalImportSelection:
        selected = self._validated_paths(paths)
        now = float(self._clock())
        token = secrets.token_urlsafe(32)
        grant = LocalImportGrant(
            token=token,
            paths=tuple(str(path) for path in selected),
            created_at=now,
            expires_at=now + self._ttl_seconds,
            identities=tuple(self._path_identity(path) for path in selected),
        )
        with self._lock:
            self._purge_locked(now)
            self._grants[token] = grant
        primary = selected[0]
        return LocalImportSelection(
            token=token,
            name=primary.name if len(selected) == 1 else f"{primary.name} 等 {len(selected)} 项",
            kind="directory" if len(selected) == 1 and primary.is_dir() else "bundle" if len(selected) > 1 else "file",
            asset_count=len(selected),
            size_bytes=sum(self._path_size(path) for path in selected),
            expires_at=grant.expires_at,
        )

    def consume(self, token: str) -> LocalImportGrant:
        value = str(token or "").strip()
        if not value:
            raise ValueError("本地导入授权缺失")
        now = float(self._clock())
        with self._lock:
            self._purge_locked(now)
            grant = self._grants.pop(value, None)
        if grant is None or grant.expires_at <= now:
            raise ValueError("本地导入授权已失效，请重新选择数据")
        # Re-resolve at consumption so a replaced path cannot inherit the grant.
        selected = self._validated_paths(grant.paths)
        identities = tuple(self._path_identity(path) for path in selected)
        if grant.identities and identities != grant.identities:
            raise ValueError("所选数据在确认前已发生变化，请重新选择")
        return LocalImportGrant(
            token=grant.token,
            paths=tuple(str(path) for path in selected),
            created_at=grant.created_at,
            expires_at=grant.expires_at,
            identities=identities,
        )

    def _purge_locked(self, now: float) -> None:
        expired = [token for token, grant in self._grants.items() if grant.expires_at <= now]
        for token in expired:
            self._grants.pop(token, None)


_DEFAULT_GRANTS = LocalImportGrantStore()


def default_local_import_grants() -> LocalImportGrantStore:
    return _DEFAULT_GRANTS


def create_desktop_import_bridge(parent):
    """Create the Qt adapter lazily so source-mode tests do not require Qt."""

    from PySide6.QtCore import QObject, Slot
    from PySide6.QtWidgets import QFileDialog

    grants = default_local_import_grants()

    class DesktopImportBridge(QObject):
        @Slot(result=str)
        def selectFiles(self) -> str:  # noqa: N802 - Qt interface
            paths, _selected_filter = QFileDialog.getOpenFileNames(
                parent,
                "选择科研数据文件",
                "",
                "科研数据 (*);;所有文件 (*)",
            )
            if not paths:
                return ""
            try:
                return grants.issue(paths).to_json()
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

        @Slot(result=str)
        def selectDirectory(self) -> str:  # noqa: N802 - Qt interface
            path = QFileDialog.getExistingDirectory(parent, "选择科研数据文件夹", "")
            if not path:
                return ""
            try:
                return grants.issue([path]).to_json()
            except ValueError as exc:
                return json.dumps({"error": str(exc)}, ensure_ascii=False)

    return DesktopImportBridge(parent)
