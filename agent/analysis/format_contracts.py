"""Accepted dataset formats for catalog-backed scientific workflows.

The catalog remains the stable product baseline. This module adds storage
representations that the local scientific loader can convert into the
catalog's declared analysis shape. Keeping the extension here avoids
rewriting 213 product templates merely because a workflow can now ingest an
additional native scientific format.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent.analysis.models import AnalysisTemplate


_SCIENTIFIC_TEMPLATE_FORMATS: Mapping[str, tuple[str, ...]] = {
    "bulk_rnaseq": ("mtx", "10x_h5", "h5ad"),
    "pathway_enrichment": ("gff", "gtf"),
    "wgcna_network": ("mtx", "10x_h5", "h5ad"),
    "single_cell": ("mtx", "10x_h5", "h5ad"),
    "genomic_ranges": ("bed", "narrowpeak", "gff", "gtf", "vcf", "bcf", "bam"),
    "microbiome_amplicon": ("biom",),
    "proteomics_ms": ("mzml", "mzxml"),
    "flow_cytometry": ("fcs",),
    "multiomics_integration": ("mtx", "10x_h5", "h5ad", "biom"),
    "rnaseq_batch_qc": ("mtx", "10x_h5", "h5ad"),
    "scrna_marker_enrichment": ("mtx", "10x_h5", "h5ad"),
    "scrna_sample_integration": ("mtx", "10x_h5", "h5ad"),
    "peak_region_analysis": ("bed", "narrowpeak", "gff", "gtf", "bam"),
    "metabolomics_xcms": ("mzml", "mzxml"),
    "multiassay_clinical_bridge": ("mtx", "10x_h5", "h5ad"),
    "omics_gwas_prs": ("vcf", "bcf"),
    "omics_variant_burden_annotation": ("vcf", "bcf"),
    "omics_scrna_trajectory": ("mtx", "10x_h5", "h5ad"),
    "omics_scrna_differential_abundance": ("mtx", "10x_h5", "h5ad"),
    "omics_spatial_transcriptomics": ("mtx", "10x_h5", "h5ad"),
    "omics_atac_chip_binding": ("mtx", "10x_h5", "h5ad", "bed", "narrowpeak", "bam"),
    "omics_alternative_splicing": ("gff", "gtf"),
    "omics_eqtl": ("vcf", "bcf"),
    "omics_somatic_mutation_cnv": ("vcf", "bcf", "bed"),
    "omics_microbiome_compositional": ("biom",),
    "omics_metabolomics_pathway": ("mzml", "mzxml"),
}

_MATRIX_TEMPLATE_MODALITIES: Mapping[str, tuple[str, ...]] = {
    "bulk_rnaseq": ("bulk_rnaseq", "rnaseq", "transcriptomics", "gene_expression"),
    "microarray_expression": ("microarray", "transcriptomics", "gene_expression"),
    "wgcna_network": (
        "bulk_rnaseq",
        "rnaseq",
        "microarray",
        "transcriptomics",
        "gene_expression",
    ),
    "microbiome_amplicon": ("microbiome", "microbiome_artifact", "amplicon"),
    "proteomics_ms": ("proteomics", "mass_spectrometry"),
    "multiomics_integration": ("multi_omics", "multiomics"),
    "rnaseq_batch_qc": ("bulk_rnaseq", "rnaseq", "transcriptomics", "gene_expression"),
    "omics_metabolomics_pathway": ("metabolomics", "mass_spectrometry"),
    "single_cell": ("single_cell",),
    "scrna_marker_enrichment": ("single_cell",),
    "scrna_sample_integration": ("single_cell",),
    "omics_scrna_trajectory": ("single_cell",),
    "omics_scrna_differential_abundance": ("single_cell",),
    "omics_spatial_transcriptomics": ("spatial",),
}

_MATRIX_TEMPLATE_SEMANTICS: Mapping[str, str] = {
    "bulk_rnaseq": "bulk RNA-seq 基因 counts/表达特征",
    "microarray_expression": "芯片探针或基因表达特征",
    "wgcna_network": "适合共表达网络的基因表达特征",
    "microbiome_amplicon": "OTU/ASV 丰度特征",
    "proteomics_ms": "蛋白或肽段定量强度特征",
    "multiomics_integration": "已标明组学来源并完成样本对齐的多组学特征",
    "rnaseq_batch_qc": "RNA-seq 基因表达特征",
    "omics_metabolomics_pathway": "代谢物或代谢峰定量特征",
    "single_cell": "单细胞基因counts/表达特征；观测ID和生物学供体ID分别确认",
    "scrna_marker_enrichment": "单细胞基因counts/表达特征；观测ID和生物学供体ID分别确认",
    "scrna_sample_integration": "单细胞基因counts/表达特征；观测ID和生物学供体ID分别确认",
    "omics_scrna_trajectory": "单细胞基因counts/表达特征与已确认群体注释",
    "omics_scrna_differential_abundance": "单细胞基因counts/表达特征与供体、类型、分组注释",
    "omics_spatial_transcriptomics": "空间spot的基因counts/表达特征与坐标、供体注释",
}

_KNOWN_SPECIFIC_SCIENTIFIC_MODALITIES = frozenset(
    {
        *(value for values in _MATRIX_TEMPLATE_MODALITIES.values() for value in values),
        "aligned_reads",
        "flow_cytometry",
        "genomic_ranges",
        "genomic_signal",
        "genotypes",
        "single_cell",
        "spatial",
    }
)

SCIENTIFIC_FEATURE_AXIS_NAMES = frozenset(
    {
        "feature",
        "features",
        "gene",
        "genes",
        "variable",
        "variables",
        "taxa",
        "peak",
        "peaks",
    }
)
SCIENTIFIC_SAMPLE_AXIS_NAMES = frozenset(
    {"sample", "samples", "cell", "cells", "subject", "subjects", "spot", "spots"}
)

# RData and generic HDF5 require runtime inspection before a method can know
# which object/assay they contain. They therefore never enter the catalog-wide
# static format allow-list. A future object-aware gate must establish these
# path-free fields before admitting one dynamically.
RUNTIME_OBJECT_VALIDATION_FIELDS = (
    "profile_revision",
    "object_kind",
    "modality",
    "representation",
    "processing_stage",
)

# These representations are intentionally cheap to inspect as bounded handles,
# but the production R loader materializes the real object when an analysis
# starts.  Their inspection-only ``upstream_required=reload with analysis``
# marker must therefore not be treated as a scientific preprocessing failure.
ANALYSIS_MATERIALIZABLE_FORMATS = frozenset(
    {
        "mtx",
        "10x_h5",
        "h5ad",
        "h5seurat",
        "fcs",
        "bed",
        "narrowpeak",
        "gff",
        "gtf",
        "biom",
        "mzml",
        "mzxml",
        "bigwig",
        "newick",
        "plink_bed",
    }
)


def scientific_template_formats(template_id: str) -> tuple[str, ...]:
    """Return native scientific formats added for one stable template ID."""
    return _SCIENTIFIC_TEMPLATE_FORMATS.get(str(template_id or "").strip(), ())


def scientific_matrix_semantic_label(template_id: str) -> str:
    """Return the scientific feature meaning a matrix template expects."""

    return _MATRIX_TEMPLATE_SEMANTICS.get(
        str(template_id or "").strip(),
        "与目标方法一致的科学特征",
    )


def scientific_matrix_modality_compatibility(
    template_id: str,
    modality: object,
) -> bool | None:
    """Compare a known scientific modality with a matrix template.

    ``None`` means the profile is generic or not specific enough to decide;
    callers must then rely on explicit matrix semantics or inspected axes.
    """

    normalized = str(modality or "").strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized or normalized in {
        "unknown",
        "unspecified",
        "tabular",
        "table",
        "matrix",
        "omics_matrix",
    }:
        return None
    accepted = _MATRIX_TEMPLATE_MODALITIES.get(str(template_id or "").strip())
    if not accepted or normalized not in _KNOWN_SPECIFIC_SCIENTIFIC_MODALITIES:
        return None
    return normalized in accepted


def accepted_template_formats(template: AnalysisTemplate) -> tuple[str, ...]:
    """Return the catalog baseline plus loader-backed storage formats."""
    return tuple(
        dict.fromkeys(
            (
                *(str(item).strip().lower().lstrip(".") for item in template.formats),
                *scientific_template_formats(template.id),
            )
        )
    )


def dataset_asset_formats(dataset_profile: Mapping[str, Any]) -> tuple[str, ...]:
    """Return only primary formats from a path-free profile.

    Auxiliary sample tables and indexes cannot make an otherwise unsupported
    primary object pass workflow preflight.
    """
    values: list[str] = []

    def add(value: object) -> None:
        normalized = str(value or "").strip().lower().lstrip(".")
        if normalized and normalized not in values:
            values.append(normalized)

    raw_assets = dataset_profile.get("assets")
    if isinstance(raw_assets, list):
        primary_assets = [
            asset
            for asset in raw_assets
            if isinstance(asset, Mapping) and str(asset.get("role") or "").strip() == "primary"
        ]
        selected_assets = primary_assets or (
            [raw_assets[0]] if raw_assets and isinstance(raw_assets[0], Mapping) else []
        )
        for asset in selected_assets:
            if isinstance(asset, Mapping):
                add(asset.get("format"))
    if not values:
        add(dataset_profile.get("format"))
    return tuple(values)


def has_scientific_matrix_evidence(
    template_id: str,
    dataset_profile: Mapping[str, Any],
) -> bool:
    """Return whether inspected data prove a usable scientific matrix shape."""

    if set(dataset_asset_formats(dataset_profile)).intersection(
        scientific_template_formats(template_id)
    ):
        return True
    scientific = dataset_profile.get("scientific")
    scientific = scientific if isinstance(scientific, Mapping) else {}
    axes_value = scientific.get("axes")
    axes = axes_value if isinstance(axes_value, Mapping) else {}
    normalized_axes = {
        str(key or "").strip().casefold().replace("-", "_").replace(" ", "_")
        for key in axes
    }
    feature_axis = bool(normalized_axes.intersection(SCIENTIFIC_FEATURE_AXIS_NAMES))
    sample_axis = bool(normalized_axes.intersection(SCIENTIFIC_SAMPLE_AXIS_NAMES))
    if not (feature_axis and sample_axis):
        return False
    modality = str(
        dataset_profile.get("modality") or scientific.get("modality") or ""
    ).strip().casefold()
    return bool(scientific.get("assays")) or modality in {
        "matrix",
        "omics_matrix",
        "multi_omics",
        "single_cell",
        "spatial",
    }
