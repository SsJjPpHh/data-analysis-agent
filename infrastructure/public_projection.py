"""Helpers for projecting internal runtime records into public payloads."""

from __future__ import annotations

import re
from typing import Any, Collection


PUBLIC_R_RESULT_PRIVATE_KEYS = frozenset(
    {
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
        "execution_snapshot",
    }
)


_FILESYSTEM_PATH_RE = re.compile(
    r"(?<![\w/])(?:"
    r"[A-Za-z]:[\\/][^\s\"'<>]+|"
    r"\\\\[^\\/\s\"'<>]+[\\/][^\s\"'<>]+|"
    r"/(?!/)[^\s\"'<>]+"
    r")"
)


def redact_filesystem_paths(value: str) -> str:
    """Remove absolute filesystem paths embedded inside public text."""
    return _FILESYSTEM_PATH_RE.sub("[已隐藏路径]", str(value or ""))


def public_projection(value: Any, *, drop_keys: Collection[str] = ()) -> Any:
    """Recursively drop internal keys and redact paths inside all strings."""
    blocked = {str(key).lower() for key in drop_keys}

    def project(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: project(child) for key, child in item.items() if str(key).lower() not in blocked}
        if isinstance(item, (list, tuple)):
            return [project(child) for child in item]
        if isinstance(item, str):
            return redact_filesystem_paths(item)
        return item

    return project(value)


def public_r_result(value: dict[str, Any], *, error_message: str = "") -> dict[str, Any]:
    """Project an R runtime result for browser/model-visible payloads."""
    projected = public_projection(value, drop_keys=PUBLIC_R_RESULT_PRIVATE_KEYS)
    projected["error"] = str(error_message or "")
    return projected
