"""Research-mode activation is always a plain chat turn."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActivationContext:
    """Research workbench turns have no skill, command, or internal action."""

    @property
    def kind(self) -> str:
        return "none"

    @property
    def name(self) -> str:
        return ""

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name}

    def to_record(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name}
