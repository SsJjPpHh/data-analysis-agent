"""Deterministic dependency gate shared by templates and generated R code.

Template packages are alternatives: candidate mode needs one usable
implementation.  Generated code is authoritative: exact mode requires every
referenced/loader package.  The gate is read-only; package installation is a
separate, user-confirmed API action scoped by an opaque ticket.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

from r_runtime.external_dependencies import (
    inspect_external_dependencies,
    package_dependency_ids,
)
from r_runtime.factory import get_r_backend
from r_runtime.package_policy import PACKAGE_GITHUB_SOURCES, PACKAGE_REPOSITORIES


DependencyGateMode = Literal["candidate", "exact"]
_PACKAGE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9.]*")

# ── Repo catalog snapshot ─────────────────────────────────────────────────────
# One `available.packages()` lookup per TTL window: plans can freeze package
# ids that no repository hosts (LLM shorthand like "pk" for pharmacokinetics),
# and attempting to install those loops forever with cryptic build errors.
# The snapshot lets the gate tell "real but missing" from "not a package".
_REPO_SNAPSHOT_TTL_SECONDS = 1800.0
_repo_snapshot_cache: dict[str, Any] = {"at": float("-inf"), "names": None}


def _repo_catalog_snapshot(runtime) -> frozenset[str] | None:
    """Names installable from the configured repos, cached per process.

    Returns None when the snapshot is unavailable (offline, timeout, backend
    without the seam) so the gate keeps its previous fail-open behaviour and
    never blocks an analysis because the mirror lookup failed.
    """
    now = time.monotonic()
    if now - float(_repo_snapshot_cache["at"]) < _REPO_SNAPSHOT_TTL_SECONDS:
        return _repo_snapshot_cache["names"]
    names = None
    lookup = getattr(runtime, "available_package_names", None)
    if callable(lookup):
        try:
            fetched = lookup(timeout_seconds=90)
            names = frozenset(
                str(name) for name in (fetched or ()) if str(name).strip()
            ) or None
        except Exception:
            names = None
    if names is not None:
        _repo_snapshot_cache["at"] = now
        _repo_snapshot_cache["names"] = names
    else:
        # Retry failed lookups after a backoff instead of the full TTL —
        # but long enough that a slow mirror doesn't tax every preflight.
        _repo_snapshot_cache["at"] = now - _REPO_SNAPSHOT_TTL_SECONDS + 300.0
    return names


_ABI_WARNING_MARKERS = (
    "built against a different rcpp",
    "different rcpp version",
    "was built under r version",
    "unable to load shared object",
    "symbol not found",
    "abi",
)
_TICKET_TTL_SECONDS = 60 * 60
_MAX_TICKETS = 512


@dataclass(frozen=True)
class DependencyTicket:
    ticket_id: str
    session_id: str
    plan_id: str
    template_id: str
    mode: DependencyGateMode
    packages: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    intent: str
    created_at: float


_tickets: dict[str, DependencyTicket] = {}
_tickets_lock = threading.RLock()


def _normalized_packages(packages) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            package
            for value in packages
            if (package := str(value or "").strip()) and _PACKAGE_NAME.fullmatch(package)
        )
    )


def _prune_tickets(now: float) -> None:
    expired = [
        ticket_id
        for ticket_id, ticket in _tickets.items()
        if ticket.created_at < now - _TICKET_TTL_SECONDS
    ]
    for ticket_id in expired:
        _tickets.pop(ticket_id, None)
    if len(_tickets) <= _MAX_TICKETS:
        return
    ordered = sorted(_tickets.values(), key=lambda ticket: ticket.created_at)
    for ticket in ordered[: len(_tickets) - _MAX_TICKETS]:
        _tickets.pop(ticket.ticket_id, None)


def _issue_ticket(
    *,
    session_id: str,
    plan_id: str,
    template_id: str,
    mode: DependencyGateMode,
    packages: tuple[str, ...],
    dependency_ids: tuple[str, ...],
    intent: str,
) -> str:
    now = time.time()
    ticket = DependencyTicket(
        ticket_id=secrets.token_urlsafe(24),
        session_id=str(session_id or ""),
        plan_id=str(plan_id or ""),
        template_id=str(template_id or ""),
        mode=mode,
        packages=packages,
        dependency_ids=dependency_ids,
        intent=str(intent or "")[:2000],
        created_at=now,
    )
    with _tickets_lock:
        _prune_tickets(now)
        _tickets[ticket.ticket_id] = ticket
    return ticket.ticket_id


def _ticket(session_id: str, ticket_id: str) -> DependencyTicket:
    with _tickets_lock:
        _prune_tickets(time.time())
        ticket = _tickets.get(str(ticket_id or ""))
    if ticket is None or ticket.session_id != str(session_id or ""):
        raise ValueError("依赖处理请求不存在或已经过期，请重新检查分析依赖。")
    return ticket


def dependency_ticket(ticket_id: str, session_id: str) -> DependencyTicket:
    """Return one validated ticket for a user-confirmed mutation."""

    return _ticket(session_id, ticket_id)


def _is_abi_warning(item: dict[str, Any]) -> bool:
    text = " ".join(
        [
            *(str(value) for value in item.get("warnings") or []),
            str(item.get("error") or ""),
        ]
    ).casefold()
    return any(marker in text for marker in _ABI_WARNING_MARKERS)


def _runtime_dependency_evidence(package_items: list[dict[str, Any]]):
    loadable_packages = tuple(
        str(item.get("package") or "")
        for item in package_items
        if item.get("status") in {"ready", "warning"}
    )
    satisfied_dependency_ids = tuple(
        str(item.get("runtime_check") or "")
        for item in package_items
        if item.get("status") == "ready"
        and item.get("runtime_check") not in {None, "", "not_required"}
    )
    blocked_dependency_ids = tuple(
        dict.fromkeys(
            dependency_id
            for item in package_items
            if item.get("status") in {"load_error", "needs_setup"}
            for dependency_id in package_dependency_ids((str(item.get("package") or ""),))
        )
    )
    return loadable_packages, satisfied_dependency_ids, blocked_dependency_ids


def _selected_candidate_packages(
    requested: tuple[str, ...],
    items_by_package: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    for allowed_statuses in (
        ("ready",),
        ("warning",),
        ("missing",),
        ("needs_setup",),
        ("load_error",),
    ):
        selected = next(
            (
                package
                for package in requested
                if items_by_package.get(package, {}).get("status") in allowed_statuses
            ),
            "",
        )
        if selected:
            return (selected,)
    return requested[:1]


def inspect_dependency_gate(
    *,
    packages,
    intent: str = "",
    mode: DependencyGateMode = "exact",
    backend=None,
    session_id: str = "",
    plan_id: str = "",
    template_id: str = "",
    dependency_ids=(),
    issue_ticket: bool = True,
) -> dict[str, Any]:
    """Inspect the concrete dependency boundary without installing anything."""

    if mode not in {"candidate", "exact"}:
        raise ValueError("Invalid dependency gate mode")
    requested = _normalized_packages(packages)
    requested_dependency_ids = tuple(
        dict.fromkeys(str(value or "").strip() for value in dependency_ids if str(value or "").strip())
    )
    runtime = backend or get_r_backend()
    readiness_check = getattr(runtime, "package_readiness", None)
    readiness_supported = callable(readiness_check)
    if readiness_supported:
        try:
            readiness = readiness_check(
                requested,
                timeout_seconds=45,
                stop_after_usable=mode == "candidate",
            )
        except TypeError:
            # Third-party/test backends may still implement the older two-argument
            # readiness seam; retain compatibility while the local backend takes
            # the faster candidate path.
            readiness = readiness_check(requested, timeout_seconds=45)
    else:
        readiness = {
            "ok": True,
            "packages": [
                {
                    "package": package,
                    "installed": True,
                    "loadable": True,
                    "status": "ready",
                    "warnings": [],
                    "error": "",
                    "runtime_check": "not_required",
                    "runtime_detail": "",
                }
                for package in requested
            ],
        }
    raw_items = readiness.get("packages") if isinstance(readiness, dict) else []
    package_items = [dict(item) for item in raw_items or [] if isinstance(item, dict)]
    items_by_package = {
        str(item.get("package") or ""): item
        for item in package_items
        if str(item.get("package") or "")
    }
    for package in requested:
        items_by_package.setdefault(
            package,
            {
                "package": package,
                "installed": False,
                "loadable": False,
                "status": "missing",
                "warnings": [],
                "error": "Package readiness was not returned",
            },
        )
    selected = (
        _selected_candidate_packages(requested, items_by_package)
        if mode == "candidate"
        else requested
    )
    selected_items = [items_by_package[package] for package in selected]
    loadable, satisfied, blocked = _runtime_dependency_evidence(selected_items)
    external = inspect_external_dependencies(
        packages=selected,
        intent=intent if mode == "candidate" else "",
        dependency_ids=requested_dependency_ids,
        loadable_packages=loadable,
        satisfied_dependency_ids=satisfied,
        blocked_dependency_ids=blocked,
        environment=getattr(runtime, "runtime_env", None),
    )
    external_states = [
        dict(item) for item in external.get("states") or [] if isinstance(item, dict)
    ]
    pending_external = [item for item in external_states if item.get("status") != "ready"]
    external_ids_by_package = {
        package: set(package_dependency_ids((package,))) for package in selected
    }
    pending_external_ids = {
        str(item.get("dependency_id") or "") for item in pending_external
    }

    install_packages: list[str] = []
    repair_packages: list[str] = []
    warnings: list[dict[str, Any]] = []
    runtime_setup_packages: list[str] = []
    for package in selected:
        item = items_by_package[package]
        status = str(item.get("status") or "missing")
        if status == "missing":
            install_packages.append(package)
        elif status == "needs_setup":
            runtime_setup_packages.append(package)
        elif status == "load_error":
            if external_ids_by_package[package] & pending_external_ids:
                runtime_setup_packages.append(package)
            else:
                repair_packages.append(package)
        elif status == "warning":
            if _is_abi_warning(item):
                repair_packages.append(package)
            else:
                warnings.append(item)

    # ── Resolve install candidates against the configured repos ─────────
    # A frozen plan can carry a package id no repository hosts (LLM
    # shorthand like "pk" for pharmacokinetics).  Installing those loops
    # forever with cryptic build errors.  Exact repo names keep the normal
    # flow; case variants resolve under their canonical spelling; unknown
    # names are reported honestly and never reach the installer.
    unknown_packages: list[dict[str, Any]] = []
    # Only pay the mirror lookup when something actually needs installing —
    # a slow/unreachable mirror must not tax every preflight of a healthy
    # environment.
    repo_names = _repo_catalog_snapshot(runtime) if install_packages else None
    if repo_names is not None and install_packages:
        casefold_index: dict[str, str] = {}
        for name in repo_names:
            casefold_index.setdefault(name.casefold(), name)

        def _canonical_ready(canonical: str) -> bool:
            readiness_check = getattr(runtime, "package_readiness", None)
            if not callable(readiness_check):
                return False
            try:
                probe = readiness_check([canonical], timeout_seconds=30)
            except TypeError:
                try:
                    probe = readiness_check([canonical])
                except Exception:
                    return False
            except Exception:
                return False
            for probe_item in (probe.get("packages") if isinstance(probe, dict) else []) or []:
                if isinstance(probe_item, dict) and str(probe_item.get("package") or "") == canonical:
                    return bool(probe_item.get("loadable"))
            return False

        resolved: list[str] = []
        # Packages published outside CRAN/Bioconductor (GitHub-only or on
        # dedicated repositories) never appear in available.packages(); they
        # are known-installable and must not be flagged as unknown.
        extra_known_sources = set(PACKAGE_GITHUB_SOURCES) | set(PACKAGE_REPOSITORIES)
        for package in list(install_packages):
            item = items_by_package.get(package) or {"package": package}
            if package in repo_names or package in extra_known_sources:
                resolved.append(package)
                continue
            canonical = casefold_index.get(package.casefold())
            warnings_for_item = list(item.get("warnings") or [])
            if canonical is None:
                suggestions = sorted(
                    name
                    for name in repo_names
                    if name.casefold().startswith(package.casefold()) and len(package) >= 2
                )[:5]
                item["status"] = "unknown_package"
                item["installed"] = False
                item["loadable"] = False
                item["error"] = "包名在已配置的 CRAN/Bioconductor 镜像源上不存在；请使用确切的 CRAN 包名"
                item["suggestions"] = suggestions
                items_by_package[package] = item
                unknown_packages.append(item)
                continue
            warnings_for_item.append(
                f"请求的包名 {package} 在镜像源上不存在；已按实际包名 {canonical} 解析。"
            )
            item["resolved_as"] = canonical
            if mode == "candidate" and _canonical_ready(canonical):
                # Candidate mode packages are plan metadata, not code
                # references — an installed canonical spelling satisfies the
                # requirement (one usable implementation is present).
                item["status"] = "ready"
                item["installed"] = True
                item["loadable"] = True
                item["error"] = ""
                warnings_for_item.append(
                    f"请勿在 R 代码中引用 {package}：R 包名区分大小写，应使用 {canonical}。"
                )
            else:
                # Exact mode packages mirror literal code references: R's
                # library() is case-sensitive, so the run stays blocked until
                # the canonical name is installed (and the code corrected for
                # candidate repairs).
                if canonical not in resolved:
                    resolved.append(canonical)
                warnings_for_item.append(
                    f"R 代码中的 {package} 需改为 {canonical}（R 包名区分大小写）。"
                )
            item["warnings"] = warnings_for_item
            items_by_package[package] = item
        install_packages = resolved
        warnings.extend(
            {
                "package": str(item.get("package") or ""),
                "status": "resolved_as_canonical",
                "message": "；".join(str(w) for w in (item.get("warnings") or [])),
                "resolved_as": str(item.get("resolved_as") or ""),
            }
            for item in items_by_package.values()
            if item.get("resolved_as")
        )

    unsupported = [
        item
        for item in pending_external
        if item.get("dependency_id") == "cuda"
        and str(external.get("platform", {}).get("system") or "") == "darwin"
    ]
    if install_packages:
        status = "installable"
        headline = "需要安装 R 包"
        summary = "确认安装后会重新检测，并恢复当前分析计划。"
    elif unknown_packages:
        status = "package_names_unresolved"
        names_text = "、".join(
            str(item.get("package") or "") for item in unknown_packages[:6]
        )
        headline = "R 包名无法在镜像源解析"
        summary = (
            f"以下包名在已配置的 CRAN/Bioconductor 源上不存在：{names_text}。"
            "请改用确切的 CRAN 包名（注意大小写）后重新分析。"
        )
    elif unsupported:
        status = "unsupported"
        headline = "当前机器不支持所选加速路径"
        summary = "macOS 不支持 NVIDIA CUDA；请改用 CPU/兼容加速路径或换到 NVIDIA 机器。"
    elif pending_external or runtime_setup_packages:
        status = "needs_setup"
        headline = "需要配置本机科研组件"
        summary = "完成下列组件配置并重新检测后，可恢复当前分析计划。"
    elif repair_packages:
        status = "repair_required"
        headline = "R 包需要修复"
        summary = "当前包存在 namespace、共享库或 ABI 风险，修复并重新检测后再运行。"
    else:
        status = "ready"
        headline = "分析依赖已就绪"
        summary = "所选实现可以在当前本机 R 环境中运行。"

    ticket_id = ""
    if status != "ready" and issue_ticket and session_id:
        ticket_id = _issue_ticket(
            session_id=session_id,
            plan_id=plan_id,
            template_id=template_id,
            mode=mode,
            packages=requested,
            dependency_ids=requested_dependency_ids,
            intent=intent,
        )
    return {
        "schema_version": "1.0.0",
        "status": status,
        "headline": headline,
        "summary": summary,
        "mode": mode,
        "plan_id": str(plan_id or ""),
        "template_id": str(template_id or ""),
        "requested_packages": list(requested),
        "requested_external_dependencies": list(requested_dependency_ids),
        "selected_packages": list(selected),
        "install_packages": install_packages,
        "repair_packages": repair_packages,
        "runtime_setup_packages": runtime_setup_packages,
        "package_readiness": [items_by_package[package] for package in requested],
        "external_dependencies": external_states,
        "warnings": warnings,
        "ticket_id": ticket_id,
        "can_install": bool(install_packages),
        "can_recheck": status != "ready",
        "can_resume": status == "ready",
        "policy": {
            "installs_without_confirmation": False,
            "candidate_requires_all_packages": False,
            "operating_system_installation": False,
            "readiness_adapter_available": readiness_supported,
        },
    }


def check_dependency_ticket(session_id: str, ticket_id: str, *, backend=None) -> dict[str, Any]:
    ticket = _ticket(session_id, ticket_id)
    report = inspect_dependency_gate(
        packages=ticket.packages,
        dependency_ids=ticket.dependency_ids,
        intent=ticket.intent,
        mode=ticket.mode,
        backend=backend,
        session_id=ticket.session_id,
        plan_id=ticket.plan_id,
        template_id=ticket.template_id,
        issue_ticket=False,
    )
    report["ticket_id"] = ticket.ticket_id if report["status"] != "ready" else ""
    return report
