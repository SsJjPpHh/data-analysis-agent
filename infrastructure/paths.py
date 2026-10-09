"""Read-only source resources and separate per-user writable runtime data.

No legacy desktop data or credentials are copied automatically.
"""
from __future__ import annotations
import os
from pathlib import Path


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_data_root() -> Path:
    return Path.home() / ".data-analysis-agent"


def data_root() -> Path:
    raw = str(os.environ.get("BAA_DATA_DIR") or "").strip()
    path = Path(raw).expanduser() if raw else default_data_root()
    if not path.is_absolute():
        raise ValueError("BAA_DATA_DIR must be an absolute path")
    return path.resolve(strict=False)


def resource_root() -> Path:
    return source_root()


def resource_path(*parts: str | os.PathLike[str]) -> Path:
    return resource_root().joinpath(*parts)


def data_path(*parts: str | os.PathLike[str]) -> Path:
    return data_root().joinpath(*parts)


def runtime_config_path(filename: str) -> Path:
    return data_path("config", filename)
