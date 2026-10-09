"""R package inventory and help tools for the research workbench."""

from __future__ import annotations

import json
import re
from typing import Any

from agent.r_capability_catalog import search_capabilities
from agent.r_package_inventory import load_r_package_inventory
from r_runtime.external_dependencies import inspect_external_dependencies, package_dependency_ids


class RPackageToolsMixin:
    _PACKAGE_QUERY_ALIASES = {
        "生存": "survival kaplan meier cox hazard competing risk prognosis",
        "预后": "prognosis survival cox nomogram calibration decision curve",
        "诊断": "diagnostic roc auc sensitivity specificity calibration",
        "预测": "prediction machine learning classification regression calibration",
        "机器学习": "machine learning feature importance shap cross-validation",
        "深度学习": "deep learning neural network tensorflow keras3 torch",
        "神经网络": "neural network deep learning tensorflow keras3 torch",
        "因果": "causal treatment effect propensity matching weighting mediation",
        "倾向": "propensity matching weighting balance cobalt matchit weightit",
        "流行": "epidemiology incidence prevalence outbreak public health",
        "公共卫生": "public health epidemiology surveillance incidence prevalence",
        "问卷": "questionnaire psychometrics reliability factor analysis sem irt",
        "量表": "scale reliability cronbach factor analysis cfa sem irt",
        "社会科学": "social science survey questionnaire psychometrics mediation moderation",
        "时间序列": "time series forecast trend seasonality change point",
        "空间": "spatial gis map raster coordinates environmental exposure",
        "环境": "environmental exposure distributed lag spatial public health",
        "文本": "text mining corpus topic model sentiment keyword",
        "网络": "network graph centrality community social network",
        "转录组": "rna-seq transcriptomics differential expression volcano heatmap",
        "差异表达": "differential expression deseq2 edger limma volcano heatmap",
        "富集": "enrichment go kegg reactome gsea pathway annotation",
        "通路": "pathway enrichment go kegg reactome gsea",
        "单细胞": "single-cell scrna clustering umap marker pseudotime cell type",
        "基因组": "genomics variant chip-seq methylation genomic ranges",
        "甲基化": "methylation epigenomics minfi dmr methylkit",
        "微生物": "microbiome 16s diversity ordination differential abundance",
        "蛋白": "proteomics mass spectrometry protein differential abundance",
        "多组学": "multi-omics integration mixomics multiassayexperiment",
        "影像": "medical imaging nifti image ebimage rnifti imager",
        "缺失": "missing imputation multiple imputation sensitivity",
        "插补": "imputation missing data mice missforest",
        "meta": "meta-analysis forest plot network meta-analysis",
        "样本量": "sample size power clinical trial",
        "临床试验": "clinical trial power sample size interim equivalence",
    }
    @classmethod
    def _expanded_package_query(cls, query: str) -> str:
        text = (query or "").strip()
        additions = [
            alias for needle, alias in cls._PACKAGE_QUERY_ALIASES.items() if needle.lower() in text.lower()
        ]
        return " ".join(item for item in [text, *additions] if item).strip()

    @staticmethod
    def _package_record_name(record: Any) -> str:
        if isinstance(record, dict):
            return str(record.get("package") or record.get("Package") or "")
        return str(record or "")

    @staticmethod
    def _match_package_records(
        records: list[dict[str, Any]],
        query: str,
        limit: int,
    ) -> list[dict[str, str]]:
        terms = list(
            dict.fromkeys(
                term.lower() for term in re.split(r"[\s,;/|]+", query or "") if len(term.strip()) >= 2
            )
        )
        ranked: list[tuple[int, int, dict[str, str]]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            name = str(record.get("package") or record.get("Package") or "")
            if not name:
                continue
            name_lower = name.lower()
            title = str(record.get("title") or record.get("Title") or "")
            description = str(record.get("description") or record.get("Description") or "")
            haystack = f"{name_lower} {title.lower()} {description.lower()}"
            score = 0
            for term in terms:
                if name_lower == term:
                    score += 10
                elif name_lower.startswith(term):
                    score += 8
                elif term in name_lower:
                    score += 5
                elif term in haystack:
                    score += 1
            if not terms:
                score = 1
            if score <= 0:
                continue
            ranked.append(
                (
                    score,
                    len(name),
                    {
                        "package": name,
                        "version": str(record.get("version") or record.get("Version") or ""),
                        "title": title,
                    },
                )
            )
        ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]["package"].lower()))
        return [entry for _, _, entry in ranked[:limit]]

    def _tool_get_r_package_inventory(
        self,
        query: str = "",
        include_all: bool = False,
        limit: int | str = 80,
    ) -> str:
        try:
            safe_limit = min(300, max(10, int(limit)))
        except (TypeError, ValueError, OverflowError):
            safe_limit = 80

        inventory = load_r_package_inventory()
        expanded_query = self._expanded_package_query(query)
        catalog_capabilities = search_capabilities(expanded_query, limit=20)
        catalog_packages: list[str] = []
        for capability in catalog_capabilities:
            for package in capability.get("packages", []):
                if not isinstance(package, str):
                    continue
                if package not in catalog_packages:
                    catalog_packages.append(package)

        backend = self._local_r_backend()
        live_payload = backend.list_packages(timeout_seconds=30)
        live_ok = bool(live_payload.get("ok"))
        raw_records = live_payload.get("packages") if isinstance(live_payload, dict) else []
        live_records = [record for record in raw_records if isinstance(record, dict)]
        live_names = sorted(
            {
                self._package_record_name(record)
                for record in live_records
                if self._package_record_name(record)
            }
        )
        live_name_set = set(live_names)
        capabilities = catalog_capabilities
        if live_ok:
            capabilities = [
                {
                    **capability,
                    "packages": [
                        package
                        for package in capability.get("packages", [])
                        if isinstance(package, str) and package in live_name_set
                    ],
                }
                for capability in catalog_capabilities
            ]
        capability_packages = [
            package
            for package in catalog_packages
            if not live_ok or package in live_name_set
        ]
        declared_matches = [
            package
            for package in inventory.installable
            if not expanded_query
            or any(
                term in package.lower()
                for term in re.split(r"[\s,;/|]+", expanded_query.lower())
                if len(term.strip()) >= 2
            )
        ][:safe_limit]
        installed_capability_packages = [
            package for package in capability_packages if package in live_name_set
        ][:safe_limit]

        matched_installed = self._match_package_records(
            live_records,
            expanded_query,
            safe_limit,
        )
        query_terms = {
            term.casefold()
            for term in re.split(r"[\s,;/|]+", expanded_query)
            if term.strip()
        }
        exact_installed = [
            str(item.get("package") or "")
            for item in matched_installed
            if str(item.get("package") or "").casefold() in query_terms
        ]
        readiness_packages: list[str] = []
        for package in (
            *exact_installed,
            *installed_capability_packages,
            *(str(item.get("package") or "") for item in matched_installed),
        ):
            if package and package not in readiness_packages:
                readiness_packages.append(package)
            if len(readiness_packages) >= 6:
                break
        readiness_check = getattr(backend, "package_readiness", None)
        package_readiness = (
            readiness_check(tuple(readiness_packages), timeout_seconds=30)
            if callable(readiness_check)
            else {
                "ok": False,
                "error": "R package readiness is unavailable for this runtime adapter.",
                "packages": [],
            }
        )
        loadable_packages = tuple(
            str(item.get("package") or "")
            for item in package_readiness.get("packages") or []
            if isinstance(item, dict) and item.get("status") == "ready"
        )
        satisfied_dependency_ids = tuple(
            str(item.get("runtime_check") or "")
            for item in package_readiness.get("packages") or []
            if isinstance(item, dict)
            and item.get("status") == "ready"
            and item.get("runtime_check") not in {None, "", "not_required"}
        )
        blocked_dependency_ids = tuple(
            dict.fromkeys(
                dependency_id
                for item in package_readiness.get("packages") or []
                if isinstance(item, dict)
                and item.get("status") in {"load_error", "needs_setup"}
                for dependency_id in package_dependency_ids((str(item.get("package") or ""),))
            )
        )
        external_packages = tuple(
            dict.fromkeys((*readiness_packages, *catalog_packages[:20]))
        )
        external_readiness = inspect_external_dependencies(
            packages=external_packages,
            intent=expanded_query,
            loadable_packages=loadable_packages,
            satisfied_dependency_ids=satisfied_dependency_ids,
            blocked_dependency_ids=blocked_dependency_ids,
            environment=getattr(backend, "runtime_env", None),
        )

        payload: dict[str, Any] = {
            "ok": True,
            "query": query,
            "expanded_query": expanded_query,
            "counts": {
                "declared_research_packages": len(inventory.installable),
                "github_fallback_packages": len(inventory.github_fallbacks),
                "live_installed_packages": int(live_payload.get("package_count") or len(live_names) or 0),
                "capability_groups": len(search_capabilities("", limit=1000)),
            },
            "runtime": {
                "live_inventory_ok": live_ok,
                "r_version": str(live_payload.get("r_version") or ""),
                "error": "" if live_ok else str(live_payload.get("error") or ""),
            },
            "matched_capabilities": capabilities,
            "recommended_candidate_packages": capability_packages[:safe_limit],
            "recommended_installed_packages": installed_capability_packages,
            "matched_installed_packages": matched_installed,
            "matched_declared_research_packages": declared_matches,
            "github_fallbacks": inventory.github_fallbacks,
            "package_runtime_readiness": package_readiness,
            "external_dependency_readiness": external_readiness,
            "usage_protocol": [
                "Use inspect_dataset first when variable roles or data shape are unknown.",
                "Select packages from matched_capabilities, matched_installed_packages, or the full installed package list.",
                "Before using a non-base package in run_r_code, check requireNamespace('pkg', quietly = TRUE).",
                "If the selected package is missing, run_r_code may install it into the library selected in System Settings.",
                "If package installation reports an external prerequisite, tell the user which compiler, Java/CUDA/CmdStan component, system library, or service is required.",
                "Treat package_runtime_readiness status=warning/load_error as actionable evidence; installed does not always mean loadable.",
                "Use external_dependency_readiness to distinguish ready, missing, and needs_setup host dependencies before writing expensive code.",
                "Persist primary outputs with baa_table(), baa_table_one(), baa_plot(), baa_text(), and baa_model_summary().",
            ],
        }
        if include_all:
            payload["all_installed_package_names"] = live_names
            payload["all_declared_research_package_names"] = list(inventory.installable)
        else:
            payload["installed_package_name_sample"] = live_names[:safe_limit]

        return json.dumps(payload, ensure_ascii=False)

    def _tool_get_r_package_help(
        self,
        package: str = "",
        packages: list[str] | None = None,
        query: str = "",
        limit: int | str = 30,
        include_docs: bool | str = True,
        doc_limit: int | str = 3,
        max_doc_chars: int | str = 4000,
    ) -> str:
        try:
            safe_limit = min(100, max(1, int(limit)))
        except (TypeError, ValueError, OverflowError):
            safe_limit = 30
        try:
            safe_doc_limit = min(8, max(0, int(doc_limit)))
        except (TypeError, ValueError, OverflowError):
            safe_doc_limit = 3
        try:
            safe_max_doc_chars = min(12000, max(1000, int(max_doc_chars)))
        except (TypeError, ValueError, OverflowError):
            safe_max_doc_chars = 4000
        include_docs_bool = self._bool_value(include_docs, default=True)

        requested: list[str] = []
        if package:
            requested.append(str(package))
        if isinstance(packages, list):
            requested.extend(str(item) for item in packages if str(item).strip())
        normalized: list[str] = []
        for item in requested:
            name = item.strip()
            if name and name not in normalized:
                normalized.append(name)
        normalized = normalized[:5]

        backend = self._local_r_backend()
        if not normalized:
            if not str(query or "").strip():
                return json.dumps(
                    {
                        "ok": False,
                        "error": "Provide at least one R package name or a search query.",
                        "packages": [],
                        "results": [],
                    },
                    ensure_ascii=False,
                )
            result = backend.package_help(
                package="",
                query=query,
                limit=safe_limit,
                include_docs=include_docs_bool,
                doc_limit=safe_doc_limit,
                max_doc_chars=safe_max_doc_chars,
                timeout_seconds=30,
            )
            payload = {
                "ok": bool(result.get("ok")),
                "mode": "cross_package_search",
                "query": query,
                "packages": [],
                "results": [],
                "cross_package_search": result,
                "usage_protocol": [
                    "This searched the installed R help index because no package name was provided.",
                    "Choose a candidate package from recommended_packages/search_results, then call get_r_package_help(package=..., query=...) for package-specific docs.",
                    "Generated analysis still must run through run_r_code and persist outputs with baa_* helpers.",
                    "Prefer package::function calls so the code is explicit about which package is used.",
                ],
            }
            return json.dumps(payload, ensure_ascii=False)

        results = [
            backend.package_help(
                package=name,
                query=query,
                limit=safe_limit,
                include_docs=include_docs_bool,
                doc_limit=safe_doc_limit,
                max_doc_chars=safe_max_doc_chars,
                timeout_seconds=30,
            )
            for name in normalized
        ]
        readiness_check = getattr(backend, "package_readiness", None)
        package_readiness = (
            readiness_check(tuple(normalized), timeout_seconds=45)
            if callable(readiness_check)
            else {
                "ok": False,
                "error": "R package readiness is unavailable for this runtime adapter.",
                "packages": [],
            }
        )
        loadable_packages = tuple(
            str(item.get("package") or "")
            for item in package_readiness.get("packages") or []
            if isinstance(item, dict) and item.get("status") == "ready"
        )
        satisfied_dependency_ids = tuple(
            str(item.get("runtime_check") or "")
            for item in package_readiness.get("packages") or []
            if isinstance(item, dict)
            and item.get("status") == "ready"
            and item.get("runtime_check") not in {None, "", "not_required"}
        )
        blocked_dependency_ids = tuple(
            dict.fromkeys(
                dependency_id
                for item in package_readiness.get("packages") or []
                if isinstance(item, dict)
                and item.get("status") in {"load_error", "needs_setup"}
                for dependency_id in package_dependency_ids((str(item.get("package") or ""),))
            )
        )
        payload = {
            "ok": all(bool(item.get("ok")) for item in results),
            "query": query,
            "packages": normalized,
            "results": results,
            "package_runtime_readiness": package_readiness,
            "external_dependency_readiness": inspect_external_dependencies(
                packages=tuple(normalized),
                intent=query,
                loadable_packages=loadable_packages,
                satisfied_dependency_ids=satisfied_dependency_ids,
                blocked_dependency_ids=blocked_dependency_ids,
                environment=getattr(backend, "runtime_env", None),
            ),
            "usage_protocol": [
                "Use these package docs to choose functions and syntax; this is not a per-package template.",
                "Generated analysis still must run through run_r_code and persist outputs with baa_* helpers.",
                "Prefer package::function calls so the code is explicit about which package is used.",
                "Resolve any package load warning/error or missing external dependency before expensive analysis.",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)
