"""Package preparation policy for local R execution."""

from __future__ import annotations

from collections.abc import Iterable

from r_runtime.external_dependencies import dependency_setup_requirements


CORE_RUNTIME_PACKAGES = frozenset({"jsonlite"})

# Some important research packages are intentionally published outside the
# default CRAN/Bioconductor repositories.  These are installation routes, not
# an allowlist: arbitrary packages referenced by generated R code remain
# eligible for installation.
PACKAGE_REPOSITORIES: dict[str, str] = {
    "INLA": "https://inla.r-inla-download.org/R/stable",
    "cmdstanr": "https://stan-dev.r-universe.dev",
}

PACKAGE_GITHUB_SOURCES: dict[str, str] = {
    "ArchR": "GreenleafLab/ArchR",
    "CellChat": "jinworks/CellChat",
    "SCENIC": "aertslab/SCENIC",
    "monocle3": "cole-trapnell-lab/monocle3",
}

def package_setup_requirements(packages: Iterable[str]) -> tuple[str, ...]:
    """Return concrete non-R prerequisites for the requested package set."""

    return dependency_setup_requirements(packages)


_FORMAT_PACKAGES: dict[str, tuple[str, ...]] = {
    "csv": ("readr",),
    "tsv": ("readr",),
    "txt": ("readr",),
    "xlsx": ("readxl",),
    "xls": ("readxl",),
    "sav": ("haven",),
    "dta": ("haven",),
    "parquet": ("arrow",),
    "mtx": ("Matrix",),
    "h5ad": ("hdf5r",),
    "h5": ("hdf5r",),
    "generic_hdf5": ("hdf5r",),
    "10x_h5": ("hdf5r", "Seurat"),
    "loom": ("hdf5r",),
    "h5seurat": ("hdf5r",),
    "fcs": ("flowCore",),
    "vcf": ("Rsamtools",),
    "bcf": ("Rsamtools",),
    "bed": ("rtracklayer",),
    "narrowpeak": ("rtracklayer",),
    "gff": ("rtracklayer",),
    "gtf": ("rtracklayer",),
    "biom": ("biomformat",),
    "mzml": ("MSnbase",),
    "mzxml": ("MSnbase",),
    "bam": ("Rsamtools",),
    "fastq": ("ShortRead",),
    "fasta": ("Biostrings",),
    "cram": ("Rsamtools",),
    "bigwig": ("rtracklayer",),
    "maf": ("readr",),
    "newick": ("ape",),
    "plink_bed": ("BEDMatrix",),
}

_INDEX_PACKAGES = frozenset({"Rsamtools", "GenomicRanges", "IRanges"})
_INDEX_FORMATS = frozenset({"bam_index", "cram_index", "vcf_index"})
_LAZY_FORMATS = frozenset({"csv", "tsv", "txt", "parquet"})
_LAZY_PACKAGES = frozenset({"duckdb", "DBI", "dbplyr", "dplyr"})

LOADER_PACKAGES = frozenset(
    {
        *CORE_RUNTIME_PACKAGES,
        *(package for packages in _FORMAT_PACKAGES.values() for package in packages),
        *_INDEX_PACKAGES,
        *_LAZY_PACKAGES,
        "anndataR",
        "rhdf5",
        "SingleCellExperiment",
    }
)


def dataset_loader_packages(
    primary_format: str,
    asset_formats: Iterable[str] = (),
    *,
    inspection: bool = False,
    lazy: bool = False,
) -> tuple[str, ...]:
    """Return packages needed before the R helper can load one dataset."""

    normalized_primary = str(primary_format or "").strip().lower().lstrip(".")
    normalized_assets = {
        str(value or "").strip().lower().lstrip(".") for value in asset_formats if value
    }
    if normalized_primary == "bed" and {"plink_bim", "plink_fam"} <= normalized_assets:
        normalized_primary = "plink_bed"

    packages = set(CORE_RUNTIME_PACKAGES)
    packages.update(_FORMAT_PACKAGES.get(normalized_primary, ()))
    if normalized_assets & _INDEX_FORMATS:
        packages.update(_INDEX_PACKAGES)
    if lazy and normalized_primary in _LAZY_FORMATS:
        packages.update(_LAZY_PACKAGES)
    if normalized_primary == "h5ad" and not inspection:
        packages.update(("anndataR", "rhdf5", "SingleCellExperiment"))
    if inspection and normalized_primary == "10x_h5":
        packages.discard("Seurat")
    return tuple(sorted(packages, key=str.casefold))
