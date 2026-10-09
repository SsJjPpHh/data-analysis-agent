"""Keep reference dependencies aligned with literal native R implementations."""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = {"base", "stats", "utils", "graphics", "grDevices", "methods", "splines",
        "parallel", "datasets", "tools", "grid"}
BIOC = {"Biobase", "DESeq2", "GenomicRanges", "IRanges", "S4Vectors", "MultiAssayExperiment",
        "SingleCellExperiment", "SpatialExperiment", "SummarizedExperiment", "clusterProfiler",
        "edgeR", "enrichplot", "limma", "phyloseq", "slingshot", "tradeSeq", "scater"}


def references() -> list[str]:
    paths = [p for p in (ROOT / "agent/analysis").glob("*cores.py") if p.name != "skeleton_cores.py"]
    paths += list((ROOT / "agent/analysis").glob("*workflows.py"))
    paths += list((ROOT / "tests/science").glob("test_*implementations.py"))
    paths += list((ROOT / "tests/science").glob("test_*workflows.py"))
    paths += [ROOT / "tests/science" / name for name in (
        "test_method_numerics.py", "test_core_method_references.py", "test_script_library.py")]
    names = {"jsonlite", "readr", "ggplot2"}
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                code = "\n".join(line.split("#")[0] for line in node.value.splitlines())
                names.update(re.findall(r"(?<![\w.])([A-Za-z][\w.]*)::", code))
                names.update(re.findall(r"requireNamespace\([\"']([\w.]+)", code))
    # Include the actual registered legacy cores without importing namespaces
    # from obsolete, unreachable skeletons. Render only; never execute R.
    sys.path.insert(0, str(ROOT))
    from agent.analysis.catalog import AnalysisCatalog
    from agent.analysis.models import AnalysisPlan
    from agent.analysis.script_library import render_script

    for template in AnalysisCatalog().snapshot().templates:
        plan = AnalysisPlan(
            plan_id="dependency-inventory", source="template", dataset_id="inventory",
            intent=template.title, category=template.category, method_family=template.family,
            scenario=template.scenario, variable_bindings={}, packages=template.packages,
            prerequisites=(), assumptions=(), diagnostics=template.diagnostics,
            required_outputs=template.required_outputs, resource_class="standard",
            template_id=template.id, resolved_inputs={},
        )
        code = render_script(plan)
        if code:
            code = "\n".join(line.split("#")[0] for line in code.splitlines())
            names.update(re.findall(r"(?<![\w.])([A-Za-z][\w.]*)::", code))
            names.update(re.findall(r"requireNamespace\([\"']([\w.]+)", code))
    names -= BASE | {"pkg"}
    return [("bioc::" if name in BIOC else "cran::") + name for name in sorted(names)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = ROOT / "scripts/science-reference-packages.txt"
    expected = "\n".join(references()) + "\n"
    if args.check:
        if not manifest.is_file() or manifest.read_text(encoding="utf-8") != expected:
            raise SystemExit("Scientific dependencies changed; run scripts/sync_science_dependencies.py")
    else:
        manifest.write_text(expected, encoding="utf-8")
