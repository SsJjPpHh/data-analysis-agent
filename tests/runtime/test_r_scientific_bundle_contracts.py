import json
import os
import subprocess
from pathlib import Path

import pytest

from agent.prompts import get_system_prompt
from r_runtime.index_validation import INDEX_CONTENT_VALIDATION_R_CODE
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from r_runtime.environment import HostREnvironment, REnvironmentError


host_environment = HostREnvironment.locate()
try:
    host_verified = host_environment.prepare()
    rscript_bin = str(host_verified.rscript)
    host_r_env = host_environment.process_environment(verified=host_verified)
except REnvironmentError:
    rscript_bin = None
    host_r_env = {}
rscript_required = pytest.mark.skipif(rscript_bin is None, reason="Local R is unavailable")


def _run_r(tmp_path: Path, code: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    assert rscript_bin is not None
    script_path = tmp_path / "scientific_bundle_contract.R"
    script_path.write_text(code, encoding="utf-8")
    env = host_r_env.copy()
    env["BAA_OUTPUT_DIR"] = str(tmp_path / "artifacts")
    return subprocess.run(
        [rscript_bin, "--vanilla", str(script_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def test_prompt_declares_lazy_and_scientific_dataset_contracts():
    prompt = get_system_prompt()

    assert "baa_lazy_dataset" in prompt
    assert "baa_scientific_handle" in prompt
    assert "baa_collect_dataset()" in prompt
    assert "baa_sample_dataset()" in prompt
    assert "DuckDB" in prompt
    assert "must not be passed directly to ordinary data.frame templates" in prompt


@rscript_required
def test_hdf5_router_never_treats_generic_h5_as_10x(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    generic_path = tmp_path / "generic.h5"
    tenx_path = tmp_path / "tenx.h5"
    h5ad_path = tmp_path / "sample.h5ad"
    h5ad_layers_path = tmp_path / "sample-layers.h5ad"
    loom_path = tmp_path / "sample.loom"
    h5seurat_path = tmp_path / "sample.h5seurat"
    code = f"""
source({json.dumps(str(helper))})

generic_file <- hdf5r::H5File$new({json.dumps(str(generic_path))}, mode = "w")
generic_file[["measurements"]] <- matrix(1:6, nrow = 2)
generic_file$close_all()

tenx_file <- hdf5r::H5File$new({json.dumps(str(tenx_path))}, mode = "w")
matrix_group <- tenx_file$create_group("matrix")
matrix_group[["data"]] <- c(1, 2)
matrix_group[["indices"]] <- c(0L, 1L)
matrix_group[["indptr"]] <- c(0L, 1L, 2L)
matrix_group[["shape"]] <- c(2L, 2L)
matrix_group[["barcodes"]] <- c("cell1", "cell2")
features_group <- matrix_group$create_group("features")
features_group[["id"]] <- c("g1", "g2")
features_group[["name"]] <- c("Gene 1", "Gene 2")
features_group$close()
matrix_group$close()
tenx_file$close_all()

h5ad_file <- hdf5r::H5File$new({json.dumps(str(h5ad_path))}, mode = "w")
h5ad_file$create_group("X")$close()
h5ad_file$create_group("obs")$close()
h5ad_file$create_group("var")$close()
h5ad_file$close_all()

h5ad_layers_file <- hdf5r::H5File$new({json.dumps(str(h5ad_layers_path))}, mode = "w")
h5ad_layers_file$create_group("layers")$close()
h5ad_layers_file$create_group("obs")$close()
h5ad_layers_file$create_group("var")$close()
h5ad_layers_file$close_all()

loom_file <- hdf5r::H5File$new({json.dumps(str(loom_path))}, mode = "w")
loom_file[["matrix"]] <- matrix(1:4, nrow = 2)
loom_file$create_group("row_attrs")$close()
loom_file$create_group("col_attrs")$close()
loom_file$close_all()

h5seurat_file <- hdf5r::H5File$new({json.dumps(str(h5seurat_path))}, mode = "w")
h5seurat_file$create_group("assays")$close()
h5seurat_file$create_group("meta.data")$close()
h5seurat_file$close_all()

stopifnot(
  identical(baa_classify_hdf5({json.dumps(str(generic_path))}), "generic_hdf5"),
  identical(baa_classify_hdf5({json.dumps(str(tenx_path))}), "10x_h5"),
  identical(baa_classify_hdf5({json.dumps(str(h5ad_path))}), "h5ad"),
  identical(baa_classify_hdf5({json.dumps(str(h5ad_layers_path))}), "h5ad"),
  identical(baa_classify_hdf5({json.dumps(str(loom_path))}), "loom"),
  identical(baa_classify_hdf5({json.dumps(str(h5seurat_path))}), "h5seurat")
)

tenx_inspection <- baa_read_dataset(
  {json.dumps(str(tenx_path))}, "10x_h5", load_profile = "inspection"
)
h5ad_inspection <- baa_read_dataset(
  {json.dumps(str(h5ad_path))}, "h5ad", load_profile = "inspection"
)
stopifnot(
  inherits(tenx_inspection, "baa_scientific_handle"),
  identical(tenx_inspection$format, "10x_h5"),
  !inherits(tenx_inspection, "Seurat"),
  inherits(h5ad_inspection, "baa_scientific_handle"),
  identical(h5ad_inspection$format, "h5ad"),
  nrow(tenx_inspection$profile_data) <= 256L,
  nrow(h5ad_inspection$profile_data) <= 256L
)
if (!requireNamespace("anndataR", quietly = TRUE)) {{
  h5ad_analysis_message <- tryCatch(
    baa_read_dataset(
      {json.dumps(str(h5ad_path))}, "h5ad", load_profile = "analysis"
    ),
    error = function(e) conditionMessage(e)
  )
  stopifnot(grepl("anndataR", h5ad_analysis_message, fixed = TRUE))
}}

generic <- baa_read_dataset({json.dumps(str(generic_path))}, "h5")
stopifnot(
  inherits(generic, "baa_scientific_handle"),
  identical(generic$format, "generic_hdf5"),
  identical(generic$adapter_id, "generic_hdf5_handle"),
  identical(generic$processing_stage, "raw"),
  nrow(generic$profile_data) <= 256L,
  !inherits(generic, "Seurat")
)
invalid_message <- tryCatch(
  baa_read_dataset({json.dumps(str(generic_path))}, "10x_h5"),
  error = function(e) conditionMessage(e)
)
stopifnot(grepl("10x", invalid_message, ignore.case = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_nested_10x_bundle_returns_validated_multimodal_object_and_profile(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    matrix_path = tmp_path / "matrix.mtx"
    features_path = tmp_path / "features.tsv"
    barcodes_path = tmp_path / "barcodes.tsv"
    matrix_path.write_text(
        "%%MatrixMarket matrix coordinate integer general\n3 2 4\n1 1 1\n2 2 2\n3 1 3\n3 2 4\n",
        encoding="utf-8",
    )
    features_path.write_text(
        "g1\tGene A\tGene Expression\ng2\tGene B\tGene Expression\na1\tADT 1\tAntibody Capture\n",
        encoding="utf-8",
    )
    barcodes_path.write_text("Cell1\nCell2\n", encoding="utf-8")
    code = f"""
source({json.dumps(str(helper))})
profile_code <- {json.dumps(DATASET_PROFILE_R_CODE)}
bundle <- baa_read_scientific_bundle(list(
  format = "mtx",
  assets = list(list(
    role = "10x_bundle",
    assets = list(
      list(path = {json.dumps(str(matrix_path))}, format = "mtx", role = "primary", kind = "file"),
      list(path = {json.dumps(str(features_path))}, format = "tsv", role = "features", kind = "file"),
      list(path = {json.dumps(str(barcodes_path))}, format = "tsv", role = "barcodes", kind = "file")
    )
  ))
))
dataset <- bundle$dataset
stopifnot(
  inherits(dataset, "baa_10x_multimodal"),
  identical(names(dataset), c("Gene Expression", "Antibody Capture")),
  identical(dim(dataset[["Gene Expression"]]), c(2L, 2L)),
  identical(dim(dataset[["Antibody Capture"]]), c(1L, 2L)),
  identical(colnames(dataset[[1]]), colnames(dataset[[2]])),
  identical(bundle$adapter_id, "matrix_market_10x")
)
eval(parse(text = profile_code))
stopifnot(
  identical(structure_profile$representation, "10x_multimodal"),
  identical(structure_profile$axes$modalities, 2L),
  identical(structure_profile$axes$cells, 2L),
  nrow(profile_data) == 2L
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_matrix_market_inspection_reads_only_header_structure(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    matrix_path = tmp_path / "huge-declared.mtx"
    matrix_path.write_text(
        "%%MatrixMarket matrix coordinate integer general\n"
        "% dimensions are deliberately too large to materialize\n"
        "1000000000 1000000000 0\n",
        encoding="utf-8",
    )
    too_deep = tmp_path / "too-deep"
    nested = too_deep
    for name in ("a", "b", "c", "d", "e"):
        nested = nested / name
    nested.mkdir(parents=True)
    (nested / "matrix.mtx").write_text(
        "%%MatrixMarket matrix coordinate integer general\n1 1 0\n",
        encoding="utf-8",
    )
    code = f"""
source({json.dumps(str(helper))})
dataset <- baa_read_dataset(
  {json.dumps(str(matrix_path))}, "mtx", load_profile = "inspection"
)
stopifnot(
  inherits(dataset, "baa_scientific_handle"),
  identical(dataset$format, "mtx"),
  identical(dataset$axes$features, 1000000000),
  identical(dataset$axes$samples, 1000000000),
  identical(dataset$structure$nonzero_entries, 0),
  nrow(dataset$profile_data) <= 256L
)
depth_message <- tryCatch(
  baa_read_dataset(
    {json.dumps(str(too_deep))}, "mtx", load_profile = "inspection"
  ),
  error = function(e) conditionMessage(e)
)
stopifnot(grepl("maximum inspection depth", depth_message, fixed = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_fcs_inspection_rejects_segment_offsets_outside_the_file(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    invalid_fcs = tmp_path / "invalid-offsets.fcs"
    header = (
        "FCS3.1"
        + " " * 4
        + f"{58:>8}{999:>8}{0:>8}{0:>8}{0:>8}{0:>8}"
    )
    assert len(header.encode("ascii")) == 58
    invalid_fcs.write_bytes(header.encode("ascii"))
    code = f"""
source({json.dumps(str(helper))})
message <- tryCatch(
  baa_read_dataset(
    {json.dumps(str(invalid_fcs))}, "fcs", load_profile = "inspection"
  ),
  error = function(e) conditionMessage(e)
)
stopifnot(grepl("outside the file", message, fixed = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_space_ranger_root_prefers_filtered_matrix_and_attaches_spatial_roles(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    outs = tmp_path / "outs"
    filtered = outs / "filtered_feature_bc_matrix"
    raw = outs / "raw_feature_bc_matrix"
    spatial = outs / "spatial"
    for folder in (filtered, raw, spatial):
        folder.mkdir(parents=True)
    matrix_text = "%%MatrixMarket matrix coordinate integer general\n2 2 2\n1 1 1\n2 2 1\n"
    for folder, first_gene in ((filtered, "Filtered A"), (raw, "Raw A")):
        (folder / "matrix.mtx").write_text(matrix_text, encoding="utf-8")
        (folder / "features.tsv").write_text(
            f"g1\t{first_gene}\tGene Expression\ng2\tGene B\tGene Expression\n",
            encoding="utf-8",
        )
        (folder / "barcodes.tsv").write_text("Cell1\nCell2\n", encoding="utf-8")
    (spatial / "tissue_positions.csv").write_text(
        "barcode,in_tissue,array_row,array_col\nCell1,1,1,2\nCell2,1,3,4\n",
        encoding="utf-8",
    )
    (spatial / "scalefactors_json.json").write_text("{}", encoding="utf-8")
    (spatial / "tissue_lowres_image.png").write_bytes(b"png")
    code = f"""
source({json.dumps(str(helper))})
dataset <- baa_read_dataset({json.dumps(str(outs))}, "mtx")
context <- attr(dataset, "baa_10x_bundle_context", exact = TRUE)
stopifnot(
  inherits(dataset, "Matrix"),
  identical(rownames(dataset), c("Filtered A", "Gene B")),
  !is.null(context),
  identical(context$profile_bounded, TRUE),
  nrow(context$coordinate_profile) == 2L,
  all(c("spatial_coordinates", "spatial_metadata", "spatial_image") %in%
      vapply(context$assets, function(asset) asset$role, character(1)))
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_space_ranger_coordinates_fail_closed_when_any_count_barcode_is_missing(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    matrix_path = tmp_path / "matrix.mtx"
    features_path = tmp_path / "features.tsv"
    barcodes_path = tmp_path / "barcodes.tsv"
    coordinates_path = tmp_path / "tissue_positions.csv"
    matrix_path.write_text(
        "%%MatrixMarket matrix coordinate integer general\n2 2 2\n1 1 1\n2 2 1\n",
        encoding="utf-8",
    )
    features_path.write_text("g1\tGene A\ng2\tGene B\n", encoding="utf-8")
    barcodes_path.write_text("Cell1\nCell2\n", encoding="utf-8")
    coordinates_path.write_text(
        "barcode,in_tissue,array_row,array_col\nCell1,1,1,2\nWRONG,1,3,4\n",
        encoding="utf-8",
    )
    code = f"""
source({json.dumps(str(helper))})
message <- tryCatch(
  baa_read_scientific_bundle(list(assets = list(
    list(path = {json.dumps(str(matrix_path))}, format = "mtx", role = "primary", kind = "file"),
    list(path = {json.dumps(str(features_path))}, format = "tsv", role = "features", kind = "file"),
    list(path = {json.dumps(str(barcodes_path))}, format = "tsv", role = "barcodes", kind = "file"),
    list(path = {json.dumps(str(coordinates_path))}, format = "csv", role = "spatial_coordinates", kind = "file")
  ))),
  error = function(e) conditionMessage(e)
)
stopifnot(grepl("do not cover", message, fixed = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_composite_roles_validate_indexes_and_load_batch_fcs(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    bam_path = tmp_path / "sample.bam"
    bad_bai_path = tmp_path / "other.bai"
    vcf_path = tmp_path / "cohort.vcf.gz"
    bad_tbi_path = tmp_path / "other.vcf.gz.tbi"
    for path in (bam_path, bad_bai_path, vcf_path, bad_tbi_path):
        path.write_bytes(b"placeholder")
    fcs_one = tmp_path / "one.fcs"
    fcs_two = tmp_path / "two.fcs"
    code = f"""
source({json.dumps(str(helper))})

bam_message <- tryCatch(
  baa_read_scientific_bundle(list(assets = list(
    list(path = {json.dumps(str(bam_path))}, format = "bam", role = "primary", kind = "file"),
    list(path = {json.dumps(str(bad_bai_path))}, format = "bai", role = "index", kind = "file")
  ))),
  error = function(e) conditionMessage(e)
)
vcf_message <- tryCatch(
  baa_read_scientific_bundle(list(assets = list(
    list(path = {json.dumps(str(vcf_path))}, format = "vcf", role = "primary", kind = "file"),
    list(path = {json.dumps(str(bad_tbi_path))}, format = "tbi", role = "index", kind = "file")
  ))),
  error = function(e) conditionMessage(e)
)
stopifnot(
  grepl("does not match", bam_message, fixed = TRUE),
  grepl("does not match", vcf_message, fixed = TRUE)
)

frame_one <- flowCore::flowFrame(
  exprs = matrix(1:12, nrow = 6, dimnames = list(NULL, c("FSC", "SSC")))
)
frame_two <- flowCore::flowFrame(
  exprs = matrix(13:24, nrow = 6, dimnames = list(NULL, c("FSC", "SSC")))
)
flowCore::write.FCS(frame_one, {json.dumps(str(fcs_one))})
flowCore::write.FCS(frame_two, {json.dumps(str(fcs_two))})
fcs_bundle <- baa_read_scientific_bundle(list(assets = list(
  list(path = {json.dumps(str(fcs_one))}, format = "fcs", role = "primary", kind = "file"),
  list(path = {json.dumps(str(fcs_two))}, format = "fcs", role = "replicate", kind = "file")
)))
fcs_inspection <- baa_read_scientific_bundle(list(assets = list(
  list(path = {json.dumps(str(fcs_one))}, format = "fcs", role = "primary", kind = "file"),
  list(path = {json.dumps(str(fcs_two))}, format = "fcs", role = "replicate", kind = "file")
)), load_profile = "inspection")
stopifnot(
  methods::is(fcs_bundle$dataset, "flowSet"),
  length(fcs_bundle$dataset) == 2L,
  identical(fcs_bundle$adapter_id, "fcs_flowcore_batch"),
  inherits(fcs_inspection$dataset, "baa_scientific_handle"),
  identical(fcs_inspection$dataset$axes$files, 2L),
  nrow(fcs_inspection$dataset$profile_data) == 2L
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_bcf_and_range_inspection_are_bounded_metadata_handles(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    bed_path = tmp_path / "regions.bed"
    gff_path = tmp_path / "features.gff"
    bed_path.write_text(
        "".join(f"chr1\t{i}\t{i + 1}\tregion-{i}\n" for i in range(300)),
        encoding="utf-8",
    )
    gff_path.write_text(
        "##gff-version 3\n"
        + "".join(
            f"chr1\ttest\tgene\t{i + 1}\t{i + 2}\t.\t+\t.\tID=g{i}\n"
            for i in range(300)
        ),
        encoding="utf-8",
    )
    code = f"""
source({json.dumps(str(helper))})
bcf_path <- system.file("extdata", "ex1.bcf.gz", package = "Rsamtools", mustWork = TRUE)
bcf <- baa_read_dataset(bcf_path, "bcf", load_profile = "inspection")
bed <- baa_read_dataset({json.dumps(str(bed_path))}, "bed", load_profile = "inspection")
gff <- baa_read_dataset({json.dumps(str(gff_path))}, "gff", load_profile = "inspection")
stopifnot(
  inherits(bcf, "baa_scientific_handle"),
  identical(bcf$adapter_id, "bcf_rsamtools_handle"),
  nrow(bcf$profile_data) <= 256L,
  inherits(bed, "baa_scientific_handle"),
  identical(bed$axes$ranges, NA_integer_),
  nrow(bed$profile_data) == 256L,
  inherits(gff, "baa_scientific_handle"),
  nrow(gff$profile_data) == 256L
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_python_sidecar_format_names_load_real_indexed_bam_and_vcf(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    code = f"""
source({json.dumps(str(helper))})
sam_path <- file.path({json.dumps(str(tmp_path))}, "sample.sam")
writeLines(c(
  "@HD\\tVN:1.6\\tSO:coordinate",
  "@SQ\\tSN:chr1\\tLN:100",
  "r1\\t0\\tchr1\\t1\\t60\\t4M\\t*\\t0\\t0\\tACTG\\tIIII"
), sam_path)
bam_path <- Rsamtools::asBam(
  sam_path,
  destination = file.path({json.dumps(str(tmp_path))}, "sample"),
  overwrite = TRUE,
  indexDestination = TRUE
)
bam_bundle <- baa_read_scientific_bundle(list(assets = list(
  list(path = bam_path, format = "bam", role = "primary", kind = "file"),
  list(path = paste0(bam_path, ".bai"), format = "bam_index", role = "alignment_index", kind = "file")
)))

vcf_path <- file.path({json.dumps(str(tmp_path))}, "cohort.vcf")
writeLines(c(
  "##fileformat=VCFv4.2",
  "##contig=<ID=chr1,length=100>",
  "#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO",
  "chr1\\t1\\t.\\tA\\tG\\t.\\tPASS\\t."
), vcf_path)
vcf_bgzip <- Rsamtools::bgzip(vcf_path, overwrite = TRUE)
vcf_index <- Rsamtools::indexTabix(vcf_bgzip, format = "vcf")
vcf_bundle <- baa_read_scientific_bundle(list(assets = list(
  list(path = vcf_bgzip, format = "vcf", role = "primary", kind = "file"),
  list(path = vcf_index, format = "vcf_index", role = "variant_index", kind = "file")
)))
stopifnot(
  inherits(bam_bundle$dataset, "baa_scientific_handle"),
  identical(bam_bundle$dataset$structure$indexed, TRUE),
  identical(bam_bundle$adapter_id, "bam_rsamtools_indexed_handle"),
  inherits(vcf_bundle$dataset, "baa_scientific_handle"),
  identical(vcf_bundle$dataset$structure$indexed, TRUE),
  identical(vcf_bundle$adapter_id, "vcf_indexed_handle")
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_renamed_alignment_and_variant_indexes_fail_content_preflight(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    code = f"""
source({json.dumps(str(helper))})
{INDEX_CONTENT_VALIDATION_R_CODE}

make_bam <- function(stem, target_length) {{
  sam_path <- paste0(stem, ".sam")
  writeLines(c(
    "@HD\\tVN:1.6\\tSO:coordinate",
    paste0("@SQ\\tSN:chr1\\tLN:", target_length),
    "r1\\t0\\tchr1\\t1\\t60\\t4M\\t*\\t0\\t0\\tACTG\\tIIII"
  ), sam_path)
  Rsamtools::asBam(sam_path, destination = stem, overwrite = TRUE, indexDestination = TRUE)
}}

sample_bam <- make_bam(file.path({json.dumps(str(tmp_path))}, "sample"), 100L)
other_bam <- make_bam(file.path({json.dumps(str(tmp_path))}, "other"), 200L)
sample_index <- paste0(sample_bam, ".bai")
manifest <- list(assets = list(
  list(path = sample_bam, format = "bam", role = "primary"),
  list(path = sample_index, format = "bam_index", role = "alignment_index")
))
stopifnot(isTRUE(baa_validate_dataset_index_content(manifest)))
file.copy(paste0(other_bam, ".bai"), sample_index, overwrite = TRUE)
message <- tryCatch(
  baa_validate_dataset_index_content(manifest),
  error = function(e) conditionMessage(e)
)
stopifnot(is.character(message), grepl("content", message, ignore.case = TRUE))

make_vcf <- function(stem, sequence_name) {{
  source <- paste0(stem, ".vcf")
  writeLines(c(
    "##fileformat=VCFv4.2",
    paste0("##contig=<ID=", sequence_name, ",length=100>"),
    "#CHROM\\tPOS\\tID\\tREF\\tALT\\tQUAL\\tFILTER\\tINFO",
    paste0(sequence_name, "\\t1\\t.\\tA\\tG\\t.\\tPASS\\t.")
  ), source)
  compressed <- Rsamtools::bgzip(source, overwrite = TRUE)
  index <- Rsamtools::indexTabix(compressed, format = "vcf")
  list(path = compressed, index = index)
}}

sample_vcf <- make_vcf(file.path({json.dumps(str(tmp_path))}, "cohort"), "chr1")
other_vcf <- make_vcf(file.path({json.dumps(str(tmp_path))}, "other-cohort"), "chr2")
variant_manifest <- list(assets = list(
  list(path = sample_vcf$path, format = "vcf", role = "primary"),
  list(path = sample_vcf$index, format = "vcf_index", role = "variant_index")
))
stopifnot(isTRUE(baa_validate_dataset_index_content(variant_manifest)))
file.copy(other_vcf$index, sample_vcf$index, overwrite = TRUE)
variant_message <- tryCatch(
  baa_validate_dataset_index_content(variant_manifest),
  error = function(e) conditionMessage(e)
)
stopifnot(is.character(variant_message), grepl("content|dictionary", variant_message, ignore.case = TRUE))

# A .csi declaration is also operationally opened and queried; relabeling a
# foreign TBI as CSI must never be enough to publish indexed capability.
fake_csi <- paste0(sample_vcf$path, ".csi")
file.copy(other_vcf$index, fake_csi, overwrite = TRUE)
csi_manifest <- list(assets = list(
  list(path = sample_vcf$path, format = "vcf", role = "primary"),
  list(path = fake_csi, format = "vcf_index", role = "variant_index")
))
csi_message <- tryCatch(
  baa_validate_dataset_index_content(csi_manifest),
  error = function(e) conditionMessage(e)
)
stopifnot(is.character(csi_message), grepl("content|index|dictionary", csi_message, ignore.case = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_raw_sequence_alignment_archive_and_tree_adapters_are_truthful(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    fasta = tmp_path / "reads.fasta"
    fastq = tmp_path / "reads.fastq"
    sam = tmp_path / "alignments.sam"
    maf = tmp_path / "variants.maf"
    qza = tmp_path / "artifact.qza"
    tree = tmp_path / "tree.nwk"
    fasta.write_text(">seq1\nACTG\n>seq2\nAACCGG\n", encoding="utf-8")
    fastq.write_text("@r1\nACTG\n+\nIIII\n@r2\nAACC\n+\nIIII\n", encoding="utf-8")
    sam.write_text("@HD\tVN:1.6\tSO:unknown\n", encoding="utf-8")
    maf.write_text("Hugo_Symbol\tTumor_Sample_Barcode\nTP53\tS1\n", encoding="utf-8")
    tree.write_text("(A:0.1,B:0.2);\n", encoding="utf-8")
    qza.write_bytes(b"not-a-real-archive")
    code = f"""
source({json.dumps(str(helper))})
fasta <- baa_read_dataset({json.dumps(str(fasta))}, "fasta")
fastq <- baa_read_dataset({json.dumps(str(fastq))}, "fastq")
sam <- baa_read_dataset({json.dumps(str(sam))}, "sam")
maf <- baa_read_dataset({json.dumps(str(maf))}, "maf")
qza <- baa_read_dataset({json.dumps(str(qza))}, "qza")
tree <- baa_read_dataset({json.dumps(str(tree))}, "newick")
stopifnot(
  inherits(fasta, "baa_scientific_handle"), nrow(fasta$profile_data) == 2L,
  identical(fasta$processing_stage, "raw"), length(fasta$upstream_required) > 0L,
  inherits(fastq, "baa_scientific_handle"), nrow(fastq$profile_data) == 2L,
  identical(fastq$processing_stage, "raw"),
  inherits(sam, "baa_scientific_handle"), identical(sam$modality, "aligned_reads"),
  inherits(maf, "baa_scientific_handle"), identical(maf$processing_stage, "raw"),
  inherits(qza, "baa_scientific_handle"), identical(qza$processing_stage, "raw"),
  inherits(tree, "phylo")
)
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_checkpoint_load_prefers_read_only_checkpoint_directory(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    output_dir = tmp_path / "output"
    checkpoint_dir = tmp_path / "checkpoints"
    output_dir.mkdir()
    checkpoint_dir.mkdir()
    code = f"""
Sys.setenv(
  BAA_OUTPUT_DIR = {json.dumps(str(output_dir))},
  BAA_CHECKPOINT_DIR = {json.dumps(str(checkpoint_dir))}
)
source({json.dumps(str(helper))})
saveRDS(list(source = "checkpoint_dir"), file.path({json.dumps(str(checkpoint_dir))}, "checkpoint_model.rds"))
saveRDS(list(source = "output_dir"), file.path({json.dumps(str(output_dir))}, "checkpoint_model.rds"))
loaded <- baa_load_checkpoint("model")
stopifnot(identical(loaded$source, "checkpoint_dir"))
unlink(file.path({json.dumps(str(checkpoint_dir))}, "checkpoint_model.rds"))
fallback <- baa_load_checkpoint("model")
stopifnot(identical(fallback$source, "output_dir"))
invalid <- tryCatch(baa_load_checkpoint("../model"), error = function(e) conditionMessage(e))
stopifnot(grepl("checkpoint name", invalid, ignore.case = TRUE))
"""

    completed = _run_r(tmp_path, code)

    assert completed.returncode == 0, completed.stderr
