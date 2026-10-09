"""Dataset-scoped semantic-role memory for low-friction template reuse."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatasetRoleMemory:
    """Remember confirmed roles without leaking them across dataset revisions."""

    _values: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _key(
        dataset_id: str,
        workspace_generation: str,
        profile_revision: str,
        semantic_role: str,
    ) -> str:
        return "\x1f".join(
            (
                str(dataset_id or "").strip(),
                str(workspace_generation or "").strip(),
                str(profile_revision or "").strip(),
                str(semantic_role or "").strip(),
            )
        )

    def remember(
        self,
        *,
        dataset_id: str,
        workspace_generation: str,
        profile_revision: str,
        semantic_role: str,
        value: Any,
    ) -> None:
        if not dataset_id or not semantic_role:
            return
        self._values[self._key(dataset_id, workspace_generation, profile_revision, semantic_role)] = deepcopy(
            value
        )

    def recall(
        self,
        *,
        dataset_id: str,
        workspace_generation: str,
        profile_revision: str,
        semantic_role: str,
    ) -> Any | None:
        value = self._values.get(self._key(dataset_id, workspace_generation, profile_revision, semantic_role))
        return deepcopy(value)

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self._values)

    @classmethod
    def from_dict(cls, value: object) -> "DatasetRoleMemory":
        return cls(deepcopy(value) if isinstance(value, dict) else {})
