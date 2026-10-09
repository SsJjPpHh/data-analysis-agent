from __future__ import annotations

import re
from typing import Any

from r_runtime.package_policy import package_setup_requirements


_COMMON_FUNCTION_PACKAGES = {
    "Surv": "survival",
    "coxph": "survival",
    "survfit": "survival",
    "cox.zph": "survival",
    "ggsurvplot": "survminer",
    "roc": "pROC",
    "auc": "pROC",
    "ci.auc": "pROC",
    "glmnet": "glmnet",
    "cv.glmnet": "glmnet",
    "randomForest": "randomForest",
    "xgboost": "xgboost",
    "lmer": "lme4",
    "glmer": "lme4",
    "gam": "mgcv",
    "mice": "mice",
    "matchit": "MatchIt",
    "bal.tab": "cobalt",
    "svydesign": "survey",
    "svyglm": "survey",
    "metagen": "meta",
    "metabin": "meta",
    "forest": "meta",
    "rma": "metafor",
    "sem": "lavaan",
    "cfa": "lavaan",
    "alpha": "psych",
    "fa": "psych",
    "CreateTableOne": "tableone",
    "tbl_summary": "gtsummary",
    "tbl_regression": "gtsummary",
    "deseq": "DESeq2",
    "DESeq": "DESeq2",
    "DGEList": "edgeR",
    "voom": "limma",
    "lmFit": "limma",
    "FindMarkers": "Seurat",
    "RunUMAP": "Seurat",
    "FindClusters": "Seurat",
    "phyloseq": "phyloseq",
    "estimate_richness": "phyloseq",
    "adonis2": "vegan",
    "metaMDS": "vegan",
    "st_as_sf": "sf",
    "rast": "terra",
    "tm_shape": "tmap",
    "tokens": "quanteda",
    "dfm": "quanteda",
    "graph_from_data_frame": "igraph",
    "ggraph": "ggraph",
}


