# -*- coding: utf-8 -*-
"""R package routing policy derived from the active analysis catalog."""

from __future__ import annotations

from dataclasses import dataclass

from agent.analysis.catalog import AnalysisCatalog
from r_runtime.package_policy import LOADER_PACKAGES, PACKAGE_GITHUB_SOURCES


@dataclass(frozen=True)
class RPackageInventory:
    installable: tuple[str, ...]
    github_fallbacks: dict[str, str]


def load_r_package_inventory() -> RPackageInventory:
    snapshot = AnalysisCatalog().snapshot()
    installable = {
        package
        for template in snapshot.templates
        for package in template.packages
        if package
    } | set(LOADER_PACKAGES)
    # Capability routing is intentionally broader than the finite template
    # catalog.  Its packages must remain eligible for free-form analysis and
    # on-demand installation even when no dedicated template names them.
    from agent.r_capability_catalog import CAPABILITIES

    for capability in CAPABILITIES:
        installable.update(
            package.strip() for package in capability.packages.split(",") if package.strip()
        )
    return RPackageInventory(
        installable=tuple(sorted(installable, key=str.casefold)),
        github_fallbacks=dict(PACKAGE_GITHUB_SOURCES),
    )


def build_r_package_inventory_prompt() -> str:
    inventory = load_r_package_inventory()
    return (
        "R package inventory access:\n"
        f"- The active template and loader catalog names {len(inventory.installable)} package candidates.\n"
        "- The live package inventory from the user's R installation is the current truth.\n"
        "- No package family is disabled by application policy.\n"
        "Inventory-use rules:\n"
        "- For specialized or uncertain requests, call get_r_package_inventory before writing R code.\n"
        "- Call get_r_package_help to inspect installed documentation for unfamiliar functions.\n"
        "- The runtime may install a missing CRAN/Bioconductor package into the library selected in System Settings.\n"
        "- Installed packages outside the routing examples may still be used after checking requireNamespace().\n"
        "- If a package needs compilers, Java, CUDA, CmdStan, system libraries, or an external database, report that concrete prerequisite so the user can install it.\n"
        "- Use EXACT CRAN package names, including capitalization (e.g. 'PK' for pharmacokinetic NCA, 'DESeq2', 'ggplot2'). Never invent abbreviations like 'pk' — a wrong name hard-blocks the analysis at the dependency gate.\n"
    )
