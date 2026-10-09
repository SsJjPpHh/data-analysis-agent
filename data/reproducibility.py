"""Portable execution records included in the research delivery ZIP."""

from __future__ import annotations

import hashlib
import json
import zipfile

from infrastructure.resources import resource_text
from infrastructure.public_projection import public_projection
from r_runtime.replay_snapshot import unpack_replay_snapshot


def without_replay_snapshots(value):
    """Keep binary execution inputs out of editable browser state."""
    if isinstance(value, dict):
        return {key: without_replay_snapshots(child) for key, child in value.items() if key != "execution_snapshot"}
    if isinstance(value, list):
        return [without_replay_snapshots(child) for child in value]
    return value


REPLAY_R = r'''# Rscript replay.R step_001 /absolute/path/to/input_manifest.json
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2L) stop("Supply the step directory and an input manifest with your local asset paths.")
step_dir <- normalizePath(args[[1]], mustWork = TRUE)
input_file <- normalizePath(args[[2]], mustWork = TRUE)
script_file <- sub("^--file=", "", grep("^--file=", commandArgs(), value = TRUE)[[1]])
if (!requireNamespace("jsonlite", quietly = TRUE)) stop("Install jsonlite first.")
runtime <- jsonlite::fromJSON(file.path(step_dir, "runtime.json"), simplifyVector = FALSE)
helper_file <- file.path(step_dir, "baa_helpers.R")
if (!file.exists(helper_file)) stop("Exact execution helper is unavailable for this historical run.")
if (!identical(runtime$snapshot_status, "complete")) stop("Execution input snapshot is incomplete; restore missing inputs before replay.")
checksums <- jsonlite::fromJSON(file.path(step_dir, "snapshot_checksums.json"))
for (name in names(checksums)) {
  file <- file.path(step_dir, name)
  if (!file.exists(file) || unname(tools::md5sum(file)) != checksums[[name]]) stop(paste("Replay input changed:", name))
}
Sys.setenv(BAA_CHECKPOINT_DIR = file.path(step_dir, "checkpoints"))
output_dir <- file.path(step_dir, "replay_output")
dir.create(output_dir, showWarnings = FALSE)
Sys.setenv(BAA_OUTPUT_DIR = output_dir)
setwd(output_dir)
options(encoding = "UTF-8", baa.dataset_load_profile = "analysis")
options(mc.cores = as.integer(if (is.null(runtime$workers)) 1L else runtime$workers))
source(helper_file)
for (package in names(runtime$packages)) {
  if (!requireNamespace(package, quietly = TRUE)) stop(paste("Required package is missing:", package))
  if (as.character(utils::packageVersion(package)) != runtime$packages[[package]]) {
    warning(paste("Package version differs:", package, "recorded", runtime$packages[[package]]))
  }
}
for (package in c("readr", "readxl", "haven", "dplyr", "ggplot2")) {
  if (requireNamespace(package, quietly = TRUE)) suppressPackageStartupMessages(base::attachNamespace(getNamespace(package)))
}
dataset_manifest <- jsonlite::fromJSON(input_file, simplifyVector = FALSE)
dataset_bundle <- baa_read_scientific_bundle(dataset_manifest)
dataset <- baa_bundle_dataset(dataset_bundle)
dataset_assets <- dataset_bundle$assets
if (!is.null(runtime$rng_kind)) do.call(RNGkind, as.list(unlist(runtime$rng_kind)))
if (!is.null(runtime$rng_before)) {
  assign(".Random.seed", as.integer(unlist(runtime$rng_before)), envir = .GlobalEnv)
} else if (!is.null(runtime$seed)) {
  set.seed(as.integer(runtime$seed))
} else stop("This historical execution has no recorded RNG state; inspect its code before replaying.")
source(file.path(step_dir, "analysis.R"), local = .GlobalEnv)
baa_flush_artifacts()
'''


