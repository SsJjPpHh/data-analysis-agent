#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Logical system Workspace backed by allowlisted project directories.

The system Workspace does not move files.  It exposes stable virtual roots
(``uploads/`` and ``outputs/``), keeps a metadata-only incremental
index, and never injects file contents into an LLM prompt automatically.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

import fnmatch
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_INDEXED_FILES = 10_000
MAX_LIST_LIMIT = 100
MAX_LIST_CHARS = 12_000
MAX_SEARCH_LIMIT = 50
MAX_RECENT_FILES = 5
INDEX_REFRESH_SECONDS = 30.0

_COMMON_SKIP_DIRS = {
    ".git",
    ".idea",
    ".ruff_cache",
    ".pytest_cache",
    ".workspace",
    "__pycache__",
    ".venv",
    "node_modules",
}


@dataclass(frozen=True)
class SystemRootPolicy:
    name: str
    path: Path
    writable: bool = False
    skip_dirs: frozenset[str] = frozenset()


class SystemWorkspace:
    """Virtual, policy-controlled view of selected project directories."""

    def __init__(
        self,
        project_root: Path,
        *,
        data_root_path: Path | None = None,
        resource_root_path: Path | None = None,
    ) -> None:
        project_root = project_root.resolve()
        self.project_root = project_root
        writable_root = (data_root_path or project_root).resolve()
        resources = (resource_root_path or project_root).resolve()
        self.roots: dict[str, SystemRootPolicy] = {
            "uploads": SystemRootPolicy("uploads", writable_root / "uploads"),
            "outputs": SystemRootPolicy("outputs", writable_root / "outputs", writable=True),
        }
        self._state_dir = writable_root / "outputs" / ".workspace"
        self._index_path = self._state_dir / "system-index.json"
        self._lock = threading.RLock()
        self._last_refresh: dict[str, float] = {}
        self._index = self._load_index()

    def _load_index(self) -> dict:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"version": 1, "roots": {}}
        except (OSError, json.JSONDecodeError) as e:
            log.debug("[system_workspace] failed to load index, using default: %s", e)
            return {"version": 1, "roots": {}}

    def _save_index(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        temp = self._index_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self._index, ensure_ascii=False), encoding="utf-8")
        temp.replace(self._index_path)

    def policy(self, root_name: str) -> SystemRootPolicy:
        try:
            return self.roots[root_name.lower()]
        except KeyError as exc:
            raise ValueError(f"unknown system workspace root: {root_name}") from exc

    def parse_virtual_path(self, value: str) -> tuple[str, str] | None:
        """Return ``(root, relative_path)`` for an explicit virtual path."""
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip().replace("\\", "/")
        if normalized.lower().startswith("workspace://"):
            normalized = normalized[len("workspace://") :]
        normalized = normalized.lstrip("/")
        first, _, rest = normalized.partition("/")
        key = first.lower()
        if key not in self.roots:
            return None
        return key, rest or "."

    def resolve(self, root_name: str, relative: str = ".", *, write: bool = False) -> Path:
        policy = self.policy(root_name)
        if write and not policy.writable:
            raise ValueError(f"system workspace root '{root_name}' is read-only")
        raw = Path(relative or ".")
        if raw.is_absolute():
            raise ValueError("system workspace paths must be virtual, not absolute")
        try:
            resolved = (policy.path / raw).resolve(strict=False)
            resolved.relative_to(policy.path.resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("path must stay inside the system workspace root") from exc
        relative_parts = {part.lower() for part in resolved.relative_to(policy.path.resolve()).parts}
        blocked = _COMMON_SKIP_DIRS | {item.lower() for item in policy.skip_dirs}
        if relative_parts & blocked or any(part.startswith(".") for part in relative_parts):
            raise ValueError("path is hidden by the system workspace policy")
        return resolved

    def virtual_name(self, root_name: str, path: Path) -> str:
        relative = path.resolve().relative_to(self.policy(root_name).path.resolve())
        suffix = relative.as_posix()
        return root_name if suffix == "." else f"{root_name}/{suffix}"

    def _scan_root(self, root_name: str) -> dict:
        policy = self.policy(root_name)
        entries: dict[str, dict] = {}
        truncated = False
        if policy.path.is_dir():
            blocked = _COMMON_SKIP_DIRS | {item.lower() for item in policy.skip_dirs}
            root_resolved = policy.path.resolve()
            for current, dirs, files in os.walk(policy.path, followlinks=False):
                dirs[:] = [name for name in dirs if name.lower() not in blocked and not name.startswith(".")]
                for filename in files:
                    if filename.startswith("."):
                        continue
                    item = Path(current) / filename
                    try:
                        if item.is_symlink():
                            continue
                        safe = item.resolve()
                        rel = safe.relative_to(root_resolved)
                        stat = safe.stat()
                    except (OSError, ValueError) as e:
                        log.debug("[system_workspace] skipping file %s: %s", item, e)
                        continue
                    entries[rel.as_posix()] = {
                        "size": stat.st_size,
                        "modified_ns": stat.st_mtime_ns,
                        "suffix": safe.suffix.lower(),
                    }
                    if len(entries) >= MAX_INDEXED_FILES:
                        truncated = True
                        break
                if truncated:
                    break
        return {
            "scanned_at": time.time(),
            "truncated": truncated,
            "entries": entries,
        }

    def refresh(self, root_name: str, *, force: bool = False) -> dict:
        root_name = root_name.lower()
        with self._lock:
            now = time.monotonic()
            cached = self._index.setdefault("roots", {}).get(root_name)
            if cached and not force and now - self._last_refresh.get(root_name, 0.0) < INDEX_REFRESH_SECONDS:
                return cached
            scanned = self._scan_root(root_name)
            # Metadata is replaced atomically; unchanged files retain identical
            # size/mtime records and no file content is read during refresh.
            self._index.setdefault("roots", {})[root_name] = scanned
            self._last_refresh[root_name] = now
            self._save_index()
            return scanned

    def entries(self, root_name: str, *, force: bool = False) -> dict[str, dict]:
        return dict(self.refresh(root_name, force=force).get("entries", {}))

    def invalidate(self, root_name: str) -> None:
        with self._lock:
            self._last_refresh.pop(root_name.lower(), None)

    def summary(self) -> dict:
        roots = []
        for name, policy in self.roots.items():
            indexed = self.refresh(name)
            entries = indexed.get("entries", {})
            recent = sorted(
                entries.items(),
                key=lambda item: item[1].get("modified_ns", 0),
                reverse=True,
            )[:MAX_RECENT_FILES]
            roots.append(
                {
                    "name": name,
                    "uri": f"workspace://{name}",
                    "access": "read-write" if policy.writable else "read-only",
                    "file_count": len(entries),
                    "total_bytes": sum(item.get("size", 0) for item in entries.values()),
                    "recent": [
                        {"path": f"{name}/{path}", "size": meta.get("size", 0)} for path, meta in recent
                    ],
                    "truncated": bool(indexed.get("truncated")),
                }
            )
        return {"roots": roots}

    def list_files(
        self,
        root_name: str,
        base: str = ".",
        pattern: str = "**/*",
        *,
        limit: int = 20,
        cursor: int = 0,
    ) -> dict:
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        cursor = max(0, int(cursor))
        base_path = Path(base or ".").as_posix().strip("./")
        prefix = f"{base_path}/" if base_path else ""
        rows = []
        for path, meta in self.entries(root_name).items():
            if prefix and not path.startswith(prefix):
                continue
            local = path[len(prefix) :] if prefix else path
            wanted = pattern or "**/*"
            if (
                wanted in {"*", "**", "**/*"}
                or fnmatch.fnmatch(local, wanted)
                or fnmatch.fnmatch(path, wanted)
            ):
                rows.append((path, meta))
        rows.sort(key=lambda item: item[1].get("modified_ns", 0), reverse=True)
        page = []
        rendered_chars = 0
        for row in rows[cursor : cursor + limit]:
            row_chars = len(root_name) + len(row[0]) + 80
            if page and rendered_chars + row_chars > MAX_LIST_CHARS:
                break
            page.append(row)
            rendered_chars += row_chars
        next_cursor = cursor + len(page) if cursor + len(page) < len(rows) else None
        return {
            "matches": [{"path": f"{root_name}/{path}", **meta} for path, meta in page],
            "count": len(page),
            "total": len(rows),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "truncated": next_cursor is not None,
        }
