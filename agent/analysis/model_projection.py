"""Bounded, private-field-free projection of local R results for the LLM."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from infrastructure.public_projection import redact_filesystem_paths


PRIVATE_KEYS = frozenset(
    {
        "code",
        "stdout",
        "stderr",
        "path",
        "stored_path",
        "artifacts_dir",
        "file_path",
        "source_path",
        "output_path",
        "workdir",
        "cwd",
        "reproducibility",
        "analysis_plan",
        "dataset_manifest",
    }
)
INSPECTION_RAW_KEYS = frozenset(
    {"sample", "samples", "sample_rows", "raw_sample", "raw_values", "head", "records"}
)


class ModelResultProjector:
    MAX_TABLE_ROWS = 20
    MAX_TABLE_COLUMNS = 30
    MAX_FIELD_CHARS = 256
    # Diagnostic fields keep a much larger TAIL budget: R prints the actual
    # error near the END of its output, and a 256-char head truncation hid
    # the real failure from the model — degrading every repair round.
    MAX_ERROR_FIELD_CHARS = 4000
    MAX_PAYLOAD_BYTES = 32 * 1024
    PRIORITY_KEYS = (
        "ok",
        "job_id",
        "artifact_previews",
        "result",
        "execution",
        "repair_diagnostics",
        "partial_result",
        "dependency_gate",
        "preparation_plan",
        "summary",
        "dataset",
    )

    @classmethod
    def _ordered_items(cls, value: dict[Any, Any]) -> list[tuple[Any, Any]]:
        priority = [key for key in cls.PRIORITY_KEYS if key in value]
        priority_set = set(priority)
        return [(key, value[key]) for key in priority] + [
            (key, child) for key, child in value.items() if key not in priority_set
        ]

    @staticmethod
    def _parse(value: object) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        is_error = text.startswith("ERROR:")
        body = text.removeprefix("ERROR:").strip() if is_error else text
        start = body.find("{")
        if start < 0:
            limit = (
                ModelResultProjector.MAX_ERROR_FIELD_CHARS
                if is_error
                else ModelResultProjector.MAX_FIELD_CHARS
            )
            return {"ok": not is_error, "message": ModelResultProjector._tail(body, limit)}
        try:
            parsed = json.loads(body[start:])
        except (TypeError, ValueError):
            limit = (
                ModelResultProjector.MAX_ERROR_FIELD_CHARS
                if is_error
                else ModelResultProjector.MAX_FIELD_CHARS
            )
            return {"ok": not is_error, "message": ModelResultProjector._tail(body, limit)}
        if isinstance(parsed, dict) and is_error:
            parsed.setdefault("ok", False)
        return parsed

    @staticmethod
    def _tail(value: str, limit: int) -> str:
        """Keep the END of a diagnostic string; R reports the real error late."""
        if limit <= 0 or len(value) <= limit:
            return value
        return "…" + value[-limit:]

    @classmethod
    def _csv_preview(
        cls,
        value: str,
        field_limit: int,
        *,
        row_limit: int,
        column_limit: int,
    ) -> str:
        try:
            rows = list(csv.reader(io.StringIO(value)))[
                : min(cls.MAX_TABLE_ROWS, max(1, row_limit)) + 1
            ]
        except (csv.Error, TypeError):
            return value[:field_limit]
        if not rows:
            return ""
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        for row in rows:
            writer.writerow(
                [
                    str(field)[:field_limit]
                    for field in row[: min(cls.MAX_TABLE_COLUMNS, max(3, column_limit))]
                ]
            )
        return output.getvalue().rstrip("\n")

    @classmethod
    def _bound(
        cls,
        value: Any,
        *,
        field_limit: int,
        list_limit: int,
        inspection: bool,
        parent_key: str = "",
        omissions: list[str] | None = None,
        field_path: str = "",
    ) -> Any:
        omissions = omissions if omissions is not None else []
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            stderr_tail = ""
            raw_stderr = value.get("stderr")
            if isinstance(raw_stderr, str) and raw_stderr.strip():
                redacted_stderr = redact_filesystem_paths(raw_stderr)
                stderr_tail = cls._tail(redacted_stderr, cls.MAX_ERROR_FIELD_CHARS)
            public_items = [
                (key, child) for key, child in cls._ordered_items(value)
                if str(key).lower() not in PRIVATE_KEYS
                and not (inspection and str(key).lower() in INSPECTION_RAW_KEYS)
            ]
            if len(public_items) > cls.MAX_TABLE_COLUMNS:
                omissions.append(field_path or "root")
            for key, child in public_items[: cls.MAX_TABLE_COLUMNS]:
                normalized = str(key).lower()
                if normalized in PRIVATE_KEYS or (inspection and normalized in INSPECTION_RAW_KEYS):
                    continue
                result[str(key)] = cls._bound(
                    child,
                    field_limit=field_limit,
                    list_limit=list_limit,
                    inspection=inspection,
                    parent_key=normalized,
                    omissions=omissions,
                    field_path=f"{field_path}.{key}".lstrip("."),
                )
            if stderr_tail:
                # R prints the decisive error at the end of stderr; without
                # this tail the model debugs its own code blind.
                result["stderr_tail"] = stderr_tail
            return result
        if isinstance(value, (list, tuple)):
            if len(value) > list_limit:
                omissions.append(field_path)
            return [
                cls._bound(
                    child,
                    field_limit=field_limit,
                    list_limit=list_limit,
                    inspection=inspection,
                    parent_key=parent_key,
                    omissions=omissions,
                    field_path=field_path + "[]",
                )
                for child in list(value)[:list_limit]
            ]
        if isinstance(value, str):
            value = redact_filesystem_paths(value)
            if parent_key == "preview_csv":
                preview = cls._csv_preview(
                    value,
                    field_limit,
                    row_limit=list_limit,
                    column_limit=max(8, list_limit),
                )
                if preview != value.rstrip("\r\n"):
                    omissions.append(field_path)
                return preview
            if parent_key in {"error", "stderr_tail"}:
                # Diagnostic tail: R reports the real error at the end.
                if len(value) > cls.MAX_ERROR_FIELD_CHARS:
                    omissions.append(field_path)
                return cls._tail(value, cls.MAX_ERROR_FIELD_CHARS)
            if parent_key in {"message", "detail"} and len(value) > cls.MAX_ERROR_FIELD_CHARS:
                omissions.append(field_path)
                return value[: cls.MAX_ERROR_FIELD_CHARS]
            if len(value) > field_limit:
                omissions.append(field_path)
            return value[:field_limit]
        return value

    @staticmethod
    def _size(value: Any) -> int:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def project(self, payload: object, *, inspection: bool = False) -> dict[str, Any]:
        parsed = self._parse(payload)
        variable_index: list[dict[str, str]] = []
        def collect_variables(value: Any) -> None:
            nonlocal variable_index
            if isinstance(value, dict):
                for key, child in value.items():
                    if str(key).lower() in PRIVATE_KEYS | INSPECTION_RAW_KEYS:
                        continue
                    if key in {"variables", "columns"} and isinstance(child, list):
                        index = [
                            {"name": str(item["name"]), "type": str(item.get("semantic_type") or item.get("type") or "unknown")}
                            for item in child if isinstance(item, dict) and item.get("name")
                        ]
                        if len(index) > len(variable_index):
                            variable_index = index
                    elif isinstance(child, dict):
                        collect_variables(child)
        if inspection:
            collect_variables(parsed)
        stages = ((256, 20), (128, 16), (96, 12), (64, 8), (32, 4))
        projected: Any = {}
        for field_limit, list_limit in stages:
            omissions: list[str] = []
            projected = self._bound(
                parsed,
                field_limit=field_limit,
                list_limit=list_limit,
                inspection=inspection,
                omissions=omissions,
            )
            if not isinstance(projected, dict):
                projected = {"result": projected}
            if omissions:
                projected["truncated"] = True
                projected["projection"] = {
                    "bounded_fields": list(dict.fromkeys(omissions))[:20],
                    "detail_limit": list_limit,
                    "notice": "部分详情已省略；不得把省略视为不存在。",
                }
            if variable_index:
                projected["variable_index"] = variable_index
                projected["variable_index_count"] = len(variable_index)
                projected["detail_lookup"] = "inspect_dataset(column_names=[精确列名])，每次最多 20 列。索引仅包含已检查的列；summary.columns 是数据总列数。"
            if self._size(projected) <= self.MAX_PAYLOAD_BYTES:
                break
        if variable_index and self._size(projected) > self.MAX_PAYLOAD_BYTES:
            projected["variable_index"] = list(variable_index)
            while projected["variable_index"] and self._size(projected) > self.MAX_PAYLOAD_BYTES - 256:
                projected["variable_index"].pop()
            projected["truncated"] = True
            projected["variable_index_returned"] = len(projected["variable_index"])
            projected["detail_lookup"] = "索引超出预算；使用 inspect_dataset(column_names=[精确列名]) 读取指定变量。"
        if self._size(projected) > self.MAX_PAYLOAD_BYTES:
            slim = {
                "ok": bool(projected.get("ok")),
                "truncated": True,
                "message": "Local R result exceeded the model projection budget; use saved artifacts.",
            }
            # Error diagnostics survive even the worst-case shrink: losing
            # tables is recoverable, losing the failure reason is not.
            for key in ("error", "stderr_tail"):
                if isinstance(projected.get(key), str):
                    slim[key] = self._tail(projected[key], 2000)
            projected = slim
        return projected
