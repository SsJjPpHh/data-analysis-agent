"""User-selected model sharing settings, independent of local research data.

Selecting sensitive columns is a disclosure control, not an anonymity guarantee.
The provider request projection must enforce this policy on every outbound path.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Literal


MAX_SENSITIVE_COLUMNS = 256
MAX_COLUMN_NAME_LENGTH = 256
_FIELDS = frozenset({"result_mode", "sensitive_columns", "send_result_images", "send_attached_images", "version"})


@dataclass(frozen=True)
class ModelSharingPolicy:
    result_mode: Literal["summary_only", "deidentified"] = "summary_only"
    sensitive_columns: tuple[str, ...] = ()
    send_result_images: bool = False
    send_attached_images: bool = True
    version: int = 1

    @classmethod
    def from_dict(cls, payload, *, strict: bool = False) -> "ModelSharingPolicy":
        """Validate API input; old or malformed saved state falls back safely."""
        try:
            if not isinstance(payload, Mapping):
                raise ValueError("外发设置必须是 JSON 对象。")
            unknown = set(payload) - _FIELDS
            if unknown:
                raise ValueError("外发设置包含未知字段。")
            mode = payload.get("result_mode", "summary_only")
            if mode not in ("summary_only", "deidentified"):
                raise ValueError("result_mode 只能是 summary_only 或 deidentified。")
            images = payload.get("send_result_images", False)
            attachments = payload.get("send_attached_images", True)
            if not isinstance(images, bool) or not isinstance(attachments, bool):
                raise ValueError("图片外发选项必须是布尔值。")
            version = payload.get("version", 1)
            if type(version) is not int or version != 1:
                raise ValueError("不支持该外发设置版本。")
            columns = payload.get("sensitive_columns", [])
            if not isinstance(columns, list):
                raise ValueError("sensitive_columns 必须是列名列表。")
            if len(columns) > MAX_SENSITIVE_COLUMNS:
                raise ValueError(f"敏感列不能超过 {MAX_SENSITIVE_COLUMNS} 个。")
            normalized = []
            seen = set()
            for column in columns:
                if not isinstance(column, str):
                    raise ValueError("敏感列名必须是文本。")
                # Dataframe column names can contain meaningful edge spaces.
                # Validate emptiness without changing the user's exact identifier.
                name = column
                if not name.strip() or len(name) > MAX_COLUMN_NAME_LENGTH or any(ord(char) < 32 for char in name):
                    raise ValueError(f"敏感列名不能为空、含控制字符或超过 {MAX_COLUMN_NAME_LENGTH} 个字符。")
                if name not in seen:
                    seen.add(name)
                    normalized.append(name)
            return cls(mode, tuple(normalized), images, attachments, version)
        except (TypeError, ValueError):
            if strict:
                raise
            return cls()

    def to_dict(self) -> dict:
        return {
            "result_mode": self.result_mode,
            "sensitive_columns": list(self.sensitive_columns),
            "send_result_images": self.send_result_images,
            "send_attached_images": self.send_attached_images,
            "version": self.version,
        }
