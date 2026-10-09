"""Extract concrete package references from generated R code."""

from __future__ import annotations

import re


_COMMAND_DEPENDENCIES = {
    "samtools": "samtools",
    "bcftools": "bcftools",
    "bgzip": "bcftools",
    "tabix": "bcftools",
    "fastqc": "fastqc",
    "fastp": "fastp",
    "star": "read_aligner",
    "bwa": "read_aligner",
    "bowtie2": "read_aligner",
    "salmon": "transcript_quantifier",
    "kallisto": "transcript_quantifier",
    "plink": "plink",
    "plink2": "plink",
    "qiime": "qiime2",
    "spark-submit": "spark",
    "nvidia-smi": "cuda",
    "nvcc": "cuda",
}


def _scrub_r_code(code: str, *, keep_strings: bool) -> str:
    source = str(code or "")
    output: list[str] = []
    quote = ""
    escaped = False
    in_comment = False
    for character in source:
        if in_comment:
            if character in "\r\n":
                in_comment = False
                output.append(character)
            else:
                output.append(" ")
            continue
        if quote:
            if keep_strings:
                output.append(character)
            else:
                output.append(quote if character == quote and not escaped else " ")
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if character in {"'", '"', "`"}:
            quote = character
            escaped = False
            output.append(character)
        elif character == "#":
            in_comment = True
            output.append(" ")
        else:
            output.append(character)
    return "".join(output)


def referenced_r_packages(code: str) -> tuple[str, ...]:
    """Return executable namespace/library references, excluding prose and comments."""

    code_without_comments = _scrub_r_code(code, keep_strings=True)
    executable_code = _scrub_r_code(code, keep_strings=False)
    packages = set(re.findall(r"\b([A-Za-z][A-Za-z0-9.]*)\s*:{2,3}", executable_code))
    call_pattern = re.compile(
        r"\b(?:library|require|requireNamespace|loadNamespace|getNamespace|packageVersion)"
        r"\s*\(\s*(?:package\s*=\s*)?"
        r"(?:['\"]([A-Za-z][A-Za-z0-9.]*)['\"]|([A-Za-z][A-Za-z0-9.]*))"
    )
    for quoted, bare in call_pattern.findall(code_without_comments):
        packages.add(quoted or bare)
    assignments = {
        name: value
        for name, value in re.findall(
            r"\b([A-Za-z.][A-Za-z0-9._]*)\s*(?:<-|=)\s*['\"]([A-Za-z][A-Za-z0-9.]*)['\"]",
            code_without_comments,
        )
    }
    dynamic_call_pattern = re.compile(
        r"\b(?:library|require|requireNamespace|loadNamespace|getNamespace|packageVersion)"
        r"\s*\(\s*([A-Za-z.][A-Za-z0-9._]*)\s*(?:,\s*character\.only\s*=\s*TRUE)?"
    )
    for variable in dynamic_call_pattern.findall(executable_code):
        if variable in assignments:
            packages.discard(variable)
            packages.add(assignments[variable])
    if re.search(r"\bbaa_table(?:_one)?\s*\(", executable_code):
        packages.add("readr")
    return tuple(sorted(packages, key=str.casefold))


def referenced_external_dependencies(code: str) -> tuple[str, ...]:
    """Return concrete host tools referenced by executable R code."""

    code_without_comments = _scrub_r_code(code, keep_strings=True)
    dependencies: list[str] = []

    def add_command(command: str) -> None:
        normalized = str(command or "").strip().split()[0].rsplit("/", 1)[-1].casefold()
        dependency_id = _COMMAND_DEPENDENCIES.get(normalized)
        if dependency_id and dependency_id not in dependencies:
            dependencies.append(dependency_id)

    for command in re.findall(
        r"\b(?:system2|Sys\.which|processx::run|processx::process\$new)"
        r"\s*\(\s*['\"]([^'\"]+)['\"]",
        code_without_comments,
    ):
        add_command(command)
    for shell in re.findall(r"\bsystem\s*\(\s*['\"]([^'\"]+)['\"]", code_without_comments):
        add_command(shell)
    if re.search(
        r"(?:device\s*=\s*['\"]cuda|torch_device\s*\(\s*['\"]cuda|with_device\s*\([^)]*cuda)",
        code_without_comments,
        re.IGNORECASE,
    ):
        add_command("nvidia-smi")
    return tuple(dependencies)
