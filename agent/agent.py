#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

"""Research Statistics Workbench agent — main entry point.

The heavy lifting is split across:
  prompts.py             — base system prompt and path setup
  tools/schemas.py       — AGENT_TOOLS (JSON schemas sent to the LLM)
  tools/research/r_tools.py — R dataset inspection and code execution tools
  tools/research/r_binding.py — frozen-plan accessors and job-path probes
  tools/research/r_packages.py — R package inventory and help
"""
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict
from typing import Iterator, List, Dict, Any, Optional, Tuple

from .analysis import (
    AnalysisCatalog,
    AnalysisCompletionController,
    AnalysisPlan,
    AnalysisPlanner,
    CallbackCancelHandle,
    ConversationPlanDraft,
    ModelResultProjector,
    RequiredOutput,
    RRepairPolicy,
    ResolvedInput,
    ResultAssessment,
    ResultValidator,
    RuntimeCapabilityResolver,
    WorkflowRunSpec,
    default_workflow_registry,
    workflow_system_section,
    validation_evidence_from_manifest,
    research_run_controller,
)
from .prompts import get_system_prompt, build_temp_prompt_section, build_result_assessment_section
from .tools.schemas import AGENT_TOOLS
from .tools.research import RToolsMixin
from .tools.exposure import filter_tools_for_turn
from .tools.results import make_tool_result
from .analysis.result_validation import merge_diagnostic_statuses
from data.workspace import workspace_manager
from r_runtime.dependency_gate import inspect_dependency_gate
from infrastructure.llm_transport import default_llm_timeout, stream_stall_seconds
from .analysis import script_library
from .analysis.model_egress import ModelEgressGuard
from .compaction import (
    should_compact_history,
    compact_history_rolling,
    reuse_compaction_view,
    reuse_view_is_small_enough,
    should_trim_history,
    trim_oversized_tool_results,
)
from .vision import (
    MAX_IMAGES_PER_TURN,
    VisionImage,
    build_r_artifact_review,
    build_user_message,
    content_token_estimate,
    hydrate_messages_for_llm,
    strip_images_for_compaction,
)
from .retry import call_with_retry as _call_with_retry, is_retryable as _is_retryable
from .validate import validate_tool_args as _validate_tool_args
from .reasoning import ThinkTagStreamParser

log = logging.getLogger(__name__)


def _as_bool_arg(value: Any) -> bool:
    """Coerce tool-call booleans without treating the string 'False' as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


def _research_artifacts_from_tool_result(raw: Any) -> list[dict]:
    text = str(raw or "").strip()
    if text.startswith("ERROR:"):
        text = text.removeprefix("ERROR:").strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []

    artifacts: list[dict] = []
    payload_job_id = str(payload.get("job_id") or "").strip()
    for collection, artifact_type in (
        ("tables", "table"),
        ("figures", "figure"),
        ("texts", "text"),
        ("models", "model"),
    ):
        items = result.get(collection) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            path = str(item.get("path") or "")
            name = str(item.get("name") or path.rsplit("/", 1)[-1] or artifact_type)
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            artifacts.append(
                {
                    "type": artifact_type,
                    "artifact_id": f"research:{collection}:{path}",
                    "name": name,
                    "path": path,
                    "kind": str(item.get("kind") or artifact_type),
                    "mime_type": str(item.get("mime_type") or ""),
                    "metadata": metadata,
                    "source_tool": "run_r_code",
                    "job_id": payload_job_id or str(metadata.get("job_id") or ""),
                }
            )
    return artifacts


_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:Users|tmp|private|var|baa-data)/[^\s\"')\]}]+")


def _redact_model_visible_paths(value: Any) -> Any:
    if isinstance(value, str):
        return _LOCAL_PATH_RE.sub("<platform-managed-artifact>", value)
    if isinstance(value, list):
        return [_redact_model_visible_paths(item) for item in value]
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"path", "stored_path", "artifacts_dir"}:
                clean[key] = "<platform-managed-artifact>"
            else:
                clean[key] = _redact_model_visible_paths(item)
        return clean
    return value


def _sanitize_r_tool_result_for_model(raw: Any) -> Any:
    """Compatibility wrapper around the bounded model-only R projection."""
    projected = ModelResultProjector().project(raw)
    return json.dumps(projected, ensure_ascii=False)


def _sanitize_tool_artifacts_for_model(artifacts: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        item: dict[str, Any] = {}
        for key in ("type", "name", "kind", "mime_type", "metadata", "source_tool"):
            if key in artifact:
                item[key] = _redact_model_visible_paths(artifact.get(key))
        if item:
            sanitized.append(item)
    return sanitized


def _r_tool_payload(raw: Any) -> dict[str, Any]:
    text = str(raw or "").strip()
    body = text.removeprefix("ERROR:").strip() if text.startswith("ERROR:") else text
    start = body.find("{")
    if start < 0:
        return {}
    try:
        payload = json.loads(body[start:])
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _attach_result_assessment(
    raw: Any,
    assessment: Any,
    *,
    workflow_state: dict[str, Any] | None = None,
) -> str:
    text = str(raw or "").strip()
    is_error = text.startswith("ERROR:")
    payload = _r_tool_payload(text)
    if not payload:
        return text
    payload["result_assessment"] = asdict(assessment)
    execution = payload.get("execution")
    if isinstance(execution, dict):
        workflow = execution.get("workflow")
        if isinstance(workflow, dict):
            if workflow_state is not None:
                workflow.update(workflow_state)
            else:
                phase_status = workflow.get("phase_status")
                if not isinstance(phase_status, dict):
                    phase_status = {}
                # Compatibility for callers without a production executor.
                # The live agent path supplies workflow_state from the durable
                # lifecycle instead of mutating phase labels independently.
                if phase_status.get("analyze") == "completed":
                    diagnostics_complete = not assessment.missing_diagnostics
                    phase_status["diagnose"] = (
                        "completed" if diagnostics_complete else "incomplete"
                    )
                    if diagnostics_complete:
                        phase_status["publish"] = (
                            "completed" if assessment.complete else "incomplete"
                        )
                    else:
                        phase_status["publish"] = "not_started"
                workflow["phase_status"] = phase_status
    serialized = json.dumps(payload, ensure_ascii=False)
    return f"ERROR: {serialized}" if is_error else serialized


def _result_assessment_facts(
    assessment: ResultAssessment,
    diagnostic_status: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A small view of the current validator state, independent of tool budgets.

    This is derived on each request from the research loop's existing state,
    never recovered from model-written assessments or old tool-message text.
    Diagnostic labels are values so the sharing guard can redact them too.
    """
    facts = asdict(assessment)
    facts["diagnostics"] = [
        {"diagnostic": name, "status": status}
        for name, status in (diagnostic_status or {}).items()
    ]
    facts["diagnostic_conflicts"] = [
        warning for warning in assessment.warnings
        if warning.startswith("诊断声明冲突：")
    ]
    return facts