def write_reproducibility_bundle(archive: zipfile.ZipFile, state: dict) -> None:
    records = []
    total_bytes = 0
    helper_code = resource_text("r_runtime/helpers/baa_helpers.R")
    helper_sha256 = hashlib.sha256(helper_code.encode("utf-8")).hexdigest()

    def write(name: str, content: str | bytes) -> None:
        nonlocal total_bytes
        total_bytes += len(content.encode("utf-8") if isinstance(content, str) else content)
        if total_bytes > 32 * 1024 * 1024:
            raise OverflowError("复现记录超过 32 MiB，请减少本次交付包包含的分析轮次。")
        archive.writestr("reproducibility/" + name, content)

    for turn in state.get("analysis_turns", []):
        for execution in turn.get("execution_refs", []):
            if not execution.get("code"):
                continue
            index = len(records) + 1
            directory = f"step_{index:03d}"
            code = str(execution["code"])
            raw_runtime = dict(execution.get("reproducibility") or {"status": "not_recorded"})
            snapshot = raw_runtime.pop("execution_snapshot", {})
            snapshot_files = unpack_replay_snapshot(snapshot)
            runtime = public_projection(
                raw_runtime,
                drop_keys={"path", "stored_path", "source_path", "file_path"},
            )
            input_manifest = dict(runtime.pop("dataset_manifest", {}) or {})
            # Historical records can reuse the current helper only if its
            # fingerprint proves it is byte-identical. Missing checkpoints
            # remain unrecorded rather than being reconstructed from new state.
            if not snapshot and runtime.get("helper_sha256") == helper_sha256:
                snapshot_files["baa_helpers.R"] = helper_code.encode("utf-8")
            runtime["snapshot_status"] = snapshot.get("status", "not_recorded")
            runtime["snapshot_omissions"] = snapshot.get("omissions", [])
            exact_helper = snapshot_files.get("baa_helpers.R", b"")
            runtime["exported_helper_sha256"] = hashlib.sha256(exact_helper).hexdigest() if exact_helper else None
            runtime["helper_matches_execution"] = bool(exact_helper) and runtime.get("helper_sha256") == runtime["exported_helper_sha256"]
            if not runtime["helper_matches_execution"]:
                runtime["snapshot_status"] = "partial"
            checksums = {"analysis.R": hashlib.md5(code.encode("utf-8")).hexdigest()}
            for name, raw in snapshot_files.items():
                write(directory + "/" + name, raw)
                checksums[name] = hashlib.md5(raw).hexdigest()
            write(directory + "/snapshot_checksums.json", json.dumps(checksums, indent=2))
            # Preserve format, roles and recorded identity fingerprints. Paths
            # are explicitly supplied by the researcher on another machine.
            input_manifest["path"] = "REPLACE_WITH_LOCAL_PRIMARY_PATH"
            for asset in input_manifest.get("assets", []):
                asset["path"] = "REPLACE_WITH_LOCAL_ASSET_PATH"
            write(directory + "/analysis.R", code)
            write(directory + "/runtime.json", json.dumps(runtime, ensure_ascii=False, indent=2))
            write(directory + "/input_manifest.example.json", json.dumps(input_manifest, ensure_ascii=False, indent=2))
            write(directory + "/analysis_plan.json", json.dumps(
                public_projection(execution.get("analysis_plan") or {}, drop_keys={"path"}),
                ensure_ascii=False, indent=2,
            ))
            records.append({
                "directory": directory, "turn_id": turn.get("turn_id"),
                "job_id": execution.get("job_id"), "status": execution.get("status"),
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "runtime_status": runtime.get("status", "not_recorded"),
                "diagnostics": execution.get("diagnostic_status", {}),
            })
    write("executions.json", json.dumps(records, ensure_ascii=False, indent=2))
    write("baa_helpers.R", helper_code)
    write("replay.R", REPLAY_R)
    write("README.md", (
        "# 分析复现记录\n\n每个 step 目录保存实际执行代码、冻结计划、运行环境和输入资产说明。"
        "runtime.json 中的 RNG 状态记录于用户分析代码执行前；代码内显式 set.seed() 仍生效。"
        "历史执行缺少的信息标记为 not_recorded，不回填推测版本。\n\n"
        "原始输入文件未自动包含；checkpoint 可能含研究数据，请按研究数据权限保管交付包。复制 input_manifest.example.json 并填写对应本地资产路径，"
        "保持格式、角色和文件内容一致；fingerprint_sha256 是原运行的资产指纹，不能当作全文件 SHA-256。"
        "代码可能包含研究者指定的本地路径，跨机器时请核对。\n\n"
        "在 reproducibility 目录执行：`Rscript replay.R step_001 /absolute/path/to/input_manifest.json`。"
        "先安装记录的 R 与 R 包版本；回放不会自动安装依赖。多个步骤须按顺序回放。"
        "新执行逐步保存原 helper 和执行前挂载的 checkpoint（每步上限 16 MiB）；超限或历史缺失明确标记，回放会停止。外部文件需自行恢复。"
        "环境、外部资源或并行算法的差异仍可能影响结果。\n"
    ))