def diagnose_r_failure(
    *,
    error: str = "",
    stderr: str = "",
    stdout: str = "",
    code: str = "",
) -> dict[str, Any]:
    """Classify common R execution failures and return model-facing repair hints."""
    text = "\n".join(str(item or "") for item in (error, stderr, stdout))
    lowered = text.lower()
    code_text = str(code or "")

    category = "r_execution_error"
    severity = "error"
    facts: list[str] = []
    hints: list[str] = [
        "Read stderr and fix the R code before retrying.",
        "Keep all primary outputs persisted with baa_table(), baa_plot(), baa_text(), or baa_model_summary().",
    ]
    next_actions: list[str] = []
    recommended_tool_calls: list[dict[str, Any]] = []
    minimal_probe_code: list[str] = []
    retry_strategy: list[str] = []
    detected: dict[str, Any] = {
        "missing_package": "",
        "missing_function": "",
        "missing_object": "",
        "namespace_package": "",
        "namespace_function": "",
        "external_dependency_id": "",
        "missing_header": "",
        "code_namespace_calls": _namespace_calls(code_text),
        "code_function_calls": _called_functions(code_text),
    }

    def add_fact(value: str) -> None:
        if value and value not in facts:
            facts.append(value)

    def add_hint(value: str) -> None:
        if value and value not in hints:
            hints.append(value)

    def add_action(value: str) -> None:
        if value and value not in next_actions:
            next_actions.append(value)

    def add_tool_call(tool: str, arguments: dict[str, Any], reason: str) -> None:
        call = {"tool": tool, "arguments": arguments, "reason": reason}
        if call not in recommended_tool_calls:
            recommended_tool_calls.append(call)

    def add_probe(value: str) -> None:
        if value and value not in minimal_probe_code:
            minimal_probe_code.append(value)

    def add_retry(value: str) -> None:
        if value and value not in retry_strategy:
            retry_strategy.append(value)

    package_missing = re.search(r"there is no package called ['\"]([^'\"]+)['\"]", text, re.I)
    function_missing = re.search(r"could not find function ['\"]([^'\"]+)['\"]", text, re.I)
    object_missing = re.search(r"object ['\"]([^'\"]+)['\"] not found", text, re.I)
    namespace_export_missing = re.search(
        r"['\"]([^'\"]+)['\"] is not an exported object from ['\"]namespace:([^'\"]+)['\"]",
        text,
        re.I,
    )
    column_missing = (
        re.search(r"undefined columns selected", text, re.I)
        or re.search(r"can't subset columns that don't exist", text, re.I)
        or re.search(r"column[s]? .* doesn't exist", text, re.I)
    )

    if "timed out" in lowered or "timeout" in lowered:
        category = "timeout"
        add_fact("R execution exceeded the configured timeout.")
        add_hint(
            "Reduce workload, sample for exploration, simplify models, or increase timeout_seconds only when the analysis is expected to be long-running."
        )
        add_action(
            "Retry with a smaller/interpretable analysis first; save intermediate tables before expensive steps."
        )
        add_retry(
            "Reduce the analysis scope first, then increase timeout only when the reduced code path is correct."
        )

    if package_missing:
        category = "missing_package"
        package = package_missing.group(1)
        detected["missing_package"] = package
        add_fact(f"Package not available at runtime: {package}")
        add_hint("Call get_r_package_inventory to confirm alternatives in the active local R installation.")
        add_hint(
            "If the package is not installed, allow the local runtime to install it or choose an installed alternative."
        )
        for requirement in package_setup_requirements((package,)):
            add_hint(requirement)
        add_action(f"Search package inventory for alternatives to {package}.")
        add_tool_call(
            "get_r_package_inventory",
            {"query": package, "include_all": False, "limit": 80},
            "Confirm whether the missing package or a suitable alternative exists in the local R installation.",
        )
        add_retry(
            "Replace the missing package with an installed alternative or simplify the method before rerunning."
        )

    if function_missing:
        category = "missing_function"
        function = function_missing.group(1)
        detected["missing_function"] = function
        add_fact(f"R function not found: {function}")
        add_hint(
            "The package may not be loaded, the function may not be exported, or the function name may be wrong."
        )
        add_hint("Use package::function syntax after checking requireNamespace('package', quietly = TRUE).")
        likely_package = _COMMON_FUNCTION_PACKAGES.get(function)
        if likely_package:
            add_fact(f"Likely package candidate for {function}: {likely_package}")
            add_tool_call(
                "get_r_package_help",
                {"package": likely_package, "query": function, "include_docs": True, "doc_limit": 2},
                "Inspect the likely package docs and use package::function syntax.",
            )
            add_action(
                f"Call get_r_package_help(package='{likely_package}', query='{function}') before retrying."
            )
        else:
            add_tool_call(
                "get_r_package_help",
                {"query": function, "include_docs": True, "doc_limit": 2},
                "Search the installed R help index to identify the package/function before retrying.",
            )
            add_action(
                f"Call get_r_package_help(query='{function}') without a package to search installed docs before retrying."
            )
        add_retry("Load or namespace the correct package, then rerun the smallest corrected code path.")

    if object_missing:
        category = "missing_object"
        name = object_missing.group(1)
        detected["missing_object"] = name
        add_fact(f"Object not found: {name}")
        if name != "dataset":
            add_hint(
                'Check whether this is a dataset column. Use names(dataset) or inspect_dataset output and reference columns with dataset[["column"]] when needed.'
            )
        else:
            add_hint("The uploaded data frame should be named dataset; do not overwrite or remove it.")
        add_action("Inspect exact variable names and update the code to use existing objects/columns.")
        add_probe(
            'baa_table("available_columns", data.frame(column = names(dataset), class = vapply(dataset, function(x) paste(class(x), collapse = "/"), character(1))))'
        )
        add_retry(
            'Use dataset[["exact_column_name"]] for data columns and avoid bare symbols unless they are objects created in the code.'
        )

    if column_missing:
        category = "missing_column"
        add_fact("The code selected one or more columns that do not exist.")
        add_hint(
            'Use exact column names from inspect_dataset or names(dataset); preserve spaces/case with dataset[["column name"]].'
        )
        add_action(
            "Run a small names(dataset) / glimpse-style check or rely on the existing inspection output before retrying."
        )
        add_probe(
            'baa_table("available_columns", data.frame(column = names(dataset), class = vapply(dataset, function(x) paste(class(x), collapse = "/"), character(1))))'
        )
        add_retry("Replace misspelled/renamed columns with exact uploaded dataset names before rerunning.")

    if "non-numeric argument" in lowered or "requires numeric" in lowered or "must be numeric" in lowered:
        category = "type_mismatch"
        add_fact("A numeric method received non-numeric data.")
        add_hint(
            "Convert variables deliberately with as.numeric only after checking labels/coding; for categorical values, recode factors instead of forcing numeric blindly."
        )
        add_action("Inspect variable classes and recode/convert only the variables used by the model.")
        add_probe(
            'baa_table("variable_type_check", data.frame(column = names(dataset), class = vapply(dataset, function(x) paste(class(x), collapse = "/"), character(1)), example = ""))'
        )
        add_retry(
            "Recode labelled/categorical variables explicitly, then rerun the model with checked variable classes."
        )

    if "contrasts can be applied only to factors with 2 or more levels" in lowered:
        category = "factor_level_error"
        add_fact(
            "A factor/categorical predictor has fewer than two usable levels after filtering/missing removal."
        )
        add_hint(
            "Check group counts after filtering. Drop single-level predictors, combine sparse levels, or ask the user if the variable role is unclear."
        )
        add_action("Create a level-count table before retrying the model.")
        add_probe(
            'baa_table("factor_level_check", data.frame(column = names(dataset), n_unique = vapply(dataset, function(x) length(unique(stats::na.omit(x))), integer(1)), missing = vapply(dataset, function(x) sum(is.na(x)), integer(1))))'
        )
        add_retry("Drop or recode predictors that have fewer than two usable levels after filtering.")

    if "complete separation" in lowered or "fitted probabilities numerically 0 or 1 occurred" in lowered:
        category = "model_instability"
        add_fact("The model may have separation or instability.")
        add_hint(
            "Report the instability, inspect sparse categories, and consider penalized logistic regression or simpler predictors."
        )
        add_action("Save a diagnostic table of outcome by predictor levels before selecting a remedy.")
        add_retry(
            "Prefer simpler predictors or penalized/robust alternatives, and explicitly report the instability."
        )

    if "singular" in lowered or "rank deficient" in lowered:
        category = "model_singularity"
        add_fact("The model design matrix appears singular or rank deficient.")
        add_hint(
            "Check collinearity, constants, duplicated variables, and overparameterization relative to sample size."
        )
        add_action("Simplify the model or remove collinear/constant predictors before retrying.")
        add_retry(
            "Remove constant or collinear terms, then rerun a simpler model before adding complexity back."
        )

    if "cannot allocate vector" in lowered or "memory exhausted" in lowered:
        category = "memory_limit"
        add_fact("R ran into a memory limit.")
        add_hint(
            "Use smaller intermediate objects, avoid dense matrix conversion, sample for exploration, or use sparse/domain-specific methods."
        )
        add_action("Retry with a smaller intermediate analysis and save artifacts incrementally.")
        add_retry("Avoid dense all-by-all objects and choose sparse or summarized computations.")

    if "unexpected many-to-many relationship" in lowered or "many-to-many relationship between" in lowered:
        category = "join_cardinality"
        add_fact("A dplyr join matched duplicate keys on both sides, creating a many-to-many merge.")
        add_hint(
            "For Table One/baseline tables, do not hand-build side-by-side joined summaries; use baa_table_one() as the primary output."
        )
        add_hint(
            "For legitimate joins, make keys unique first with summarise()/distinct(), or explicitly set relationship='many-to-many' only when row multiplication is intended."
        )
        add_action(
            "Check join keys and remove duplicated header/level rows before retrying the table construction."
        )
        add_retry(
            "Prefer a single baa_table_one() call for group-comparison descriptive tables; retry only custom joins after proving key uniqueness."
        )

    if "permission denied" in lowered or "cannot open file" in lowered:
        category = "file_path_error"
        add_fact("The code attempted an invalid or inaccessible file operation.")
        add_hint(
            "Each run_r_code call uses an isolated artifact directory; do not read previous saved artifact filenames or write primary outputs with manual paths."
        )
        add_hint(
            "Use baa_table(), baa_plot(), baa_text(), and baa_model_summary(), or recompute needed values from `dataset` in the same R call."
        )
        add_action("Remove manual file reads/writes and rerun with platform artifact helpers.")
        add_retry("Replace manual file paths with dataset-based computation plus baa_* artifact helpers.")

    if namespace_export_missing:
        category = "api_mismatch"
        function = namespace_export_missing.group(1)
        package = namespace_export_missing.group(2)
        detected["namespace_package"] = package
        detected["namespace_function"] = function
        add_fact(f"Function is not exported by package namespace: {package}::{function}")
        add_hint("The package may use a different exported function name or the function may be internal.")
        add_tool_call(
            "get_r_package_help",
            {"package": package, "query": function, "include_docs": True, "doc_limit": 2},
            "Inspect the installed package exports and help topics before selecting a public API.",
        )
        add_action(
            f"Call get_r_package_help(package='{package}', query='{function}') and switch to an exported function."
        )
        add_retry("Use only exported functions or documented wrappers in generated analysis code.")

    if "unused argument" in lowered:
        category = "api_mismatch"
        add_fact("A function was called with an argument it does not accept.")
        add_hint(
            "Call get_r_package_help for the selected package/function and adjust arguments to the installed package version."
        )
        namespace_calls = detected["code_namespace_calls"]
        if namespace_calls:
            package = namespace_calls[0]["package"]
            function = namespace_calls[0]["function"]
            add_tool_call(
                "get_r_package_help",
                {"package": package, "query": function, "include_docs": True, "doc_limit": 2},
                "Check the installed function signature and supported arguments.",
            )
            add_action(f"Look up {package}::{function} help and retry with supported arguments.")
        elif detected["code_function_calls"]:
            function = detected["code_function_calls"][0]
            add_tool_call(
                "get_r_package_help",
                {"query": function, "include_docs": True, "doc_limit": 2},
                "Find the package/function documentation for the call with an unsupported argument.",
            )
            add_action(f"Search installed docs for function '{function}' and retry with supported arguments.")
        else:
            add_action("Look up the function help and retry with supported arguments.")
        add_retry(
            "Remove unsupported arguments or switch to the documented argument names for the installed package version."
        )

    external_dependency_id = ""
    external_detail = ""
    if any(
        marker in lowered
        for marker in (
            "unable to locate a java runtime",
            "java_home cannot be determined",
            "java_home is not set",
            "jni_createjavavm",
            "libjvm",
        )
    ):
        external_dependency_id = "java"
        external_detail = "Java runtime could not be started by the R package."
    elif any(
        marker in lowered
        for marker in (
            "no cmdstan installation found",
            "cmdstan path has not been set",
            "cmdstanr::install_cmdstan",
        )
    ):
        external_dependency_id = "cmdstan"
        external_detail = "CmdStan is not installed or is not configured for cmdstanr."
    elif any(
        marker in lowered
        for marker in (
            "valid installation of tensorflow not found",
            "no module named 'tensorflow'",
            'no module named "tensorflow"',
            "python shared library not found",
            "could not find a python environment",
        )
    ):
        external_dependency_id = "python_ml"
        external_detail = "The R interface cannot start its required Python/framework runtime."
    elif any(
        marker in lowered
        for marker in (
            "gfortran: command not found",
            "no fortran compiler found",
            "fortran compiler cannot create executables",
        )
    ):
        external_dependency_id = "fortran_toolchain"
        external_detail = "A working Fortran compiler is required."
    elif any(
        marker in lowered
        for marker in (
            "c++ compiler cannot create executables",
            "c compiler cannot create executables",
            "make: command not found",
            "xcode license",
            "xcrun: error",
            "rtools is required",
        )
    ):
        external_dependency_id = "c_cpp_toolchain"
        external_detail = "A working C/C++ compiler and make toolchain is required."

    missing_header = re.search(
        r"fatal error:\s*['<]([^'>]+)[>']\s*file not found",
        text,
        re.I,
    )
    if missing_header:
        detected["missing_header"] = missing_header.group(1)
        if not external_dependency_id:
            external_dependency_id = "system_library"
            external_detail = f"Required system header is missing: {missing_header.group(1)}"

    if external_dependency_id:
        category = "external_dependency"
        detected["external_dependency_id"] = external_dependency_id
        add_fact(external_detail)
        add_hint(
            "Use get_r_package_inventory for the same method/package and inspect external_dependency_readiness setup_action and verification_action."
        )
        add_action("Install or configure the reported host dependency, recheck readiness, then retry the same method.")
        add_tool_call(
            "get_r_package_inventory",
            {"query": detected.get("missing_package") or external_dependency_id, "include_all": False, "limit": 80},
            "Resolve the concrete host dependency and its verification route before retrying R code.",
        )
        add_retry("Do not switch statistical methods merely to hide a missing host dependency.")

    if any(
        marker in lowered
        for marker in (
            "namespace load failed",
            "unable to load shared object",
            "symbol not found",
            "built against a different rcpp version",
            "was built under r version",
        )
    ):
        category = "package_binary_incompatibility"
        add_fact("The installed package or one of its compiled dependencies is not binary-compatible with the active R runtime.")
        add_hint(
            "Reinstall the affected package and compiled dependencies with the active R version and architecture; then re-run package readiness."
        )
        add_action("Check package_runtime_readiness warnings/errors for the selected package before analysis.")
        add_retry("Retry only after the namespace loads without ABI or shared-library errors.")

    if not retry_strategy:
        add_retry("Apply the smallest code correction that directly addresses the categorized error.")

    add_action("Run corrected code with run_r_code after applying the smallest clear fix.")

    return {
        "category": category,
        "severity": severity,
        "detected": detected,
        "facts": facts,
        "repair_hints": hints,
        "next_actions": next_actions,
        "recommended_tool_calls": recommended_tool_calls,
        "minimal_probe_code": minimal_probe_code,
        "retry_strategy": retry_strategy,
        "stderr_excerpt": _excerpt(stderr or text, tail=True),
        "code_excerpt": _excerpt(code_text, limit=1600),
    }


