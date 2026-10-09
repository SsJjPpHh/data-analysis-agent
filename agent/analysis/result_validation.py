"""Validate execution ownership and server-verified analysis-plan deliverables."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from agent.analysis.models import AnalysisPlan


_DISPOSITION_TIERS = {"recommended", "conditional", "diagnostic", "sensitivity"}
_TERMINAL_DISPOSITIONS = {"not_applicable", "limited", "declined"}
_RECEIPT_SCHEMA_VERSION = 3
_SUPPORTED_RECEIPT_SCHEMA_VERSIONS = frozenset({1, 2, _RECEIPT_SCHEMA_VERSION})
_RECEIPT_VALIDATOR = "data-analysis-agent"
_HASH_CHUNK_BYTES = 1024 * 1024
_STRUCTURE_HEAD_BYTES = 256 * 1024
_STRUCTURE_TAIL_BYTES = 8 * 1024
_TABLE_SAMPLE_ROWS = 128
_ASCII_WHITESPACE = b" \t\r\n\v\f\x00"
_RECEIPT_KINDS = {"table", "figure", "text", "model", "artifact"}


def merge_diagnostic_statuses(
    *reports: Mapping[str, Any], warnings: list[str] | None = None,
) -> dict[str, str]:
    """Keep the most severe declaration within one frozen analysis plan."""
    severity = {"passed": 0, "not_assessed": 1, "warning": 2, "failed": 3}
    declarations: dict[str, set[str]] = {}
    for report in reports:
        if not isinstance(report, Mapping):
            continue
        for name, value in report.items():
            status = str(value or "").strip().lower()
            if status in severity:
                declarations.setdefault(str(name), set()).add(status)
    merged = {name: max(statuses, key=severity.get) for name, statuses in declarations.items()}
    if warnings is not None:
        for name, statuses in declarations.items():
            if len(statuses) > 1:
                message = f"诊断声明冲突：{name}（{' / '.join(sorted(statuses, key=severity.get))}）；保留 {merged[name]}，请核对对应产物。"
                if message not in warnings:
                    warnings.append(message)
    return merged


def _normalized_header(value: object) -> str:
    """Normalize one bounded table header for conservative semantic checks."""

    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").casefold())


def _table_semantic_checks(header: list[str], sampled_data_rows: int) -> dict[str, bool]:
    """Derive bounded, non-claim-based evidence from a CSV/TSV header."""

    columns = [_normalized_header(item) for item in header]
    identifier = {
        "variable", "variables", "characteristic", "characteristics", "term",
        "predictor", "covariate", "feature", "parameter", "变量", "特征",
        "指标", "协变量", "参数", "因素",
        "comparison", "contrast", "outcome", "endpoint", "effectmeasure",
        "比较", "对比", "结局", "终点", "效应指标", "效应度量", "效应量类型",
    }
    overall = {"overall", "total", "all", "总体", "总计", "合计", "全部"}
    p_value = {"p", "pvalue", "pval", "pvalues", "p值", "概率值"}
    smd = {
        "smd", "standardizeddifference", "standardizedmeandifference",
        "standardiseddifference", "standardisedmeandifference", "标准化差异",
        "标准化均数差",
    }
    estimate = {
        "estimate", "effect", "effectestimate", "coefficient", "coef", "beta",
        "oddsratio", "or", "hazardratio", "hr", "shr", "subdistributionhazardratio", "riskratio", "relativerisk",
        "rr", "riskdifference", "rd", "效应", "效应量", "估计", "估计值",
        "系数", "比值比", "风险比",
    }
    lower = {"lower", "lowerci", "conflow", "lcl", "cilower", "ci下限", "下限", "置信下限"}
    upper = {"upper", "upperci", "confhigh", "ucl", "ciupper", "ci上限", "上限", "置信上限"}
    uncertainty = {
        "ci", "confidenceinterval", "standarderror", "stderr", "se", "secoef", "robustse", "置信区间", "标准误",
    }
    diagnostic_measure = {
        "diagnostic", "diagnosticname", "test", "testname", "assumption", "metric",
        "check", "诊断", "检验", "假设", "诊断项", "指标",
    }
    diagnostic_result = {
        "statistic", "value", "result", "status", "decision", "conclusion",
        "统计量", "值", "结果", "状态", "结论",
    }

    has_identifier = any(value in identifier for value in columns)
    has_overall = any(
        value in overall or value.startswith(("overalln", "totaln", "总体n", "总计n"))
        for value in columns
    )
    has_p_value = any(value in p_value or value.startswith("pvalue") for value in columns)
    has_smd = any(
        value in smd or value.startswith("smd") or "standardizeddifference" in value
        for value in columns
    )
    has_estimate = any(
        value in estimate or value.startswith(("estimate", "effectestimate", "coefficient"))
        for value in columns
    )
    has_lower = any(value in lower or value.startswith(("lowerci", "cilower")) for value in columns)
    has_upper = any(value in upper or value.startswith(("upperci", "ciupper")) for value in columns)
    has_uncertainty = (has_lower and has_upper) or any(
        value in uncertainty
        or value in {"95ci", "90ci", "99ci"}
        or "confidenceinterval" in value
        for value in columns
    )
    has_diagnostic_measure = any(
        value in diagnostic_measure
        or value.startswith(("diagnostic", "testname", "assumption"))
        for value in columns
    )
    has_diagnostic_result = has_p_value or any(
        value in diagnostic_result or value.startswith(("statistic", "result", "status"))
        for value in columns
    )

    auxiliary = {"level", "category", "missing", "missingn", "n", "样本量", "水平", "类别", "缺失"}
    group_tokens = (
        "group", "arm", "treatment", "control", "exposed", "unexposed",
        "组", "病例", "对照", "干预",
    )
    token_group_columns = [
        value
        for value in columns
        if any(token in value for token in group_tokens)
    ]
    unclassified_columns = [
        value
        for value in columns
        if value
        and value not in identifier | auxiliary
        and not (
            value in overall | p_value | smd | estimate | lower | upper | uncertainty
            or value.startswith(("overalln", "totaln", "总体n", "总计n", "pvalue", "smd"))
        )
    ]
    return {
        "has_data_rows": sampled_data_rows > 0,
        "has_identifier": has_identifier,
        "has_overall": has_overall,
        "has_group_comparison": len(set(token_group_columns)) >= 2
        or (has_overall and len(set(unclassified_columns)) >= 2),
        "has_p_value": has_p_value,
        "has_smd": has_smd,
        "has_effect_estimate": has_estimate,
        "has_uncertainty": has_uncertainty,
        "has_diagnostic_measure": has_diagnostic_measure,
        "has_diagnostic_result": has_diagnostic_result,
    }


def _text_semantic_checks(snapshot: Mapping[str, Any]) -> dict[str, bool]:
    try:
        text = bytes(snapshot["head"]).decode("utf-8-sig").casefold()
    except UnicodeDecodeError:
        text = ""
    return {
        "has_result_interpretation": any(
            token in text for token in ("result", "effect", "estimate", "结果", "效应", "估计")
        ),
        "has_uncertainty": any(
            token in text
            for token in ("confidence interval", "uncertaint", "p value", "置信区间", "不确定", "p 值")
        ),
        "has_limitations": any(
            token in text for token in ("limitation", "applicab", "限制", "局限", "适用")
        ),
        "has_next_step": any(
            token in text for token in ("next step", "recommend", "further", "下一步", "建议", "进一步")
        ),
    }


class ArtifactValidationError(ValueError):
    """Raised when a runtime artifact cannot produce a trusted receipt."""


def _evidence_items(value: object) -> set[str]:
    if isinstance(value, dict):
        values = [key for key, accepted in value.items() if accepted]
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    elif value is None:
        values = []
    else:
        values = [value]
    return {" ".join(str(item).split()).casefold() for item in values if str(item).strip()}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt_bindings(
    metadata: Mapping[str, Any],
    *,
    schema_version: int = _RECEIPT_SCHEMA_VERSION,
) -> dict[str, str]:
    bindings = {
        "dataset_id": str(metadata.get("dataset_id") or ""),
        "workspace_generation": str(metadata.get("workspace_generation") or ""),
        "job_id": str(metadata.get("job_id") or ""),
        "source_execution_status": str(metadata.get("source_execution_status") or ""),
        "plan_id": str(metadata.get("plan_id") or ""),
        "role": str(metadata.get("role") or metadata.get("output_role") or "").strip(),
        "semantic_key": str(metadata.get("semantic_key") or "").strip(),
        "portfolio_status": str(metadata.get("portfolio_status") or "").strip().lower(),
        "portfolio_reason": str(metadata.get("portfolio_reason") or "").strip(),
        "declared_acceptance_evidence_sha256": _canonical_hash(
            sorted(_evidence_items(metadata.get("acceptance_evidence")))
        ),
    }
    if schema_version >= 2:
        bindings["variable_bindings_sha256"] = _canonical_hash(
            dict(metadata.get("variable_bindings") or {})
            if isinstance(metadata.get("variable_bindings"), Mapping)
            else {}
        )
    if schema_version >= 3:
        bindings["resolved_inputs_sha256"] = _canonical_hash(
            dict(metadata.get("resolved_inputs") or {})
            if isinstance(metadata.get("resolved_inputs"), Mapping)
            else {}
        )
    return bindings


def _read_regular_file(path: Path) -> dict[str, Any]:
    try:
        before = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise ArtifactValidationError("artifact file does not exist") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ArtifactValidationError("artifact file must not be a symbolic link")
    if not stat.S_ISREG(before.st_mode):
        raise ArtifactValidationError("artifact file must be a regular file")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise ArtifactValidationError("artifact file could not be opened safely") from exc

    digest = hashlib.sha256()
    head = bytearray()
    tail = bytearray()
    has_non_whitespace = False
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ArtifactValidationError("artifact file must be a regular file")
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ArtifactValidationError("artifact file changed while it was opened")
        while True:
            chunk = os.read(descriptor, _HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            if len(head) < _STRUCTURE_HEAD_BYTES:
                head.extend(chunk[: _STRUCTURE_HEAD_BYTES - len(head)])
            if len(chunk) >= _STRUCTURE_TAIL_BYTES:
                tail = bytearray(chunk[-_STRUCTURE_TAIL_BYTES:])
            else:
                tail.extend(chunk)
                if len(tail) > _STRUCTURE_TAIL_BYTES:
                    del tail[: len(tail) - _STRUCTURE_TAIL_BYTES]
            if not has_non_whitespace and chunk.strip(_ASCII_WHITESPACE):
                has_non_whitespace = True
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise ArtifactValidationError("artifact file changed during validation")
    try:
        final = os.lstat(path)
    except (OSError, ValueError) as exc:
        raise ArtifactValidationError("artifact file disappeared during validation") from exc
    if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
        raise ArtifactValidationError("artifact file changed during validation")
    identity_final = (
        final.st_dev,
        final.st_ino,
        final.st_size,
        final.st_mtime_ns,
    )
    if identity_before != identity_final:
        raise ArtifactValidationError("artifact file changed during validation")
    if int(after.st_size) <= 0:
        raise ArtifactValidationError("artifact file must be non-empty")
    return {
        "size_bytes": int(after.st_size),
        "sha256": digest.hexdigest(),
        "head": bytes(head),
        "tail": bytes(tail),
        "has_non_whitespace": has_non_whitespace,
        "file_identity": {
            "device": int(after.st_dev),
            "inode": int(after.st_ino),
            "mtime_ns": int(after.st_mtime_ns),
        },
    }


def _artifact_path_within_root(
    path: str | os.PathLike[str],
    artifacts_root: str | os.PathLike[str],
) -> tuple[Path, str]:
    root = Path(os.fspath(artifacts_root))
    candidate = Path(os.fspath(path))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        relative = resolved_candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArtifactValidationError("artifact file is outside its run output directory") from exc
    if not relative.parts:
        raise ArtifactValidationError("artifact path must identify a file inside the run output directory")
    return candidate, relative.as_posix()


def _complete_csv_prefix(data: bytes, *, truncated: bool) -> bytes:
    if not truncated:
        return data
    in_quotes = False
    last_record_boundary = -1
    index = 0
    while index < len(data):
        value = data[index]
        if value == 34:
            if in_quotes and index + 1 < len(data) and data[index + 1] == 34:
                index += 2
                continue
            in_quotes = not in_quotes
        elif value in {10, 13} and not in_quotes:
            last_record_boundary = index + 1
        index += 1
    if last_record_boundary < 0:
        raise ArtifactValidationError("table header exceeds the bounded validation sample")
    return data[:last_record_boundary]


def _table_structure(path: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        delimiter = ","
        format_name = "csv"
    elif suffix in {".tsv", ".tab"}:
        delimiter = "\t"
        format_name = "tsv"
    else:
        raise ArtifactValidationError("table artifact must use CSV or TSV format")
    head = bytes(snapshot["head"])
    if b"\x00" in head:
        raise ArtifactValidationError("table artifact must not contain NUL bytes")
    sample = _complete_csv_prefix(
        head,
        truncated=int(snapshot["size_bytes"]) > len(head),
    )
    try:
        decoded = sample.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArtifactValidationError("table artifact must be valid UTF-8 CSV or TSV") from exc

    reader = csv.reader(io.StringIO(decoded, newline=""), delimiter=delimiter, strict=True)
    header: list[str] | None = None
    sampled_data_rows = 0
    try:
        for row in reader:
            if not row or not any(cell.strip() for cell in row):
                continue
            if header is None:
                header = row
                if not any(cell.strip() for cell in header):
                    raise ArtifactValidationError("table artifact must have a parseable header")
                continue
            if len(row) != len(header):
                raise ArtifactValidationError("table rows must have a consistent column count")
            sampled_data_rows += 1
            if sampled_data_rows >= _TABLE_SAMPLE_ROWS:
                break
    except csv.Error as exc:
        raise ArtifactValidationError("table artifact is not parseable CSV or TSV") from exc
    if header is None:
        raise ArtifactValidationError("table artifact must have a parseable header")
    return {
        "valid": True,
        "format": format_name,
        "column_count": len(header),
        "header_sha256": _canonical_hash(header),
        "sampled_data_rows": sampled_data_rows,
        "sample_bounded": (
            int(snapshot["size_bytes"]) > len(head)
            or sampled_data_rows >= _TABLE_SAMPLE_ROWS
        ),
        "semantic_checks": _table_semantic_checks(header, sampled_data_rows),
    }


def _figure_structure(path: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    suffix = path.suffix.casefold()
    head = bytes(snapshot["head"])
    tail = bytes(snapshot["tail"])
    valid = False
    format_name = suffix.removeprefix(".")
    if suffix == ".png":
        valid = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        format_name = "jpeg"
        valid = head.startswith(b"\xff\xd8\xff")
    elif suffix == ".gif":
        valid = head.startswith((b"GIF87a", b"GIF89a"))
    elif suffix == ".webp":
        valid = len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    elif suffix in {".tif", ".tiff"}:
        format_name = "tiff"
        valid = head.startswith((b"II*\x00", b"MM\x00*"))
    elif suffix == ".pdf":
        valid = b"%PDF-" in head[:1024] and b"%%EOF" in tail
    elif suffix == ".svg":
        try:
            parser = ET.XMLPullParser(events=("start",))
            parser.feed(head.decode("utf-8-sig"))
            root = next((element for _event, element in parser.read_events()), None)
            if root is not None:
                valid = str(root.tag).rsplit("}", 1)[-1].casefold() == "svg"
        except (UnicodeDecodeError, ET.ParseError):
            valid = False
    else:
        raise ArtifactValidationError("figure artifact uses an unsupported format")
    if not valid:
        raise ArtifactValidationError("figure artifact signature does not match its format")
    return {"valid": True, "format": format_name}


def _structure_summary(kind: str, path: Path, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if kind == "table":
        return _table_structure(path, snapshot)
    if kind == "figure":
        return _figure_structure(path, snapshot)
    if kind in {"text", "model"}:
        if not bool(snapshot["has_non_whitespace"]):
            raise ArtifactValidationError(f"{kind} artifact must contain non-whitespace content")
        return {
            "valid": True,
            "format": "text",
            "non_whitespace": True,
            "semantic_checks": _text_semantic_checks(snapshot),
        }
    return {
        "valid": True,
        "format": path.suffix.casefold().removeprefix(".") or "regular_file",
    }


def build_artifact_validation_receipt(
    path: str | os.PathLike[str],
    *,
    kind: str,
    metadata: Mapping[str, Any],
    source_tool: str = "run_r_code",
    job_id: str = "",
    artifacts_root: str | os.PathLike[str] | None = None,
    execution_succeeded: bool = True,
) -> dict[str, Any]:
    """Hash and structurally inspect one artifact with bounded memory."""

    normalized_kind = str(kind or "").strip().casefold()
    normalized_source = str(source_tool or "").strip()
    if normalized_source != "run_r_code":
        raise ArtifactValidationError("artifact receipt source must be run_r_code")
    if normalized_kind not in _RECEIPT_KINDS:
        raise ArtifactValidationError("artifact receipt kind is unsupported")
    normalized_job_id = str(job_id or metadata.get("job_id") or "").strip()
    relative_path = ""
    if artifacts_root is not None:
        if not normalized_job_id:
            raise ArtifactValidationError("artifact receipt must identify its run_r_code job")
        artifact_path, relative_path = _artifact_path_within_root(path, artifacts_root)
    else:
        artifact_path = Path(os.fspath(path))
    snapshot = _read_regular_file(artifact_path)
    try:
        canonical_path_sha256 = hashlib.sha256(
            str(artifact_path.resolve(strict=True)).encode("utf-8")
        ).hexdigest()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ArtifactValidationError("artifact file disappeared during validation") from exc
    body: dict[str, Any] = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "validator": _RECEIPT_VALIDATOR,
        "status": "accepted",
        "source_tool": normalized_source,
        "job_id": normalized_job_id,
        "execution_status": "succeeded" if execution_succeeded else "failed",
        "relative_path": relative_path,
        "canonical_path_sha256": canonical_path_sha256,
        "kind": normalized_kind,
        "size_bytes": snapshot["size_bytes"],
        "bytes_hashed": snapshot["size_bytes"],
        "sha256": snapshot["sha256"],
        "file_identity": snapshot["file_identity"],
        "structure": _structure_summary(normalized_kind, artifact_path, snapshot),
        "bindings": _receipt_bindings(metadata),
    }
    body["receipt_sha256"] = _canonical_hash(body)
    return body


def _artifact_kind(artifact: Mapping[str, Any]) -> str:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    return str(
        artifact.get("type")
        or artifact.get("kind")
        or metadata.get("kind")
        or ""
    ).strip().casefold()


def artifact_has_trusted_receipt(
    artifact: Mapping[str, Any],
    *,
    expected_kind: str = "",
    verify_file: bool = False,
) -> bool:
    """Return whether an artifact is bound to an intact server receipt and file."""

    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    receipt = metadata.get("artifact_validation_receipt")
    if not isinstance(receipt, Mapping):
        return False
    actual_kind = _artifact_kind(artifact)
    normalized_expected = str(expected_kind or "").strip().casefold()
    if normalized_expected and actual_kind != normalized_expected:
        return False
    if str(artifact.get("source_tool") or "") != "run_r_code":
        return False
    if str(metadata.get("source_tool") or "") != "run_r_code":
        return False
    artifact_job_id = str(artifact.get("job_id") or "").strip()
    metadata_job_id = str(metadata.get("job_id") or "").strip()
    if not artifact_job_id or artifact_job_id != metadata_job_id:
        return False
    if str(metadata.get("source_execution_status") or "") != "succeeded":
        return False
    schema_version = receipt.get("schema_version")
    if (
        schema_version not in _SUPPORTED_RECEIPT_SCHEMA_VERSIONS
        or receipt.get("validator") != _RECEIPT_VALIDATOR
        or receipt.get("status") != "accepted"
        or receipt.get("source_tool") != "run_r_code"
        or receipt.get("job_id") != artifact_job_id
        or receipt.get("execution_status") != "succeeded"
        or str(receipt.get("kind") or "") != actual_kind
    ):
        return False
    relative_path = str(receipt.get("relative_path") or "")
    relative = PurePosixPath(relative_path)
    if not relative_path or relative.is_absolute() or ".." in relative.parts:
        return False
    try:
        size_bytes = int(receipt.get("size_bytes") or 0)
    except (TypeError, ValueError):
        return False
    file_hash = str(receipt.get("sha256") or "")
    if size_bytes <= 0 or len(file_hash) != 64:
        return False
    try:
        if int(receipt.get("bytes_hashed") or 0) != size_bytes:
            return False
    except (TypeError, ValueError):
        return False
    try:
        int(file_hash, 16)
    except ValueError:
        return False
    try:
        if int(metadata.get("artifact_size_bytes") or 0) != size_bytes:
            return False
    except (TypeError, ValueError):
        return False
    if str(metadata.get("artifact_sha256") or "") != file_hash:
        return False
    structure = receipt.get("structure")
    if not isinstance(structure, Mapping) or structure.get("valid") is not True:
        return False
    if schema_version == 1 and metadata.get("variable_bindings"):
        # A legacy receipt never committed to frozen variable bindings. Keep
        # genuinely old artifacts readable, but never let v1 authenticate
        # binding metadata that was added later.
        return False
    if schema_version < 3 and metadata.get("resolved_inputs"):
        # v1/v2 did not authenticate the complete typed-input contract.
        return False
    if receipt.get("bindings") != _receipt_bindings(metadata, schema_version=schema_version):
        return False
    receipt_body = dict(receipt)
    supplied_receipt_hash = str(receipt_body.pop("receipt_sha256", ""))
    if supplied_receipt_hash != _canonical_hash(receipt_body):
        return False

    path = str(artifact.get("path") or "").strip()
    if not path:
        return False
    try:
        canonical_path_sha256 = hashlib.sha256(
            str(Path(path).resolve(strict=True)).encode("utf-8")
        ).hexdigest()
    except (OSError, RuntimeError, ValueError):
        return False
    if receipt.get("canonical_path_sha256") != canonical_path_sha256:
        return False
    try:
        current = os.lstat(path)
    except (OSError, ValueError):
        return False
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        return False
    identity = receipt.get("file_identity")
    if not isinstance(identity, Mapping):
        return False
    if (
        int(current.st_size) != size_bytes
        or int(identity.get("device", -1)) != int(current.st_dev)
        or int(identity.get("inode", -1)) != int(current.st_ino)
        or int(identity.get("mtime_ns", -1)) != int(current.st_mtime_ns)
    ):
        return False
    if verify_file:
        artifact_root = Path(path)
        for _part in relative.parts:
            artifact_root = artifact_root.parent
        try:
            verified = build_artifact_validation_receipt(
                path,
                kind=actual_kind,
                metadata=metadata,
                source_tool="run_r_code",
                job_id=artifact_job_id,
                artifacts_root=artifact_root,
                execution_succeeded=True,
            )
        except (ArtifactValidationError, OSError, ValueError):
            return False
        if schema_version == 1:
            verified["schema_version"] = 1
            verified["bindings"] = _receipt_bindings(metadata, schema_version=1)
            verified_body = dict(verified)
            verified_body.pop("receipt_sha256", None)
            verified["receipt_sha256"] = _canonical_hash(verified_body)
        if verified != dict(receipt):
            return False
    return True


def artifact_satisfies_output_contract(
    artifact: Mapping[str, Any],
    *,
    kind: str,
    semantic_key: str = "",
    acceptance: tuple[str, ...] | list[str] = (),
) -> bool:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), Mapping) else {}
    if not artifact_has_trusted_receipt(artifact, expected_kind=kind):
        return False
    actual_semantic_key = str(metadata.get("semantic_key") or "").strip()
    if semantic_key and actual_semantic_key != semantic_key:
        return False
    receipt = metadata.get("artifact_validation_receipt")
    structure = receipt.get("structure") if isinstance(receipt, Mapping) else None
    if not isinstance(structure, Mapping):
        return False
    raw_checks = structure.get("semantic_checks")
    semantic_checks = dict(raw_checks) if isinstance(raw_checks, Mapping) else {}
    role = str(metadata.get("role") or metadata.get("output_role") or "").strip().casefold()
    identity = f"{role} {actual_semantic_key or semantic_key or role}".casefold()

    baseline_checks = ["has_data_rows", "has_identifier", "has_overall"]
    if "总体描述" not in acceptance:
        baseline_checks.extend(("has_group_comparison", "has_p_value", "has_smd"))
    if "baseline_table" in identity and not all(
        semantic_checks.get(item) is True
        for item in baseline_checks
    ):
        return False
    if any(
        token in identity
        for token in (
            "effect_estimate", "effect_table", "effect_estimates", "causal_effect",
            "epidemiologic_measure", "pooled_estimate",
        )
    ) and not all(
        semantic_checks.get(item) is True
        for item in ("has_data_rows", "has_identifier", "has_effect_estimate", "has_uncertainty")
    ):
        return False
    if ("diagnostic" in identity or "assumption" in identity) and kind == "table" and not all(
        semantic_checks.get(item) is True
        for item in ("has_data_rows", "has_diagnostic_measure", "has_diagnostic_result")
    ):
        return False

    for item in acceptance:
        clause = " ".join(str(item).split()).casefold()
        compact = _normalized_header(clause)
        if not clause:
            continue
        if "smd" in compact or "标准化差异" in compact or "标准化均数差" in compact:
            if semantic_checks.get("has_smd") is not True:
                return False
        elif "p值" in compact or "pvalue" in compact or "pval" in compact:
            if semantic_checks.get("has_p_value") is not True:
                return False
        elif compact == "总体描述":
            if semantic_checks.get("has_overall") is not True:
                return False
        elif "总体" in compact and "分组" in compact:
            if not (
                semantic_checks.get("has_overall") is True
                and semantic_checks.get("has_group_comparison") is True
            ):
                return False
        elif "效应量" in compact or "uncertaint" in clause or "不确定性" in compact:
            if not any(
                semantic_checks.get(name) is True
                for name in ("has_effect_estimate", "has_smd", "has_uncertainty")
            ):
                return False
        elif any(token in compact for token in ("导出", "export", "csv", "xlsx", "docx")):
            if kind != "table" or structure.get("format") not in {"csv", "tsv"}:
                return False
        elif "解释主要结果" in compact or "效应方向" in compact:
            if not all(
                semantic_checks.get(name) is True
                for name in ("has_result_interpretation", "has_uncertainty")
            ):
                return False
        elif any(token in compact for token in ("适用范围", "数据限制", "下一步分析")):
            if not all(
                semantic_checks.get(name) is True
                for name in ("has_limitations", "has_next_step")
            ):
                return False
        else:
            # Self-declared or currently unverifiable clauses must remain partial.
            return False
    return True


def _artifact_satisfies_output(artifact: dict, output) -> bool:
    return artifact_satisfies_output_contract(
        artifact,
        kind=output.kind,
        semantic_key=output.semantic_key or output.role,
        acceptance=output.acceptance,
    )


@dataclass(frozen=True)
class ResultAssessment:
    complete: bool
    r_executed: bool
    missing_roles: tuple[str, ...]
    missing_diagnostics: tuple[str, ...]
    warnings: tuple[str, ...]
    invalid_roles: tuple[str, ...] = ()
    disposition_roles: tuple[str, ...] = ()
    outstanding_roles: tuple[str, ...] = ()
    next_action: str = "complete"
    diagnostic_quality: str = "not_assessed"
    failed_diagnostics: tuple[str, ...] = ()
    warning_diagnostics: tuple[str, ...] = ()
    validation_scope: str = "artifact_structure"
    scientific_review_status: str = "not_reviewed"


class ResultValidator:
    """Check plan fulfillment without recomputing or judging statistics."""

    def validate(
        self,
        plan: AnalysisPlan,
        execution: dict,
        artifacts: list[dict],
    ) -> ResultAssessment:
        if str(execution.get("dataset_id") or "") != plan.dataset_id:
            raise ValueError("Execution does not belong to the planned dataset")
        execution_plan_id = str(execution.get("plan_id") or "")
        if execution_plan_id and execution_plan_id != plan.plan_id:
            raise ValueError("Execution does not belong to the active analysis plan")

        r_executed = execution.get("status") == "succeeded" or execution.get("ok") is True
        owned = [
            item
            for item in artifacts
            if str((item.get("metadata") or {}).get("dataset_id") or "") == plan.dataset_id
            and str((item.get("metadata") or {}).get("plan_id") or "") == plan.plan_id
        ]
        artifacts_by_role: dict[str, list[dict]] = {}
        for item in owned:
            role = str((item.get("metadata") or {}).get("role") or "").strip()
            if role:
                artifacts_by_role.setdefault(role, []).append(item)

        missing: list[str] = []
        invalid: list[str] = []
        dispositions: list[str] = []
        for output in plan.output_portfolio:
            if not output.required:
                continue
            accepted_roles = tuple(dict.fromkeys((output.role, *output.legacy_roles)))
            candidates = [
                artifact
                for role in accepted_roles
                for artifact in artifacts_by_role.get(role, [])
            ]
            if not candidates:
                missing.append(output.role)
                continue
            accepted = False
            disposed = False
            for artifact in candidates:
                metadata = artifact.get("metadata") or {}
                if not artifact_has_trusted_receipt(artifact):
                    continue
                status = str(metadata.get("portfolio_status") or "").strip().lower()
                reason = str(metadata.get("portfolio_reason") or "").strip()
                if status in _TERMINAL_DISPOSITIONS and reason and output.tier in _DISPOSITION_TIERS:
                    disposed = True
                    accepted = True
                    break
                if status:
                    continue
                if _artifact_satisfies_output(artifact, output):
                    accepted = True
                    break
            if disposed:
                dispositions.append(output.role)
            elif not accepted:
                invalid.append(output.role)

        missing_roles = tuple(sorted(set(missing)))
        invalid_roles = tuple(sorted(set(invalid)))
        diagnostic_status = execution.get("diagnostic_status") or {}
        if not isinstance(diagnostic_status, dict):
            diagnostic_status = {}
        missing_diagnostics = tuple(
            name
            for name in plan.diagnostics
            if str(diagnostic_status.get(name) or "") not in {"passed", "warning", "failed", "not_assessed"}
        )
        warnings: list[str] = [str(item) for item in execution.get("diagnostic_conflicts") or []]
        failed_diagnostics = tuple(name for name in plan.diagnostics if diagnostic_status.get(name) == "failed")
        warning_diagnostics = tuple(name for name in plan.diagnostics if diagnostic_status.get(name) == "warning")
        unassessed = tuple(name for name in plan.diagnostics if diagnostic_status.get(name) == "not_assessed")
        if unassessed:
            warnings.append("诊断未评估：" + "、".join(unassessed))
        if failed_diagnostics:
            warnings.append("方法诊断未通过：" + "、".join(failed_diagnostics) + "；产物齐全不代表结论可靠。")
        if warning_diagnostics:
            warnings.append("方法诊断需注意：" + "、".join(warning_diagnostics))
        if not r_executed:
            warnings.append("R 执行未成功")
        if missing_roles:
            warnings.append(f"缺少产物：{', '.join(missing_roles)}")
        if invalid_roles:
            warnings.append(f"产物类型、语义或服务器校验不符：{', '.join(invalid_roles)}")
        if missing_diagnostics:
            warnings.append(f"缺少诊断：{', '.join(missing_diagnostics)}")
        complete = r_executed and not missing_roles and not invalid_roles and not missing_diagnostics
        if not r_executed:
            next_action = "retry"
        elif invalid_roles:
            next_action = "repair"
        elif missing_roles or missing_diagnostics:
            next_action = "continue"
        else:
            next_action = "complete"
        outstanding_roles = tuple(sorted(set((*missing_roles, *invalid_roles))))
        return ResultAssessment(
            complete=complete,
            r_executed=r_executed,
            missing_roles=missing_roles,
            missing_diagnostics=missing_diagnostics,
            warnings=tuple(warnings),
            invalid_roles=invalid_roles,
            disposition_roles=tuple(sorted(set(dispositions))),
            outstanding_roles=outstanding_roles,
            next_action=next_action,
            diagnostic_quality=(
                "failed" if failed_diagnostics else "warning" if warning_diagnostics
                else "incomplete" if missing_diagnostics else "not_assessed" if unassessed else "passed" if plan.diagnostics else "not_assessed"
            ),
            failed_diagnostics=failed_diagnostics,
            warning_diagnostics=warning_diagnostics,
        )
