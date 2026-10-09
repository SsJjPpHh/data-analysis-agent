from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from r_runtime.protocol import DatasetRef, RRunRequest, RRunResult


class RExecutionBackend(ABC):
    @abstractmethod
    def inspect_dataset(self, dataset: DatasetRef, timeout_seconds: int = 0) -> RRunResult:
        raise NotImplementedError

    @abstractmethod
    def run_code(self, request: RRunRequest) -> RRunResult:
        raise NotImplementedError

    def list_packages(self, timeout_seconds: int = 30) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "R package listing is not supported by this backend.",
            "packages": [],
        }

    def package_readiness(
        self,
        packages: list[str] | tuple[str, ...],
        timeout_seconds: int = 30,
        *,
        stop_after_usable: bool = False,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "R package readiness checks are not supported by this backend.",
            "packages": [],
        }

    def package_help(
        self,
        package: str,
        query: str = "",
        limit: int = 30,
        include_docs: bool = True,
        doc_limit: int = 3,
        max_doc_chars: int = 4000,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "R package help lookup is not supported by this backend.",
            "package": package,
        }