def _excerpt(text: str, limit: int = 2000, *, tail: bool = False) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    if tail:
        # R prints the decisive error at the end of its output.
        return "...[truncated]\n" + value[-limit:]
    return value[:limit] + "\n...[truncated]"


def _namespace_calls(code: str) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    for package, function in re.findall(
        r"\b([A-Za-z][A-Za-z0-9.]*):::{0,1}([A-Za-z.][A-Za-z0-9._]*)\b",
        str(code or ""),
    ):
        item = {"package": package, "function": function}
        if item not in calls:
            calls.append(item)
    return calls[:20]


def _called_functions(code: str) -> list[str]:
    ignored = {
        "if",
        "for",
        "while",
        "function",
        "return",
        "library",
        "require",
        "suppressPackageStartupMessages",
        "try",
        "tryCatch",
        "withCallingHandlers",
        "data.frame",
        "list",
        "c",
    }
    names: list[str] = []
    scrubbed = re.sub(r"(['\"])(?:\\.|(?!\1).)*\1", "", str(code or ""))
    for name in re.findall(r"(?<![:$@])\b([A-Za-z.][A-Za-z0-9._]*)\s*\(", scrubbed):
        if name in ignored or name.startswith("."):
            continue
        if name not in names:
            names.append(name)
    return names[:30]
