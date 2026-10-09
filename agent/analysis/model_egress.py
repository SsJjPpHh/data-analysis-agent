"""Model-only sharing views. Local analysis inputs and artifacts are immutable.

This is a configurable minimization boundary, not an assertion that arbitrary
text or user-authorized images have been anonymized. Apply before compaction
and again before each provider request; historical results obey today's policy.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
from typing import Any

from infrastructure.public_projection import redact_filesystem_paths
from .model_sharing import ModelSharingPolicy


def _key(value: str) -> str:
    return re.sub(r"[\s_.\-]+", "", str(value).casefold())


_IDENTIFIERS = {_key(x) for x in (
    "id", "subject_id", "patient_id", "participant_id", "sample_id", "sample",
    "donor_id", "observation_id", "cell_id", "spot_id", "barcode", "record_id",
    "individual_id", "person_id", "case_id", "name", "patient_name", "full_name",
    "email", "phone", "mobile", "address", "身份证", "身份证号", "姓名", "电话",
    "手机号", "住址", "患者编号", "受试者编号", "样本编号", "样本ID", "个体ID",
)}
_INDIVIDUAL_HEADERS = {_key(x) for x in (
    "prediction", "predicted", "predicted_probability", "fitted", "residual",
    "shap_value", "factor_score", "PC1", "UMAP1", "tSNE1", "Dim.1",
)}
_SUMMARY_MEASURES = {_key(x) for x in (
    "estimate", "coefficient", "coef", "p_value", "p.value", "p", "se",
    "std_error", "statistic", "test", "metric", "auc", "n", "count", "total",
    "effect", "rr", "or", "hr", "loading", "mean", "median", "sd", "variance",
    "proportion", "percent", "percentage", "frequency", "mean_abs_shap",
    "accuracy", "sensitivity", "specificity", "precision", "recall", "f1",
    "rmse", "mae", "mse", "r_squared", "df", "power", "alpha", "c_index", "aic", "bic", "loglik",
    "估计", "系数", "样本量", "样本数", "人数", "统计量", "指标", "均值", "中位数", "标准差",
    "估计值", "效应", "效应量", "回归系数", "稳健效应", "稳健系数", "标准误", "稳健标准误",
    "P值", "调整P值", "校正P值",
)}
_SUMMARY_LABELS = {_key(x) for x in (
    "term", "variable", "group", "arm", "feature", "method", "model", "outcome",
    "level", "category", "comparison", "contrast", "component", "factor",
    "lower", "upper", "conf_low", "conf_high", "ci_lower", "ci_upper", "ci_low", "ci_high",
    "变量", "组别", "方法", "模型", "水平", "类别", "下限", "上限",
    "变量名", "比较", "对比", "结局", "终点", "成分", "因子",
    "CI下限", "CI上限", "95%CI下限", "95%CI上限", "稳健CI下限", "稳健CI上限",
)}
_INFERENCE_VALUES = {_key(x) for x in (
    "estimate", "coefficient", "coef", "effect", "rr", "or", "hr", "se", "std_error",
    "估计", "估计值", "系数", "效应", "效应量", "回归系数", "稳健效应", "稳健系数", "标准误", "稳健标准误",
)}
_P_VALUES = {_key(x) for x in ("p", "p_value", "P值", "调整P值", "校正P值")}
_LOWER_CI = {_key(x) for x in (
    "lower", "conf_low", "ci_lower", "ci_low", "下限", "CI下限", "95%CI下限", "稳健CI下限",
)}
_UPPER_CI = {_key(x) for x in (
    "upper", "conf_high", "ci_upper", "ci_high", "上限", "CI上限", "95%CI上限", "稳健CI上限",
)}
_TERM_LABELS = {_key(x) for x in ("term", "variable", "feature", "变量", "变量名")}
_SCALE_ITEM_LABELS = {_key(x) for x in ("条目", "题项", "item", "item_name")}
_SCALE_COMMUNALITIES = {_key(x) for x in ("共同度", "communality", "communalities")}
_SCALE_RELIABILITY = {_key(x) for x in ("整体alpha", "overall_alpha", "Cronbach_alpha")}
_SCALE_EXTERNAL_CORRELATION = {_key(x) for x in ("外部总分相关", "external_total_correlation")}
_FACTOR_LOADING_COLUMN = re.compile(r"pa[1-9]\d*")
_INDIVIDUAL_LABEL = re.compile(
    r"^(?:synthetic[_ -]?)?(?:subject|patient|participant|sample|donor|cell|spot|record|case|individual|person)"
    r"[_ -]*(?:id[_ -]*)?(?:\d|private|confirmed)|^(?:受试者|患者|个体|样本)(?:编号|ID)?[_ -]*\d",
    re.I,
)
_INDIVIDUAL_TABLE = re.compile(
    r"(?:individual|per[_ -]?(?:sample|patient|cell)|sample[_ -]?score|shap[_ -]?values|coordinate|个体|逐样本|逐患者)",
    re.I,
)
_RAW_KEYS = {"sample", "samples", "sample_rows", "raw_sample", "raw_values", "head", "records"}
_ENGINE_KEYS = {"stdout", "stderr", "code", "execution_snapshot", "reproducibility",
                "path", "stored_path", "file_path", "artifacts_dir", "workdir", "cwd"}
_INDIVIDUAL_COLLECTIONS = {_key(x) for x in (
    "predictions", "fitted_values", "residuals", "shap_values", "coordinates",
    "sample_scores", "cell_scores", "embeddings", "individuals", "points",
)}
_STRUCTURAL_COLLECTIONS = {
    "variables", "columns", "variable_index", "tables", "texts", "figures", "artifacts", "models",
    "required_outputs", "diagnostics", "assumptions", "prerequisites", "packages", "results",
    "stages", "events", "steps", "checks", "role_candidates", "requirements", "missing_outputs",
}
_AGGREGATE_VECTORS = {_key(x) for x in ("ci", "confidence_interval", "confint", "quantiles", "counts", "histogram", "breaks")}
_NUMERIC = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_WITHHELD_HISTORY = "[旧科研结果预览不完整，未发送；完整产物保留在本机]"


def _item_factor_schema(keys: list[str]) -> bool:
    """Identify a narrow item-level factor summary, never coordinate rows.

    PA loading names are dynamic, so permit them only alongside an item label
    and communalities. All other columns must be explicit scale aggregates.
    A role/title does not supply any of this evidence.
    """
    allowed = _SCALE_ITEM_LABELS | _SCALE_COMMUNALITIES | _SCALE_RELIABILITY | _SCALE_EXTERNAL_CORRELATION
    return (
        len(set(keys) & _SCALE_ITEM_LABELS) == 1
        and bool(set(keys) & _SCALE_COMMUNALITIES)
        and any(_FACTOR_LOADING_COLUMN.fullmatch(key) for key in keys)
        and all(key in allowed or _FACTOR_LOADING_COLUMN.fullmatch(key) for key in keys)
    )


class ModelEgressGuard:
    def __init__(self, policy: ModelSharingPolicy | dict | None = None):
        self.policy = policy if isinstance(policy, ModelSharingPolicy) else ModelSharingPolicy.from_dict(policy)
        self.sensitive = frozenset(self.policy.sensitive_columns)
        self._tokens: dict[str, str] = {}
        self._aliases: dict[tuple[str, str], str] = {}
        self.omissions: set[str] = set()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps({"projection_version": 4, "policy": self.policy.to_dict()}, sort_keys=True).encode()).hexdigest()

    def _remember(self, value: Any, replacement: str) -> None:
        # Short numeric IDs are protected in their column, not replaced in all
        # estimates/CI/P values. Arbitrary free text needs the user's judgment.
        token = str(value).strip()
        if token.startswith("[个体_") or token.startswith("[敏感值"):
            return
        numeric = _NUMERIC.fullmatch(token.replace(",", "")) or token.casefold() in {"nan", "inf", "+inf", "-inf"}
        if token and len(token) <= 256 and not numeric:
            if len(self._tokens) < 4096:
                self._tokens[token] = replacement

    @staticmethod
    def _summary_schema(keys: list[str]) -> bool:
        # A grouping/feature label alone never establishes aggregation. Unknown
        # columns fail closed; raw group,age,bmi must not pass as a summary.
        if _item_factor_schema(keys):
            return True
        known = _SUMMARY_LABELS | _SUMMARY_MEASURES
        return bool(set(keys) & _SUMMARY_MEASURES) and all(
            key in known or re.fullmatch(r"(?:mean|median|sd|count|n|percent|proportion|ci|conf)(?:low|high|lower|upper|[a-z]+)", key)
            for key in keys
        )

    def _summary_row(self, keys: list[str], row: list[Any]) -> bool:
        # A role/title is only context, never proof of aggregation. Inference
        # columns must contain statistics; relabeled observation IDs fail closed.
        scale_summary = _item_factor_schema(keys)
        for key, value in zip(keys, row):
            text = str(value).strip()
            if scale_summary:
                if key in _SCALE_ITEM_LABELS:
                    if (
                        not text or _NUMERIC.fullmatch(text) or _INDIVIDUAL_LABEL.search(text)
                        or re.search(r"\[(?:个体|敏感值)", text)
                        or re.fullmatch(r"[^\x00-\x1f\x7f]{1,256}", text) is None
                        or self._text(text) != text
                    ):
                        return False
                else:
                    # An absent optional external criterion is explicitly NA;
                    # loadings/communalities/reliability must be finite numbers.
                    if key in _SCALE_EXTERNAL_CORRELATION and text.casefold() in {"na", "nan", "null", "none", ""}:
                        continue
                    if not _NUMERIC.fullmatch(text) or not math.isfinite(float(text)):
                        return False
                    if key in _SCALE_EXTERNAL_CORRELATION and abs(float(text)) > 1 + 1e-8:
                        return False
                continue
            if key in _SUMMARY_LABELS and (
                _INDIVIDUAL_LABEL.search(text)
                or key in _TERM_LABELS and (not text or _NUMERIC.fullmatch(text))
            ):
                return False
            if key not in _INFERENCE_VALUES | _P_VALUES | _LOWER_CI | _UPPER_CI:
                continue
            if text.casefold() in {"", "na", "nan", "null", "none"}:
                continue
            numeric = re.sub(r"^[<>≤≥]=?\s*", "", text) if key in _P_VALUES else text
            if not _NUMERIC.fullmatch(numeric):
                return False
            if not math.isfinite(float(numeric)):
                return False
            if key in _P_VALUES and not 0 <= float(numeric) <= 1:
                return False
        lower = [str(row[i]).strip() for i, key in enumerate(keys) if key in _LOWER_CI]
        upper = [str(row[i]).strip() for i, key in enumerate(keys) if key in _UPPER_CI]
        if len(lower) == len(upper) == 1 and all(_NUMERIC.fullmatch(x) for x in lower + upper):
            return float(lower[0]) <= float(upper[0])
        return True

    @staticmethod
    def _individual_field(key: str) -> bool:
        normalized = _key(key)
        return normalized in _INDIVIDUAL_HEADERS | _INDIVIDUAL_COLLECTIONS or bool(re.match(
            r"^(?:individual|persample|perpatient|percell|prediction|predictedprobabilit|factorscores|samplescores|cellscores)", normalized
        ))

    def _unclassified_collection(self, key: str, value: Any) -> bool:
        # Classify by shape as well as names. Custom names for observation
        # rows or vectors cannot silently bypass the summary-only mode.
        if not isinstance(value, list) or not value:
            return False
        if all(isinstance(row, dict) and _item_factor_schema(list(map(_key, row))) for row in value):
            labels = [str(next(child for column, child in row.items() if _key(column) in _SCALE_ITEM_LABELS)).strip().casefold() for row in value]
            return len(labels) != len(set(labels)) or not all(
                self._summary_row(list(map(_key, row)), list(row.values())) for row in value
            )
        if key in _STRUCTURAL_COLLECTIONS and all(isinstance(row, dict) for row in value):
            if any(any(_key(column) in _IDENTIFIERS and not (column == "name" and self._structural_name(row, key)) for column in row) for row in value):
                return True
            return False
        if key in _STRUCTURAL_COLLECTIONS or _key(key) in _AGGREGATE_VECTORS:
            return False
        if all(isinstance(row, dict) for row in value):
            return not all(
                self._summary_schema(list(map(_key, row)))
                and self._summary_row(list(map(_key, row)), list(row.values()))
                for row in value
            )
        return any(isinstance(row, (dict, list, int, float)) and not isinstance(row, bool) for row in value)

    @staticmethod
    def _structural_name(value: dict, parent: str) -> bool:
        return any(key in value for key in ("preview_csv", "preview_text", "semantic_type", "unique_count", "mime_type")) or (
            parent in {"variables", "columns", "variable_index", "tables", "texts", "figures", "artifacts", "models"}
            and any(key in value for key in ("type", "kind", "url", "uri", "path"))
        ) or (
            parent in {"diagnostics", "results", "checks", "stages", "steps", "requirements"}
            and value.get("status") in {"passed", "warning", "failed", "not_assessed", "completed", "incomplete", "succeeded", "not_run"}
        )

    def _alias(self, column: str, value: Any) -> str:
        if re.fullmatch(r"\[个体_\d+\]", str(value)):
            return str(value)
        key = (_key(column), str(value))
        if key not in self._aliases:
            self._aliases[key] = f"[个体_{len(self._aliases) + 1}]"
        return self._aliases[key]

    def _table(self, value: str, name: str = "", metadata: Any = None) -> str:
        try:
            rows = list(csv.reader(io.StringIO(value), strict=True))
        except (csv.Error, TypeError):
            self.omissions.add("unparseable_table")
            return "[未发送无法可靠解析的表格预览]"
        if not rows:
            return ""
        headers = rows[0]
        if not headers or any(len(row) != len(headers) for row in rows[1:]):
            self.omissions.add("unparseable_table")
            return "[未发送缺少完整表头或行的表格预览]"
        keys = [_key(h) for h in headers]
        sensitive = {i for i, header in enumerate(headers) if header in self.sensitive}
        identifiers = {i for i, key in enumerate(keys) if key in _IDENTIFIERS}
        for row in rows[1:]:
            for i in sensitive | identifiers:
                if i < len(row):
                    replacement = "[敏感值已隐藏]" if i in sensitive else self._alias(headers[i], row[i])
                    self._remember(row[i], replacement)
            for i, key in enumerate(keys):
                if key in _SUMMARY_LABELS | _SCALE_ITEM_LABELS and _INDIVIDUAL_LABEL.search(row[i]):
                    self._remember(row[i], self._alias(headers[i], row[i]))
        context = [name]
        if isinstance(metadata, dict):
            context.extend(str(metadata.get(key) or "") for key in ("role", "semantic_key", "title"))
        individual = bool(identifiers or set(keys) & _INDIVIDUAL_HEADERS or any(
            _INDIVIDUAL_TABLE.search(label) for label in context))
        repeated_items = False
        if _item_factor_schema(keys):
            label_index = next(i for i, key in enumerate(keys) if key in _SCALE_ITEM_LABELS)
            item_labels = [str(row[label_index]).strip().casefold() for row in rows[1:]]
            repeated_items = len(item_labels) != len(set(item_labels))
        if self.policy.result_mode == "summary_only" and (
            individual or repeated_items or len(set(keys)) != len(keys) or not self._summary_schema(keys)
            or not all(self._summary_row(keys, row) for row in rows[1:])
        ):
            self.omissions.add("individual_or_unclassified_table")
            return "[个体或未分类结果表未发送；完整结果保留在本机]"
        keep = [i for i in range(len(headers)) if i not in sensitive]
        if sensitive:
            self.omissions.add("sensitive_columns")
        if not keep:
            return "[表格仅含屏蔽列，未发送]"
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow([headers[i] for i in keep])
        for row in rows[1:]:
            writer.writerow([
                self._alias(headers[i], row[i]) if i in identifiers and i < len(row)
                else self._text(row[i]) if i < len(row) else ""
                for i in keep
            ])
        return output.getvalue().rstrip("\n")

    def _text(self, text: str) -> str:
        return self._sensitive_text(redact_filesystem_paths(text))

    def _sensitive_text(self, text: str) -> str:
        """Mask learned values without applying prose-only path heuristics."""
        for token, replacement in sorted(self._tokens.items(), key=lambda item: len(item[0]), reverse=True):
            # Avoid changing unrelated English words when a known value is a
            # short name such as Al; Chinese names have no word separators.
            if len(token) <= 2 and token.isascii():
                text = re.sub(r"(?<!\w)" + re.escape(token) + r"(?!\w)", lambda _: replacement, text)
            else:
                text = text.replace(token, replacement)
        return text

    @staticmethod
    def _r_string_value(body: str) -> str:
        """Decode R string escapes needed to recognize encoded local paths."""
        escapes = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}

        def decode(match: re.Match) -> str:
            value = match.group(0)[1:]
            if value in escapes:
                return escapes[value]
            if value in {"\\", '"', "'", "`"}:
                return value
            if value[0] in "uUx":
                digits = value[1:].strip("{}")
                number = int(digits, 16)
            else:
                number = int(value, 8)
            return chr(number) if number <= 0x10FFFF else match.group(0)

        return re.sub(
            r'''\\(?:[abfnrtv\\"'`]|[uU]\{[0-9A-Fa-f]{1,8}\}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|x[0-9A-Fa-f]{1,2}|[0-7]{1,3})''',
            decode, body,
        )

    def _r_code(self, code: str) -> str:
        """Project R source without interpreting division/formulas as paths.

        Strings, backtick names, and comments can contain actual local paths.
        The executable regions use only learned sensitive-value masking. Keep
        unchanged literals byte-for-byte; re-escape only redacted literals so
        the historical code remains syntactically useful for model repairs.
        """
        parts = []
        index = 0
        plain_start = 0
        raw_string = re.compile(r'''[rR](["'])(-*)([([{])''')
        while index < len(code):
            raw = raw_string.match(code, index)
            if raw and (index == 0 or not (code[index - 1].isalnum() or code[index - 1] in "._")):
                parts.append(self._sensitive_text(code[plain_start:index]))
                closing = {"(": ")", "[": "]", "{": "}"}[raw.group(3)] + raw.group(2) + raw.group(1)
                body_start = raw.end()
                body_end = code.find(closing, body_start)
                terminated = body_end >= 0
                body_end = body_end if terminated else len(code)
                parts.append(code[index:body_start] + self._text(code[body_start:body_end]))
                if terminated:
                    parts.append(closing)
                index = body_end + len(closing) if terminated else body_end
                plain_start = index
                continue
            char = code[index]
            if char == "#":
                parts.append(self._sensitive_text(code[plain_start:index]))
                comment_end = index
                while comment_end < len(code) and code[comment_end] not in "\r\n":
                    comment_end += 1
                parts.append(self._text(code[index:comment_end]))
                index = comment_end
                plain_start = index
                continue
            if char not in {'"', "'", "`"}:
                index += 1
                continue
            parts.append(self._sensitive_text(code[plain_start:index]))
            quote = char
            body_start = index + 1
            cursor = body_start
            while cursor < len(code):
                if code[cursor] == "\\":
                    cursor += 2
                elif code[cursor] == quote:
                    break
                else:
                    cursor += 1
            body_end = min(cursor, len(code))
            body = code[body_start:body_end]
            decoded = self._r_string_value(body)
            cleaned = self._text(decoded)
            if cleaned != decoded:
                body = json.dumps(cleaned, ensure_ascii=False)[1:-1]
                if quote != '"':
                    body = body.replace('\\"', '"').replace(quote, "\\" + quote)
            parts.append(quote + body + (quote if cursor < len(code) else ""))
            index = min(cursor + 1, len(code))
            plain_start = index
        parts.append(self._sensitive_text(code[plain_start:]))
        return "".join(parts)

    def _tool_arguments(self, arguments: str, *, r_code: bool) -> str:
        """Parse protocol JSON before masking payloads, preserving its syntax."""
        try:
            parsed = json.loads(arguments)
        except (ValueError, TypeError):
            return self._text(arguments)

        def project(value: Any, *, code_field: bool = False) -> Any:
            if isinstance(value, str):
                return self._r_code(value) if code_field else self._text(value)
            if isinstance(value, dict):
                return {key: project(child) for key, child in value.items()}
            if isinstance(value, list):
                return [project(child) for child in value]
            return value

        if isinstance(parsed, dict):
            cleaned = {key: project(child, code_field=r_code and key == "code") for key, child in parsed.items()}
        else:
            cleaned = project(parsed)
        return json.dumps(cleaned, ensure_ascii=False)

    def _learn(self, value: Any, *, parent: str = "") -> None:
        if isinstance(value, dict):
            if isinstance(value.get("preview_csv"), str):
                self._table(value["preview_csv"], str(value.get("name") or ""), value.get("metadata"))
            variable = str(value.get("name") or "")
            if (variable in self.sensitive or _key(variable) in _IDENTIFIERS) and any(
                key in value for key in ("type", "semantic_type", "unique_count", "levels")):
                for key in ("levels", "values", "top_values", "sample", "examples"):
                    self._learn_values(value.get(key))
            for key, child in value.items():
                structural = key == "name" and self._structural_name(value, parent)
                if not structural and key in self.sensitive:
                    self._learn_values(child)
                elif not structural and _key(key) in _IDENTIFIERS and not isinstance(child, (dict, list)):
                    self._remember(child, self._alias(key, child))
                elif _key(key) in _SUMMARY_LABELS and isinstance(child, str) and _INDIVIDUAL_LABEL.search(child):
                    self._remember(child, self._alias(key, child))
                if key not in {"image_url", "image_ref"}:
                    self._learn(child, parent=str(key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._learn(child, parent=parent)
        elif isinstance(value, str):
            parsed = self._json(value)
            if parsed is not None:
                self._learn(parsed)
            else:
                for match in re.finditer(r"【表 ([^】]+)】\n(.*?)(?=\n\n【|$)", value, re.S):
                    self._table(match[2], match[1])

    def _learn_values(self, value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                self._learn_values(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                self._learn_values(child)
        elif value is not None:
            self._remember(value, "[敏感值已隐藏]")

    @staticmethod
    def _json(text: str) -> Any:
        # Tool envelopes have a human-readable first line. Never reuse that
        # line after filtering, since it can duplicate an unsanitized preview.
        body = text.split("\n", 1)[1] if text.startswith("[TOOL_RESULT]") and "\n" in text else text
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            return None

    def _walk(self, value: Any, *, parent: str = "", name: str = "") -> Any:
        if isinstance(value, dict):
            if (
                self.policy.result_mode == "summary_only"
                and _item_factor_schema(list(map(_key, value)))
                and not self._summary_row(list(map(_key, value)), list(value.values()))
            ):
                self.omissions.add("individual_or_unclassified_scale_summary")
                return "[个体或无效条目汇总未发送；完整结果保留在本机]"
            if self.policy.result_mode == "summary_only" and parent and value and all(
                isinstance(child, (int, float)) and not isinstance(child, bool) for child in value.values()
            ) and not self._summary_schema(list(map(_key, value))) and _key(parent) not in _AGGREGATE_VECTORS:
                self.omissions.add("unclassified_numeric_mapping")
                return "[未分类数值映射未发送；完整结果保留在本机]"
            result = {}
            variable = str(value.get("name") or "")
            scientific_envelope = value.get("tool") in {"run_r_code", "inspect_dataset"}
            protected_variable = (variable in self.sensitive or _key(variable) in _IDENTIFIERS) and any(
                key in value for key in ("type", "semantic_type", "unique_count", "levels"))
            for key, child in value.items():
                if (key in _ENGINE_KEYS and not (key == "code" and parent == "arguments")) or key in _RAW_KEYS and isinstance(child, (list, dict)):
                    continue
                if key == "image_url":
                    # Sending the original picture requires explicit policy
                    # permission. Never mutate image bytes and call it masking.
                    result[key] = copy.deepcopy(child)
                    continue
                if protected_variable and key not in {"name", "type", "semantic_type", "unique_count", "missing_count", "missing_ratio"}:
                    self.omissions.add("sensitive_variable_details")
                    continue
                if key == "name" and (protected_variable or self._structural_name(value, parent)):
                    result[key] = child
                elif key == "code" and parent == "arguments" and isinstance(child, str):
                    result[key] = self._r_code(child)
                elif key == "preview_csv" and isinstance(child, str):
                    result[key] = self._table(child, str(value.get("name") or name), value.get("metadata"))
                elif scientific_envelope and key == "data" and isinstance(child, str) and self._json(child) is None:
                    self.omissions.add("incomplete_historical_result")
                    result[key] = _WITHHELD_HISTORY
                elif key in self.sensitive:
                    result[key] = "[敏感值已隐藏]"
                elif _key(key) in _IDENTIFIERS and not isinstance(child, (dict, list)):
                    self.omissions.add("identifier_fields")
                    result[key] = "[个体字段未发送]" if self.policy.result_mode == "summary_only" else self._alias(key, child)
                elif self.policy.result_mode == "summary_only" and (
                    self._individual_field(key) and not (isinstance(child, dict) and self._summary_schema(list(map(_key, child))))
                    or self._unclassified_collection(key, child)
                ):
                    self.omissions.add("individual_result_fields")
                    result[key] = "[个体或未分类结果未发送；完整结果保留在本机]"
                else:
                    result[key] = self._walk(child, parent=str(key), name=str(value.get("name") or name))
            # Preserve the decisive R failure reason, with the same bounded
            # tail as the existing result projector, instead of dropping it.
            if isinstance(value.get("stderr"), str) and value["stderr"].strip():
                result["stderr_tail"] = self._text(value["stderr"])[-4000:]
            if scientific_envelope:
                result["summary"] = "本机科研工具结果，已按当前模型外发设置处理。"
            return result
        if isinstance(value, (list, tuple)):
            return [self._walk(child, parent=parent, name=name) for child in value]
        if isinstance(value, str):
            if parent == "preview_csv":
                return self._table(value, name)
            if parent == "preview_text" and self.policy.result_mode == "summary_only":
                self.omissions.add("unstructured_preview")
                return "[未发送自由文本或模型文件预览；完整产物保留在本机]"
            parsed = self._json(value)
            if parsed is not None:
                cleaned = self._walk(parsed, parent=parent)
                if isinstance(cleaned, dict) and cleaned.get("tool") in {"run_r_code", "inspect_dataset"}:
                    cleaned["summary"] = "本机科研工具结果，已按当前模型外发设置处理。"
                prefix = "[TOOL_RESULT]\n" if value.startswith("[TOOL_RESULT]") else ""
                return prefix + json.dumps(cleaned, ensure_ascii=False)
            value = re.sub(r"【表 ([^】]+)】\n(.*?)(?=\n\n【|$)",
                           lambda match: f"【表 {match[1]}】\n{self._table(match[2], match[1])}", value, flags=re.S)
            if self.policy.result_mode == "summary_only":
                value = re.sub(r"【(?:文本|模型) ([^】]+)】\n(.*?)(?=\n\n【|$)",
                               "[未发送自由文本或模型文件预览]", value, flags=re.S)
            return self._text(value)
        return value

    def project(self, payload: Any) -> Any:
        self._learn(payload)
        # The second walk applies newly learned tokens to earlier fields too.
        clean = self._walk(payload)
        return self._walk(clean)

    def messages(self, messages: list[dict]) -> list[dict]:
        # Message/tool protocol fields are not dataset column values. In
        # particular a sensitive column named 'name' must not alter a function
        # name, tool_call_id, or the generated R command structure.
        for message in messages:
            self._learn(message.get("content"))
        result = []
        for original in messages:
            message = copy.deepcopy(original)
            is_review = message.get("kind") == "artifact_review"
            if is_review and not self.policy.send_result_images:
                # Old automatic reviews can contain raw numeric excerpts as
                # free text. Do not replay either them or their image refs.
                message["content"] = "分析图反馈未发送。请依据已允许的结果预览解释，不要声称你看到了图。"
            content = message.get("content")
            if isinstance(content, list):
                allow = self.policy.send_result_images if is_review else self.policy.send_attached_images
                content = [part for part in content if not isinstance(part, dict) or
                           part.get("type") not in {"image_url", "image", "image_ref"} or allow]
                message["content"] = [
                    {**part, "text": self._walk(part["text"])} if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                    else part for part in content
                ] or "[图片未发送，当前外发设置已关闭]"
            elif isinstance(content, str):
                if message.get("role") == "tool" and self._json(content) is None and (
                    content.startswith("[TOOL_RESULT]") or "[result truncated for history" in content or "[full result persisted; omitted" in content
                ):
                    self.omissions.add("incomplete_historical_result")
                    message["content"] = _WITHHELD_HISTORY
                else:
                    message["content"] = self._walk(content)
            if isinstance(message.get("reasoning_content"), str):
                message["reasoning_content"] = self._text(message["reasoning_content"])
            for call in message.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    function["arguments"] = self._tool_arguments(
                        function["arguments"], r_code=function.get("name") == "run_r_code",
                    )
            result.append(message)
        return result
