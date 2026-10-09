"""Research-mode R inspection, execution, and artifact stamping."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.analysis.readiness import build_analysis_readiness_plan
from agent.analysis.result_validation import (
    ArtifactValidationError,
    build_artifact_validation_receipt,
    merge_diagnostic_statuses,
)
from agent.analysis.workflows import (
    ScientificWorkflowExecutor,
    WorkflowContractError,
    default_workflow_registry,
    validate_workflow_run_spec,
    workflow_run_spec_projection,
)
from data.research_dataset import ResearchDatasetStore
from data.research_results import generation_root
from infrastructure.paths import data_path
from infrastructure.public_projection import PUBLIC_R_RESULT_PRIVATE_KEYS, public_projection, public_r_result
from r_runtime.diagnostics import diagnose_r_failure
from r_runtime.dependency_gate import inspect_dependency_gate
from r_runtime.execution import dataset_inspection_request, run_r_request
from r_runtime.factory import get_r_backend
from r_runtime.inspection import inspection_summary
from r_runtime.package_policy import dataset_loader_packages
from r_runtime.package_references import referenced_external_dependencies, referenced_r_packages
from r_runtime.protocol import DatasetAssetRef, DatasetRef, RRunRequest, RRunResult
from r_runtime.resource_policy import compute_local_resource_policy

from .r_binding import BindingHelpersMixin, _checkpoint_dir_for_frozen_plan
from .r_packages import RPackageToolsMixin

log = logging.getLogger(__name__)


class RToolsMixin(RPackageToolsMixin, BindingHelpersMixin):
    def _local_r_backend(self):
        backend = getattr(self, "_local_r_backend_instance", None)
        if backend is None:
            backend = get_r_backend()
            self._local_r_backend_instance = backend
        return backend

    def _research_store(self) -> ResearchDatasetStore:
        return ResearchDatasetStore(data_path("uploads", "research"))

    def _resolve_research_dataset(self, dataset_id: str = ""):
        sid = getattr(self, "_session_id", "")
        store = self._research_store()
        datasets = store.list(sid)
        requested_id = str(dataset_id or "").strip()
        active_id = str(getattr(self, "_research_dataset_id", "") or "").strip()
        if active_id and requested_id and requested_id != active_id:
            raise ValueError("当前分析已固定数据集，不能在分析中切换 dataset_id。")
        selected_id = active_id or requested_id
        if selected_id:
            try:
                dataset = store.get(sid, selected_id)
            except ValueError:
                dataset = None
            if dataset is not None:
                return dataset
            if not active_id and len(datasets) == 1:
                return store.get(sid, datasets[0].dataset_id) or datasets[0]
            if not datasets:
                raise FileNotFoundError("No research dataset has been uploaded for this session.")
            raise FileNotFoundError(
                f"Research dataset not found: {selected_id}. "
                "Select an uploaded dataset before running the analysis."
            )

        if not datasets:
            raise FileNotFoundError("No research dataset has been uploaded for this session.")
        if len(datasets) > 1:
            raise ValueError("检测到多个数据集，请选择当前数据集后再运行分析。")
        return store.get(sid, datasets[0].dataset_id) or datasets[0]

    def _dataset_ref(self, dataset) -> DatasetRef:
        return DatasetRef(
            dataset_id=dataset.dataset_id,
            path=dataset.stored_path,
            format=dataset.format,
            name=dataset.name,
            assets=tuple(
                DatasetAssetRef(
                    path=asset.path,
                    format=asset.format,
                    role=asset.role,
                    relative_mount_name=asset.relative_mount_name,
                    kind=asset.kind,
                    name=asset.name,
                    size_bytes=asset.size_bytes,
                    device=asset.device,
                    inode=asset.inode,
                    object_type=asset.object_type,
                    mtime_ns=asset.mtime_ns,
                    ctime_ns=asset.ctime_ns,
                    fingerprint_sha256=asset.fingerprint_sha256,
                )
                for asset in getattr(dataset, "assets", ())
            ),
        )

    @staticmethod
    def _coerce_timeout_seconds(value: Any) -> int:
        try:
            timeout = int(value)
        except (TypeError, ValueError, OverflowError):
            return 0
        return min(2_147_483_647, max(0, timeout))

    def _tool_inspect_dataset(
        self, dataset_id: str = "", column_names: list[str] | None = None, refresh: bool = False,
    ) -> str:
        dataset = self._resolve_research_dataset(dataset_id)
        if column_names is not None and (
            not isinstance(column_names, list) or len(column_names) > 20
            or any(not isinstance(name, str) or not name for name in column_names)
        ):
            raise ValueError("column_names must contain at most 20 exact column names")
        cached_summary = dataset.profile.get("summary") if isinstance(dataset.profile, dict) else None
        if not refresh and isinstance(cached_summary, dict) and cached_summary.get("variables"):
            summary = public_projection(cached_summary, drop_keys={"local_semantics", "analysis_context", "role_memory"})
            if column_names:
                lookup = {item["name"]: item for item in summary.get("variables", []) if isinstance(item, dict) and item.get("name")}
                unknown = [name for name in column_names if name not in lookup]
                if unknown:
                    return "ERROR: " + json.dumps({"ok": False, "unprofiled_columns": unknown, "message": "这些列没有已保存的详细画像；不能按不存在处理。可使用 refresh=true 重新检查，或先确认数据结构。"}, ensure_ascii=False)
                summary["variables"] = [lookup[name] for name in column_names]
            return json.dumps({
                "ok": True, "cached": True,
                "dataset": {"dataset_id": dataset.dataset_id},
                "summary": summary,
            }, ensure_ascii=False)
        store = self._research_store()
        session_id = str(getattr(self, "_session_id", "") or "")
        inspection_request = dataset_inspection_request(
            self._dataset_ref(dataset),
            session_id=session_id,
            workspace_generation=str(getattr(self, "_research_workspace_generation", "") or ""),
            conversation_job_id=str(getattr(self, "_conversation_job_id", "") or ""),
        )
        try:
            result = self._run_r_request(inspection_request)
        except (PermissionError, ValueError) as exc:
            result = RRunResult(
                ok=False,
                error=str(exc),
                code=inspection_request.code,
            )
        internal_result = result.to_dict()
        summary = inspection_summary(result) if result.ok else {}
        public_summary = public_projection(
            summary,
            drop_keys={"local_semantics", "analysis_context", "role_memory"},
        )
        if result.ok:
            analysis_context = (
                dataset.profile.get("analysis_context")
                if isinstance(dataset.profile, dict)
                and isinstance(dataset.profile.get("analysis_context"), dict)
                else {}
            )
            dataset.profile = {
                "inspection": internal_result,
                "summary": summary,
                "analysis_context": analysis_context,
            }
            store.save(dataset)
        public_error = ""
        if not result.ok:
            error_id = uuid.uuid4().hex[:12]
            public_error = f"数据理解失败（错误编号：{error_id}）。"
            log.error(
                "[research-tool] dataset inspection failed error_id=%s dataset_id=%s error=%s stderr=%s",
                error_id,
                dataset.dataset_id,
                result.error,
                result.stderr,
            )
        payload = {
            "dataset": public_projection(
                dataset.to_dict(),
                drop_keys=PUBLIC_R_RESULT_PRIVATE_KEYS,
            ),
            "result": public_r_result(internal_result, error_message=public_error),
        }
        if result.ok:
            payload["summary"] = public_summary
            if column_names:
                # Reuse the just-saved profile for a bounded, targeted reply.
                return self._tool_inspect_dataset(dataset.dataset_id, column_names=column_names)
        text = json.dumps(payload, ensure_ascii=False)
        return text if result.ok else f"ERROR: {text}"

    @staticmethod
    def _artifact_text_preview(path: str, *, max_lines: int = 30, max_chars: int = 8000) -> str:
        try:
            artifact_path = Path(path)
            if not artifact_path.is_file():
                return ""
            lines = artifact_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        preview = "\n".join(lines[:max_lines])
        if len(preview) > max_chars:
            preview = preview[:max_chars] + "\n...[truncated]"
        elif len(lines) > max_lines:
            preview += f"\n...[truncated after {max_lines} lines]"
        return preview

    @staticmethod
    def _artifact_table_preview(
        path: str,
        *,
        max_rows: int = 20,
        max_columns: int = 30,
        max_field_chars: int = 256,
    ) -> dict[str, Any]:
        artifact_path = Path(path)
        delimiter = "\t" if artifact_path.suffix.lower() in {".tsv", ".tab"} else ","
        preview_rows: list[list[str]] = []
        total_rows = 0
        total_columns = 0
        field_truncated = False
        try:
            with artifact_path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.reader(handle, delimiter=delimiter)
                for index, row in enumerate(reader):
                    total_columns = max(total_columns, len(row))
                    if index > 0:
                        total_rows += 1
                    if index > max_rows:
                        continue
                    bounded: list[str] = []
                    for field in row[:max_columns]:
                        text = str(field)
                        if len(text) > max_field_chars:
                            field_truncated = True
                        bounded.append(text[:max_field_chars])
                    preview_rows.append(bounded)
        except (OSError, csv.Error):
            return {
                "preview_csv": "",
                "truncated": False,
                "total_rows": 0,
                "total_columns": 0,
            }
        output = io.StringIO()
        csv.writer(output, lineterminator="\n").writerows(preview_rows)
        return {
            "preview_csv": output.getvalue().rstrip("\n"),
            "truncated": field_truncated or total_rows > max_rows or total_columns > max_columns,
            "total_rows": total_rows,
            "total_columns": total_columns,
        }

    def _artifact_previews(self, result) -> dict[str, list[dict[str, Any]]]:
        previews: dict[str, list[dict[str, Any]]] = {"tables": [], "texts": [], "models": []}
        for table in result.tables[:2]:
            preview = self._artifact_table_preview(table.path)
            if preview["preview_csv"]:
                previews["tables"].append(
                    {
                        "name": table.name,
                        "kind": table.kind,
                        **preview,
                    }
                )
        for text in result.texts[:2]:
            preview = self._artifact_text_preview(text.path, max_lines=24, max_chars=3500)
            if preview:
                previews["texts"].append(
                    {
                        "name": text.name,
                        "kind": text.kind,
                        "preview_text": preview,
                    }
                )
        for model in result.models[:2]:
            preview = self._artifact_text_preview(model.path, max_lines=32, max_chars=5000)
            if preview:
                previews["models"].append(
                    {
                        "name": model.name,
                        "kind": model.kind,
                        "preview_text": preview,
                    }
                )
        return previews

    def _attach_analysis_plan_metadata(
        self,
        result: RRunResult,
        dataset_id: str,
        *,
        job_id: str,
        artifacts_root: str,
    ) -> dict[str, str]:
        """Stamp server-trusted ownership and collect explicit diagnostic states."""
        plan = getattr(self, "_analysis_plan", None)
        outputs_by_role: dict[str, Any] = {}
        outputs_by_kind: dict[str, list[Any]] = {
            "table": [],
            "figure": [],
            "text": [],
            "model": [],
        }
        if plan is not None:
            for output in plan.output_portfolio:
                outputs_by_kind.setdefault(output.kind, []).append(output)
                for role in (output.role, *output.legacy_roles):
                    if role:
                        outputs_by_role.setdefault(role, output)
        diagnostic_status: dict[str, str] = {}
        collections = (
            ("table", result.tables),
            ("figure", result.figures),
            ("text", result.texts),
            ("model", result.models),
            ("artifact", result.artifacts),
        )
        for kind, artifacts in collections:
            validated_artifacts = []
            for artifact in artifacts:
                metadata = artifact.metadata
                for key in (
                    "artifact_size_bytes",
                    "artifact_sha256",
                    "artifact_validation_receipt",
                    "artifact_validation_error",
                ):
                    metadata.pop(key, None)
                metadata["dataset_id"] = dataset_id
                metadata["source_tool"] = "run_r_code"
                metadata["job_id"] = job_id
                metadata["source_execution_status"] = "succeeded" if result.ok else "failed"
                workspace_generation = str(getattr(self, "_research_workspace_generation", "") or "")
                if workspace_generation:
                    metadata["workspace_generation"] = workspace_generation
                if plan is not None:
                    metadata["plan_id"] = plan.plan_id
                    metadata["variable_bindings"] = dict(plan.variable_bindings)
                    metadata["resolved_inputs"] = {
                        input_id: (
                            dict(item)
                            if isinstance(item, Mapping)
                            else asdict(item)
                        )
                        for input_id, item in dict(plan.resolved_inputs or {}).items()
                    }
                role = str(metadata.get("role") or metadata.get("output_role") or "").strip()
                matched_output = outputs_by_role.get(role) if role else None
                if plan is not None and matched_output is None and role == "baseline":
                    matched_output = next(
                        (
                            output
                            for output in outputs_by_kind.get("table", [])
                            if output.role == "baseline_table" or "baseline" in output.legacy_roles
                        ),
                        None,
                    )
                if plan is not None and matched_output is None and role == "model_summary":
                    model_outputs = outputs_by_kind.get("model", [])
                    matched_output = model_outputs[0] if len(model_outputs) == 1 else None
                if matched_output is not None and matched_output.kind == kind:
                    role = matched_output.role
                    metadata["role"] = role
                    metadata["semantic_key"] = matched_output.semantic_key or matched_output.role
                    metadata["portfolio_tier"] = matched_output.tier
                try:
                    receipt = build_artifact_validation_receipt(
                        artifact.path,
                        kind=kind,
                        metadata=metadata,
                        source_tool="run_r_code",
                        job_id=job_id,
                        artifacts_root=artifacts_root,
                        execution_succeeded=result.ok,
                    )
                except (ArtifactValidationError, OSError, ValueError) as exc:
                    metadata["artifact_size_bytes"] = 0
                    metadata["artifact_validation_error"] = str(exc)
                    warning = f"Artifact {artifact.name!r} failed server validation: {exc}"
                    if warning not in result.warnings:
                        result.warnings.append(warning)
                else:
                    metadata["artifact_size_bytes"] = receipt["size_bytes"]
                    metadata["artifact_sha256"] = receipt["sha256"]
                    metadata["artifact_validation_receipt"] = receipt
                    validated_artifacts.append(artifact)
                    declared = metadata.get("diagnostic_status") or metadata.get("diagnostics")
                    if isinstance(declared, dict):
                        diagnostic_status = merge_diagnostic_statuses(
                            diagnostic_status, declared, warnings=result.warnings,
                        )
            artifacts[:] = validated_artifacts
        return diagnostic_status

    def _workflow_executor_for_spec(self, workflow_spec):
        """Reuse or recover the production ledger for the active frozen spec."""

        plan = getattr(self, "_analysis_plan", None)
        if plan is None:
            raise WorkflowContractError("compiled workflow has no active frozen analysis plan")
        profile_builder = getattr(self, "_analysis_dataset_profile", None)
        profile = (
            profile_builder()
            if callable(profile_builder)
            else dict(workflow_spec.dataset_profile)
        )
        expected_spec = default_workflow_registry().compile(plan, dataset_profile=profile)
        identity = (
            "run_id",
            "source",
            "dataset_id",
            "source_plan_id",
            "workflow_id",
            "variant",
        )
        if any(getattr(workflow_spec, key) != getattr(expected_spec, key) for key in identity) or (
            workflow_spec.definition.contract_signature
            != expected_spec.definition.contract_signature
        ) or workflow_spec.steps != expected_spec.steps:
            raise WorkflowContractError(
                "compiled workflow does not match the active plan's registered steps"
            )
        expected_checks = {
            step.phase: tuple(step.checks) for step in expected_spec.steps
        }
        current = getattr(self, "_scientific_workflow_executor", None)
        if (
            isinstance(current, ScientificWorkflowExecutor)
            and current.run_spec is not None
            and current.run_spec.run_id == workflow_spec.run_id
            and current.run_spec.definition.contract_signature
            == workflow_spec.definition.contract_signature
            and current.run_spec.steps == workflow_spec.steps
        ):
            current.validate_production_contract()
            return current
        prior_manifest = getattr(self, "_prior_portfolio_manifest", {})
        recovered_state = (
            prior_manifest.get("workflow_run")
            if isinstance(prior_manifest, Mapping)
            and isinstance(prior_manifest.get("workflow_run"), Mapping)
            else None
        )
        executor = ScientificWorkflowExecutor.for_run(
            workflow_spec,
            recovered_state=recovered_state,
            expected_checks_by_phase=expected_checks,
        )
        self._scientific_workflow_executor = executor
        return executor

    def _tool_run_r_code(
        self,
        code: str,
        dataset_id: str = "",
        timeout_seconds: int | str = 0,
    ) -> str:
        dataset = self._resolve_research_dataset(dataset_id)
        job_id = uuid.uuid4().hex[:12]
        session_id = str(getattr(self, "_session_id", "") or "")
        active_plan = getattr(self, "_analysis_plan", None)
        workflow_spec = getattr(self, "_workflow_run_spec", None)
        workspace_generation = str(getattr(self, "_research_workspace_generation", "") or "")
        workflow_payload = (
            workflow_run_spec_projection(workflow_spec) if workflow_spec is not None else {}
        )
        try:
            workflow_executor = (
                self._workflow_executor_for_spec(workflow_spec)
                if workflow_spec is not None
                else None
            )
        except WorkflowContractError as exc:
            reason = f"科学工作流合同不可执行：{exc}"
            payload = {
                "job_id": job_id,
                "dataset": dataset.to_dict(),
                "artifact_previews": {"tables": [], "texts": [], "models": []},
                "result": {
                    "ok": False,
                    "error": reason,
                    "execution_stage": "preflight",
                    "tables": [],
                    "figures": [],
                    "texts": [],
                    "models": [],
                    "artifacts": [],
                },
                "execution": {
                    "dataset_id": dataset.dataset_id,
                    "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                    "workspace_generation": workspace_generation,
                    "status": "blocked",
                    "diagnostic_status": {},
                    "portfolio_version": str(
                        getattr(active_plan, "portfolio_version", "") or "1.0"
                    ),
                    "output_portfolio": (
                        [asdict(item) for item in active_plan.output_portfolio]
                        if active_plan is not None
                        else []
                    ),
                    "planned_diagnostics": (
                        list(active_plan.diagnostics) if active_plan is not None else []
                    ),
                    "workflow": workflow_payload,
                },
                "repair_diagnostics": {
                    "category": "workflow_contract",
                    "facts": [reason],
                    "next_actions": ["重新编译当前冻结分析计划后再执行。"],
                    "retry_strategy": "不得绕过未注册的步骤、依赖或检查直接运行 R。",
                },
            }
            return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        if workflow_executor is not None:
            workflow_payload.update(workflow_executor.state)
        blocked_reason = self._blocked_platform_probe_reason(code)
        if blocked_reason:
            payload = {
                "job_id": job_id,
                "dataset": dataset.to_dict(),
                "artifact_previews": {"tables": [], "texts": [], "models": []},
                "result": {
                    "ok": False,
                    "stdout": "",
                    "stderr": blocked_reason,
                    "tables": [],
                    "figures": [],
                    "texts": [],
                    "models": [],
                    "artifacts": [],
                    "warnings": [],
                    "error": blocked_reason,
                    "code": code,
                },
                "repair_diagnostics": {
                    "category": "platform_artifact_probe",
                    "facts": [blocked_reason],
                    "next_actions": [
                        "Run the actual analysis directly with baa_table(), baa_table_one(), baa_plot(), baa_text(), or baa_model_summary().",
                        "Use artifact_previews from the previous successful run_r_code result instead of reading saved artifact paths.",
                    ],
                    "retry_strategy": "Remove the helper/artifact probe and either finalize from artifact_previews or compute values from dataset in the same analysis call.",
                },
            }
            return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        binding_report = self._frozen_binding_report(code, active_plan, dataset)
        if binding_report["status"] in {"failed", "unverified"}:
            if binding_report["status"] == "failed":
                mismatch = binding_report["mismatches"][0]
                expected = ", ".join(mismatch["expected"])
                reason = (
                    "冻结分析方案变量绑定不一致："
                    f"{mismatch['symbol']} 被设为 {mismatch['value']}，"
                    f"但当前方案要求 {expected}。"
                )
                category = "frozen_binding_mismatch"
            else:
                missing_families = binding_report.get("missing_families") or []
                missing_inputs = binding_report.get("missing_inputs") or []
                missing = ", ".join([*missing_families, *missing_inputs])
                required_values = [
                    value
                    for family in missing_families
                    for value in binding_report.get("expected", {}).get(family, [])
                ]
                required_values.extend(
                    value
                    for input_id in missing_inputs
                    for value in binding_report.get("expected", {}).get(
                        f"input:{input_id}", []
                    )
                )
                required = ", ".join(dict.fromkeys(required_values))
                reason = (
                    "冻结分析方案变量绑定尚未被代码显式验证："
                    f"缺少 {missing or '受保护输入'} 的 baa_plan_input() 访问或可验证字面量"
                    f"（要求 {required or '冻结方案中的确切值'}）。"
                )
                category = "frozen_binding_unverified"
            payload = {
                "job_id": job_id,
                "dataset": dataset.to_dict(),
                "artifact_previews": {"tables": [], "texts": [], "models": []},
                "result": {
                    "ok": False,
                    "error": reason,
                    "execution_stage": "binding_preflight",
                    "tables": [],
                    "figures": [],
                    "texts": [],
                    "models": [],
                    "artifacts": [],
                },
                "execution": {
                    "dataset_id": dataset.dataset_id,
                    "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                    "workspace_generation": workspace_generation,
                    "status": "blocked",
                    "variable_bindings": dict(getattr(active_plan, "variable_bindings", {}) or {}),
                    "binding_validation": binding_report,
                    "diagnostic_status": {},
                    "workflow": workflow_payload,
                },
                "repair_diagnostics": {
                    "category": category,
                    "facts": [reason],
                    "next_actions": [
                        "按冻结方案中的确切变量名修改角色变量后，仅重新执行本次分析。"
                    ],
                    "retry_strategy": "不得用数据集中的其他列替换已确认的变量绑定。",
                },
            }
            return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        if workflow_spec is not None:
            assert workflow_executor is not None
            summary = (
                dataset.profile.get("summary")
                if isinstance(getattr(dataset, "profile", None), dict)
                and isinstance(dataset.profile.get("summary"), dict)
                else {}
            )
            assets = [asset.to_dict() for asset in getattr(dataset, "assets", ())]
            preflight_profile = {
                **summary,
                "dataset_id": dataset.dataset_id,
                "format": dataset.format,
                "assets": assets,
                "asset_formats": list(
                    dict.fromkeys(
                        str(asset.get("format") or "")
                        for asset in assets
                        if asset.get("format")
                    )
                ),
            }
            preflight = validate_workflow_run_spec(workflow_spec, preflight_profile)
            workflow_executor.record_preflight(preflight)
            workflow_payload.update(workflow_executor.state)
            if not preflight.ready:
                issue_rows = [
                    {"code": issue.code, "message": issue.message}
                    for issue in preflight.issues
                ]
                readiness_plan = build_analysis_readiness_plan(
                    workflow_spec,
                    preflight_profile,
                    preflight.issues,
                )
                reason = "；".join(issue.message for issue in preflight.issues)
                payload = {
                    "job_id": job_id,
                    "dataset": dataset.to_dict(),
                    "artifact_previews": {"tables": [], "texts": [], "models": []},
                    "result": {
                        "ok": False,
                        "error": f"科学工作流预检未通过：{reason}",
                        "execution_stage": "preflight",
                    },
                    "execution": {
                        "dataset_id": dataset.dataset_id,
                        "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                        "workspace_generation": workspace_generation,
                        "status": "blocked",
                        "diagnostic_status": {},
                        "portfolio_version": str(
                            getattr(active_plan, "portfolio_version", "") or "1.0"
                        ),
                        "output_portfolio": (
                            [asdict(item) for item in active_plan.output_portfolio]
                            if active_plan is not None
                            else []
                        ),
                        "planned_diagnostics": (
                            list(active_plan.diagnostics) if active_plan is not None else []
                        ),
                        "workflow": workflow_payload,
                    },
                    "repair_diagnostics": {
                        "category": "workflow_preflight",
                        "facts": issue_rows,
                        "next_actions": [
                            str(item.get("title") or "")
                            for item in readiness_plan.get("actions") or []
                            if item.get("title")
                        ],
                        "retry_strategy": (
                            "按 preparation_plan 准备派生数据；重新检查通过后自动回到原模板，"
                            "不得绕过预检直接运行下游统计。"
                        ),
                    },
                    "preparation_plan": readiness_plan,
                }
                return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        asset_formats = tuple(
            str(getattr(asset, "format", "") or "")
            for asset in getattr(dataset, "assets", ())
            if str(getattr(asset, "format", "") or "")
        )
        backend = self._local_r_backend()
        try:
            dataset_path = Path(str(getattr(dataset, "path", "") or ""))
            resource_policy = compute_local_resource_policy(
                environment=getattr(backend, "runtime_env", {}),
                storage_path=dataset_path.parent,
                resource_class=str(
                    getattr(active_plan, "resource_class", "standard") or "standard"
                ),
                workload_size_bytes=dataset_path.stat().st_size,
            )
            lazy_dataset = (
                dataset_path.stat().st_size
                > resource_policy.in_memory_threshold_bytes
            )
        except (OSError, TypeError, ValueError):
            lazy_dataset = False
        exact_packages = tuple(
            dict.fromkeys(
                (
                    *dataset_loader_packages(
                        dataset.format,
                        asset_formats,
                        inspection=False,
                        lazy=lazy_dataset,
                    ),
                    *referenced_r_packages(code),
                )
            )
        )
        dependency_gate = inspect_dependency_gate(
            packages=exact_packages,
            intent=" ".join(
                (
                    str(getattr(active_plan, "intent", "") or ""),
                    str(getattr(active_plan, "method_family", "") or ""),
                    *(getattr(active_plan, "prerequisites", ()) or ()),
                )
            ),
            mode="exact",
            session_id=session_id,
            plan_id=str(getattr(active_plan, "plan_id", "") or ""),
            template_id=str(getattr(active_plan, "template_id", "") or ""),
            dependency_ids=referenced_external_dependencies(code),
            backend=backend,
        )
        unresolved_gate_items = [
            item
            for item in (dependency_gate.get("package_readiness") or [])
            if isinstance(item, dict) and item.get("status") == "unknown_package"
        ]
        if dependency_gate["status"] == "package_names_unresolved" and unresolved_gate_items:
            # Invented package names cannot be fixed by the user — only by
            # rewriting the code.  Keep this inside the AI repair loop: no
            # user-facing modal, no install ticket, no dependency_gate key
            # (its presence is what pops the frontend dialog).
            def _describe_unknown(item: dict) -> str:
                package = str(item.get("package") or "")
                hints = [str(hint) for hint in (item.get("suggestions") or [])][:4]
                suffix = f"（相近名称：{'、'.join(hints)}）" if hints else ""
                return f"{package}{suffix}"

            names_text = "、".join(_describe_unknown(item) for item in unresolved_gate_items)
            error_text = (
                f"代码引用了不存在的 R 包：{names_text}。"
                "R 包名区分大小写且必须真实存在于 CRAN/Bioconductor；"
                "请删除这些引用，或改用真实存在的包名后重新生成代码。"
            )
            payload = {
                "job_id": job_id,
                "dataset": dataset.to_dict(),
                "artifact_previews": {"tables": [], "texts": [], "models": []},
                "result": {
                    "ok": False,
                    "error": error_text,
                    "execution_stage": "dependency_preflight",
                    "code": code,
                    "tables": [],
                    "figures": [],
                    "texts": [],
                    "models": [],
                    "artifacts": [],
                },
                "execution": {
                    "dataset_id": dataset.dataset_id,
                    "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                    "workspace_generation": workspace_generation,
                    "status": "failed",
                    "diagnostic_status": {},
                    "portfolio_version": str(
                        getattr(active_plan, "portfolio_version", "") or "1"
                    ),
                    "workflow": workflow_payload,
                },
                "repair_diagnostics": {
                    "category": "invalid_package_reference",
                    "facts": [error_text],
                    "next_actions": [
                        "删除对不存在包的引用，或改用真实存在的 CRAN/Bioconductor 包名（区分大小写）。",
                        "常见纠正：药代动力学分析使用 PK；不要使用编造的缩写。",
                    ],
                    "retry_strategy": (
                        "改写代码后重新执行 run_r_code；不要请用户安装不存在的包。"
                    ),
                },
            }
            return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        if dependency_gate["status"] != "ready":
            reason = str(dependency_gate.get("headline") or "分析依赖尚未就绪")
            payload = {
                "job_id": job_id,
                "dataset": dataset.to_dict(),
                "artifact_previews": {"tables": [], "texts": [], "models": []},
                "result": {
                    "ok": False,
                    "error": reason,
                    "execution_stage": "dependency_preflight",
                    "code": code,
                    "tables": [],
                    "figures": [],
                    "texts": [],
                    "models": [],
                    "artifacts": [],
                },
                "execution": {
                    "dataset_id": dataset.dataset_id,
                    "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                    "workspace_generation": workspace_generation,
                    "status": "waiting_for_dependencies",
                    "diagnostic_status": {},
                    "portfolio_version": str(
                        getattr(active_plan, "portfolio_version", "") or "1.0"
                    ),
                    "output_portfolio": (
                        [asdict(item) for item in active_plan.output_portfolio]
                        if active_plan is not None
                        else []
                    ),
                    "planned_diagnostics": (
                        list(active_plan.diagnostics) if active_plan is not None else []
                    ),
                    "workflow": workflow_payload,
                },
                "dependency_gate": dependency_gate,
                "repair_diagnostics": {
                    "category": "dependency_preflight",
                    "facts": [str(dependency_gate.get("summary") or reason)],
                    "next_actions": [
                        "在依赖窗口中确认安装或按提示配置本机组件，然后重新检测。"
                    ],
                    "retry_strategy": (
                        "依赖就绪后恢复同一个冻结分析计划；不得静默换用其他统计方法。"
                    ),
                },
            }
            return f"ERROR: {json.dumps(payload, ensure_ascii=False)}"
        checkpoint_dir = _checkpoint_dir_for_frozen_plan(
            session_id=session_id,
            workspace_generation=workspace_generation,
            dataset_id=dataset.dataset_id,
            plan=active_plan,
        )
        artifacts_dir = (
            generation_root(session_id, workspace_generation) / "artifacts" / job_id
            if workspace_generation
            else data_path("outputs", "research", session_id, job_id)
        )
        code_to_run = (
            self._worker_compatibility_prelude()
            + "\n"
            + self._frozen_input_prelude(active_plan)
            + "\n"
            + self._frozen_user_code_block(str(code or ""), active_plan)
            + "\n"
            + self._frozen_input_epilogue(active_plan)
        )
        run_request = RRunRequest(
            job_id=job_id,
            dataset=self._dataset_ref(dataset),
            code=code_to_run,
            artifacts_dir=str(artifacts_dir),
            timeout_seconds=self._coerce_timeout_seconds(timeout_seconds),
            conversation_job_id=str(getattr(self, "_conversation_job_id", "") or ""),
            checkpoint_dir=str(checkpoint_dir) if checkpoint_dir is not None else "",
            resource_class=str(getattr(active_plan, "resource_class", "standard") or "standard"),
        )
        try:
            result = self._run_r_request(run_request)
        except (PermissionError, ValueError) as exc:
            result = RRunResult(
                ok=False,
                error=str(exc),
                code=code_to_run,
            )
        diagnostic_status = self._attach_analysis_plan_metadata(
            result,
            dataset.dataset_id,
            job_id=job_id,
            artifacts_root=str(artifacts_dir),
        )
        if workflow_payload and workflow_executor is not None:
            workflow_executor.record_runtime_result(result)
            workflow_payload.update(workflow_executor.state)
        payload = {
            "job_id": job_id,
            "dataset": dataset.to_dict(),
            "artifact_previews": self._artifact_previews(result),
            "result": result.to_dict(),
            "execution": {
                "dataset_id": dataset.dataset_id,
                "plan_id": str(getattr(active_plan, "plan_id", "") or ""),
                "workspace_generation": workspace_generation,
                "status": "succeeded" if result.ok else "failed",
                "diagnostic_status": diagnostic_status,
                "portfolio_version": str(getattr(active_plan, "portfolio_version", "") or "1.0"),
                "output_portfolio": (
                    [asdict(item) for item in active_plan.output_portfolio] if active_plan is not None else []
                ),
                "planned_diagnostics": (list(active_plan.diagnostics) if active_plan is not None else []),
                "variable_bindings": dict(getattr(active_plan, "variable_bindings", {}) or {}),
                "analysis_plan": asdict(active_plan) if active_plan is not None else {},
                "binding_validation": binding_report,
                "workflow": workflow_payload,
            },
        }
        has_artifacts = bool(
            result.tables or result.figures or result.texts or result.models or result.artifacts
        )
        if not result.ok:
            payload["repair_diagnostics"] = diagnose_r_failure(
                error=result.error,
                stderr=result.stderr,
                stdout=result.stdout,
                code=result.code or code,
            )
            if has_artifacts:
                payload["partial_result"] = True
                diagnostics = payload["repair_diagnostics"]
                next_actions = list(diagnostics.get("next_actions") or [])
                partial_action = (
                    "Usable artifacts were already saved before the R error. "
                    "Do not discard them or rerun broad code; explain the saved "
                    "tables/figures/texts, then retry only the failed downstream "
                    "step if it is essential."
                )
                if partial_action not in next_actions:
                    next_actions.insert(0, partial_action)
                diagnostics["next_actions"] = next_actions
                retry_strategy = list(diagnostics.get("retry_strategy") or [])
                partial_retry = (
                    "Finalize from saved artifacts when they satisfy the user request; "
                    "otherwise run a narrow follow-up that only repairs the missing output."
                )
                if partial_retry not in retry_strategy:
                    retry_strategy.insert(0, partial_retry)
                diagnostics["retry_strategy"] = retry_strategy
        text = json.dumps(payload, ensure_ascii=False)
        return text if result.ok else f"ERROR: {text}"

    def _run_r_request(self, request: RRunRequest) -> RRunResult:
        return run_r_request(
            self._local_r_backend(),
            request,
            progress_callback=getattr(self, "_progress_publisher", None),
        )
