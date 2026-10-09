from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import tempfile
import time
import threading
from contextlib import nullcontext
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.analysis.run_control import ResearchRunCancelled, run_cancellable_process
from infrastructure.resources import materialized_resource
from r_runtime.backends.base import RExecutionBackend
from r_runtime.index_validation import INDEX_CONTENT_VALIDATION_R_CODE
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from r_runtime.isolation import RIsolationError, isolated_r_command
from r_runtime.package_policy import dataset_loader_packages
from r_runtime.package_references import referenced_r_packages
from r_runtime.replay_snapshot import capture_replay_snapshot
from r_runtime.protocol import (
    CHECKPOINT_MAX_FILES,
    DatasetAssetRef,
    DatasetRef,
    RRunRequest,
    RRunResult,
    promote_checkpoint_artifacts,
    validate_checkpoint_inventory,
    validate_dataset_asset_mount,
    validated_checkpoint_dir,
)
from r_runtime.resource_policy import compute_local_resource_policy, parse_size_bytes
from r_runtime.tempfiles import (
    RTemporaryDirectoryError, private_r_temp_directory, r_temporary_environment,
)


class LocalRscriptBackend(RExecutionBackend):
    def __init__(
        self,
        rscript_bin: str = "<local-r-not-configured>",
        helper_path: Path | None = None,
        runtime_env: Mapping[str, str] | None = None,
        package_installer: Callable[[Sequence[str]], object] | None = None,
    ):
        self.rscript_bin = rscript_bin
        self.runtime_env = dict(runtime_env or {})
        self.package_installer = package_installer
        self.helper_path = Path(helper_path) if helper_path is not None else None
        self._package_state_lock = threading.RLock()
        self._prepared_packages: set[str] = set()
        self._readiness_verified_packages: set[str] = set()

    def inspect_dataset(self, dataset: DatasetRef, timeout_seconds: int = 0) -> RRunResult:
        return self.run_code(
            RRunRequest(
                job_id=f"inspect-{dataset.dataset_id}",
                dataset=dataset,
                code=DATASET_PROFILE_R_CODE,
                artifacts_dir=str(Path(dataset.path).parent / ".baa_inspect"),
                timeout_seconds=timeout_seconds,
            )
        )

    def available_package_names(self, timeout_seconds: int = 90) -> tuple[str, ...]:
        """Best-effort names of packages installable from the configured repos.

        Used by the dependency gate to tell "real but missing" packages apart
        from ids that no repository hosts (e.g. invented abbreviations).
        Mirrors the installer's repository order: primary CRAN mirror,
        fallback CRAN, plus Bioconductor repositories when BiocManager is
        available.
        """
        script = r'''
repos <- unique(c(
  Sys.getenv("BAA_CRAN_MIRROR", "https://mirrors.tuna.tsinghua.edu.cn/CRAN"),
  Sys.getenv("BAA_CRAN_FALLBACK", "https://cloud.r-project.org")
))
if (requireNamespace("BiocManager", quietly = TRUE)) {
  repos <- unique(c(
    repos,
    tryCatch(BiocManager::repositories(), error = function(e) character(0))
  ))
}
options(timeout = max(300L, as.integer(getOption("timeout", 60L))))
names <- tryCatch(
  suppressWarnings(available.packages(repos = repos)[, "Package"]),
  error = function(e) character(0)
)
writeLines(as.character(unique(names)))
'''
        completed = self._run_script_text(
            script, timeout_seconds=max(1, int(timeout_seconds))
        )
        if completed.returncode != 0:
            return ()
        return tuple(
            line.strip()
            for line in str(completed.stdout or "").splitlines()
            if line.strip()
        )

    def list_packages(self, timeout_seconds: int = 30) -> dict[str, Any]:
        if preparation_error := self._prepare_packages(("jsonlite",)):
            return {"ok": False, "error": preparation_error, "packages": []}
        try:
            with r_temporary_environment(self._process_environment()) as environment:
                completed = run_cancellable_process(
                    [self.rscript_bin, "--vanilla", "-e", self._package_query_script()],
                    timeout_seconds=max(1, int(timeout_seconds)),
                    env=environment,
                )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": f"R package listing timed out after {timeout_seconds}s",
                "stdout": self._subprocess_text(exc.stdout),
                "stderr": self._subprocess_text(exc.stderr),
                "packages": [],
            }
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Unable to list R packages: {exc}",
                "packages": [],
            }
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"Rscript exited with code {completed.returncode}",
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "packages": [],
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": f"Invalid package inventory JSON: {exc}",
                "stdout": completed.stdout[:2000],
                "stderr": completed.stderr,
                "packages": [],
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "Invalid package inventory JSON: expected object",
                "packages": [],
            }
        payload.setdefault("ok", True)
        payload.setdefault("packages", [])
        return payload

    def package_readiness(
        self,
        packages: list[str] | tuple[str, ...],
        timeout_seconds: int = 30,
        *,
        stop_after_usable: bool = False,
    ) -> dict[str, Any]:
        requested = list(
            dict.fromkeys(
                str(package).strip()
                for package in packages
                if re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*", str(package).strip())
            )
        )
        if not requested:
            return {"ok": True, "package_count": 0, "packages": []}
        with self._package_state_lock:
            cached = [
                package
                for package in requested
                if package in self._readiness_verified_packages
            ]
        if (stop_after_usable and cached):
            cached = cached[:1]
        if cached and (stop_after_usable or len(cached) == len(requested)):
            return {
                "ok": True,
                "package_count": len(cached),
                "packages": [
                    {
                        "package": package,
                        "installed": True,
                        "loadable": True,
                        "status": "ready",
                        "warnings": [],
                        "error": "",
                        "runtime_check": "not_required",
                        "runtime_detail": "Reused from this local R session.",
                    }
                    for package in cached
                ],
                "cached": True,
            }
        try:
            completed = self._run_script_text(
                self._package_readiness_script(
                    requested,
                    stop_after_usable=stop_after_usable,
                ),
                timeout_seconds=max(1, int(timeout_seconds)),
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": f"R package readiness check timed out after {timeout_seconds}s",
                "stdout": self._subprocess_text(exc.stdout),
                "stderr": self._subprocess_text(exc.stderr),
                "packages": [],
            }
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Unable to check R package readiness: {exc}",
                "packages": [],
            }
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"Rscript exited with code {completed.returncode}",
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "packages": [],
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": f"Invalid package readiness JSON: {exc}",
                "stdout": completed.stdout[:2000],
                "stderr": completed.stderr,
                "packages": [],
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "Invalid package readiness JSON: expected object",
                "packages": [],
            }
        with self._package_state_lock:
            ready = {
                str(item.get("package") or "")
                for item in payload.get("packages") or []
                if isinstance(item, dict) and item.get("status") == "ready"
            }
            self._prepared_packages.update(ready)
            self._readiness_verified_packages.update(ready)
        return payload

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
        package = str(package or "").strip()
        query = str(query or "")
        if not package and not query.strip():
            return {
                "ok": False,
                "error": "Invalid payload: provide either an R package name or a search query",
                "package": package,
            }
        if package and not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*", package):
            return {
                "ok": False,
                "error": "Invalid payload: package must be an R package name",
                "package": package,
            }
        if preparation_error := self._prepare_packages(("jsonlite",)):
            return {"ok": False, "error": preparation_error, "package": package}
        try:
            safe_limit = min(100, max(1, int(limit)))
            if package:
                script = self._package_help_script(
                    package,
                    query,
                    safe_limit,
                    include_docs=include_docs,
                    doc_limit=doc_limit,
                    max_doc_chars=max_doc_chars,
                )
            else:
                script = self._package_help_search_script(
                    query,
                    safe_limit,
                    include_docs=include_docs,
                    doc_limit=doc_limit,
                    max_doc_chars=max_doc_chars,
                )
            completed = self._run_script_text(script, timeout_seconds=max(1, int(timeout_seconds)))
        except subprocess.TimeoutExpired as exc:
            return {
                "ok": False,
                "error": f"R package help lookup timed out after {timeout_seconds}s",
                "stdout": self._subprocess_text(exc.stdout),
                "stderr": self._subprocess_text(exc.stderr),
                "package": package,
            }
        except (OSError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"Unable to inspect R package help: {exc}",
                "package": package,
            }
        if completed.returncode != 0:
            return {
                "ok": False,
                "error": f"Rscript exited with code {completed.returncode}",
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "package": package,
            }
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "error": f"Invalid package help JSON: {exc}",
                "stdout": completed.stdout[:2000],
                "stderr": completed.stderr,
                "package": package,
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "error": "Invalid package help JSON: expected object",
                "package": package,
            }
        return payload

    def _run_script_text(self, script: str, timeout_seconds: int):
        script_path = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".R",
                encoding="utf-8",
                delete=False,
            ) as handle:
                handle.write(script)
                script_path = handle.name
            with r_temporary_environment(self._process_environment()) as environment:
                return run_cancellable_process(
                    [self.rscript_bin, "--vanilla", script_path],
                    timeout_seconds=timeout_seconds,
                    env=environment,
                    cwd=str(Path(script_path).parent),
                )
        finally:
            if script_path:
                try:
                    Path(script_path).unlink()
                except OSError:
                    pass

    def run_code(self, request: RRunRequest, *, progress_callback=None) -> RRunResult:
        try:
            with private_r_temp_directory() as scratch_dir:
                return self._run_code(
                    request, scratch_dir=scratch_dir, progress_callback=progress_callback,
                )
        except RTemporaryDirectoryError as exc:
            return RRunResult(ok=False, error=str(exc), code=request.code)

    def _run_code(
        self, request: RRunRequest, *, scratch_dir: Path, progress_callback=None,
    ) -> RRunResult:
        try:
            dataset_path = self._validated_dataset_path(request.dataset)
        except (OSError, RuntimeError, ValueError) as exc:
            return RRunResult(
                ok=False,
                error=f"Research dataset cannot be mounted safely: {exc}",
                code=request.code,
            )

        try:
            resource_policy = compute_local_resource_policy(
                environment=self.runtime_env,
                storage_path=dataset_path.parent,
                resource_class=request.resource_class,
                workload_size_bytes=dataset_path.stat().st_size,
            )
        except (OSError, ValueError) as exc:
            return RRunResult(
                ok=False,
                error=f"Local R resource policy is invalid: {exc}",
                code=request.code,
            )

        preparation_error = self._prepare_packages(
            self._required_packages(request, dataset_path, policy=resource_policy),
            conversation_job_id=request.conversation_job_id,
        )
        if preparation_error:
            return RRunResult(
                ok=False,
                error=preparation_error,
                code=request.code,
            )

        checkpoint_max_bytes = 0
        try:
            checkpoint_dir = validated_checkpoint_dir(request.checkpoint_dir)
            if checkpoint_dir is not None:
                policy = compute_local_resource_policy(storage_path=checkpoint_dir)
                checkpoint_max_bytes = parse_size_bytes(
                    policy.output_size,
                    field="checkpoint output budget",
                )
                if checkpoint_dir.is_dir():
                    validate_checkpoint_inventory(
                        checkpoint_dir,
                        max_files=CHECKPOINT_MAX_FILES,
                        max_bytes=checkpoint_max_bytes,
                    )
        except (OSError, RuntimeError, ValueError) as exc:
            return RRunResult(
                ok=False,
                error=f"Checkpoint directory is invalid: {exc}",
                code=request.code,
            )

        out_dir = Path(request.artifacts_dir).expanduser().resolve(strict=False)
        out_dir.mkdir(parents=True, exist_ok=True)
        preexisting_files = self._snapshot_output_files(out_dir)

        for record_name in ("artifacts.json", "runtime.json"):
            manifest_path = out_dir / record_name
            if not manifest_path.exists() and not manifest_path.is_symlink():
                continue
            try:
                manifest_path.unlink()
            except OSError as exc:
                return RRunResult(
                    ok=False,
                    error=f"Unable to remove stale artifact manifest: {exc}",
                    code=request.code,
                )

        helper_context = (
            nullcontext(self.helper_path)
            if self.helper_path is not None
            else materialized_resource("r_runtime/helpers/baa_helpers.R", suffix=".R")
        )
        with helper_context as helper_path:
            replay_snapshot = capture_replay_snapshot(Path(helper_path), checkpoint_dir)
            try:
                helper_sha256 = hashlib.sha256(Path(helper_path).read_bytes()).hexdigest()
            except OSError:
                # R reports an unreadable helper through the normal execution
                # error path; unavailable provenance must not escape as a
                # Python exception or be filled with a fabricated digest.
                helper_sha256 = ""
            script_path = out_dir / "analysis.R"
            try:
                self._write_analysis_script(script_path, self._build_script(
                    request, dataset_path=dataset_path, helper_path=helper_path,
                ))
            except OSError as exc:
                return RRunResult(ok=False, error=f"Unable to write analysis script safely: {exc}", code=request.code)

            env = self._process_environment(policy=resource_policy)
            env["BAA_OUTPUT_DIR"] = str(out_dir)
            env.update(TMPDIR=str(scratch_dir), TMP=str(scratch_dir), TEMP=str(scratch_dir))
            env.pop("BAA_CHECKPOINT_DIR", None)
            if checkpoint_dir is not None and checkpoint_dir.is_dir():
                env["BAA_CHECKPOINT_DIR"] = str(checkpoint_dir)

            try:
                self._validated_dataset_path(request.dataset)
            except (OSError, RuntimeError, ValueError) as exc:
                return RRunResult(
                    ok=False,
                    error=f"Research dataset cannot be mounted safely: {exc}",
                    code=request.code,
                )

            try:
                command = self._analysis_command(
                    request, dataset_path=dataset_path, helper_path=Path(helper_path),
                    output_dir=out_dir, checkpoint_dir=checkpoint_dir, scratch_dir=scratch_dir,
                )
            except (OSError, ValueError, RIsolationError) as exc:
                return RRunResult(ok=False, error=f"R isolation unavailable: {exc}", code=request.code)

            effective_timeout = request.timeout_seconds
            if effective_timeout <= 0:
                # A model that omits a timeout must still honour the local
                # resource policy ceiling instead of running unbounded.
                effective_timeout = max(
                    0, int(getattr(resource_policy, "timeout_seconds", 0) or 0)
                )
            progress_state = {"last_emit": 0.0, "last_line": ""}
            started_at = time.monotonic()

            def _emit_progress(message: str, *, force: bool = False) -> None:
                if progress_callback is None:
                    return
                now = time.monotonic()
                if not force and now - progress_state["last_emit"] < 6.0:
                    return
                progress_state["last_emit"] = now
                try:
                    progress_callback(str(message)[:200])
                except Exception:
                    pass

            def _on_r_output(tail: str) -> None:
                progress_state["last_line"] = str(tail or "").strip().splitlines()[-1][-160:]
                _emit_progress(f"R 执行中：{progress_state['last_line']}")

            stop_ticker = threading.Event()
            ticker = None
            if progress_callback is not None:

                def _tick() -> None:
                    while not stop_ticker.wait(10.0):
                        if progress_state.get("finished"):
                            break
                        elapsed = int(time.monotonic() - started_at)
                        last = progress_state.get("last_line") or ""
                        _emit_progress(
                            f"R 执行中… 已运行 {elapsed}s" + (f"｜{last}" if last else ""),
                            force=True,
                        )

                ticker = threading.Thread(target=_tick, name="r-progress", daemon=True)
                ticker.start()

            try:
                completed = run_cancellable_process(
                    command,
                    timeout_seconds=effective_timeout,
                    conversation_job_id=request.conversation_job_id,
                    env=env,
                    cwd=str(out_dir),
                    on_output=_on_r_output if progress_callback is not None else None,
                )
            except ResearchRunCancelled:
                return self._partial_run_result(
                    out_dir,
                    request,
                    preexisting_files,
                    checkpoint_dir,
                    checkpoint_max_bytes,
                    error="R execution canceled",
                )
            except subprocess.TimeoutExpired as exc:
                return self._partial_run_result(
                    out_dir,
                    request,
                    preexisting_files,
                    checkpoint_dir,
                    checkpoint_max_bytes,
                    error=(
                        f"R execution timed out after {effective_timeout}s"
                        if effective_timeout > 0
                        else "R execution timed out"
                    ),
                    stdout=self._subprocess_text(exc.stdout),
                    stderr=self._subprocess_text(exc.stderr),
                )
            except OSError as exc:
                return RRunResult(
                    ok=False,
                    error=f"Unable to start Rscript: {exc}",
                    code=request.code,
                )
            finally:
                progress_state["finished"] = True
                stop_ticker.set()

        manifest, manifest_error = self._read_manifest(out_dir)
        manifest = self._augment_manifest_with_discovered_artifacts(
            manifest,
            out_dir,
            preexisting_files=preexisting_files,
        )
        checkpoint_warnings = promote_checkpoint_artifacts(
            manifest,
            output_dir=out_dir,
            checkpoint_dir=checkpoint_dir,
            max_files=CHECKPOINT_MAX_FILES,
            max_bytes=checkpoint_max_bytes,
            excluded_sources=preexisting_files,
        )
        if checkpoint_warnings:
            warnings = manifest.get("warnings")
            if warnings is None:
                manifest["warnings"] = checkpoint_warnings
            elif isinstance(warnings, list):
                warnings.extend(checkpoint_warnings)
        run_error = "" if completed.returncode == 0 else f"Rscript exited with code {completed.returncode}"
        if run_error and manifest_error:
            error = f"{run_error}; {manifest_error}"
        else:
            error = manifest_error or run_error

        payload = {
            **manifest,
            "ok": completed.returncode == 0 and not manifest_error,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": error,
            "code": request.code,
            "reproducibility": self._read_runtime_record(out_dir, request),
        }
        payload["reproducibility"]["helper_sha256"] = helper_sha256
        payload["reproducibility"]["isolation"] = {
            "backend": getattr(command, "isolation_backend", "") or ("macos_seatbelt" if command[0] == "/usr/bin/sandbox-exec" else "linux_bubblewrap" if Path(command[0]).name == "bwrap" else "unverified"),
            "inputs": "read_only", "outputs": "job_directory", "network": "denied",
            "temporary_files": "private_operation_directory",
        }
        payload["reproducibility"]["execution_snapshot"] = replay_snapshot
        if payload.get("ok") and not any(
            payload.get(group) for group in ("tables", "figures", "texts", "models", "artifacts")
        ):
            # Without a plan there is no portfolio controller to catch this;
            # an explicit warning stops the model from narrating success over
            # an empty result set.
            warnings = payload.get("warnings")
            zero_note = (
                "本次 R 执行成功但没有生成任何表格、图形、文本或模型产物；"
                "请检查代码是否遗漏了 baa_table()/baa_plot()/baa_text() 调用。"
            )
            if isinstance(warnings, list):
                warnings.append(zero_note)
            else:
                payload["warnings"] = [zero_note]
        try:
            return RRunResult.from_dict(payload)
        except ValueError as exc:
            return RRunResult(
                ok=False,
                stdout=completed.stdout,
                stderr=completed.stderr,
                error=f"Invalid artifact manifest: {exc}",
                code=request.code,
            )

    @staticmethod
    def _write_analysis_script(path: Path, code: str) -> None:
        # Replace the directory entry itself. Opening analysis.R for writing
        # would follow a symlink or truncate an outside file through a hardlink
        # left by an earlier R run in this reusable output directory.
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=".analysis-", suffix=".R", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(code)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _analysis_command(
        self, request: RRunRequest, *, dataset_path: Path, helper_path: Path,
        output_dir: Path, checkpoint_dir: Path | None, scratch_dir: Path,
    ) -> list[str]:
        runtime_home = self.runtime_env.get("BAA_R_HOME")
        if not runtime_home:
            # Custom callers also use verified R resources; never infer a read
            # grant from the executable's parent (it may be an entire home).
            probe = self._run_script_text('cat(normalizePath(R.home(), winslash="/"))', timeout_seconds=15)
            if probe.returncode != 0:
                raise RIsolationError("Unable to locate the R runtime read root")
            runtime_home = probe.stdout.strip()
        roots = [Path(runtime_home)]
        roots.extend(Path(p) for p in self.runtime_env.get("R_LIBS_USER", "").split(os.pathsep) if p and Path(p).is_dir())
        inputs = [dataset_path, helper_path]
        inputs.extend(validate_dataset_asset_mount(asset) for asset in request.dataset.assets)
        return isolated_r_command(
            [self.rscript_bin, "--vanilla", str(output_dir / "analysis.R")],
            read_paths=inputs, runtime_roots=roots, output_dir=output_dir,
            checkpoint_dir=checkpoint_dir, scratch_dir=scratch_dir,
        )

    @staticmethod
    def _read_runtime_record(out_dir: Path, request: RRunRequest) -> dict[str, Any]:
        record: dict[str, Any] = {"status": "not_recorded"}
        path = out_dir / "runtime.json"
        try:
            if not path.is_symlink() and path.stat().st_size <= 128 * 1024:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    record = value
        except (OSError, ValueError):
            pass
        record["code_sha256"] = hashlib.sha256(request.code.encode("utf-8")).hexdigest()
        record["dataset_manifest"] = request.dataset.to_dict()
        record["resource_class"] = request.resource_class
        return record

    def _process_environment(
        self,
        *,
        resource_class: str = "standard",
        workload_size_bytes: int = 0,
        policy=None,
    ) -> dict[str, str]:
        # ``runtime_env`` was constructed from a sanitized host snapshot by
        # HostREnvironment. Re-reading os.environ here would expose API keys or
        # resurrect conflicting R/loader variables.
        selected = policy or compute_local_resource_policy(
            environment=self.runtime_env,
            resource_class=resource_class,
            workload_size_bytes=workload_size_bytes,
        )
        environment = dict(self.runtime_env)
        workers = str(selected.worker_count)
        native_threads = str(selected.native_thread_count)
        environment.update(
            {
                "BAA_R_WORKERS": workers,
                "BAA_R_NATIVE_THREADS": native_threads,
                "BAA_R_MEMORY_BUDGET_BYTES": str(selected.memory_budget_bytes),
                "BAA_R_RESOURCE_CLASS": selected.resource_class,
                "BAA_R_PARALLEL_BACKEND": selected.parallel_backend,
                "BAA_LAZY_DATASET_THRESHOLD_BYTES": str(
                    selected.in_memory_threshold_bytes
                ),
                "OMP_NUM_THREADS": native_threads,
                "OMP_THREAD_LIMIT": native_threads,
                "OPENBLAS_NUM_THREADS": native_threads,
                "MKL_NUM_THREADS": native_threads,
                "VECLIB_MAXIMUM_THREADS": native_threads,
                "RCPP_PARALLEL_NUM_THREADS": native_threads,
                "ARROW_NUM_THREADS": native_threads,
                "R_DATATABLE_NUM_PROCS_PERCENT": str(
                    max(
                        1,
                        min(
                            100,
                            round(
                                selected.native_thread_count
                                * 100
                                / max(1, selected.host.cpu_count)
                            ),
                        ),
                    )
                ),
            }
        )
        return environment

    def _prepare_packages(self, packages: Sequence[str], conversation_job_id: str = "") -> str:
        if self.package_installer is None:
            return ""
        requested = tuple(dict.fromkeys(str(package) for package in packages if package))
        with self._package_state_lock:
            pending = tuple(
                package for package in requested if package not in self._prepared_packages
            )
            if not pending:
                return ""
            try:
                self._call_installer(pending, conversation_job_id)
            except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
                return f"R package preparation failed: {exc}"
            self._prepared_packages.update(pending)
        return ""

    def _call_installer(self, pending: Sequence[str], conversation_job_id: str):
        """Invoke the installer, passing the cancellation scope when supported.

        Implicit installs triggered inside an analysis must register with the
        run controller (via the job id) so the user's stop and app-exit
        cancel_all reach the compiler/download process instead of leaving an
        unbounded orphan.
        """
        installer = self.package_installer
        if conversation_job_id:
            import inspect

            try:
                params = inspect.signature(installer).parameters
            except (TypeError, ValueError):
                params = {}
            accepts_job_id = any(
                parameter.kind
                in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                for parameter in params.values()
            ) and len([p for p in params.values() if p.kind is not inspect.Parameter.VAR_POSITIONAL and p.kind is not inspect.Parameter.VAR_KEYWORD]) >= 2
            if accepts_job_id:
                return installer(pending, conversation_job_id)
        return installer(pending)

    @staticmethod
    def _referenced_packages(code: str) -> tuple[str, ...]:
        return referenced_r_packages(code)

    @staticmethod
    def _helper_packages(code: str) -> tuple[str, ...]:
        return tuple(package for package in referenced_r_packages(code) if package == "readr")

    def _required_packages(
        self,
        request: RRunRequest,
        dataset_path: Path,
        *,
        policy=None,
    ) -> tuple[str, ...]:
        inspection = request.code.strip() == DATASET_PROFILE_R_CODE.strip()
        asset_formats = tuple(asset.format for asset in request.dataset.assets)
        policy_environment = self.runtime_env or {
            name: value
            for name, value in os.environ.items()
            if name.startswith("BAA_R_") or name.startswith("BAA_LAZY_")
        }
        selected = policy or compute_local_resource_policy(
            environment=policy_environment,
            resource_class=request.resource_class,
            workload_size_bytes=dataset_path.stat().st_size,
        )
        try:
            lazy = dataset_path.stat().st_size > selected.in_memory_threshold_bytes
        except (OSError, ValueError):
            lazy = False
        packages = set(
            dataset_loader_packages(
                request.dataset.format,
                asset_formats,
                inspection=inspection,
                lazy=lazy,
            )
        )
        packages.update(self._helper_packages(request.code))
        if not inspection:
            packages.update(self._referenced_packages(request.code))
        return tuple(sorted(packages, key=str.casefold))

    @staticmethod
    def _validated_dataset_path(dataset: DatasetRef) -> Path:
        declared_assets = list(dataset.assets)
        if not declared_assets:
            return validate_dataset_asset_mount(
                DatasetAssetRef(
                    path=dataset.path,
                    format=dataset.format,
                    role="primary",
                    kind="",
                    name=dataset.name,
                )
            )

        validated = [(asset, validate_dataset_asset_mount(asset)) for asset in declared_assets]
        dataset_path = validate_dataset_asset_mount(
            DatasetAssetRef(
                path=dataset.path,
                format=dataset.format,
                kind="",
            )
        )
        primary = next((item for item in validated if item[0].role == "primary"), None)
        if primary is None:
            primary = next((item for item in validated if item[1] == dataset_path), None)
        if primary is None or primary[1] != dataset_path:
            raise ValueError("Scientific dataset primary asset is not declared")
        return dataset_path

    def _build_script(
        self,
        request: RRunRequest,
        *,
        dataset_path: Path | None = None,
        helper_path: Path | None = None,
    ) -> str:
        selected_helper = helper_path or self.helper_path
        if selected_helper is None:
            raise ValueError("R helper path must be materialized for script generation")
        helper_literal = self._r_string(selected_helper)
        primary_path = str(dataset_path or request.dataset.path)
        dataset_manifest = request.dataset.to_dict()
        dataset_manifest["path"] = primary_path
        if not dataset_manifest["assets"]:
            dataset_manifest["assets"] = [
                {
                    "path": primary_path,
                    "format": request.dataset.format,
                    "role": "primary",
                    "relative_mount_name": Path(primary_path).name,
                    "kind": "directory" if Path(primary_path).is_dir() else "file",
                    "name": request.dataset.name or Path(primary_path).name,
                }
            ]
        dataset_manifest_json = self._r_string(
            json.dumps(dataset_manifest, ensure_ascii=False, separators=(",", ":"))
        )
        load_profile = (
            "inspection"
            if request.code.strip() == DATASET_PROFILE_R_CODE.strip()
            else "analysis"
        )
        # A stable starting RNG state is recorded for every R step. Explicit
        # set.seed() calls in the researcher code still take precedence.
        seed = int(hashlib.sha256(request.job_id.encode("utf-8")).hexdigest()[:7], 16)

        return f"""
options(encoding = "UTF-8")
.baa_configured_workers <- suppressWarnings(as.integer(Sys.getenv("BAA_R_WORKERS", "1")))
if (length(.baa_configured_workers) != 1L || is.na(.baa_configured_workers) ||
    .baa_configured_workers < 1L) {{
  .baa_configured_workers <- 1L
}}
.baa_memory_budget_bytes <- suppressWarnings(as.numeric(
  Sys.getenv("BAA_R_MEMORY_BUDGET_BYTES", "0")
))
if (length(.baa_memory_budget_bytes) != 1L || is.na(.baa_memory_budget_bytes) ||
    .baa_memory_budget_bytes < 0) {{
  .baa_memory_budget_bytes <- 0
}}
options(mc.cores = .baa_configured_workers)
options(Ncpus = .baa_configured_workers)
options(readr.num_threads = .baa_configured_workers)
options(baa.memory_budget_bytes = .baa_memory_budget_bytes)
options(baa.resource_class = Sys.getenv("BAA_R_RESOURCE_CLASS", "standard"))
options(baa.parallel_backend = Sys.getenv("BAA_R_PARALLEL_BACKEND", "multicore"))
options(parallelly.maxWorkers.localhost = .baa_configured_workers)
rm(.baa_configured_workers, .baa_memory_budget_bytes)
if (!requireNamespace("jsonlite", quietly = TRUE)) {{
  stop("The R runtime requires package 'jsonlite'.")
}}
for (.baa_compat_package in c("readr", "readxl", "haven", "dplyr", "ggplot2")) {{
  .baa_search_name <- paste0("package:", .baa_compat_package)
  if (requireNamespace(.baa_compat_package, quietly = TRUE) &&
      !.baa_search_name %in% search()) {{
    suppressPackageStartupMessages(
      base::attachNamespace(getNamespace(.baa_compat_package))
    )
  }}
}}

.baa_pending_warnings <- character()
.baa_capture_warning <- function(w) {{
  message <- conditionMessage(w)
  if (exists("baa_record_warning", mode = "function")) {{
    baa_record_warning(message)
  }} else {{
    .baa_pending_warnings <<- c(.baa_pending_warnings, message)
  }}
  invokeRestart("muffleWarning")
}}

.baa_flush_if_available <- function() {{
  if (exists("baa_flush_artifacts", mode = "function")) {{
    try(baa_flush_artifacts(), silent = TRUE)
  }}
}}

.baa_runtime_file <- file.path(Sys.getenv("BAA_OUTPUT_DIR"), "runtime.json")
.baa_rng_before <- NULL
.baa_write_runtime <- function(status) {{
  namespaces <- loadedNamespaces()
  versions <- vapply(namespaces, function(name) as.character(utils::packageVersion(name)), character(1))
  jsonlite::write_json(list(
    status = status, r_version = R.version.string, platform = R.version$platform,
    packages = as.list(versions), seed = {seed}, rng_kind = RNGkind(),
    rng_before = .baa_rng_before,
    rng_after = if (exists(".Random.seed", envir = .GlobalEnv)) get(".Random.seed", envir = .GlobalEnv) else NULL,
    locale = Sys.getlocale(), timezone = Sys.timezone(),
    workers = getOption("mc.cores"), resource_class = getOption("baa.resource_class"),
    session_info = capture.output(utils::sessionInfo())
  ), .baa_runtime_file, auto_unbox = TRUE, pretty = TRUE, null = "null")
}}

tryCatch({{
  withCallingHandlers({{
    source({helper_literal})
{INDEX_CONTENT_VALIDATION_R_CODE}
    .baa_dataset_manifest_json <- {dataset_manifest_json}
    dataset_manifest <- jsonlite::fromJSON(
      .baa_dataset_manifest_json,
      simplifyVector = FALSE
    )
    baa_validate_dataset_index_content(dataset_manifest)
    if (length(.baa_pending_warnings) > 0) {{
      for (message in .baa_pending_warnings) {{
        baa_record_warning(message)
      }}
    }}
    options(baa.dataset_load_profile = {self._r_string(load_profile)})
    dataset_bundle <- baa_read_scientific_bundle(dataset_manifest)
    dataset <- baa_bundle_dataset(dataset_bundle)
    dataset_assets <- dataset_bundle$assets
    .baa_output_dir <- normalizePath(Sys.getenv("BAA_OUTPUT_DIR"), winslash = "/", mustWork = TRUE)
    .baa_checkpoint_dir <- Sys.getenv("BAA_CHECKPOINT_DIR", unset = "")
    .baa_job_roots <- unique(c(
      .baa_output_dir,
      if (nzchar(.baa_checkpoint_dir))
        normalizePath(.baa_checkpoint_dir, winslash = "/", mustWork = FALSE)
    ))
    download.file <- function(...) {{
      stop("BAA_JOB_PATH: download.file is not allowed in analysis jobs", call. = FALSE)
    }}
    system <- function(...) {{
      stop("BAA_JOB_PATH: system() is not allowed in analysis jobs", call. = FALSE)
    }}
    system2 <- function(...) {{
      stop("BAA_JOB_PATH: system2() is not allowed in analysis jobs", call. = FALSE)
    }}
    shell <- function(...) {{
      stop("BAA_JOB_PATH: shell() is not allowed in analysis jobs", call. = FALSE)
    }}
    setwd <- function(dir) {{
      target <- tryCatch(
        normalizePath(dir, winslash = "/", mustWork = FALSE),
        error = function(e) as.character(dir)[1]
      )
      allowed <- vapply(.baa_job_roots, function(root) {{
        identical(target, root) || startsWith(paste0(target, "/"), paste0(root, "/"))
      }}, logical(1))
      if (!any(allowed)) {{
        stop(
          sprintf("BAA_JOB_PATH: setwd outside the analysis job directory is not allowed: %s", dir),
          call. = FALSE
        )
      }}
      base::setwd(dir)
    }}
    set.seed({seed})
    .baa_rng_before <- get(".Random.seed", envir = .GlobalEnv)
    .baa_write_runtime("started")
{request.code}
  }}, warning = .baa_capture_warning)
}}, error = function(e) {{
  try(.baa_write_runtime("failed"), silent = TRUE)
  .baa_flush_if_available()
  stop(e)
}})

baa_flush_artifacts()
.baa_write_runtime("succeeded")
"""

    @staticmethod
    def _package_query_script() -> str:
        return r"""
ip <- as.data.frame(
  utils::installed.packages(fields = c("Title", "Description", "Priority")),
  stringsAsFactors = FALSE
)
cols <- c("Package", "Version", "Priority", "Title", "Description")
for (col in cols) {
  if (!col %in% names(ip)) ip[[col]] <- ""
}
ip <- ip[order(tolower(ip$Package)), cols, drop = FALSE]
row.names(ip) <- NULL
items <- lapply(seq_len(nrow(ip)), function(i) {
  row <- ip[i, , drop = FALSE]
  list(
    package = as.character(row$Package),
    version = as.character(row$Version),
    priority = ifelse(is.na(row$Priority), "", as.character(row$Priority)),
    title = ifelse(is.na(row$Title), "", as.character(row$Title)),
    description = ifelse(is.na(row$Description), "", as.character(row$Description))
  )
})
cat(jsonlite::toJSON(
  list(
    ok = TRUE,
    r_version = as.character(getRversion()),
    package_count = length(items),
    packages = items
  ),
  auto_unbox = TRUE,
  null = "null",
  na = "null"
))
"""

    @staticmethod
    def _package_readiness_script(
        packages: Sequence[str],
        *,
        stop_after_usable: bool = False,
    ) -> str:
        encoded = LocalRscriptBackend._r_string(
            json.dumps(list(packages), ensure_ascii=False, separators=(",", ":"))
        )
        stop_value = "TRUE" if stop_after_usable else "FALSE"
        return f"""
requested <- jsonlite::fromJSON({encoded})
stop_after_usable <- {stop_value}
check_one <- function(package) {{
  warnings <- character()
  load_error <- ""
  installed_path <- tryCatch(find.package(package, quiet = TRUE), error = function(e) "")
  if (!length(installed_path) || !nzchar(installed_path[[1]])) {{
    return(list(
      package = package,
      installed = FALSE,
      loadable = FALSE,
      status = "missing",
      version = "",
      warnings = list(),
      error = "Package is not installed"
    ))
  }}
  loadable <- withCallingHandlers(
    tryCatch(
      {{ loadNamespace(package); TRUE }},
      error = function(e) {{ load_error <<- conditionMessage(e); FALSE }}
    ),
    warning = function(w) {{
      warnings <<- c(warnings, conditionMessage(w))
      invokeRestart("muffleWarning")
    }}
  )
  if (!isTRUE(loadable) && !nzchar(load_error)) load_error <- "Namespace could not be loaded"
  status <- if (!isTRUE(loadable)) "load_error" else if (length(warnings)) "warning" else "ready"
  runtime_check <- "not_required"
  runtime_detail <- ""
  if (isTRUE(loadable) && identical(package, "torch")) {{
    runtime_check <- "libtorch"
    torch_ready <- tryCatch(isTRUE(torch::torch_is_installed()), error = function(e) {{
      runtime_detail <<- conditionMessage(e)
      FALSE
    }})
    if (!torch_ready) {{
      status <- "needs_setup"
      if (!nzchar(runtime_detail)) runtime_detail <- "Run torch::install_torch() and restart R."
    }} else {{
      runtime_detail <- "LibTorch/Lantern is installed."
    }}
  }}
  if (isTRUE(loadable) && identical(package, "cmdstanr")) {{
    runtime_check <- "cmdstan"
    cmdstan_version <- tryCatch(
      as.character(cmdstanr::cmdstan_version(error_on_NA = FALSE)),
      error = function(e) {{ runtime_detail <<- conditionMessage(e); character() }}
    )
    if (!length(cmdstan_version) || !nzchar(cmdstan_version[[1]])) {{
      status <- "needs_setup"
      if (!nzchar(runtime_detail)) runtime_detail <- "Run cmdstanr::check_cmdstan_toolchain() and cmdstanr::install_cmdstan()."
    }} else {{
      runtime_detail <- paste("CmdStan", cmdstan_version[[1]])
    }}
  }}
  if (isTRUE(loadable) && identical(package, "tensorflow")) {{
    runtime_check <- "python_ml"
    tensorflow_ready <- tryCatch({{
      configuration <- tensorflow::tf_config()
      if (!isTRUE(configuration$available)) {{
        runtime_detail <- if (
          !is.null(configuration$error_message) &&
          length(configuration$error_message) &&
          nzchar(as.character(configuration$error_message[[1]]))
        ) as.character(configuration$error_message[[1]]) else "TensorFlow backend is unavailable"
        FALSE
      }} else TRUE
    }}, error = function(e) {{ runtime_detail <<- conditionMessage(e); FALSE }})
    if (!tensorflow_ready) {{
      status <- "needs_setup"
      if (!nzchar(runtime_detail)) runtime_detail <- "Run tensorflow::install_tensorflow() or configure a compatible existing Python backend."
    }} else {{
      runtime_detail <- "TensorFlow Python backend initialized."
    }}
  }}
  if (isTRUE(loadable) && package %in% c("keras", "keras3")) {{
    runtime_check <- "python_ml"
    keras_ready <- tryCatch({{
      checker <- get("is_keras_available", envir = asNamespace(package), inherits = FALSE)
      isTRUE(checker())
    }}, error = function(e) {{
      runtime_detail <<- conditionMessage(e)
      FALSE
    }})
    if (!keras_ready) {{
      status <- "needs_setup"
      if (!nzchar(runtime_detail)) runtime_detail <- paste0(
        "Run ", package, "::install_keras() or configure a compatible existing Python backend."
      )
    }} else {{
      runtime_detail <- "Keras Python backend initialized."
    }}
  }}
  list(
    package = package,
    installed = TRUE,
    loadable = isTRUE(loadable),
    status = status,
    version = tryCatch(as.character(utils::packageVersion(package)), error = function(e) ""),
    warnings = as.list(unique(warnings)),
    error = load_error,
    runtime_check = runtime_check,
    runtime_detail = runtime_detail
  )
}}
items <- list()
for (package in requested) {{
  item <- check_one(package)
  items[[length(items) + 1L]] <- item
  # A warning may be an ABI/shared-library defect. Continue until a fully
  # ready candidate is found so a broken first alternative cannot mask it.
  if (isTRUE(stop_after_usable) && identical(item$status, "ready")) break
}}
cat(jsonlite::toJSON(
  list(
    ok = TRUE,
    package_count = length(items),
    packages = items,
    warning_count = sum(vapply(items, function(x) identical(x$status, "warning"), logical(1))),
    error_count = sum(vapply(items, function(x) x$status %in% c("missing", "load_error", "needs_setup"), logical(1)))
  ),
  auto_unbox = TRUE,
  null = "null",
  na = "null"
))
"""

    @staticmethod
    def _package_help_search_script(
        query: str,
        limit: int,
        *,
        include_docs: bool = True,
        doc_limit: int = 3,
        max_doc_chars: int = 4000,
    ) -> str:
        return f"""
query <- {LocalRscriptBackend._r_string(query)}
limit <- as.integer({LocalRscriptBackend._r_string(limit)})
include_docs <- {LocalRscriptBackend._r_string("TRUE" if include_docs else "FALSE")} == "TRUE"
doc_limit <- as.integer({LocalRscriptBackend._r_string(doc_limit)})
max_doc_chars <- as.integer({LocalRscriptBackend._r_string(max_doc_chars)})
json_emit <- function(x) {{
  cat(jsonlite::toJSON(x, auto_unbox = TRUE, null = "null", na = "null"))
}}
query <- trimws(query)
if (!nzchar(query)) {{
  json_emit(list(ok = FALSE, mode = "cross_package_search", query = query, error = "Search query is required when package is omitted"))
  quit(save = "no", status = 0)
}}
value_or_empty <- function(x) {{
  if (is.null(x) || length(x) == 0 || is.na(x)) "" else as.character(x)[[1]]
}}
truncate_text <- function(x, n) {{
  x <- paste(as.character(x), collapse = "\\n")
  if (nchar(x, type = "chars") <= n) return(x)
  paste0(substr(x, 1, n), "\\n...[truncated]")
}}
clean_help_text <- function(x) {{
  text <- paste(as.character(x), collapse = "\\n")
  backspace <- rawToChar(as.raw(8))
  text <- gsub(paste0("_", backspace), "", text, fixed = TRUE)
  text <- gsub(backspace, "", text, fixed = TRUE)
  text
}}
terms <- unique(strsplit(tolower(query), "[[:space:],;/|]+")[[1]])
terms <- terms[nchar(terms) >= 2]
export_cache <- new.env(parent = emptyenv())
namespace_exports <- function(package) {{
  if (exists(package, envir = export_cache, inherits = FALSE)) {{
    return(get(package, envir = export_cache, inherits = FALSE))
  }}
  pkg_path <- tryCatch(find.package(package, quiet = TRUE), error = function(e) "")
  if (!nzchar(pkg_path)) {{
    assign(package, character(), envir = export_cache)
    return(character())
  }}
  ns_path <- file.path(pkg_path, "NAMESPACE")
  if (!file.exists(ns_path)) {{
    assign(package, character(), envir = export_cache)
    return(character())
  }}
  lines <- tryCatch(readLines(ns_path, warn = FALSE), error = function(e) character())
  text <- paste(lines, collapse = "\\n")
  hits <- gregexpr("export\\\\(([^)]*)\\\\)", text, perl = TRUE)[[1]]
  if (identical(hits, -1L)) {{
    assign(package, character(), envir = export_cache)
    return(character())
  }}
  out <- character()
  lengths <- attr(hits, "match.length")
  for (i in seq_along(hits)) {{
    chunk <- substr(text, hits[[i]], hits[[i]] + lengths[[i]] - 1)
    chunk <- sub("^export\\\\(", "", chunk)
    chunk <- sub("\\\\)$", "", chunk)
    parts <- trimws(unlist(strsplit(chunk, ",")))
    parts <- gsub("^[`\\\"']|[`\\\"']$", "", parts)
    out <- c(out, parts[nzchar(parts)])
  }}
  out <- sort(unique(out))
  assign(package, out, envir = export_cache)
  out
}}
run_help_search <- function(pattern, fuzzy = FALSE) {{
  tryCatch(
    utils::help.search(pattern, fields = c("alias", "concept", "title"), agrep = fuzzy)$matches,
    error = function(e) NULL
  )
}}
rows <- run_help_search(query, FALSE)
if ((is.null(rows) || NROW(rows) == 0) && length(terms) > 1) {{
  pieces <- lapply(terms, run_help_search, fuzzy = FALSE)
  pieces <- pieces[!vapply(pieces, is.null, logical(1))]
  if (length(pieces)) rows <- do.call(rbind, pieces)
}}
if (is.null(rows) || NROW(rows) == 0) {{
  rows <- run_help_search(query, TRUE)
}}
if (is.null(rows) || NROW(rows) == 0) {{
  json_emit(list(
    ok = TRUE,
    mode = "cross_package_search",
    query = query,
    search_result_count = 0,
    search_results = list(),
    recommended_packages = list(),
    help_documents = list(),
    help_document_errors = list(),
    usage_notes = list(
      "No installed help topics matched the query. Call get_r_package_inventory with the broader analysis intent.",
      "Do not invent function names when the installed help index has no match."
    )
  ))
  quit(save = "no", status = 0)
}}
df <- as.data.frame(rows, stringsAsFactors = FALSE)
for (col in c("Package", "Topic", "Title")) {{
  if (!col %in% names(df)) df[[col]] <- ""
}}
df$Package <- as.character(df$Package)
df$Topic <- as.character(df$Topic)
df$Title <- as.character(df$Title)
df <- df[nzchar(df$Package) & nzchar(df$Topic), c("Package", "Topic", "Title"), drop = FALSE]
df <- df[!duplicated(paste(df$Package, df$Topic, sep = "::")), , drop = FALSE]
score_one <- function(package, topic, title) {{
  score <- 0L
  topic_l <- tolower(topic)
  title_l <- tolower(title)
  query_l <- tolower(query)
  exports_l <- tolower(namespace_exports(package))
  if (nzchar(query_l) && query_l %in% exports_l) score <- score + 120L
  if (nzchar(query_l) && topic_l == query_l) score <- score + 100L
  if (nzchar(query_l) && startsWith(topic_l, query_l)) score <- score + 50L
  for (term in terms) {{
    if (topic_l == term) score <- score + 30L
    if (grepl(term, topic_l, fixed = TRUE)) score <- score + 15L
    if (grepl(term, title_l, fixed = TRUE)) score <- score + 8L
  }}
  score
}}
df$score <- mapply(score_one, df$Package, df$Topic, df$Title)
df <- df[order(-df$score, tolower(df$Package), tolower(df$Topic)), , drop = FALSE]
df <- head(df, limit)
ip <- as.data.frame(utils::installed.packages(fields = c("Title", "Description", "Priority")), stringsAsFactors = FALSE)
package_meta <- function(package) {{
  hit <- ip[ip$Package == package, , drop = FALSE]
  if (!nrow(hit)) return(list(version = "", title = "", description = ""))
  list(
    version = value_or_empty(hit$Version[[1]]),
    title = if ("Title" %in% names(hit)) value_or_empty(hit$Title[[1]]) else "",
    description = if ("Description" %in% names(hit)) value_or_empty(hit$Description[[1]]) else ""
  )
}}
search_results <- lapply(seq_len(nrow(df)), function(i) {{
  meta <- package_meta(df$Package[[i]])
  list(
    package = df$Package[[i]],
    topic = df$Topic[[i]],
    title = df$Title[[i]],
    score = as.integer(df$score[[i]]),
    package_version = meta$version,
    package_title = meta$title
  )
}})
split_rows <- split(df, df$Package)
recommended_packages <- lapply(names(split_rows), function(package) {{
  part <- split_rows[[package]]
  meta <- package_meta(package)
  list(
    package = package,
    version = meta$version,
    title = meta$title,
    description = meta$description,
    match_count = nrow(part),
    top_score = max(part$score),
    top_topics = as.list(head(unique(part$Topic), 6))
  )
}})
recommended_packages <- recommended_packages[order(
  -vapply(recommended_packages, function(x) x$top_score, numeric(1)),
  -vapply(recommended_packages, function(x) x$match_count, numeric(1)),
  vapply(recommended_packages, function(x) tolower(x$package), character(1))
)]
help_document_errors <- list()
record_help_error <- function(package, topic, message) {{
  help_document_errors[[length(help_document_errors) + 1]] <<- list(
    package = as.character(package),
    topic = as.character(topic),
    error = as.character(message)
  )
}}
read_help_doc <- function(package, topic) {{
  obj <- tryCatch(
    do.call(utils::help, list(topic, package = package)),
    error = function(e) {{
      record_help_error(package, topic, conditionMessage(e))
      NULL
    }}
  )
  if (is.null(obj) || length(obj) == 0) {{
    record_help_error(package, topic, "No help topic found")
    return(NULL)
  }}
  text <- tryCatch({{
    rd <- utils:::.getHelpFile(obj)
    paste(capture.output(tools::Rd2txt(rd, out = "")), collapse = "\\n")
  }}, error = function(e) {{
    record_help_error(package, topic, conditionMessage(e))
    ""
  }})
  if (!nzchar(text)) {{
    text <- tryCatch(
      paste(capture.output(print(obj)), collapse = "\\n"),
      error = function(e) {{
        record_help_error(package, topic, conditionMessage(e))
        ""
      }}
    )
  }}
  text <- clean_help_text(text)
  if (!nzchar(text)) return(NULL)
  list(package = package, topic = topic, text = truncate_text(text, max_doc_chars))
}}
help_documents <- list()
if (include_docs && nrow(df) > 0 && doc_limit > 0) {{
  for (i in seq_len(nrow(df))) {{
    doc <- read_help_doc(df$Package[[i]], df$Topic[[i]])
    if (!is.null(doc)) {{
      help_documents[[length(help_documents) + 1]] <- doc
    }}
    if (length(help_documents) >= doc_limit) break
  }}
}}
json_emit(list(
  ok = TRUE,
  mode = "cross_package_search",
  package = "",
  query = query,
  search_result_count = nrow(df),
  search_results = search_results,
  recommended_packages = recommended_packages,
  help_documents = help_documents,
  help_document_errors = help_document_errors,
  usage_notes = list(
    "This is a cross-package search over the installed R help index.",
    "Choose a candidate package, then call get_r_package_help(package=..., query=...) for package-specific exports and docs.",
    "Generated analysis still must execute with run_r_code and persist outputs through baa_* helpers."
  )
))
"""

    @staticmethod
    def _package_help_script(
        package: str,
        query: str,
        limit: int,
        *,
        include_docs: bool = True,
        doc_limit: int = 3,
        max_doc_chars: int = 4000,
    ) -> str:
        return f"""
package <- {LocalRscriptBackend._r_string(package)}
query <- {LocalRscriptBackend._r_string(query)}
limit <- as.integer({LocalRscriptBackend._r_string(limit)})
include_docs <- {LocalRscriptBackend._r_string("TRUE" if include_docs else "FALSE")} == "TRUE"
doc_limit <- as.integer({LocalRscriptBackend._r_string(doc_limit)})
max_doc_chars <- as.integer({LocalRscriptBackend._r_string(max_doc_chars)})
json_emit <- function(x) {{
  cat(jsonlite::toJSON(x, auto_unbox = TRUE, null = "null", na = "null"))
}}
pkg_path <- tryCatch(find.package(package, quiet = TRUE), error = function(e) "")
if (!nzchar(pkg_path)) {{
  json_emit(list(ok = FALSE, package = package, installed = FALSE, error = "Package is not installed"))
  quit(save = "no", status = 0)
}}
desc <- tryCatch(utils::packageDescription(package), error = function(e) NULL)
value_or_empty <- function(x) {{
  if (is.null(x) || length(x) == 0 || is.na(x)) "" else as.character(x)[[1]]
}}
namespace_exports <- function(path) {{
  ns_path <- file.path(path, "NAMESPACE")
  if (!file.exists(ns_path)) return(character())
  lines <- tryCatch(readLines(ns_path, warn = FALSE), error = function(e) character())
  text <- paste(lines, collapse = "\\n")
  hits <- gregexpr("export\\\\(([^)]*)\\\\)", text, perl = TRUE)[[1]]
  if (identical(hits, -1L)) return(character())
  out <- character()
  lengths <- attr(hits, "match.length")
  for (i in seq_along(hits)) {{
    chunk <- substr(text, hits[[i]], hits[[i]] + lengths[[i]] - 1)
    chunk <- sub("^export\\\\(", "", chunk)
    chunk <- sub("\\\\)$", "", chunk)
    parts <- trimws(unlist(strsplit(chunk, ",")))
    parts <- gsub("^[`\\\"']|[`\\\"']$", "", parts)
    out <- c(out, parts[nzchar(parts)])
  }}
  sort(unique(out))
}}
exports <- namespace_exports(pkg_path)
terms <- unique(strsplit(tolower(query), "[[:space:],;/|]+")[[1]])
terms <- terms[nchar(terms) >= 2]
match_text <- function(x) {{
  tx <- tolower(as.character(x))
  if (!length(terms)) return(TRUE)
  any(vapply(terms, function(term) grepl(term, tx, fixed = TRUE), logical(1)))
}}
matched_exports <- exports[vapply(exports, match_text, logical(1))]
matched_exports <- head(matched_exports, limit)

help_topics <- list()
if (nzchar(trimws(query))) {{
  help_rows <- tryCatch(utils::help.search(query, package = package)$matches, error = function(e) NULL)
  if (!is.null(help_rows) && NROW(help_rows) > 0) {{
    help_df <- as.data.frame(help_rows, stringsAsFactors = FALSE)
    help_df <- head(help_df, limit)
    help_topics <- lapply(seq_len(nrow(help_df)), function(i) {{
      topic <- if ("Topic" %in% names(help_df)) help_df$Topic[[i]] else ""
      title <- if ("Title" %in% names(help_df)) help_df$Title[[i]] else ""
      list(topic = as.character(topic), title = as.character(title))
    }})
  }}
}}

doc_topics <- unique(c(
  if (length(matched_exports)) matched_exports else character(),
  if (length(help_topics)) vapply(help_topics, function(x) x$topic, character(1)) else character()
))
doc_topics <- doc_topics[nzchar(doc_topics)]
doc_topics <- head(doc_topics, doc_limit)
truncate_text <- function(x, n) {{
  x <- paste(as.character(x), collapse = "\\n")
  if (nchar(x, type = "chars") <= n) return(x)
  paste0(substr(x, 1, n), "\\n...[truncated]")
}}
clean_help_text <- function(x) {{
  text <- paste(as.character(x), collapse = "\\n")
  backspace <- rawToChar(as.raw(8))
  text <- gsub(paste0("_", backspace), "", text, fixed = TRUE)
  text <- gsub(backspace, "", text, fixed = TRUE)
  text
}}
help_document_errors <- list()
record_help_error <- function(topic, message) {{
  help_document_errors[[length(help_document_errors) + 1]] <<- list(
    topic = as.character(topic),
    error = as.character(message)
  )
}}
read_help_doc <- function(topic) {{
  obj <- tryCatch(
    do.call(utils::help, list(topic, package = package)),
    error = function(e) {{
      record_help_error(topic, conditionMessage(e))
      NULL
    }}
  )
  if (is.null(obj) || length(obj) == 0) {{
    record_help_error(topic, "No help topic found")
    return(NULL)
  }}
  text <- tryCatch({{
    rd <- utils:::.getHelpFile(obj)
    paste(capture.output(tools::Rd2txt(rd, out = "")), collapse = "\\n")
  }}, error = function(e) {{
    record_help_error(topic, conditionMessage(e))
    ""
  }})
  if (!nzchar(text)) {{
    text <- tryCatch(
      paste(capture.output(print(obj)), collapse = "\\n"),
      error = function(e) {{
        record_help_error(topic, conditionMessage(e))
        ""
      }}
    )
  }}
  text <- clean_help_text(text)
  if (!nzchar(text)) return(NULL)
  list(topic = topic, text = truncate_text(text, max_doc_chars))
}}
help_documents <- list()
if (include_docs && length(doc_topics) > 0) {{
  docs <- lapply(doc_topics, read_help_doc)
  docs <- docs[!vapply(docs, is.null, logical(1))]
  help_documents <- docs
}}

vignette_rows <- tryCatch(utils::vignette(package = package)$results, error = function(e) NULL)
vignettes <- list()
if (!is.null(vignette_rows) && NROW(vignette_rows) > 0) {{
  vignette_df <- as.data.frame(vignette_rows, stringsAsFactors = FALSE)
  vignette_df <- head(vignette_df, min(limit, 20))
  vignettes <- lapply(seq_len(nrow(vignette_df)), function(i) {{
    list(
      item = if ("Item" %in% names(vignette_df)) as.character(vignette_df$Item[[i]]) else "",
      title = if ("Title" %in% names(vignette_df)) as.character(vignette_df$Title[[i]]) else ""
    )
  }})
}}

json_emit(list(
  ok = TRUE,
  package = package,
  installed = TRUE,
  path = pkg_path,
  version = value_or_empty(desc$Version),
  title = value_or_empty(desc$Title),
  description = value_or_empty(desc$Description),
  query = query,
  explicit_export_count = length(exports),
  matched_exports = as.list(matched_exports),
  help_topics = help_topics,
  help_documents = help_documents,
  help_document_errors = help_document_errors,
  vignettes = vignettes,
  usage_notes = list(
    "Use requireNamespace(package, quietly = TRUE) before calling functions.",
    "Prefer package::function syntax in generated R code.",
    "Use help_documents, help_topics, matched_exports, and vignettes to choose functions; do not treat this as a fixed template."
  )
))
"""

    def _partial_run_result(
        self,
        out_dir: Path,
        request: RRunRequest,
        preexisting_files,
        checkpoint_dir,
        checkpoint_max_bytes,
        *,
        error: str,
        stdout: str = "",
        stderr: str = "",
    ) -> RRunResult:
        """Collect artifacts that reached disk before kill/timeout/abort.

        baa_plot/baa_table write files eagerly and flush the manifest only at
        the end, so a killed process skips that flush.  Re-reading the
        manifest plus sweeping discovered files is what preserves the partial
        output for the user and the repair loop instead of losing everything.
        """
        manifest, manifest_error = self._read_manifest(out_dir)
        manifest = self._augment_manifest_with_discovered_artifacts(
            manifest,
            out_dir,
            preexisting_files=preexisting_files,
        )
        recovered = sum(
            len(manifest.get(group) or [])
            for group in ("tables", "figures", "texts", "models", "artifacts")
        )
        warnings = manifest.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        warnings.append(
            f"部分结果回收：执行在完成前被终止，已保留磁盘上生成的 {recovered} 个产物。"
        )
        manifest["warnings"] = warnings
        error_text = f"{error}; {manifest_error}" if manifest_error else error
        payload = {
            **manifest,
            "ok": False,
            "partial_result": True,
            "stdout": stdout,
            "stderr": stderr,
            "error": error_text,
            "code": request.code,
            "reproducibility": self._read_runtime_record(out_dir, request),
        }
        try:
            return RRunResult.from_dict(payload)
        except ValueError:
            return RRunResult(
                ok=False,
                stdout=stdout,
                stderr=stderr,
                error=error_text,
                code=request.code,
            )

    @staticmethod
    def _read_manifest(out_dir: Path) -> tuple[dict[str, Any], str]:
        path = out_dir / "artifacts.json"
        if not path.is_file():
            return (
                LocalRscriptBackend._empty_manifest(warnings=["artifact manifest was not created"]),
                "",
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return (
                LocalRscriptBackend._empty_manifest(warnings=["artifact manifest could not be read"]),
                "artifact manifest could not be read",
            )
        if not isinstance(manifest, dict):
            return (
                LocalRscriptBackend._empty_manifest(warnings=["artifact manifest could not be read"]),
                "Invalid artifact manifest: expected JSON object",
            )
        return manifest, ""

    @staticmethod
    def _empty_manifest(warnings: list[str] | None = None) -> dict[str, list[Any]]:
        return {
            "tables": [],
            "figures": [],
            "texts": [],
            "models": [],
            "artifacts": [],
            "warnings": list(warnings or []),
        }

    @staticmethod
    def _snapshot_output_files(out_dir: Path) -> set[Path]:
        try:
            return {path.resolve(strict=False) for path in out_dir.iterdir() if path.is_file()}
        except OSError:
            return set()

    @staticmethod
    def _artifact_name_from_path(path: Path) -> str:
        name = re.sub(r"[^A-Za-z0-9_\-]+", "_", path.stem).strip("_")
        return name or "artifact"

    @staticmethod
    def _registered_artifact_paths(manifest: dict[str, Any]) -> set[Path]:
        paths: set[Path] = set()
        for group in ("tables", "figures", "texts", "models", "artifacts"):
            items = manifest.get(group) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not item.get("path"):
                    continue
                try:
                    paths.add(Path(str(item.get("path"))).resolve(strict=False))
                except OSError:
                    continue
        return paths

    @staticmethod
    def _registered_figure_stems(manifest: dict[str, Any]) -> set[Path]:
        stems: set[Path] = set()
        items = manifest.get("figures") or []
        if not isinstance(items, list):
            return stems
        for item in items:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            try:
                stems.add(Path(str(item.get("path"))).resolve(strict=False).with_suffix(""))
            except OSError:
                continue
        return stems

    @staticmethod
    def _append_discovered_artifact(
        manifest: dict[str, Any],
        group: str,
        path: Path,
        *,
        kind: str,
        mime_type: str,
    ) -> None:
        items = manifest.get(group)
        if not isinstance(items, list):
            items = []
            manifest[group] = items
        items.append(
            {
                "name": LocalRscriptBackend._artifact_name_from_path(path),
                "path": str(path.resolve(strict=False)),
                "kind": kind,
                "mime_type": mime_type,
            }
        )

    @staticmethod
    def _augment_manifest_with_discovered_artifacts(
        manifest: dict[str, Any],
        out_dir: Path,
        *,
        preexisting_files: set[Path],
    ) -> dict[str, Any]:
        warnings = manifest.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
            manifest["warnings"] = warnings

        registered_paths = LocalRscriptBackend._registered_artifact_paths(manifest)
        registered_figure_stems = LocalRscriptBackend._registered_figure_stems(manifest)
        table_types = {
            ".csv": "text/csv",
            ".tsv": "text/tab-separated-values",
        }
        figure_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".pdf": "application/pdf",
            ".svg": "image/svg+xml",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
        }
        text_types = {
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".log": "text/plain",
        }
        scientific_artifact_types = {
            ".rds": "application/x-r-rds",
            ".h5ad": "application/x-hdf5",
            ".h5seurat": "application/x-hdf5",
            ".h5": "application/x-hdf5",
            ".hdf5": "application/x-hdf5",
            ".loom": "application/x-hdf5",
            ".vcf": "text/vcf",
        }
        reserved = {"analysis.R", "artifacts.json", "runtime.json"}
        discovered = 0

        try:
            candidates = sorted(path for path in out_dir.iterdir() if path.is_file())
        except OSError:
            return manifest

        for path in candidates:
            resolved = path.resolve(strict=False)
            if path.name in reserved or resolved in preexisting_files or resolved in registered_paths:
                continue
            suffix = path.suffix.lower()
            if suffix in figure_types and resolved.with_suffix("") in registered_figure_stems:
                # baa_plot registers the browser-previewable PNG and also writes
                # PDF/TIFF companions. They are export formats of one logical
                # plot, not additional figures.
                continue
            if suffix in table_types:
                LocalRscriptBackend._append_discovered_artifact(
                    manifest,
                    "tables",
                    path,
                    kind="table",
                    mime_type=table_types[suffix],
                )
                discovered += 1
            elif suffix in figure_types:
                LocalRscriptBackend._append_discovered_artifact(
                    manifest,
                    "figures",
                    path,
                    kind="figure",
                    mime_type=figure_types[suffix],
                )
                discovered += 1
            elif suffix in text_types:
                LocalRscriptBackend._append_discovered_artifact(
                    manifest,
                    "texts",
                    path,
                    kind="text",
                    mime_type=text_types[suffix],
                )
                discovered += 1
            elif suffix in scientific_artifact_types:
                LocalRscriptBackend._append_discovered_artifact(
                    manifest,
                    "artifacts",
                    path,
                    kind="artifact",
                    mime_type=scientific_artifact_types[suffix],
                )
                discovered += 1
        if discovered:
            warnings.append(
                f"Discovered {discovered} output artifact(s) not registered through baa_* helpers."
            )
        return manifest

    @staticmethod
    def _r_string(value: object) -> str:
        # The script file itself is UTF-8. Keeping non-ASCII text literal avoids
        # jsonlite interpreting JSON ``\\u`` escapes through a non-UTF-8 native
        # locale before paths and variable labels reach R.
        return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _subprocess_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value
