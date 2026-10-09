"""Persistent selection of the writable R package library.

The application always executes the user's installed R.  This module only
decides where packages installed on demand are written: either R's standard
per-user library or a versioned application/project-specific library.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from infrastructure.paths import data_path
from infrastructure.secure_files import write_private_text


USER_LIBRARY_MODE = "user"
MANAGED_LIBRARY_MODE = "managed"
LIBRARY_MODES = frozenset({USER_LIBRARY_MODE, MANAGED_LIBRARY_MODE})
log = logging.getLogger(__name__)


def settings_path() -> Path:
    return data_path("config", "r-library.json")


@dataclass(frozen=True)
class RLibrarySettings:
    mode: str = USER_LIBRARY_MODE
    custom_directory: str = ""
    load_warning: str = ""

    def __post_init__(self) -> None:
        if self.mode not in LIBRARY_MODES:
            raise ValueError("R 包目录模式必须是 user 或 managed")
        if self.custom_directory:
            path = Path(self.custom_directory).expanduser()
            if not path.is_absolute():
                raise ValueError("R 包专属目录必须是绝对路径")

    @property
    def managed_root(self) -> Path:
        if self.custom_directory:
            return Path(self.custom_directory).expanduser().resolve(strict=False)
        return data_path("r-library").resolve(strict=False)

    def to_dict(self) -> dict[str, str]:
        return {"mode": self.mode, "custom_directory": self.custom_directory}


def load_r_library_settings() -> RLibrarySettings:
    """Load the current choice, with explicit launch environment overrides."""

    environment_mode = str(os.environ.get("BAA_R_LIBRARY_MODE") or "").strip().lower()
    environment_directory = str(os.environ.get("BAA_R_LIBRARY_DIR") or "").strip()
    if environment_mode or environment_directory:
        mode = environment_mode or MANAGED_LIBRARY_MODE
        directory = environment_directory if mode == MANAGED_LIBRARY_MODE else ""
        return RLibrarySettings(mode=mode, custom_directory=directory)

    path = settings_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RLibrarySettings()
    except (OSError, ValueError, TypeError) as exc:
        warning = f"R 包目录设置损坏或不可读，已临时使用个人包库：{path}（{exc}）"
        log.warning(warning)
        return RLibrarySettings(load_warning=warning)
    if not isinstance(payload, dict):
        warning = f"R 包目录设置格式无效，已临时使用个人包库：{path}"
        log.warning(warning)
        return RLibrarySettings(load_warning=warning)
    try:
        return RLibrarySettings(
            mode=str(payload.get("mode") or USER_LIBRARY_MODE).strip().lower(),
            custom_directory=str(payload.get("custom_directory") or "").strip(),
        )
    except ValueError as exc:
        warning = f"R 包目录设置无效，已临时使用个人包库：{path}（{exc}）"
        log.warning(warning)
        return RLibrarySettings(load_warning=warning)


def activate_r_library_settings(settings: RLibrarySettings) -> None:
    """Apply a saved selection to the current desktop process immediately."""

    os.environ["BAA_R_LIBRARY_MODE"] = settings.mode
    if settings.mode == MANAGED_LIBRARY_MODE and settings.custom_directory:
        os.environ["BAA_R_LIBRARY_DIR"] = settings.custom_directory
    else:
        os.environ.pop("BAA_R_LIBRARY_DIR", None)


def save_r_library_settings(settings: RLibrarySettings) -> None:
    write_private_text(
        settings_path(),
        json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    activate_r_library_settings(settings)