def _attach_r_repair_decision(raw: Any, decision: Any) -> str:
    text = str(raw or "").strip()
    is_error = text.startswith("ERROR:")
    payload = _r_tool_payload(text)
    if not payload:
        return text
    payload["repair_policy"] = {
        "stop": bool(decision.stop),
        "reason": str(decision.reason or ""),
        "category": str(decision.category or ""),
        "message": str(decision.user_message or ""),
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    return f"ERROR: {serialized}" if is_error else serialized


def _analysis_plan_system_section(plan: AnalysisPlan) -> str:
    from agent.analysis.method_policy import template_method_status

    method_evidence = template_method_status(plan.template_id or "")
    bindings = ", ".join(f"{role}={column}" for role, column in plan.variable_bindings.items())

    def typed_input_value(item) -> str:
        if item.kind in {"dataset", "object"} and str(item.value) == plan.dataset_id:
            return f"CURRENT_DATASET_ASSET[id={json.dumps(plan.dataset_id, ensure_ascii=False)}]"
        return json.dumps(item.value, ensure_ascii=False, default=str)

    typed_inputs = "; ".join(
        f"id={item.input_id}; {item.label} ({item.kind})={typed_input_value(item)} "
        f"[source={item.provenance}, confidence={item.confidence:.2f}]"
        for item in plan.resolved_inputs.values()
    )
    matrix_declaration = plan.resolved_inputs.get("__matrix_orientation__")
    matrix_guard = ""
    core_output_rule = (
        "Only outputs marked required=true are mandatory. An output marked required=false remains "
        "optional even if its legacy tier is core, and may be omitted with a concrete scientific reason."
    )
    if matrix_declaration is not None:
        matrix_guard = (
            "\nMatrix declaration guard: the matrix orientation/semantics value is a user declaration, "
            "not scientific proof. Before normalization, modeling, or plotting, the first R step must "
            "validate feature columns and identifiers, dimensions, numeric/non-finite/missing values, "
            "template-appropriate scale (including nonnegative integer-like counts where required), "
            "duplicate names, and exact sample/group/batch alignment. Report this check explicitly. "
            "If any requirement is false or remains unknown, stop downstream analysis and explain the "
            "specific preparation needed; never reinterpret ordinary clinical variables as omics features.\n"
        )
        core_output_rule = (
            "Outputs marked required=true must be produced only after the matrix declaration guard "
            "passes. If it fails, do not compute downstream required outputs; save only the validation "
            "evidence and preparation guidance needed to correct the input. Outputs marked "
            "required=false remain optional regardless of tier."
        )
    fast_path = ""
    if plan.source == "template":
        fast_path = (
            "\n[TEMPLATE EXECUTION FAST PATH]\n"
            "This plan was pre-compiled by the template system: typed inputs, method family, "
            "packages, and the output portfolio are frozen with structural input checks. "
            "These checks do not validate scientific assumptions or the estimator. "
            "Execute it in one pass: write ONE complete R script that produces "
            "every applicable portfolio item (including planned diagnostics and required "
            "manuscript text roles) and save all of its artifacts in a single run_r_code call. "
            "Open another call only for a concrete R error repair or a genuinely required second "
            "execution stage. Do not re-run inspect_dataset for columns the typed resolved inputs "
            "already bind, do not call get_r_package_inventory for packages already listed in the "
            "plan, and do not create separate re-computation or evidence artifacts for results a "
            "required portfolio role already covers. Once the result assessment reports the "
            "applicable portfolio complete (or reports concrete blockers), write the final "
            "manuscript-oriented interpretation immediately. When the conversation "
            "contains a [PRE-EXECUTED TEMPLATE SCRIPT] notice, the platform already "
            "executed the fixed script for this plan: reuse its recorded results. "
            "Do not repeat execution without a concrete error or methodological reason. "
            "Disclose diagnostic failures and uncertainty; never treat a completed portfolio "
            "as scientific acceptance.\n"
            "[END TEMPLATE EXECUTION FAST PATH]\n"
        )
    outputs = "\n".join(
        (
            f"- {item.role} [{item.kind}; tier={item.tier}; "
            f"required={'true' if item.required else 'false'}; source={item.source}] "
            f"{item.label or item.role}"
            + (f"; rationale={item.rationale}" if item.rationale else "")
            + (f"; acceptance={'; '.join(item.acceptance)}" if item.acceptance else "")
            + (f"; legacy aliases={', '.join(item.legacy_roles)}" if item.legacy_roles else "")
        )
        for item in plan.output_portfolio
    )
    return (
        "\n\n[ACTIVE ANALYSIS PLAN]\n"
        f"Plan ID: {plan.plan_id}; source={plan.source}; dataset_id={plan.dataset_id}\n"
        f"Method evidence: {json.dumps(method_evidence, ensure_ascii=False)}\n"
        "Scientific review of the current study has not been performed by the artifact validator.\n"
        f"Intent/category/scenario: {plan.intent} / {plan.category} / {plan.scenario}\n"
        f"Method family: {plan.method_family}; resource_class={plan.resource_class}; "
        f"packages: {', '.join(plan.packages) or 'stats'}\n"
        f"Confirmed variable bindings: {bindings or 'none required'}\n"
        f"Typed resolved inputs: {typed_inputs or 'none required'}\n"
        f"Prerequisites: {'; '.join(plan.prerequisites) or 'none declared'}\n"
        f"Assumptions: {'; '.join(plan.assumptions) or 'none declared'}\n"
        f"Diagnostics to record: {'; '.join(plan.diagnostics) or 'none declared'}\n"
        f"Scientific output portfolio (version {plan.portfolio_version}):\n{outputs}\n"
        f"Capability evidence: {', '.join(plan.capability_evidence) or 'baseline only'}\n"
        f"{matrix_guard}"
        "Use this backend-compiled portfolio as the execution source of truth. Template outputs are "
        "compatibility baselines, not output ceilings. Save every applicable portfolio item "
        "with metadata containing its exact role and the plan_id above. Do not replace the package, "
        "output, or variable contracts with values from the browser or user prose. "
        "Obtain every frozen typed input in R with baa_plan_input(\"input_id\") and use the returned "
        "value in formulas and helper calls; do not retype or substitute variable names, column sets, "
        "levels, parameters, or specifications. The execution preflight verifies the complete typed "
        "input contract, including roles that are not covered by generic group/outcome names. Batch related "
        "artifacts in compact R calls, but do not finalize merely because a minimum count or an "
        "earlier template baseline has been reached. Every artifact must explicitly declare its canonical "
        "role; the platform will not infer a role from artifact order. Also declare semantic_key and, when "
        "acceptance clauses are listed, acceptance_evidence containing every clause the artifact actually "
        f"satisfies. {core_output_rule} A non-required "
        "output may be marked not_applicable, limited, or declined only with a concrete scientific reason.\n"
        f"{fast_path}"
        "[END ACTIVE ANALYSIS PLAN]"
    )


def _portfolio_manifest_system_section(manifest: dict[str, Any]) -> str:
    items = [item for item in manifest.get("items") or [] if isinstance(item, dict)]
    if not items:
        return ""
    rows = "\n".join(
        f"- {item.get('role')}: {item.get('status')}"
        + (f" ({item.get('reason')})" if item.get("reason") else "")
        for item in items
    )
    return (
        "\n\n[PRIOR SCIENTIFIC OUTPUT MANIFEST]\n"
        f"Plan ID: {manifest.get('plan_id', '')}; complete={bool(manifest.get('complete'))}\n"
        f"{rows}\n"
        "Do not regenerate produced or dispositioned items. Continue only the missing or invalid roles "
        "under the same frozen AnalysisPlan.\n"
        "[END PRIOR SCIENTIFIC OUTPUT MANIFEST]"
    )


class _StreamStallWatchdog:
    """Close an LLM stream when no chunk arrives within ``idle_seconds``.

    A wedged provider stream can stay open while never delivering a chunk
    (transport-level read timeouts do not fire once SSE keep-alive bytes
    start flowing). Without this watchdog the whole research turn waits
    forever; observed on 2026-08-31 13:56→16:28 where a call issued at
    13:56:24 never returned and the job had to be recovered by restart.
    """

    def __init__(self, stream, idle_seconds: float, poll_seconds: float = 5.0):
        self._stream = stream
        self._idle_seconds = float(idle_seconds)
        self._poll_seconds = poll_seconds
        self._last_touch = time.monotonic()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="llm-stream-watchdog", daemon=True
        )
        self._thread.start()

    def touch(self) -> None:
        self._last_touch = time.monotonic()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            stalled_for = time.monotonic() - self._last_touch
            if stalled_for <= self._idle_seconds:
                continue
            log.warning(
                "[llm] no stream chunk for %.0fs; aborting stalled stream", stalled_for
            )
            try:
                close = getattr(self._stream, "close", None)
                if callable(close):
                    close()
            except Exception:
                # Cancellation is best effort; the read side will surface the
                # resulting transport error through the normal retry path.
                pass
            return


