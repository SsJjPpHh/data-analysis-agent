"""Decide whether an analysis may finalize or must continue its portfolio."""

from __future__ import annotations

from dataclasses import dataclass

from agent.analysis.models import AnalysisPlan
from agent.analysis.result_validation import ResultAssessment


@dataclass(frozen=True)
class CompletionDecision:
    action: str
    allow_final: bool
    instruction: str = ""
    outstanding_roles: tuple[str, ...] = ()
    user_message: str = ""


class AnalysisCompletionController:
    """Turn semantic portfolio state into the next execution action."""

    def decide(
        self,
        plan: AnalysisPlan,
        assessment: ResultAssessment | None,
        *,
        r_calls: int,
        max_r_calls: int,
        nudge_count: int,
    ) -> CompletionDecision:
        if assessment is not None and assessment.complete:
            return CompletionDecision(
                action="finalize", allow_final=True,
                instruction=(
                    "The output portfolio is complete, but methodological diagnostics require explicit disclosure. "
                    "Explain these limitations and their effect on interpretation; do not claim all checks passed: "
                    + "; ".join(assessment.warnings)
                ) if assessment.failed_diagnostics or assessment.warning_diagnostics else "",
                user_message=("**方法诊断提示：**" + "；".join(assessment.warnings))
                if assessment.failed_diagnostics or assessment.warning_diagnostics else "",
            )

        # ``0`` means unlimited for the local desktop runtime.  A positive
        # operator override remains useful for constrained lab machines, but
        # the old hosted-era call ceiling must not silently truncate a valid
        # multi-stage scientific workflow.
        call_limit_reached = max_r_calls > 0 and r_calls >= max_r_calls
        if call_limit_reached or nudge_count >= 3:
            outstanding = assessment.outstanding_roles if assessment is not None else tuple(
                item.role for item in plan.output_portfolio if item.required
            )
            return CompletionDecision(
                action="limit",
                allow_final=True,
                outstanding_roles=outstanding,
                instruction=(
                    "The safety execution limit has been reached. Do not claim the analysis is complete. "
                    f"State the outstanding portfolio roles explicitly: {', '.join(outstanding) or 'unknown'}."
                ),
                user_message=(
                    "**本轮尚未完成全部科研产物。**\n\n"
                    "已达到本轮安全执行上限，现有结果均已保留。仍待补齐："
                    f"{ '、'.join(outstanding) or '无法确认的剩余产物' }。"
                    "可点击“补齐产物”沿用同一分析计划继续。"
                ),
            )

        outstanding = assessment.outstanding_roles if assessment is not None else tuple(
            item.role for item in plan.output_portfolio if item.required
        )
        output_lookup = {item.role: item for item in plan.output_portfolio}
        requested: list[str] = []
        emission_hints: list[str] = []
        kind_emitter = {
            "table": "baa_table",
            "figure": "baa_plot",
            "text": "baa_text",
            "model": "baa_model_summary",
        }
        for role in outstanding:
            item = output_lookup.get(role)
            if item is None:
                requested.append(role)
                continue
            detail = f"{item.role} ({item.kind}, {item.tier}, {item.label or item.role})"
            if item.rationale:
                detail += f" — {item.rationale}"
            requested.append(detail)
            emitter = kind_emitter.get(item.kind)
            if emitter:
                emission_hints.append(
                    f'{emitter}("{item.role}", <{item.kind}对象>, '
                    f'metadata=list(role="{item.role}", semantic_key="{item.role}", '
                    f'title="{item.label or item.role}", paper_ready=TRUE))'
                )
        diagnostics = assessment.missing_diagnostics if assessment is not None else plan.diagnostics
        invalid = assessment.invalid_roles if assessment is not None else ()
        instruction = (
            "[PLATFORM COMPLETION CONTROL]\n"
            "The previous answer attempted to finalize before the Scientific Output Portfolio was complete. "
            "Continue the same frozen AnalysisPlan now. Use one compact run_r_code call where practical.\n"
            f"Outstanding outputs: {'; '.join(requested) or 'none'}.\n"
            f"Outstanding diagnostics: {'; '.join(diagnostics) or 'none'}.\n"
            f"Roles requiring kind/semantic repair: {', '.join(invalid) or 'none'}.\n"
            + (
                f"Available frozen inputs (use these exact variable bindings via baa_plan_input()): "
                f"{'; '.join(f'{k}={v}' for k, v in plan.variable_bindings.items()) or 'none'}; "
                f"resolved input IDs: {', '.join(plan.resolved_inputs.keys())}.\n"
                if plan.variable_bindings or plan.resolved_inputs else ""
            )
            + "Persist each result with its exact portfolio role, semantic_key, and all applicable "
            "acceptance_evidence clauses. "
            + (
                "Exact emission calls for the outstanding outputs (run inside run_r_code): "
                + " ; ".join(emission_hints)
                + ". "
                if emission_hints
                else ""
            )
            + "A non-required planned item may use a concise baa_text artifact "
            "with that role and portfolio_status='not_applicable', 'limited', or 'declined' only when "
            "the status is scientifically justified by a non-empty portfolio_reason. Required outputs must remain "
            "outstanding until produced. Do not merely describe "
            "missing work in chat, and do not repeat artifacts already accepted.\n"
            "[END PLATFORM COMPLETION CONTROL]"
        )
        return CompletionDecision(
            action="continue" if assessment is not None else "execute",
            allow_final=False,
            instruction=instruction,
            outstanding_roles=outstanding,
        )
