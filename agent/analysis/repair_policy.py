"""Bound automatic R repair loops without limiting valid multi-step analyses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RRepairDecision:
    failed: bool = False
    stop: bool = False
    reason: str = ""
    category: str = ""
    signature: str = ""
    user_message: str = ""


def _payload(raw: object) -> dict[str, Any]:
    text = str(raw or "").strip()
    body = text.removeprefix("ERROR:").strip() if text.startswith("ERROR:") else text
    start = body.find("{")
    if start < 0:
        return {}
    try:
        parsed = json.loads(body[start:])
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalized_fact(value: object) -> str:
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text[:500]


class RRepairPolicy:
    """Track only failed R executions; successful steps reset the repair window.

    A normal analysis may execute many successful R stages. The guard applies
    only when failures repeat without an intervening successful R result.
    """

    def __init__(
        self,
        *,
        max_same_failure: int = 2,
        max_consecutive_failures: int = 3,
    ) -> None:
        self._max_same_failure = max(1, int(max_same_failure))
        self._max_consecutive_failures = max(1, int(max_consecutive_failures))
        self._consecutive_failures = 0
        self._failure_counts: dict[str, int] = {}

    def _reset(self) -> None:
        self._consecutive_failures = 0
        self._failure_counts.clear()

    def observe(self, raw: object) -> RRepairDecision:
        payload = _payload(raw)
        result = payload.get("result")
        result = result if isinstance(result, dict) else {}
        execution = payload.get("execution")
        execution = execution if isinstance(execution, dict) else {}
        diagnostics = payload.get("repair_diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        category = str(diagnostics.get("category") or "r_execution_error").strip()

        preparation_plan = payload.get("preparation_plan")
        if isinstance(preparation_plan, dict):
            headline = str(
                preparation_plan.get("headline")
                or preparation_plan.get("summary")
                or "当前数据需要先准备"
            ).strip()
            return RRepairDecision(
                failed=True,
                stop=True,
                reason="waiting_for_preparation",
                category=category,
                user_message=(
                    f"{headline}。本轮已停止自动重试；完成准备并重新导入后，"
                    "系统会沿用当前分析目标重新预检。"
                ),
            )

        dependency_gate = payload.get("dependency_gate")
        execution_status = str(execution.get("status") or "").strip()
        if isinstance(dependency_gate, dict) or execution_status == "waiting_for_dependencies":
            gate = dependency_gate if isinstance(dependency_gate, dict) else {}
            headline = str(gate.get("headline") or "分析依赖尚未就绪").strip()
            return RRepairDecision(
                failed=True,
                stop=True,
                reason="waiting_for_dependencies",
                category=category,
                user_message=(
                    f"{headline}。本轮已暂停，不会继续重复执行 R；"
                    "依赖处理并重新检测通过后可恢复同一冻结分析计划。"
                ),
            )

        if payload.get("policy_blocked") is True:
            reason = str(payload.get("reason") or "R 执行被分析策略阻止").strip()
            return RRepairDecision(
                failed=True,
                stop=True,
                reason="policy_blocked",
                category="policy_blocked",
                user_message=f"{reason}。本轮已停止，未继续消耗 R 执行次数。",
            )

        raw_error = str(raw or "").lstrip().startswith("ERROR:")
        failed = bool(
            raw_error
            or result.get("ok") is False
            or execution_status in {"failed", "blocked"}
        )
        if not failed:
            self._reset()
            return RRepairDecision()

        self._consecutive_failures += 1
        stage = str(
            result.get("execution_stage")
            or execution.get("execution_stage")
            or "unknown"
        ).strip()
        facts = diagnostics.get("facts")
        facts = facts if isinstance(facts, list) else []
        normalized_facts = tuple(
            fact for item in facts if (fact := _normalized_fact(item))
        )
        # A category-level fact such as "a referenced column is unavailable"
        # is useful to explain the failure, but is not specific enough to
        # decide that a repair was repeated.  Keep the concrete R error and
        # detected values in the signature so two genuinely different
        # failures still receive one bounded repair attempt each.
        signature_parts = (
            category.casefold(),
            stage.casefold(),
            *normalized_facts,
            _normalized_fact(result.get("error")),
            _normalized_fact(diagnostics.get("detected")),
        )
        signature = "|".join(part for part in signature_parts if part)
        self._failure_counts[signature] = self._failure_counts.get(signature, 0) + 1

        if self._failure_counts[signature] >= self._max_same_failure:
            return RRepairDecision(
                failed=True,
                stop=True,
                reason="repeated_failure",
                category=category,
                signature=signature,
                user_message=(
                    "同一 R 错误在一次明确修正后仍然出现，已停止自动重试，"
                    f"避免反复消耗。错误类别：{category}。现有诊断和已生成产物均已保留。"
                ),
            )

        if self._consecutive_failures >= self._max_consecutive_failures:
            return RRepairDecision(
                failed=True,
                stop=True,
                reason="consecutive_failures",
                category=category,
                signature=signature,
                user_message=(
                    "R 已连续三次返回不同错误，自动修正已停止，避免进入循环。"
                    "现有诊断和已生成产物均已保留。"
                ),
            )

        return RRepairDecision(
            failed=True,
            category=category,
            signature=signature,
        )