class BusinessAgent(RToolsMixin):
    MAX_ITERATIONS = 40

    # Approximate chars-per-token ratio for fast estimation (conservative)
    _CHARS_PER_TOKEN = 3.5
    # Reserve headroom for the response + tools list (default for large windows)
    _CONTEXT_RESERVE = 12000
    # Reasoning effort for continuation rounds (see _round_reasoning_effort).
    TOOL_ROUND_REASONING_EFFORT = "high"
    # Retry budget for streams that drop mid-iteration (per research turn).
    MAX_STREAM_RETRIES = 2
    # Wire-format keys kept when internal messages are sent to the provider.
    _API_MESSAGE_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")

    def _context_reserve(self) -> int:
        """Headroom reserved for the response + tools list.

        For small context windows the fixed 12k reserve would exceed the whole
        window and drive the prune budget negative — scale it down to at most
        ~30% of the window so a usable budget always remains.
        """
        window = self._get_context_window()
        return min(self._CONTEXT_RESERVE, max(1000, int(window * 0.3)))

    def __init__(
        self,
        client,
        model: str,
        data_source=None,
        combined_schema: Optional[str] = None,
        all_sources: Optional[List] = None,
        merged_source=None,
        enable_thinking: bool = False,
        thinking_budget: int = 8000,
        reasoning_effort: Optional[str] = None,
        chart_store: Optional[dict] = None,
        session_chart_ids: Optional[List[str]] = None,
        session_id: str = "",
        research_dataset_id: str = "",
        workspace_id: Optional[str] = None,
        job_runner=None,
        context_window: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        analysis_plan: AnalysisPlan | None = None,
        prior_portfolio_manifest: Optional[dict[str, Any]] = None,
        workspace_generation: str = "",
        conversation_job_id: str = "",
        model_sharing_policy: dict | None = None,
    ):
        self.client = client
        self._model_egress = ModelEgressGuard(model_sharing_policy)
        self.model = model
        self.data_source = data_source
        # All active DataSource objects — used by _route_query for multi-source routing
        self._all_sources: List = all_sources if all_sources else ([data_source] if data_source else [])
        # MergedDataSource — single DuckDB connection covering all active sources.
        # When present, cross-source SQL (containing src{N}__ prefixes) is routed here.
        self._merged_source = merged_source
        # Pre-computed merged schema (multi-source); takes priority over single-source schema
        self._combined_schema: Optional[str] = combined_schema
        self.enable_thinking = enable_thinking
        self.thinking_budget = thinking_budget
        self.reasoning_effort = reasoning_effort
        # User-configured context window (from the model config). When set, it
        # overrides the model-name heuristic so the compaction trigger and the
        # frontend context bar use the exact same number.
        self._configured_context_window: Optional[int] = (
            context_window if context_window and context_window > 0 else None
        )
        self._schema_cache: Optional[str] = None
        self._chart_store: dict = chart_store if chart_store is not None else {}
        self._session_chart_ids: List[str] = session_chart_ids if session_chart_ids is not None else []
        self._session_id: str = session_id
        self._research_dataset_id: str = str(research_dataset_id or "")
        # C5: freeze workspace identity at Agent creation.  ``None`` keeps
        # compatibility with direct/test construction by snapshotting the
        # current session binding exactly once; an explicit empty string means
        # this turn was created without a mounted user workspace.
        self._workspace_id: str = (
            str(workspace_manager.workspace_id_for_session(session_id) or "")
            if workspace_id is None
            else str(workspace_id or "")
        )
        self._job_runner = job_runner
        self._active_job_id: str = ""
        self._analysis_plan: AnalysisPlan | None = analysis_plan
        self._workflow_run_spec: WorkflowRunSpec | None = None
        self._prior_portfolio_manifest = dict(prior_portfolio_manifest or {})
        self._research_workspace_generation = str(workspace_generation or "")
        self._conversation_job_id = str(conversation_job_id or "")
        # Optional live-progress publisher (set by the chat layer); called
        # with short human-readable lines while R executes.
        self._progress_publisher = None
        # Cap for a single LLM response. Defaults to 131072 (safe upper bound);
        # caller should pass cfg.max_output_tokens so it matches the model's limit.
        self._max_output_tokens: int = (
            max_output_tokens if max_output_tokens and max_output_tokens > 0 else 131072
        )
        try:
            from r_runtime.config import is_research_mode

            self._research_mode = is_research_mode()
        except Exception:
            self._research_mode = False

    def _analysis_dataset_profile(self) -> dict[str, Any]:
        dataset = self._resolve_research_dataset("")
        summary = (dataset.profile.get("summary") or {}) if isinstance(dataset.profile, dict) else {}
        variables = summary.get("variables") if isinstance(summary, dict) else []
        profile = dict(summary) if isinstance(summary, dict) else {}
        profile.update({
            "dataset_id": dataset.dataset_id,
            "format": dataset.format,
            "columns": variables if isinstance(variables, list) else [],
            "assets": [asset.to_dict() for asset in dataset.assets],
            "asset_formats": list(dict.fromkeys(asset.format for asset in dataset.assets)),
            "workspace_generation": self._research_workspace_generation,
            "profile_revision": str(summary.get("profile_revision") or ""),
        })
        scientific = summary.get("scientific") if isinstance(summary, dict) else {}
        object_kind = str(summary.get("object_kind") or "") if isinstance(summary, dict) else ""
        modality = str(summary.get("modality") or "").strip().casefold()
        representation = str(summary.get("representation") or "").strip().casefold()
        object_tokens = {
            token
            for token in re.split(r"[/,;|\s]+", object_kind.casefold())
            if token
        }
        tabular_markers = {
            "data.frame",
            "dataframe",
            "data_frame",
            "tbl_df",
            "tbl",
            "table",
            "tabular",
        }
        is_tabular = (
            representation in tabular_markers
            or bool(object_tokens & tabular_markers)
            or (not object_kind and modality in {"table", "tabular"})
            or (not object_kind and not representation and not modality)
        )
        profile["input_shape"] = "tabular" if is_tabular else "r_object"
        if isinstance(scientific, dict):
            profile["scientific"] = dict(scientific)
        for field in ("object_kind", "modality", "representation", "processing_stage"):
            if summary.get(field) is not None:
                profile[field] = summary.get(field)
        return profile

    def _active_workflow_run_spec(self) -> WorkflowRunSpec | None:
        plan = self._analysis_plan
        if plan is None:
            self._workflow_run_spec = None
            return None
        cached = self._workflow_run_spec
        if cached is not None and cached.source_plan_id == plan.plan_id:
            return cached
        spec = default_workflow_registry().compile(
            plan,
            dataset_profile=self._analysis_dataset_profile(),
        )
        self._workflow_run_spec = spec
        return spec

    def _tool_set_analysis_plan(self, args: dict[str, Any]) -> str:
        active_plan = getattr(self, "_analysis_plan", None)
        if active_plan is not None:
            source_label = "模板" if active_plan.source == "template" else "自由对话"
            raise ValueError(
                f"当前{source_label}分析计划已经冻结；AI 不能在同一分析生命周期中覆盖它。"
                + (
                    "请在模板的分析输入中修改变量角色，系统会重新校验并冻结新计划。"
                    if active_plan.source == "template"
                    else "如需更换研究问题、变量角色或方法，请开始一轮新的分析；继续当前结果时必须沿用原计划。"
                )
            )
        outputs: list[RequiredOutput] = []
        output_roles: set[str] = set()
        for item in args.get("required_outputs") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", role):
                raise ValueError(f"Invalid output role: {role!r}")
            if role in output_roles:
                raise ValueError(f"Duplicate output role: {role}")
            output_roles.add(role)
            kind = str(item.get("kind") or "").strip()
            if kind not in {"table", "figure", "text", "model"}:
                raise ValueError(f"Invalid output kind: {kind}")
            outputs.append(
                RequiredOutput(
                    role=role,
                    kind=kind,  # type: ignore[arg-type]
                    required=bool(item.get("required", True)),
                    label=str(item.get("label") or "").strip(),
                )
            )
        resource_class = str(args.get("resource_class") or "standard").strip()
        if resource_class not in {"standard", "extended"}:
            raise ValueError(f"Invalid resource class: {resource_class}")
        bindings = {
            str(role).strip(): str(column).strip()
            for role, column in dict(args.get("variable_bindings") or {}).items()
            if str(role).strip() and str(column).strip()
        }
        analysis_inputs: list[ResolvedInput] = []
        for item in args.get("analysis_inputs") or []:
            if not isinstance(item, dict):
                continue
            input_id = re.sub(
                r"[^a-z0-9_]+",
                "_",
                str(item.get("id") or "").casefold(),
            ).strip("_")
            label = str(item.get("label") or input_id).strip()
            kind = str(item.get("kind") or "").strip()
            provenance = str(item.get("provenance") or "inferred").strip()
            depends_on = str(item.get("depends_on") or "").strip()
            if not input_id or kind not in {
                "dataset",
                "column",
                "columns",
                "level",
                "parameter",
                "object",
                "specification",
            }:
                raise ValueError("Invalid typed analysis input")
            if provenance not in {"user", "inferred", "default"}:
                provenance = "inferred"
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 1.0))))
            except (TypeError, ValueError):
                confidence = 1.0
            analysis_inputs.append(
                ResolvedInput(
                    input_id=input_id,
                    label=label,
                    kind=kind,  # type: ignore[arg-type]
                    value=item.get("value"),
                    provenance=provenance,  # type: ignore[arg-type]
                    confidence=confidence,
                    depends_on=depends_on,
                )
            )
        draft = ConversationPlanDraft(
            intent=str(args.get("intent") or "").strip(),
            category=str(args.get("category") or "").strip(),
            method_family=str(args.get("method_family") or "").strip(),
            scenario=str(args.get("scenario") or "").strip(),
            variable_bindings=bindings,
            analysis_inputs=tuple(analysis_inputs),
            package_queries=tuple(
                str(item).strip() for item in args.get("package_queries") or [] if str(item).strip()
            ),
            prerequisites=tuple(
                str(item).strip() for item in args.get("prerequisites") or [] if str(item).strip()
            ),
            assumptions=tuple(
                str(item).strip() for item in args.get("assumptions") or [] if str(item).strip()
            ),
            diagnostics=tuple(
                str(item).strip() for item in args.get("diagnostics") or [] if str(item).strip()
            ),
            required_outputs=tuple(outputs),
            resource_class=resource_class,  # type: ignore[arg-type]
        )
        resolver = RuntimeCapabilityResolver(
            self._tool_get_r_package_inventory,
            self._tool_get_r_package_help,
        )
        planner = AnalysisPlanner(AnalysisCatalog(), resolver)
        self._analysis_plan = planner.from_conversation(draft, self._analysis_dataset_profile())
        self._workflow_run_spec = default_workflow_registry().compile(
            self._analysis_plan,
            dataset_profile=self._analysis_dataset_profile(),
        )
        from data.analysis_plan_store import AnalysisPlanStore

        AnalysisPlanStore().save(self._session_id, self._analysis_plan)
        dependency_gate = inspect_dependency_gate(
            packages=self._analysis_plan.packages,
            intent=" ".join(
                (
                    self._analysis_plan.intent,
                    self._analysis_plan.method_family,
                    *self._analysis_plan.prerequisites,
                )
            ),
            mode="candidate",
            session_id=self._session_id,
            plan_id=self._analysis_plan.plan_id,
            template_id=self._analysis_plan.template_id,
        )
        return json.dumps(
            {
                "ok": True,
                "analysis_plan": asdict(self._analysis_plan),
                "dependency_gate": dependency_gate,
            },
            ensure_ascii=False,
        )

    def _analysis_plan_allows_r(self) -> tuple[bool, str]:
        if self._analysis_plan is None:
            return False, "Call set_analysis_plan before run_r_code for this natural-language request."
        active_dataset_id = str(self._analysis_dataset_profile().get("dataset_id") or "")
        if self._analysis_plan.dataset_id != active_dataset_id:
            return False, "The analysis plan belongs to a different dataset; create a new plan."
        return True, ""

    def _apply_reasoning_options(
        self, call_kwargs: Dict[str, Any], effort: Optional[str] = None
    ) -> None:
        if not self.enable_thinking:
            return
        if str(self.model or "").startswith("deepseek"):
            from LLM.llm_config_manager import deepseek_reasoning_options

            call_kwargs.update(deepseek_reasoning_options(effort or self.reasoning_effort or "max"))

    def _round_reasoning_effort(self, completed_rounds: int) -> str:
        """Reasoning effort for one LLM round of the research loop.

        Round 0 (planning and first code writing) keeps the user-configured
        effort. Continuation rounds only continue an already frozen plan after
        a tool result; at the configured maximum they repeatedly produced
        30k–85k-token thinking blocks that dominated wall time (minutes per
        round against a 1M-context model).
        """
        from LLM.llm_config_manager import normalize_deepseek_reasoning_effort

        base = self.reasoning_effort or "max"
        if str(self.model or "").startswith("deepseek"):
            base = normalize_deepseek_reasoning_effort(base)
        if completed_rounds <= 0:
            return base
        override = str(os.getenv("BAA_TOOL_ROUND_REASONING_EFFORT", "") or "").strip()
        effort = override or self.TOOL_ROUND_REASONING_EFFORT
        if str(self.model or "").startswith("deepseek"):
            return normalize_deepseek_reasoning_effort(effort, default="high")
        return effort

    @staticmethod
    def _messages_for_api(messages: List[Dict]) -> List[Dict]:
        """Project internal message dicts onto the API wire format.

        DeepSeek V4 thinking mode REQUIRES ``reasoning_content`` on every
        assistant message that carries ``tool_calls`` — stripping it causes
        a 400 error ("must be passed back to the API").  However, carrying
        the FULL text across many rounds caused 30+ minute runs (the
        original problem).  The compromise: keep it on ALL tool-call
        assistant messages but truncate each to 2000 chars (~500 tokens),
        which limits cumulative bloat while satisfying the protocol.

        Internal bookkeeping fields (``_compaction_summary`` etc.) are
        always removed.
        """
        _RC_MAX_CHARS = 2000

        projected = []
        for message in messages:
            if not isinstance(message, dict):
                projected.append(message)
                continue
            cleaned = {key: value for key, value in message.items() if key in BusinessAgent._API_MESSAGE_KEYS}
            rc = message.get("reasoning_content")
            if rc and message.get("tool_calls"):
                rc = str(rc)
                cleaned["reasoning_content"] = rc[:_RC_MAX_CHARS] + ("…" if len(rc) > _RC_MAX_CHARS else "")
            projected.append(cleaned)
        return projected

    @staticmethod
    def _is_transient_stream_error(exc: BaseException) -> bool:
        """True for transport drops that happened while consuming a stream."""
        try:
            import httpx

            if isinstance(
                exc,
                (
                    httpx.StreamClosed,
                    httpx.RemoteProtocolError,
                    httpx.ReadError,
                    httpx.WriteError,
                    httpx.ReadTimeout,
                ),
            ):
                return True
        except ImportError:
            pass
        try:
            from openai import APIConnectionError

        except ImportError:
            return False
        return isinstance(exc, APIConnectionError)

    def _workspace_runtime(self):
        """Resolve only the runtime frozen for this Agent turn (C5)."""
        if not self._workspace_id:
            return None
        return workspace_manager.get_by_workspace(self._workspace_id)

    def _workspace_path_authorization(self):
        """Return the SQL sandbox capability for this Agent's fixed Workspace."""
        return workspace_manager.path_authorization(self._workspace_id)

    def _run_as_job(self, fn, job_type: str, label: str = ""):
        """Submit ``fn(ctx)`` and bridge its persisted events into Agent output.

        B2-B4 call this helper after their payload/module-level worker functions
        are introduced. Keeping the bridge here preserves the current
        tool-call -> result -> continued reasoning flow without letting workers
        touch the LLM client or SSE generator.
        """
        if self._job_runner is None:
            raise RuntimeError("JobRunner is not available for this session")
        jid = self._job_runner.create(fn, job_type=job_type, label=label)
        self._active_job_id = jid
        try:
            for event in self._job_runner.iter_events(jid):
                yield event
            job = self._job_runner.get_status(jid)
            if job is None:
                raise RuntimeError(f"job disappeared: {jid}")
            return job
        finally:
            current = self._job_runner.get_status(jid)
            if current is not None:
                from data.jobs_store import _TERMINAL

                if current["status"] not in _TERMINAL:
                    self._job_runner.cancel(jid)
            self._active_job_id = ""

    # ── Context helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len(text) / BusinessAgent._CHARS_PER_TOKEN))

    def _estimate_messages_tokens(self, messages: List[Dict]) -> int:
        total = 0
        for m in messages:
            total += content_token_estimate(m.get("content"), self._CHARS_PER_TOKEN)
            # tool_calls entries add overhead
            if m.get("tool_calls"):
                total += self._estimate_tokens(json.dumps(m["tool_calls"]))
        return total

    def _hard_prune(
        self, system_msgs: List[Dict], history: List[Dict], extra_msgs: List[Dict], context_window: int
    ) -> List[Dict]:
        """Hard truncation safety net: drop oldest messages until tokens fit.

        Two protections beyond a naive pop(0):
          1. A compaction summary message (tagged ``_compaction_summary``) is
             never dropped — it IS the compressed earlier context.
          2. After pruning, the surviving head is never an orphan ``role: tool``
             message; OpenAI requires every tool message to follow the
             assistant message that holds its tool_calls.
        """
        budget = context_window - self._context_reserve()
        fixed_tokens = self._estimate_messages_tokens(system_msgs + extra_msgs)
        available = budget - fixed_tokens

        pruned = list(history)
        before = len(pruned)

        # Pin a leading compaction-summary message so it survives pruning.
        pinned: List[Dict] = []
        if pruned and pruned[0].get("_compaction_summary"):
            pinned = [pruned.pop(0)]

        def _fits() -> bool:
            return self._estimate_messages_tokens(pinned + pruned) <= available

        while pruned and not _fits():
            pruned.pop(0)
            # Don't leave the head as an orphan tool message.
            while pruned and pruned[0].get("role") == "tool":
                pruned.pop(0)

        result = pinned + pruned
        if len(result) < before:
            log.info(
                "[context] hard-pruned %d→%d turns (budget=%d tokens)",
                before,
                len(result),
                available,
            )
        return result

    def _inloop_context_guard(
        self,
        messages: List[Dict],
        turn_start: int,
        history_end: int,
        context_window: int,
    ) -> int:
        """Mid-turn context watermark defense; returns the (new) turn start.

        Trim/compact only run at turn entry; inside a long analysis turn the
        message list only grows (bounded R results plus up to six images per
        artifact review), and an overflowing request fails the whole turn at
        the provider.  Once the estimate crosses 90% of the window:

          1. hard-prune the oldest history (summary-pinned, orphan-safe);
          2. if still over, strip image blocks from the oldest artifact
             reviews of this turn — numeric previews survive, figures don't.

        Only ever touches the history head and in-turn image blocks, so
        assistant(tool_calls)/tool adjacency is preserved.  Pruning shifts the
        in-turn messages left; the caller must store the returned value back
        into ``_turn_start_idx``.

        Args:
            messages: the live message list (mutated in place).
            turn_start: index where this turn's own messages begin.
            history_end: index one past the last history message (messages
                between ``history_end`` and ``turn_start`` are the fixed
                prior-reasoning/user messages and are never pruned).
            context_window: the model context window.
        """
        threshold = int(context_window * 0.90)
        est = self._estimate_messages_tokens(messages)
        if est < threshold:
            return turn_start
        system_msgs = messages[:1]
        hist = messages[1:history_end]
        extras = messages[history_end:turn_start]
        turn_msgs = messages[turn_start:]
        pruned = self._hard_prune(
            system_msgs, hist, extras + turn_msgs, context_window
        )
        messages[:] = system_msgs + pruned + extras + turn_msgs
        turn_start -= len(hist) - len(pruned)
        est = self._estimate_messages_tokens(messages)
        if est < threshold:
            log.info(
                "[context] mid-loop prune: history %d→%d msgs, ~%d/%d tokens",
                len(hist),
                len(pruned),
                est,
                context_window,
            )
            return turn_start
        degraded = 0
        # Never strip the newest message — the model is most likely acting on it.
        for idx in range(1, len(messages) - 1):
            msg = messages[idx]
            if (
                msg.get("kind") == "artifact_review"
                and isinstance(msg.get("content"), list)
                and any(
                    isinstance(block, dict)
                    and str(block.get("type") or "") in {"image_url", "image", "image_ref"}
                    for block in msg["content"]
                )
            ):
                messages[idx] = {
                    **msg,
                    "content": strip_images_for_compaction(msg["content"]),
                }
                degraded += 1
                est = self._estimate_messages_tokens(messages)
                if est < threshold:
                    break
        if degraded:
            log.warning(
                "[context] mid-loop image degradation: stripped %d artifact review(s), ~%d/%d tokens",
                degraded,
                est,
                context_window,
            )
        return turn_start

    def _get_context_window(self) -> int:
        """Context window for the current model.

        Priority:
          1. User-configured value (cfg.context_window, set in 「模型设置」).
             This is the recommended path — it matches the frontend context bar
             exactly and requires no inference from the model name.
          2. Known provider defaults from LLM/llm_config_manager (fallback).
             The built-in DeepSeek entry ships its real window (1M), so an
             unset value no longer falls back to the ancient 32K default and
             trigger premature compaction on a large-window model.
          3. 32768 as the last resort for unknown custom models.
             If you add a model whose context window differs from these
             fallbacks, set it explicitly in the model config.
        """
        if self._configured_context_window:
            return self._configured_context_window
        return self._provider_default_context_window()

    def _provider_default_context_window(self) -> int:
        """Best-known window for the configured model family (fallback only).

        Single source of truth is ``LLM/llm_config_manager.DEFAULT_CONFIGS``;
        matched by provider key or the provider's default model id appearing
        in the configured model name.
        """
        try:
            from LLM.llm_config_manager import LLMConfigManager

            model = str(self.model or "").lower()
            for provider, cfg in LLMConfigManager.DEFAULT_CONFIGS.items():
                candidates = {str(provider).lower(), str(cfg.get("model", "")).lower()}
                if any(c and (c == model or c in model) for c in candidates):
                    window = int(cfg.get("context_window") or 0)
                    if window > 0:
                        return window
        except Exception:
            pass
        return 32768

    def _adaptive_thinking_budget(self, remaining_tokens: int) -> int:
        """Scale thinking budget so it never exceeds ~40% of what's left."""
        cap = int(remaining_tokens * 0.4)
        return max(1000, min(self.thinking_budget, cap))

    def set_progress_publisher(self, publisher) -> None:
        """Attach a callback receiving short R-progress lines for the UI."""
        self._progress_publisher = publisher

    def set_data_source(self, source):
        self.data_source = source
        self._schema_cache = None

    def _tool_workspace_status(self) -> str:
        """Return a bounded summary of system roots and optional user workdir."""
        try:
            runtime = self._workspace_runtime()
            result = {
                "system_workspace": workspace_manager.system_status(),
                "user_workspace": {"mounted": runtime is not None},
                "usage": (
                    "When a user workspace is mounted, omit path (or use workspace://user) to search it first. "
                    "Use explicit path uploads, outputs, or mcp only when the user refers to those roots; "
                    "then use workspace_grep or workspace_read_file only for relevant files."
                ),
            }
            if runtime is not None:
                files = runtime.list_data_files(max_files=5)
                result["user_workspace"] = {
                    "mounted": True,
                    "uri": "workspace://user",
                    "workdir": str(runtime.workdir),
                    "artifacts_dir": str(runtime.artifacts_dir),
                    "recent_data_files": files,
                    "recent_files_truncated": len(files) >= 5,
                    "data_source_state": "mounted files are already registered as tables",
                }
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            log.warning("[agent] workspace_status failed: %s", e)
            return f"Unable to check workspace status: {e}"

    # ── Agent loop ────────────────────────────────────────────────────────────

    def _compose_research_system(
        self,
        *,
        temp_prompt: str = "",
        recovery_context: str = "",
    ) -> str:
        """Build the research-mode system prompt without BI/SQL preview context."""
        system = get_system_prompt()
        if temp_prompt:
            system += build_temp_prompt_section(temp_prompt)
        if recovery_context:
            system += (
                "\n\n[RECOVERED ACTIVE CONTEXT]\n"
                + recovery_context[:6000]
                + "\n[END RECOVERED ACTIVE CONTEXT]"
            )
        if self._analysis_plan is not None:
            system += _analysis_plan_system_section(self._analysis_plan)
            workflow_spec = self._active_workflow_run_spec()
            if workflow_spec is not None:
                system += workflow_system_section(workflow_spec)
        if self._prior_portfolio_manifest:
            system += _portfolio_manifest_system_section(self._prior_portfolio_manifest)
        return system

    def run(
        self,
        user_message: str,
        history: List[Dict],
        last_reasoning: str = "",
        last_prompt_tokens: int = 0,
        temp_prompt: str = "",
        data_context: Optional[Dict] = None,
        recovery_context: str = "",
        vision_images: Optional[List[VisionImage]] = None,
        compaction_state: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict]:
        """Yield SSE events for one research-mode agent turn.

        ``compaction_state`` is the rolling-summary record stored on the
        session (see agent/compaction.py).  When this turn produces an updated
        record, a ``compaction_state`` event is yielded for the caller to
        persist.
        """
        if data_context:
            log.debug(
                "[run] ignoring legacy data_context keys=%s",
                sorted(str(key) for key in data_context),
            )
        yield from self._run_research_loop(
            user_message,
            history,
            last_reasoning=last_reasoning,
            last_prompt_tokens=last_prompt_tokens,
            temp_prompt=temp_prompt,
            recovery_context=recovery_context,
            vision_images=vision_images,
            compaction_state=compaction_state,
        )

    # ── Research loop: extracted sub-steps ─────────────────────────────────

    def _build_llm_call_kwargs(
        self,
        messages: List[Dict],
        llm_round: int,
        force_visible_answer: bool,
        reduce_thinking: bool = False,
        *,
        result_assessment: ResultAssessment | None = None,
        diagnostic_status: dict[str, str] | None = None,
    ) -> tuple:
        """Build the LLM API call kwargs for one round.

        ``reduce_thinking`` preserves tool access during budget recovery;
        ``force_visible_answer`` is reserved for a validated final explanation.
        Returns ``(call_kwargs, downgraded_effort, available_tools)``; the effort
        field is used by the provider rejection fallback in the caller.
        """
        _available_tools = filter_tools_for_turn(AGENT_TOOLS)
        if self._analysis_plan is not None:
            _available_tools = [
                schema
                for schema in _available_tools
                if str((schema.get("function") or {}).get("name") or "")
                != "set_analysis_plan"
            ]
        # Large tool projections and history compaction can omit their attached
        # result_assessment. Refresh a compact authoritative view after pruning,
        # without storing another assessment or accumulating stale messages.
        model_messages = self._model_egress.messages(messages)
        if result_assessment is not None:
            facts = self._model_egress.project(
                _result_assessment_facts(result_assessment, diagnostic_status)
            )
            context_index = 1 if model_messages and model_messages[0].get("role") == "system" else 0
            model_messages.insert(context_index, {
                "role": "system",
                "content": build_result_assessment_section(facts),
            })
        call_kwargs: Dict[str, Any] = dict(
            model=self.model,
            messages=self._messages_for_api(model_messages),
            tools=_available_tools,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=self._max_output_tokens,
        )
        log.debug("[tools] exposed=%d", len(_available_tools))

        if self.enable_thinking and self.model.startswith("claude") and not (force_visible_answer or reduce_thinking):
            _ctx = self._get_context_window()
            _used = self._estimate_messages_tokens(messages)
            _remaining = max(4000, _ctx - _used)
            _budget = self._adaptive_thinking_budget(_remaining)
            call_kwargs["temperature"] = 1
            call_kwargs["extra_body"] = {"thinking": {"type": "enabled", "budget_tokens": _budget}}

        downgraded = ""
        if force_visible_answer:
            call_kwargs["tool_choice"] = "none"
        if force_visible_answer or reduce_thinking:
            if str(self.model or "").startswith("deepseek"):
                # A shorter retry must still be able to plan/execute research.
                # Disable thinking explicitly; changing tool_choice is a
                # separate decision made only after portfolio validation.
                call_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            effort = self._round_reasoning_effort(llm_round)
            self._apply_reasoning_options(call_kwargs, effort=effort)
            if effort != (self.reasoning_effort or "max"):
                downgraded = effort
        return call_kwargs, downgraded, _available_tools

    def _open_llm_stream(self, call_kwargs: Dict[str, Any], downgraded_effort: str):
        """Open a streaming LLM connection with retry and effort fallback.

        Returns the stream object. Raises on unrecoverable failure.
        """
        for attempt in range(2):
            try:
                return _call_with_retry(self.client.chat.completions.create, **call_kwargs)
            except Exception as exc:
                message = str(exc).lower()
                effort_rejected = (
                    getattr(exc, "status_code", 400) in {400, 422}
                    and "reasoning_effort" in message
                    and any(word in message for word in ("invalid", "unsupported", "not supported", "not permitted"))
                )
                if attempt == 0 and downgraded_effort and effort_rejected:
                    log.warning(
                        "[llm] reasoning_effort=%s rejected (%s); retrying with configured effort",
                        downgraded_effort, exc,
                    )
                    self._apply_reasoning_options(call_kwargs)
                    continue
                raise

    # ── Research loop: main generator ──────────────────────────────────────

    def _run_research_loop(
        self,
        user_message: str,
        history: List[Dict],
        last_reasoning: str = "",
        last_prompt_tokens: int = 0,
        temp_prompt: str = "",
        recovery_context: str = "",
        vision_images: Optional[List[VisionImage]] = None,
        compaction_state: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Dict]:
        """
        Yields event dicts consumed by the Flask SSE stream:
          {"type": "tool_start",    "tool": str, "display": str}
          {"type": "text_delta",    "content": str}
          {"type": "chart_html",    "html": str}
          {"type": "text",          "content": str}
          {"type": "usage",         ...}
          {"type": "reasoning",     "content": str}
          {"type": "done"}
          {"type": "error",         "message": str}
        """
        log.info("[run] research_loop model=%s", self.model)

        # Refilter stored tools/reviews before the compactor sees them; old
        # summaries are reusable only under this exact sharing policy.
        history = self._model_egress.messages(history)
        if not compaction_state or compaction_state.get("sharing_fingerprint") != self._model_egress.fingerprint:
            compaction_state = None
        system = self._compose_research_system(
            temp_prompt=temp_prompt,
            recovery_context=recovery_context,
        )

        prior_reasoning_msg: List[Dict] = []
        if last_reasoning:
            # Truncate reasoning to a reasonable cap but keep it meaningful
            summary = last_reasoning[:2000]
            prior_reasoning_msg = [
                {
                    "role": "system",
                    "content": (
                        f"[Prior turn reasoning summary]\n{summary}\n"
                        "[End of prior reasoning — use this context to inform your analysis "
                        "but do not repeat or reference it explicitly to the user.]"
                    ),
                }
            ]

        _system_msg = {"role": "system", "content": system}
        _user_msg = build_user_message(
            user_message,
            vision_images if self._model_egress.policy.send_attached_images else [],
        )
        _ctx_window = self._get_context_window()

        # ── Rule-based trim (60–70% zone, zero LLM cost) ─────────────────────
        # Before considering semantic compaction, do a cheap pass that truncates
        # oversized tool result messages (large query outputs etc.).  This alone
        # is often enough to bring the context back under the compaction threshold.
        if should_trim_history(
            history=history,
            last_prompt_tokens=last_prompt_tokens,
            context_window=_ctx_window,
            chars_per_token=self._CHARS_PER_TOKEN,
        ):
            history, _n_trimmed = trim_oversized_tool_results(history)
            if _n_trimmed:
                log.info("[trim] rule-based trim: shortened %d tool result(s)", _n_trimmed)

        # ── Semantic compaction (with frontend animation) ─────────────────────
        # Trigger口径与前端上下文条一致：用上一轮真实 prompt_tokens / context_window，
        # 达到 70% 即压缩。last_prompt_tokens 由 chat.py 从会话中传入。
        #
        # Cross-turn reuse: chat.py passes the rolling-summary record stored on
        # the session.  While the rebuilt view fits comfortably below the
        # trigger it is reused for free — no summarizer call, no repeated
        # per-turn cost for the same prefix.  Only when the uncovered tail has
        # grown past that point does the summary roll forward (previous
        # summary + new segment merged into one), and the updated record is
        # handed back to the caller via a ``compaction_state`` event.
        from .compaction import _estimate_history_tokens

        _working_history: Optional[List[Dict]] = None
        _needs_compact = should_compact_history(
            history=history,
            last_prompt_tokens=last_prompt_tokens,
            context_window=_ctx_window,
            chars_per_token=self._CHARS_PER_TOKEN,
        )
        if _needs_compact:
            _reused_view = reuse_compaction_view(history, compaction_state)
            if _reused_view is not None and reuse_view_is_small_enough(
                _reused_view, _ctx_window, self._CHARS_PER_TOKEN
            ):
                _working_history = _reused_view
                log.info(
                    "[compaction] reused stored summary (%d messages covered) — no summarizer call",
                    int((compaction_state or {}).get("covered") or 0),
                )
                _est_after = _estimate_history_tokens(
                    _working_history, self._CHARS_PER_TOKEN
                ) + self._estimate_messages_tokens([_system_msg] + prior_reasoning_msg + [_user_msg])
                yield {
                    "type": "context_estimate",
                    "prompt_tokens": _est_after,
                    "context_window": _ctx_window,
                    "estimated": True,
                }
        if _needs_compact and _working_history is None:
            # First compaction of this conversation, or the stored summary no
            # longer covers enough — call the summarizer and emit the new
            # rolling record for persistence.
            _est = _estimate_history_tokens(history, self._CHARS_PER_TOKEN)
            _used = max(last_prompt_tokens or 0, _est)
            _pct = int(_used / _ctx_window * 100) if _ctx_window else 0
            yield {
                "type": "tool_start",
                "tool": "compaction",
                "display": "压缩对话历史…",
                "detail": f"上下文使用已达 {_pct}%，正在语义压缩以节省上下文空间",
            }
            _working_history, _compacted, _new_state = compact_history_rolling(
                history=history,
                client=self.client,
                model=self.model,
                prior_state=compaction_state,
            )
            yield {"type": "tool_end", "tool": "compaction"}
            log.info(
                "[compaction] trigger≈%d/%d tokens (%d%%) compacted=%s",
                _used,
                _ctx_window,
                _pct,
                _compacted,
            )
            if _compacted:
                _est_after = _estimate_history_tokens(
                    _working_history, self._CHARS_PER_TOKEN
                ) + self._estimate_messages_tokens([_system_msg] + prior_reasoning_msg + [_user_msg])
                yield {
                    "type": "context_estimate",
                    "prompt_tokens": _est_after,
                    "context_window": _ctx_window,
                    "estimated": True,
                }
                if _new_state:
                    yield {
                        "type": "compaction_state",
                        "summary": _new_state["summary"],
                        "covered": _new_state["covered"],
                        "boundary": _new_state["boundary"],
                        "sharing_fingerprint": self._model_egress.fingerprint,
                    }
        if _working_history is None:
            _working_history = history

        # ── Hard truncation safety net ────────────────────────────────────────
        _pruned_history = self._hard_prune(
            system_msgs=[_system_msg],
            history=_working_history,
            extra_msgs=prior_reasoning_msg + [_user_msg],
            context_window=_ctx_window,
        )
        _current_image_count = min(MAX_IMAGES_PER_TURN, len(vision_images or [])) if self._model_egress.policy.send_attached_images else 0
        _pruned_history = hydrate_messages_for_llm(
            _pruned_history,
            self._session_id,
            image_budget=MAX_IMAGES_PER_TURN - _current_image_count,
        )

        messages: List[Dict] = [
            _system_msg,
            *_pruned_history,
            *prior_reasoning_msg,
            _user_msg,
        ]
        # Track where this turn's new messages start (after system + history + user)
        _turn_start_idx = len(messages)
        _turn_artifact_reviews: List[Dict[str, Any]] = []

        def _tool_history_event() -> Dict[str, Any] | None:
            turn_msgs = [
                message
                for message in messages[_turn_start_idx:]
                if message.get("role") in {"assistant", "tool"}
            ]
            turn_msgs.extend(_turn_artifact_reviews)
            if not turn_msgs:
                return None
            return {"type": "tool_history", "messages": turn_msgs}

        pending_charts: List[str] = []
        all_reasoning: List[str] = []
        _consecutive_errors = 0
        _had_successful_artifacts = False
        _run_r_calls = 0
        _inspect_dataset_calls = 0
        _r_repair_policy = RRepairPolicy()
        _r_stop_decision = None
        _prior_evidence = validation_evidence_from_manifest(
            self._prior_portfolio_manifest,
            dataset_id=(self._analysis_plan.dataset_id if self._analysis_plan is not None else ""),
            plan_id=(self._analysis_plan.plan_id if self._analysis_plan is not None else ""),
        )
        _plan_artifacts: list[dict] = list(_prior_evidence.artifacts)
        _plan_diagnostic_status: dict[str, str] = dict(_prior_evidence.diagnostic_status)
        _plan_r_succeeded = _prior_evidence.r_executed
        _latest_assessment = (
            ResultValidator().validate(
                self._analysis_plan,
                {
                    "dataset_id": self._analysis_plan.dataset_id,
                    "plan_id": self._analysis_plan.plan_id,
                    "status": "succeeded",
                    "diagnostic_status": dict(_plan_diagnostic_status),
                },
                _plan_artifacts,
            )
            if self._analysis_plan is not None and _prior_evidence.r_executed
            else None
        )
        _completion_nudges = 0
        _force_visible_answer = False
        _reduce_thinking = False
        _length_budget_retried = False
        _run_start = time.monotonic()
        try:
            # A local scientific workflow has no hosted-era wall-clock ceiling
            # by default.  An operator may still set a positive deadline, and
            # the user can always stop the active Analysis Job explicitly.
            _MAX_RUN_SECONDS = max(0, int(os.getenv("BAA_AGENT_MAX_RUN_SECONDS", "0")))
        except (TypeError, ValueError):
            _MAX_RUN_SECONDS = 0
        try:
            _MAX_ITERATIONS = max(
                8, min(100, int(os.getenv("BAA_AGENT_MAX_ITERATIONS", str(self.MAX_ITERATIONS))))
            )
        except (TypeError, ValueError):
            _MAX_ITERATIONS = self.MAX_ITERATIONS
        try:
            # Local desktop analyses are user-cancellable and resource
            # monitored by the runtime.  Therefore zero is the default and
            # means "no artificial per-turn R-call ceiling".  Administrators
            # can still opt into a positive cap for unusually constrained
            # machines.
            _MAX_R_CALLS = max(0, min(200, int(os.getenv("BAA_AGENT_MAX_R_CALLS", "0"))))
        except (TypeError, ValueError):
            _MAX_R_CALLS = 0
        _MAX_CONSECUTIVE_ERRORS = 3
        _llm_round = 0
        _stream_retry_count = 0

        for _ in range(_MAX_ITERATIONS):
            _llm_round += 1
            _round_visible_yielded = False
            # ── Hard exit guards ──────────────────────────────────────────────
            if self._conversation_job_id and research_run_controller.is_cancelled(
                self._conversation_job_id
            ):
                # The user stopped this run; do not open another LLM round.
                log.info("[run] round skipped: run cancelled")
                yield {"type": "done"}
                return
            if _MAX_RUN_SECONDS > 0 and time.monotonic() - _run_start > _MAX_RUN_SECONDS:
                log.warning("[run] time limit reached (%.0fs)", _MAX_RUN_SECONDS)
                if self._analysis_plan is not None:
                    decision = AnalysisCompletionController().decide(
                        self._analysis_plan,
                        _latest_assessment,
                        r_calls=_MAX_R_CALLS,
                        max_r_calls=_MAX_R_CALLS,
                        nudge_count=3,
                    )
                    yield {
                        "type": "text",
                        "content": decision.user_message
                        or "本次科研产物组合已完成，结果已整理到结果页。",
                    }
                elif _had_successful_artifacts:
                    yield {
                        "type": "text",
                        "content": "本次结果已整理到结果页；已达到本轮安全执行时限。",
                    }
                else:
                    yield {"type": "text", "content": "分析超时，已终止。请尝试缩小问题范围后重试。"}
                history_event = _tool_history_event()
                if history_event:
                    yield history_event
                yield {"type": "done"}
                return
            if _consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                log.warning("[run] %d consecutive tool errors, aborting", _consecutive_errors)
                if self._analysis_plan is not None:
                    decision = AnalysisCompletionController().decide(
                        self._analysis_plan,
                        _latest_assessment,
                        r_calls=_MAX_R_CALLS,
                        max_r_calls=_MAX_R_CALLS,
                        nudge_count=3,
                    )
                    content = decision.user_message or "分析产物已保存，但连续工具调用失败。"
                else:
                    content = "连续工具调用失败，已终止。请检查数据源连接或简化查询。"
                yield {"type": "text", "content": content}
                yield {"type": "done"}
                return

            # ── Mid-loop context watermark ────────────────────────────────────
            # Trim/compact run only at turn entry; a long analysis turn can
            # still overflow the window from R results plus artifact-review
            # images alone.  Enforce a 90% watermark before every LLM round.
            if _ctx_window > 0:
                _turn_start_idx = self._inloop_context_guard(
                    messages,
                    _turn_start_idx,
                    _turn_start_idx - len(prior_reasoning_msg) - 1,
                    _ctx_window,
                )

            # ── LLM call setup (via extracted methods) ──────────────────────
            _scripted_code: Optional[str] = None
            if (
                _llm_round == 1
                and self._analysis_plan is not None
                and self._analysis_plan.source == "template"
                and not _force_visible_answer
            ):
                # Deterministic template path: execute reviewed script
                # before consulting the model for known statistical methods.
                _scripted_code = script_library.render_script(self._analysis_plan)

            call_kwargs, _downgraded_effort, _available_tools = self._build_llm_call_kwargs(
                messages, _llm_round - 1, _force_visible_answer, _reduce_thinking,
                result_assessment=_latest_assessment,
                diagnostic_status=_plan_diagnostic_status,
            )

            # ── Streaming path ────────────────────────────────────────────────
            call_kwargs["stream"] = True
            call_kwargs["stream_options"] = {"include_usage": True}
            _stall_seconds = stream_stall_seconds()
            _llm_timeout = default_llm_timeout(read_seconds=max(300.0, _stall_seconds))
            if _llm_timeout is not None:
                call_kwargs["timeout"] = _llm_timeout
            _t0 = time.monotonic()
            stream = None
            if _scripted_code is None:
                try:
                    stream = self._open_llm_stream(call_kwargs, _downgraded_effort)
                except Exception as exc:
                    log.error("[llm] API call failed after retries: %s", exc)
                    retryable, _ = _is_retryable(exc)
                    if retryable:
                        yield {"type": "error", "message": f"LLM 服务暂时不可用，请稍后重试: {exc}"}
                    else:
                        yield {"type": "error", "message": f"LLM 调用失败: {exc}"}
                    yield {"type": "done"}
                    return
            if self._conversation_job_id and hasattr(stream, "close"):
                research_run_controller.register(
                    self._conversation_job_id,
                    CallbackCancelHandle(stream.close),
                )

            tc_acc: Dict[int, Dict[str, str]] = {}
            content_parts: List[str] = []
            reasoning_parts: List[str] = []
            think_parser = ThinkTagStreamParser()
            usage_data = None
            finish_reason = None
            if _scripted_code is not None:
                finish_reason = "tool_calls"
                reasoning_parts.append("Deterministic template script executed before LLM consultation.")
                tc_acc[0] = {
                    "id": "baa-script-exec-1",
                    "name": "run_r_code",
                    "args": json.dumps({"code": _scripted_code}),
                }

            _watchdog = _StreamStallWatchdog(
                stream if stream is not None else [], idle_seconds=_stall_seconds
            )
            _watchdog.start()
            _stream_exc: Optional[BaseException] = None
            try:
                for chunk in ([] if _scripted_code is not None else stream):
                    _watchdog.touch()
                    if getattr(chunk, "usage", None):
                        usage_data = chunk.usage
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    delta = choice.delta

                    if delta.content:
                        visible_delta, tagged_reasoning = think_parser.feed(delta.content)
                        if visible_delta:
                            content_parts.append(visible_delta)
                            if self._analysis_plan is None:
                                _round_visible_yielded = True
                                yield {"type": "text_delta", "content": visible_delta}
                        if tagged_reasoning:
                            reasoning_parts.append(tagged_reasoning)

                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        reasoning_parts.append(rc)

                    if delta.tool_calls:
                        for tcd in delta.tool_calls:
                            idx = tcd.index
                            if idx not in tc_acc:
                                tc_acc[idx] = {"id": "", "name": "", "args": ""}
                            if tcd.id:
                                tc_acc[idx]["id"] = tcd.id
                            if tcd.function:
                                if tcd.function.name:
                                    tc_acc[idx]["name"] += tcd.function.name
                                if tcd.function.arguments:
                                    tc_acc[idx]["args"] += tcd.function.arguments
            except Exception as exc:
                _stream_exc = exc
            finally:
                _watchdog.stop()

            if _stream_exc is not None:
                if (
                    self._conversation_job_id
                    and research_run_controller.is_cancelled(self._conversation_job_id)
                ):
                    # A user stop closes the registered stream; the resulting
                    # transport error is the cancellation, not a failure.
                    log.info("[llm] stream closed by user stop; ending turn cleanly")
                    yield {"type": "done"}
                    return
                if (
                    self._is_transient_stream_error(_stream_exc)
                    and not _round_visible_yielded
                    and _stream_retry_count < self.MAX_STREAM_RETRIES
                ):
                    _stream_retry_count += 1
                    log.warning(
                        "[llm] stream interrupted (%s); re-issuing call (%d/%d)",
                        _stream_exc,
                        _stream_retry_count,
                        self.MAX_STREAM_RETRIES,
                    )
                    time.sleep(_stream_retry_count)
                    continue
                log.error("[llm] stream failed mid-iteration: %s", _stream_exc)
                yield {"type": "error", "message": f"LLM 流式响应中断，请重试: {_stream_exc}"}
                yield {"type": "done"}
                return

            visible_tail, reasoning_tail = think_parser.finish()
            if visible_tail:
                content_parts.append(visible_tail)
                if self._analysis_plan is None:
                    _round_visible_yielded = True
                    yield {"type": "text_delta", "content": visible_tail}
            if reasoning_tail:
                reasoning_parts.append(reasoning_tail)

            full_content = "".join(content_parts).strip()
            has_tool_calls = bool(tc_acc) and finish_reason == "tool_calls"
            reasoning_content = "".join(reasoning_parts) or None

            # Length-truncated reasoning is billable too. Report this round's
            # usage before any recovery continue, not only after visible text.
            if usage_data:
                _elapsed = time.monotonic() - _t0
                log.info(
                    "[llm] stream done  finish=%s  in=%.0f out=%.0f  %.2fs",
                    finish_reason,
                    usage_data.prompt_tokens,
                    usage_data.completion_tokens,
                    _elapsed,
                )
                yield {
                    "type": "usage",
                    "prompt_tokens": usage_data.prompt_tokens,
                    "completion_tokens": usage_data.completion_tokens,
                    "total_tokens": usage_data.total_tokens,
                    "context_window": _ctx_window,
                }

            if (
                not has_tool_calls
                and not full_content
                and finish_reason == "length"
                and not _length_budget_retried
            ):
                _length_budget_retried = True
                _reduce_thinking = True
                _force_visible_answer = bool(
                    self._analysis_plan is not None
                    and _plan_r_succeeded
                    and _latest_assessment is not None
                    and _latest_assessment.complete
                )
                log.warning(
                    "[llm] thinking consumed the output budget; retrying without thinking (portfolio_complete=%s)",
                    _force_visible_answer,
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "上一轮思考耗尽输出预算。科研产物已通过本机执行和完整性验证；"
                            "请依据工具结果输出最终解释，说明诊断限制，不要再次调用工具或输出思考过程。"
                            if _force_visible_answer else
                            "上一轮思考耗尽输出预算，尚不能确认科研执行完成。请停止长篇思考，"
                            "继续使用必要的科研工具：如需分析且尚无计划，先调用set_analysis_plan；"
                            "已有计划则调用run_r_code执行或补齐缺失产物。以本机执行结果和"
                            "result_assessment为依据，未执行或未验证时不得宣称已冻结、已执行或已完成分析。"
                        ),
                    }
                )
                continue

            if not has_tool_calls and not full_content:
                full_content = "模型本轮没有给出可见回答。请再发送一次，或缩短问题后重试。"

            class _F:
                def __init__(self, name, arguments):
                    self.name = name
                    self.arguments = arguments

            class _TC:
                def __init__(self, id_, name, arguments):
                    self.id = id_
                    self.function = _F(name, arguments)

            tc_objects = [_TC(v["id"], v["name"], v["args"]) for _, v in sorted(tc_acc.items())]

            # ── Dispatch tool calls ───────────────────────────────────────────
            if has_tool_calls:
                asst_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tc_objects
                    ],
                }
                if reasoning_content:
                    asst_entry["reasoning_content"] = reasoning_content
                    all_reasoning.append(reasoning_content)
                messages.append(asst_entry)

                _outline_proposed = False

                _parsed_tools = []
                for tc in tc_objects:
                    name = tc.function.name
                    try:
                        args: Dict[str, Any] = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        log.warning("[tool] %s: invalid JSON arguments", name)
                        args = {}

                    # Pre-dispatch validation: catch obviously bad args early
                    # A4：把 workspace 的 allowed_roots 传入，让 SQL 路径白名单生效
                    _ws_runtime = self._workspace_runtime()
                    _workspace_auth = self._workspace_path_authorization()
                    # A fixed but unavailable Workspace must fail closed instead
                    # of silently falling back to global uploads/Information.
                    _allowed_roots = [] if self._workspace_id and _workspace_auth is None else None
                    _val_err = (
                        "Tool is not available in the research workbench."
                        if name not in {schema["function"]["name"] for schema in _available_tools}
                        else "Tool arguments must be a JSON object."
                        if not isinstance(args, dict)
                        else _validate_tool_args(
                        name,
                        args,
                        allowed_roots=_allowed_roots,
                        workspace_authorization=_workspace_auth,
                        )
                    )
                    if _val_err:
                        log.warning("[tool] %s: argument validation failed", name)
                        # Inject as a synthetic tool result so the model can self-correct
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": f"[ARG ERROR] {_val_err}",
                            }
                        )
                        yield {"type": "tool_end", "tool": name}
                        _consecutive_errors += 1
                        continue
                    display_map = {
                        "inspect_dataset": "查看科研数据与变量详情",
                        "set_analysis_plan": f"确定分析计划: {args.get('intent', '')[:50]}",
                        "run_r_code": "执行 R 分析代码",
                        "get_r_package_inventory": f"检索 R 包能力: {args.get('query', '全量能力')}",
                        "get_r_package_help": "查看 R 包帮助",
                        "ask_user": f"向用户提问：{args.get('question', '?')[:40]}",
                    }
                    full_display = display_map.get(name, name)
                    yield {
                        "type": "tool_start",
                        "tool": name,
                        "display": full_display[:60] + ("…" if len(full_display) > 60 else ""),
                        "detail": full_display,
                    }
                    _parsed_tools.append((tc, name, args))

                pending_vision_followups: list[dict[str, Any]] = []
                for tc, name, args in _parsed_tools:
                    log.info("[tool] %s started", name)
                    _tool_t0 = time.monotonic()
                    _argument_keys = sorted(str(key) for key in args)[:20]
                    tool_sources: list[dict] = []
                    tool_artifacts: list[dict] = []

                    try:
                        if name == "inspect_dataset":
                            inspection_dataset = self._resolve_research_dataset(args.get("dataset_id", ""))
                            cached_profile = inspection_dataset.profile if isinstance(inspection_dataset.profile, dict) else {}
                            cached_summary = cached_profile.get("summary") or {}
                            needs_r = _as_bool_arg(args.get("refresh", False)) or not cached_summary.get("variables")
                            if (
                                needs_r and ((_MAX_R_CALLS > 0 and _run_r_calls >= _MAX_R_CALLS)
                                or _inspect_dataset_calls >= 1)
                            ):
                                tool_result = json.dumps(
                                    {
                                        "policy_blocked": True,
                                        "tool": name,
                                        "reason": "Per-turn R inspection budget reached.",
                                        "instruction": "Use the existing dataset profile and continue the analysis without inspecting again.",
                                    },
                                    ensure_ascii=False,
                                )
                            else:
                                if needs_r:
                                    _run_r_calls += 1
                                    _inspect_dataset_calls += 1
                                tool_result = self._tool_inspect_dataset(
                                    args.get("dataset_id", ""), column_names=args.get("column_names"),
                                    refresh=_as_bool_arg(args.get("refresh", False)),
                                )
                        elif name == "get_r_package_inventory":
                            tool_result = self._tool_get_r_package_inventory(
                                query=args.get("query", ""),
                                include_all=_as_bool_arg(args.get("include_all", False)),
                                limit=args.get("limit", 80),
                            )
                        elif name == "get_r_package_help":
                            tool_result = self._tool_get_r_package_help(
                                package=args.get("package", ""),
                                packages=args.get("packages", []),
                                query=args.get("query", ""),
                                limit=args.get("limit", 30),
                                include_docs=_as_bool_arg(args.get("include_docs", True)),
                                doc_limit=args.get("doc_limit", 3),
                                max_doc_chars=args.get("max_doc_chars", 4000),
                            )
                        elif name == "set_analysis_plan":
                            tool_result = self._tool_set_analysis_plan(args)
                        elif name == "run_r_code":
                            plan_ready, plan_error = self._analysis_plan_allows_r()
                            if not plan_ready:
                                tool_result = json.dumps(
                                    {
                                        "policy_blocked": True,
                                        "tool": name,
                                        "reason": plan_error,
                                        "instruction": (
                                            "Create a plan for the active dataset with set_analysis_plan, "
                                            "then run only the analysis defined by that plan."
                                        ),
                                    },
                                    ensure_ascii=False,
                                )
                            elif _MAX_R_CALLS > 0 and _run_r_calls >= _MAX_R_CALLS:
                                tool_result = json.dumps(
                                    {
                                        "policy_blocked": True,
                                        "tool": name,
                                        "reason": "Per-turn R execution budget reached.",
                                        "instruction": (
                                            "Do not claim the Scientific Output Portfolio is complete. "
                                            "Explain the safety limit and list outstanding roles from the latest result_assessment."
                                        ),
                                    },
                                    ensure_ascii=False,
                                )
                            else:
                                _run_r_calls += 1
                                tool_result = self._tool_run_r_code(
                                    code=args.get("code", ""),
                                    dataset_id=args.get("dataset_id", ""),
                                    timeout_seconds=args.get("timeout_seconds", 0),
                                )
                                tool_artifacts = _research_artifacts_from_tool_result(tool_result)
                                if self._analysis_plan is not None:
                                    payload = _r_tool_payload(tool_result)
                                    execution = payload.get("execution")
                                    if isinstance(execution, dict) and execution.get("dataset_id"):
                                        _plan_artifacts.extend(tool_artifacts)
                                        latest_diagnostics = execution.get("diagnostic_status")
                                        diagnostic_conflicts: list[str] = []
                                        _plan_diagnostic_status = merge_diagnostic_statuses(
                                            _plan_diagnostic_status, latest_diagnostics or {},
                                            warnings=diagnostic_conflicts,
                                        )
                                        _plan_r_succeeded = execution.get("status") == "succeeded"
                                        cumulative_execution = {
                                            **execution,
                                            "status": ("succeeded" if _plan_r_succeeded else "failed"),
                                            "diagnostic_status": dict(_plan_diagnostic_status),
                                            "diagnostic_conflicts": diagnostic_conflicts,
                                        }
                                        assessment = ResultValidator().validate(
                                            self._analysis_plan,
                                            cumulative_execution,
                                            _plan_artifacts,
                                        )
                                        workflow_state = None
                                        workflow_spec = self._active_workflow_run_spec()
                                        if workflow_spec is not None:
                                            workflow_executor = self._workflow_executor_for_spec(
                                                workflow_spec
                                            )
                                            workflow_executor.apply_assessment(assessment)
                                            workflow_state = workflow_executor.state
                                        tool_result = _attach_result_assessment(
                                            tool_result,
                                            assessment,
                                            workflow_state=workflow_state,
                                        )
                                        _latest_assessment = assessment
                            repair_decision = _r_repair_policy.observe(tool_result)
                            if repair_decision.stop:
                                _r_stop_decision = repair_decision
                                tool_result = _attach_r_repair_decision(
                                    tool_result,
                                    repair_decision,
                                )
                        elif name == "ask_user":
                            yield {
                                "type": "ask_user",
                                "question": args.get("question", ""),
                                "options": args.get("options", []),
                                "multi_select": _as_bool_arg(args.get("multi_select", False)),
                            }
                            tool_result = "问题已展示给用户，等待用户回答后继续。请不要输出任何文字。"
                            _outline_proposed = True
                        else:
                            tool_result = f"Unknown tool: {name}"

                    except Exception as exc:
                        tool_result = f"工具执行错误 [{name}]: {exc}"
                        log.error(
                            "[tool] %s failed after %.2fs (%s)",
                            name,
                            time.monotonic() - _tool_t0,
                            type(exc).__name__,
                        )
                        _consecutive_errors += 1
                    else:
                        _consecutive_errors = 0
                        log.info("[tool] %s OK %.2fs", name, time.monotonic() - _tool_t0)

                    result_budget = (
                        40000
                        if name == "inspect_dataset"
                        else 4500
                        if name == "run_r_code"
                        else 3500
                        if name in {"get_r_package_inventory", "get_r_package_help"}
                        else 12000
                    )
                    debug_payload = {
                        "elapsed_seconds": round(time.monotonic() - _tool_t0, 3),
                        "argument_keys": _argument_keys,
                    }
                    envelope = make_tool_result(
                        name,
                        tool_result,
                        sources=tool_sources,
                        artifacts=tool_artifacts,
                        debug=debug_payload,
                        session_id=self._session_id,
                        runtime=self._workspace_runtime(),
                        result_char_budget=result_budget,
                    )
                    model_envelope = envelope
                    if name in {"run_r_code", "inspect_dataset"}:
                        model_projection = ModelResultProjector().project(
                            self._model_egress.project(ModelResultProjector._parse(tool_result)),
                            inspection=name == "inspect_dataset",
                        )
                        model_envelope = make_tool_result(
                            name,
                            model_projection,
                            sources=tool_sources,
                            artifacts=_sanitize_tool_artifacts_for_model(tool_artifacts),
                            debug=debug_payload,
                            session_id=self._session_id,
                            runtime=self._workspace_runtime(),
                            # Projection already enforces the model budget.
                            # A second raw-text cut can break JSON and discard
                            # the decisive R error tail on the first request.
                            result_char_budget=max(result_budget, len(json.dumps(model_projection, ensure_ascii=False, indent=2))),
                        )
                    if name == "run_r_code" and tool_artifacts:
                        latest_r_payload = _r_tool_payload(tool_result)
                        latest_r_result = latest_r_payload.get("result")
                        if isinstance(latest_r_result, dict) and latest_r_result.get("ok") is True:
                            _had_successful_artifacts = True
                    workflow_audit: dict[str, Any] = {}
                    if name in {"run_r_code", "inspect_dataset"}:
                        audit_payload = _r_tool_payload(tool_result)
                        execution = audit_payload.get("execution")
                        if isinstance(execution, dict) and isinstance(execution.get("workflow"), dict):
                            workflow_audit = dict(execution.get("workflow") or {})
                    yield {
                        "type": "tool_audit",
                        "tool": name,
                        "ok": envelope.ok,
                        "error": envelope.error,
                        "summary": envelope.summary,
                        "content": str(envelope.data),
                        "sources": envelope.sources,
                        "artifacts": envelope.artifacts,
                        "elapsed_seconds": envelope.debug.get("elapsed_seconds"),
                        "argument_keys": envelope.debug.get("argument_keys", []),
                        "workflow": workflow_audit,
                        "phase_status": dict(workflow_audit.get("phase_status") or {}),
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": model_envelope.to_model_text(),
                        }
                    )
                    if name == "run_r_code":
                        review = build_r_artifact_review(
                            _r_tool_payload(tool_result),
                            self._session_id,
                            sharing_guard=self._model_egress,
                        )
                        if review is not None:
                            # ``kind`` marks the message for the mid-loop
                            # image-degradation pass; _messages_for_api strips
                            # it before the request goes on the wire.
                            pending_vision_followups.append(
                                {**review.llm_message, "kind": "artifact_review"}
                            )
                            _turn_artifact_reviews.append(review.history_message)
                        else:
                            result = _r_tool_payload(tool_result).get("result")
                            figures = result.get("figures") if isinstance(result, dict) else []
                            if isinstance(figures, list) and figures:
                                log.warning(
                                    "[vision] run_r_code returned %s figure(s) but no review message sid=%s",
                                    len(figures),
                                    self._session_id,
                                )
                    yield {"type": "tool_end", "tool": name}
                    if _r_stop_decision is not None:
                        # A model response may contain several sequential tool
                        # calls. Once the shared R policy reaches a terminal
                        # state, do not execute later calls from that same
                        # response before returning the diagnosis to the user.
                        break

                messages.extend(pending_vision_followups)

                if _scripted_code is not None:
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "[PRE-EXECUTED TEMPLATE SCRIPT] The run_r_code call above was the "
                                "platform's deterministic script for this template, executed before you "
                                "were consulted. Treat its artifact_previews and the latest "
                                "result_assessment as ground truth. Do not rewrite or re-run it. If the "
                                "assessment reports missing required roles or failed diagnostics, use "
                                "one compact R call to complete exactly those items; otherwise write "
                                "the final manuscript-oriented interpretation now."
                            ),
                        }
                    )

                if _r_stop_decision is not None:
                    # A bare "results are saved" told the user nothing. List
                    # what survived and what is still missing so the stop
                    # reads as an honest status report, not a dead end.
                    stop_message = _r_stop_decision.user_message
                    try:
                        produced_names = [
                            str(item.get("name") or "")
                            for item in (_plan_artifacts or [])
                            if isinstance(item, dict) and item.get("name")
                        ]
                        if produced_names:
                            shown = "、".join(produced_names[:8])
                            stop_message += f"已保留的结果：{shown}{'等' if len(produced_names) > 8 else ''}。"
                        missing = list(getattr(_latest_assessment, "missing_roles", ()) or ())
                        if missing:
                            stop_message += "尚未完成的分析项：" + "、".join(str(item) for item in missing) + "。"
                    except Exception:
                        pass
                    yield {
                        "type": "text",
                        "content": stop_message,
                    }
                    history_event = _tool_history_event()
                    if history_event:
                        yield history_event
                    yield {"type": "done"}
                    return

                if _outline_proposed:
                    for html in pending_charts:
                        yield {"type": "chart_html", "html": html}
                    pending_charts.clear()
                    yield {"type": "done"}
                    return

            # ── Final text response ───────────────────────────────────────────
            else:
                if reasoning_content:
                    all_reasoning.append(reasoning_content)

                if self._analysis_plan is not None:
                    completion_decision = AnalysisCompletionController().decide(
                        self._analysis_plan,
                        _latest_assessment,
                        r_calls=_run_r_calls,
                        max_r_calls=_MAX_R_CALLS,
                        nudge_count=_completion_nudges,
                    )
                    if not completion_decision.allow_final:
                        messages.append({"role": "user", "content": completion_decision.instruction})
                        _completion_nudges += 1
                        log.info(
                            "[completion] blocked premature final action=%s outstanding=%s",
                            completion_decision.action,
                            completion_decision.outstanding_roles,
                        )
                        continue
                    if completion_decision.user_message:
                        full_content = completion_decision.user_message + (
                            "\n\n" + full_content if full_content else ""
                        )

                if all_reasoning:
                    yield {"type": "reasoning", "content": "\n\n---\n\n".join(all_reasoning)}

                for html in pending_charts:
                    yield {"type": "chart_html", "html": html}

                yield {"type": "text", "content": full_content}
                log.info("[run] finished normally  model=%s", self.model)

                history_event = _tool_history_event()
                if history_event:
                    yield history_event

                yield {"type": "done"}
                return

        log.warning("[run] max iterations reached  model=%s", self.model)
        if self._analysis_plan is not None:
            decision = AnalysisCompletionController().decide(
                self._analysis_plan,
                _latest_assessment,
                r_calls=_MAX_R_CALLS,
                max_r_calls=_MAX_R_CALLS,
                nudge_count=3,
            )
            content = decision.user_message or "分析已完成，科研产物已保存。"
        else:
            content = "已达到本轮最大工具调用次数，现有结果均已保留。"
        yield {"type": "text", "content": content}
        yield {"type": "done"}
