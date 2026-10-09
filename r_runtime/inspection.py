from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent.analysis.semantics import infer_semantic_type
from r_runtime.protocol import RRunResult


def read_table_artifact(result: RRunResult, name: str) -> list[dict[str, Any]]:
    for artifact in result.tables:
        if artifact.name != name:
            continue
        path = Path(artifact.path)
        if not path.is_file():
            continue
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))
        except OSError:
            return []
    return []


def read_text_artifact(result: RRunResult, name: str) -> str:
    for artifact in result.texts:
        if artifact.name != name:
            continue
        path = Path(artifact.path)
        if not path.is_file():
            continue
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def inspection_summary(result: RRunResult) -> dict[str, Any]:
    columns = read_table_artifact(result, "dataset_profile")
    levels = read_table_artifact(result, "dataset_levels")
    overview = read_text_artifact(result, "dataset_overview")
    structure_text = read_text_artifact(result, "dataset_structure")
    try:
        scientific = json.loads(structure_text) if structure_text else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        scientific = {}
    if not isinstance(scientific, dict):
        scientific = {}
    rows_match = re.search(r"Rows:\s*([0-9,]+)", overview)
    cols_match = re.search(r"Columns:\s*([0-9,]+)", overview)
    object_match = re.search(r"Object kind:\s*([^\r\n]+)", overview)
    rows = int(rows_match.group(1).replace(",", "")) if rows_match else None
    column_count = int(cols_match.group(1).replace(",", "")) if cols_match else len(columns)

    total_missing = 0.0
    missing_columns = 0
    type_counts: dict[str, int] = {}
    normalized_columns: list[dict[str, Any]] = []
    for position, column in enumerate(columns):
        missing = _number(column.get("missing")) or 0
        missing_pct = _number(column.get("missing_pct")) or 0
        if missing > 0:
            missing_columns += 1
        total_missing += missing
        type_name = str(column.get("type") or "unknown")
        unique_number = _number(column.get("unique_count"))
        unique_count = int(unique_number) if unique_number is not None else None
        non_missing_number = _number(column.get("non_missing"))
        non_missing = (
            int(non_missing_number)
            if non_missing_number is not None
            else max(0, int(rows or 0) - int(missing))
        )
        name = str(column.get("column") or "")
        type_counts[type_name] = type_counts.get(type_name, 0) + 1
        semantic_type = infer_semantic_type(name, type_name, unique_count, rows)
        numeric_coding = (
            "labels_unconfirmed"
            if semantic_type == "categorical"
            and re.search(r"integer|numeric|double|float|decimal", type_name, re.IGNORECASE)
            else ""
        )
        normalized_columns.append(
            {
                "column_id": "col_" + hashlib.sha256(f"{position}:{name}".encode()).hexdigest()[:12],
                "position": position,
                "name": name,
                "type": type_name,
                "label": str(column.get("label") or ""),
                "semantic_type": semantic_type,
                "numeric_coding": numeric_coding,
                "missing": int(missing),
                "missing_pct": missing_pct,
                "non_missing": non_missing,
                "unique_count": unique_count,
            }
        )

    denominator = (rows or 0) * (column_count or 0)
    # Column statistics come from a bounded sample whenever the R profiler
    # says so (lazy datasets AND row-sampled data.frames).  An overall
    # missing rate computed over unknown/unsampled rows would present sample
    # stats as full-population facts, so report None and mark the scope.
    inspection_meta = scientific.get("inspection") if isinstance(scientific.get("inspection"), dict) else {}
    sample_rows = inspection_meta.get("sample_rows")
    sampled = (not rows) or bool(
        sample_rows and rows and int(sample_rows) < int(rows)
    )
    overall_missing_pct = (
        round(total_missing / denominator * 100, 2)
        if denominator and not sampled
        else None
    )
    local_levels: dict[str, list[str]] = {}
    valid_names = {item["name"] for item in normalized_columns}
    for item in levels[:8192]:
        name = str(item.get("column") or "").strip()
        level = str(item.get("level") or "")[:128]
        if not name or name not in valid_names or not level:
            continue
        values = local_levels.setdefault(name, [])
        if level not in values and len(values) < 32:
            values.append(level)
    revision_source = [
        {
            "name": item["name"],
            "type": item["type"],
            "unique_count": item["unique_count"],
            "missing": item["missing"],
        }
        for item in normalized_columns
    ]
    profile_revision = hashlib.sha256(
        json.dumps(revision_source, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    object_kind = str(
        scientific.get("object_kind") or (object_match.group(1) if object_match else "data.frame")
    ).strip()
    modality = str(scientific.get("modality") or "tabular").strip()
    representation = str(scientific.get("representation") or object_kind).strip()
    return {
        "rows": rows,
        "columns": column_count,
        "object_kind": object_kind,
        "modality": modality,
        "representation": representation,
        "processing_stage": str(scientific.get("processing_stage") or "analysis_ready"),
        "scientific": scientific,
        "profile_revision": profile_revision,
        "profile_scope": "sample" if sampled else "full",
        "profile_note": (
            "画像基于前 2000 行抽样：缺失率与唯一值数为抽样估计，非全量统计。"
            if sampled
            else ""
        ),
        "overall_missing_pct": overall_missing_pct,
        "missing_columns": missing_columns,
        "type_counts": type_counts,
        "variables": normalized_columns,
        "local_semantics": {"levels": local_levels},
        "overview": overview,
    }
