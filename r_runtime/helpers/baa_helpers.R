baa_artifacts <- new.env(parent = emptyenv())
baa_artifacts$tables <- list()
baa_artifacts$figures <- list()
baa_artifacts$texts <- list()
baa_artifacts$models <- list()
baa_artifacts$artifacts <- list()
baa_artifacts$warnings <- list()

`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

baa_output_dir <- Sys.getenv("BAA_OUTPUT_DIR", unset = getwd())
dir.create(baa_output_dir, recursive = TRUE, showWarnings = FALSE)

baa_sanitize_name <- function(name) {
  clean <- gsub("[^A-Za-z0-9_\\-]+", "_", as.character(name))
  clean <- gsub("_+", "_", clean)
  clean <- gsub("^_+|_+$", "", clean)
  if (!nzchar(clean)) {
    clean <- "artifact"
  }
  clean
}

baa_artifact_path <- function(name, ext) {
  clean_ext <- sub("^\\.", "", as.character(ext))
  file.path(baa_output_dir, paste0(baa_sanitize_name(name), ".", clean_ext))
}

baa_register_artifact <- function(group, artifact) {
  items <- baa_artifacts[[group]]
  items[[length(items) + 1]] <- artifact
  baa_artifacts[[group]] <- items
  invisible(artifact)
}

baa_record_warning <- function(message) {
  items <- baa_artifacts$warnings
  items[[length(items) + 1]] <- as.character(message)
  baa_artifacts$warnings <- items
  invisible(message)
}

baa_normalize_path <- function(path) {
  normalizePath(path, winslash = "/", mustWork = FALSE)
}

baa_scientific_adapter_registry <- function() {
  list(
    csv = list(
      adapter_id = "tabular_readr",
      required_packages = "readr",
      required_functions = "readr::read_csv",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    tsv = list(
      adapter_id = "tabular_readr",
      required_packages = "readr",
      required_functions = "readr::read_tsv",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    txt = list(
      adapter_id = "tabular_readr_delimited",
      required_packages = "readr",
      required_functions = "readr::read_delim",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    xlsx = list(
      adapter_id = "spreadsheet_readxl",
      required_packages = "readxl",
      required_functions = "readxl::read_excel",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    xls = list(
      adapter_id = "spreadsheet_readxl",
      required_packages = "readxl",
      required_functions = "readxl::read_excel",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    sav = list(
      adapter_id = "labelled_haven",
      required_packages = "haven",
      required_functions = "haven::read_sav",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    dta = list(
      adapter_id = "labelled_haven",
      required_packages = "haven",
      required_functions = "haven::read_dta",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    rds = list(
      adapter_id = "rds_base",
      required_packages = "base",
      required_functions = "base::readRDS",
      essential_when_matched = TRUE,
      object_family = "native_r_object"
    ),
    parquet = list(
      adapter_id = "parquet_arrow",
      required_packages = "arrow",
      required_functions = "arrow::read_parquet",
      essential_when_matched = TRUE,
      object_family = "tabular"
    ),
    rdata = list(
      adapter_id = "rdata_base",
      required_packages = "base",
      required_functions = "base::load",
      essential_when_matched = TRUE,
      object_family = "native_r_object"
    ),
    mtx = list(
      adapter_id = "matrix_market_10x",
      required_packages = "Matrix",
      required_functions = "Matrix::readMM",
      essential_when_matched = TRUE,
      object_family = "sparse_matrix"
    ),
    h5ad = list(
      adapter_id = "h5ad_structure_router",
      legacy_adapter_id = "h5ad_anndatar",
      required_packages = "hdf5r",
      required_functions = "hdf5r::h5file",
      materialization_packages = c("anndataR", "rhdf5", "SingleCellExperiment"),
      materialization_functions = "anndataR::read_h5ad",
      essential_when_matched = TRUE,
      object_family = "single_cell_experiment"
    ),
    h5 = list(
      adapter_id = "hdf5_structure_router",
      required_packages = "hdf5r",
      required_functions = "hdf5r::h5file",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    generic_hdf5 = list(
      adapter_id = "generic_hdf5_handle",
      required_packages = "hdf5r",
      required_functions = "hdf5r::h5file",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    `10x_h5` = list(
      adapter_id = "tenx_h5_seurat",
      required_packages = c("hdf5r", "Seurat"),
      required_functions = c("hdf5r::h5file", "Seurat::Read10X_h5"),
      essential_when_matched = TRUE,
      object_family = "sparse_matrix"
    ),
    loom = list(
      adapter_id = "loom_hdf5_handle",
      required_packages = "hdf5r",
      required_functions = "hdf5r::h5file",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    h5seurat = list(
      adapter_id = "h5seurat_structure_handle",
      required_packages = "hdf5r",
      required_functions = "hdf5r::h5file",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    fcs = list(
      adapter_id = "fcs_flowcore",
      required_packages = "flowCore",
      required_functions = "flowCore::read.FCS",
      essential_when_matched = TRUE,
      object_family = "flow_frame"
    ),
    vcf = list(
      adapter_id = "vcf_indexed_handle",
      legacy_adapter_id = "vcf_variantannotation",
      required_packages = "Rsamtools",
      required_functions = "Rsamtools::TabixFile",
      essential_when_matched = TRUE,
      object_family = "variant_annotation"
    ),
    bcf = list(
      adapter_id = "bcf_rsamtools",
      required_packages = "Rsamtools",
      required_functions = c(
        "Rsamtools::BcfFile", "Rsamtools::scanBcfHeader", "Rsamtools::scanBcf"
      ),
      essential_when_matched = TRUE,
      object_family = "variant_annotation"
    ),
    bed = list(
      adapter_id = "genomic_ranges_rtracklayer",
      required_packages = "rtracklayer",
      required_functions = "rtracklayer::import",
      essential_when_matched = TRUE,
      object_family = "genomic_ranges"
    ),
    narrowpeak = list(
      adapter_id = "genomic_ranges_rtracklayer",
      required_packages = "rtracklayer",
      required_functions = "rtracklayer::import",
      essential_when_matched = TRUE,
      object_family = "genomic_ranges"
    ),
    gff = list(
      adapter_id = "genomic_ranges_rtracklayer",
      required_packages = "rtracklayer",
      required_functions = "rtracklayer::import",
      essential_when_matched = TRUE,
      object_family = "genomic_ranges"
    ),
    gtf = list(
      adapter_id = "genomic_ranges_rtracklayer",
      required_packages = "rtracklayer",
      required_functions = "rtracklayer::import",
      essential_when_matched = TRUE,
      object_family = "genomic_ranges"
    ),
    biom = list(
      adapter_id = "biom_biomformat",
      required_packages = "biomformat",
      required_functions = "biomformat::read_biom",
      essential_when_matched = TRUE,
      object_family = "biom"
    ),
    mzml = list(
      adapter_id = "mass_spectrometry_msnbase",
      required_packages = "MSnbase",
      required_functions = "MSnbase::readMSData",
      essential_when_matched = TRUE,
      object_family = "mass_spectrometry"
    ),
    mzxml = list(
      adapter_id = "mass_spectrometry_msnbase",
      required_packages = "MSnbase",
      required_functions = "MSnbase::readMSData",
      essential_when_matched = TRUE,
      object_family = "mass_spectrometry"
    ),
    bam = list(
      adapter_id = "bam_rsamtools_handle",
      legacy_adapter_id = "bam_genomicalignments",
      required_packages = "Rsamtools",
      required_functions = "Rsamtools::BamFile",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    fastq = list(
      adapter_id = "fastq_shortread_stream",
      required_packages = "ShortRead",
      required_functions = "ShortRead::FastqStreamer",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    fasta = list(
      adapter_id = "fasta_biostrings_bounded",
      required_packages = "Biostrings",
      required_functions = "Biostrings::readDNAStringSet",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    sam = list(
      adapter_id = "sam_raw_handle",
      required_packages = "base",
      required_functions = "base::readLines",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    cram = list(
      adapter_id = "cram_rsamtools_handle",
      required_packages = "Rsamtools",
      required_functions = "Rsamtools::BamFile",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    bigwig = list(
      adapter_id = "bigwig_rtracklayer_handle",
      required_packages = "rtracklayer",
      required_functions = "rtracklayer::BigWigFile",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    maf = list(
      adapter_id = "maf_bounded_raw",
      required_packages = "readr",
      required_functions = "readr::read_tsv",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    newick = list(
      adapter_id = "newick_ape",
      required_packages = "ape",
      required_functions = "ape::read.tree",
      essential_when_matched = TRUE,
      object_family = "phylogenetic_tree"
    ),
    qza = list(
      adapter_id = "qza_archive_handle",
      required_packages = "base",
      required_functions = "base::file",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    ),
    plink_bed = list(
      adapter_id = "plink_bedmatrix",
      required_packages = "BEDMatrix",
      required_functions = "BEDMatrix::BEDMatrix",
      essential_when_matched = TRUE,
      object_family = "scientific_handle"
    )
  )
}

baa_scalar_text <- function(value, default = "") {
  if (is.null(value) || length(value) == 0 || is.na(value[[1]])) {
    return(default)
  }
  as.character(value[[1]])
}

baa_normalize_load_profile <- function(load_profile = NULL) {
  configured <- baa_scalar_text(
    load_profile,
    baa_scalar_text(getOption("baa.dataset_load_profile"), "analysis")
  )
  profile <- tolower(trimws(configured))
  if (!profile %in% c("analysis", "inspection")) {
    stop("Scientific dataset load_profile must be 'analysis' or 'inspection'.", call. = FALSE)
  }
  profile
}

baa_infer_scientific_format <- function(path) {
  lowered <- tolower(baa_scalar_text(path))
  suffixes <- c(
    ".h5seurat" = "h5seurat",
    ".narrowpeak.gz" = "narrowpeak",
    ".matrix.mtx.gz" = "mtx",
    ".fastq.gz" = "fastq",
    ".fasta.gz" = "fasta",
    ".csv.gz" = "csv",
    ".tsv.gz" = "tsv",
    ".txt.gz" = "txt",
    ".fq.gz" = "fastq",
    ".fa.gz" = "fasta",
    ".fna.gz" = "fasta",
    ".mzxml.gz" = "mzxml",
    ".mzml.gz" = "mzml",
    ".vcf.bgz" = "vcf",
    ".vcf.gz" = "vcf",
    ".gff3.gz" = "gff",
    ".gff.gz" = "gff",
    ".gtf.gz" = "gtf",
    ".bed.gz" = "bed",
    ".mtx.gz" = "mtx",
    ".narrowpeak" = "narrowpeak",
    ".rdata" = "rdata",
    ".parquet" = "parquet",
    ".bigwig" = "bigwig",
    ".newick" = "newick",
    ".mzxml" = "mzxml",
    ".hdf5" = "generic_hdf5",
    ".h5ad" = "h5ad",
    ".loom" = "loom",
    ".fastq" = "fastq",
    ".fasta" = "fasta",
    ".mzml" = "mzml",
    ".xlsx" = "xlsx",
    ".gff3" = "gff",
    ".biom" = "biom",
    ".csv" = "csv",
    ".tsv" = "tsv",
    ".txt" = "txt",
    ".cram" = "cram",
    ".sam" = "sam",
    ".maf" = "maf",
    ".nwk" = "newick",
    ".tree" = "newick",
    ".qza" = "qza",
    ".crai" = "crai",
    ".bai" = "bai",
    ".tbi" = "tbi",
    ".csi" = "csi",
    ".bim" = "bim",
    ".fam" = "fam",
    ".bw" = "bigwig",
    ".fq" = "fastq",
    ".fa" = "fasta",
    ".fna" = "fasta",
    ".xls" = "xls",
    ".sav" = "sav",
    ".dta" = "dta",
    ".rds" = "rds",
    ".rda" = "rdata",
    ".mtx" = "mtx",
    ".h5" = "h5",
    ".fcs" = "fcs",
    ".vcf" = "vcf",
    ".bcf" = "bcf",
    ".bed" = "bed",
    ".gff" = "gff",
    ".gtf" = "gtf",
    ".bam" = "bam"
  )
  for (suffix in names(suffixes)) {
    if (endsWith(lowered, suffix)) {
      return(unname(suffixes[[suffix]]))
    }
  }
  if (dir.exists(path)) {
    members <- tolower(list.files(path, recursive = FALSE))
    if (any(grepl("^matrix\\.mtx(\\.gz)?$", members))) {
      return("mtx")
    }
  }
  ""
}

baa_normalize_scientific_format <- function(format, path = "") {
  fmt <- tolower(trimws(baa_scalar_text(format)))
  fmt <- sub("^\\.", "", fmt)
  aliases <- c(
    rda = "rdata",
    matrixmarket = "mtx",
    matrix_market = "mtx",
    `matrix-market` = "mtx",
    `10x` = "mtx",
    `10x_mtx` = "mtx",
    hdf5 = "generic_hdf5",
    auto_hdf5 = "h5",
    `10x_h5` = "10x_h5",
    fq = "fastq",
    fa = "fasta",
    fna = "fasta",
    bw = "bigwig",
    nwk = "newick",
    tree = "newick",
    plink = "plink_bed",
    bam_index = "bai",
    cram_index = "crai",
    plink_bim = "bim",
    plink_fam = "fam",
    `csv.gz` = "csv",
    `tsv.gz` = "tsv",
    `txt.gz` = "txt",
    gff3 = "gff"
  )
  if (nzchar(fmt) && fmt %in% names(aliases)) {
    fmt <- unname(aliases[[fmt]])
  }
  if (identical(fmt, "vcf_index")) {
    fmt <- if (endsWith(tolower(path), ".csi")) "csi" else "tbi"
  }
  if (!nzchar(fmt)) {
    fmt <- baa_infer_scientific_format(path)
  }
  fmt
}

baa_require_adapter_package <- function(package, adapter_id) {
  if (identical(package, "base") || requireNamespace(package, quietly = TRUE)) {
    return(invisible(TRUE))
  }
  stop(
    paste0(
      "Scientific data adapter '", adapter_id,
      "' requires R package '", package,
      "', but it is not installed."
    ),
    call. = FALSE
  )
}

baa_require_adapter_function <- function(reference, adapter_id) {
  parts <- strsplit(reference, "::", fixed = TRUE)[[1]]
  if (length(parts) != 2 || !all(nzchar(parts))) {
    stop(paste("Invalid scientific adapter function reference:", reference), call. = FALSE)
  }
  package <- parts[[1]]
  function_name <- parts[[2]]
  baa_require_adapter_package(package, adapter_id)
  namespace <- if (identical(package, "base")) baseenv() else asNamespace(package)
  if (!exists(function_name, envir = namespace, mode = "function", inherits = FALSE)) {
    stop(
      paste0(
        "Scientific data adapter '", adapter_id,
        "' requires function '", reference,
        "', but it is unavailable."
      ),
      call. = FALSE
    )
  }
  invisible(TRUE)
}

baa_check_adapter_dependencies <- function(spec) {
  for (package in spec$required_packages) {
    baa_require_adapter_package(package, spec$adapter_id)
  }
  for (reference in spec$required_functions) {
    baa_require_adapter_function(reference, spec$adapter_id)
  }
  invisible(TRUE)
}

baa_check_adapter_materialization_dependencies <- function(spec) {
  for (package in spec$materialization_packages %||% character()) {
    baa_require_adapter_package(package, spec$adapter_id)
  }
  for (reference in spec$materialization_functions %||% character()) {
    baa_require_adapter_function(reference, spec$adapter_id)
  }
  invisible(TRUE)
}

baa_scientific_adapter_error <- function(adapter_id, asset_name, expression) {
  tryCatch(
    force(expression),
    error = function(e) {
      message <- conditionMessage(e)
      if (startsWith(message, "Scientific data adapter '") ||
          startsWith(message, "Scientific data asset")) {
        stop(message, call. = FALSE)
      }
      stop(
        paste0(
          "Scientific data adapter '", adapter_id,
          "' failed for '", asset_name, "': ", message
        ),
        call. = FALSE
      )
    }
  )
}

baa_tag_dataset <- function(dataset, adapter_id) {
  attr(dataset, "baa_adapter_id") <- as.character(adapter_id)
  dataset
}

baa_dataset_adapter_id <- function(dataset, fallback = "") {
  if (inherits(dataset, "baa_scientific_handle")) {
    return(baa_scalar_text(dataset$adapter_id, fallback))
  }
  if (baa_is_lazy_dataset(dataset)) {
    return(baa_scalar_text(dataset$adapter_id, fallback))
  }
  baa_scalar_text(attr(dataset, "baa_adapter_id", exact = TRUE), fallback)
}

baa_scientific_asset_summary <- function(asset) {
  list(
    path = baa_scalar_text(asset$path),
    name = baa_scalar_text(asset$name, basename(baa_scalar_text(asset$path))),
    format = baa_scalar_text(asset$format),
    role = baa_scalar_text(asset$role),
    kind = baa_scalar_text(asset$kind)
  )
}

baa_new_scientific_handle <- function(
  format,
  adapter_id,
  modality,
  assets,
  structure = list(),
  axes = list(),
  profile_data = data.frame(),
  processing_stage = "raw",
  upstream_required = character(),
  capabilities = character(),
  native = NULL
) {
  if (!is.data.frame(profile_data)) {
    stop("Scientific handle profile_data must be a bounded data.frame.", call. = FALSE)
  }
  if (nrow(profile_data) > 256L) {
    stop("Scientific handle profile_data must contain at most 256 rows.", call. = FALSE)
  }
  value <- list(
    format = as.character(format),
    adapter_id = as.character(adapter_id),
    modality = as.character(modality),
    assets = lapply(assets, baa_scientific_asset_summary),
    structure = structure,
    axes = axes,
    profile_data = profile_data,
    processing_stage = as.character(processing_stage),
    upstream_required = as.character(upstream_required),
    capabilities = as.character(capabilities),
    native = native
  )
  format_class <- paste0("baa_", gsub("[^a-z0-9]+", "_", tolower(format)), "_handle")
  class(value) <- unique(c(format_class, "baa_scientific_handle", "list"))
  value
}

baa_is_scientific_handle <- function(dataset) {
  inherits(dataset, "baa_scientific_handle")
}

baa_scientific_native <- function(dataset) {
  if (!baa_is_scientific_handle(dataset)) {
    stop("Expected a baa_scientific_handle.", call. = FALSE)
  }
  if (is.null(dataset$native)) {
    requirement <- paste(dataset$upstream_required, collapse = "; ")
    stop(
      paste0(
        "Scientific dataset is not analysis-ready.",
        if (nzchar(requirement)) paste0(" Upstream processing required: ", requirement, ".") else ""
      ),
      call. = FALSE
    )
  }
  dataset$native
}

print.baa_scientific_handle <- function(x, ...) {
  cat(
    "<baa_scientific_handle>", x$format, "via", x$adapter_id, "\n",
    "Modality:", x$modality, "\n",
    "Processing stage:", x$processing_stage, "\n"
  )
  if (length(x$upstream_required)) {
    cat("Upstream required:", paste(x$upstream_required, collapse = "; "), "\n")
  }
  invisible(x)
}

as.data.frame.baa_scientific_handle <- function(x, ...) {
  stop(
    "A baa_scientific_handle is not a tabular dataset; inspect its bounded profile_data or complete the declared upstream processing.",
    call. = FALSE
  )
}

baa_require_any_adapter_package <- function(packages, adapter_id) {
  installed <- packages[vapply(packages, requireNamespace, logical(1), quietly = TRUE)]
  if (length(installed)) return(installed[[1]])
  stop(
    paste0(
      "Scientific data adapter '", adapter_id, "' requires one of R packages: ",
      paste(packages, collapse = ", "), "."
    ),
    call. = FALSE
  )
}

baa_hdf5_bounded_structure <- function(path, max_nodes = 256L) {
  baa_require_adapter_package("hdf5r", "hdf5_structure_router")
  limit <- as.integer(max(1L, min(as.integer(max_nodes), 256L)))
  handle <- hdf5r::h5file(path, mode = "r")
  on.exit(handle$close_all(), add = TRUE)
  queue <- ""
  rows <- list()
  while (length(queue) && length(rows) < limit) {
    current_path <- queue[[1]]
    queue <- queue[-1]
    group <- if (nzchar(current_path)) {
      tryCatch(handle[[current_path]], error = function(e) NULL)
    } else {
      handle
    }
    if (is.null(group) || !inherits(group, c("H5Group", "H5File"))) next
    group_info <- tryCatch(group$group_info(), error = function(e) NULL)
    child_count <- if (is.null(group_info)) 0L else as.integer(group_info$nlinks[[1]])
    visit_count <- min(child_count, limit - length(rows))
    if (visit_count < 1L) {
      if (!identical(group, handle)) try(group$close(), silent = TRUE)
      next
    }
    for (index in seq_len(visit_count) - 1L) {
      name <- tryCatch(as.character(group$link_name_by_idx(index)), error = function(e) "")
      if (!nzchar(name)) next
      object_path <- if (nzchar(current_path)) paste(current_path, name, sep = "/") else name
      object_info <- tryCatch(group$obj_info_by_idx(index), error = function(e) NULL)
      type_code <- if (is.null(object_info)) NA_integer_ else as.integer(object_info$type[[1]])
      object_type <- if (identical(type_code, 0L)) {
        "H5I_GROUP"
      } else if (identical(type_code, 1L)) {
        "H5I_DATASET"
      } else {
        "H5I_OTHER"
      }
      dimensions <- ""
      if (identical(type_code, 1L)) {
        dataset_handle <- tryCatch(group$open_by_idx(index), error = function(e) NULL)
        if (!is.null(dataset_handle)) {
          dimensions <- paste(as.integer(dataset_handle$dims), collapse = " x ")
          try(dataset_handle$close(), silent = TRUE)
        }
      }
      rows[[length(rows) + 1L]] <- data.frame(
        path = object_path,
        object_type = object_type,
        dimensions = dimensions,
        stringsAsFactors = FALSE
      )
      if (identical(type_code, 0L) && length(rows) < limit) queue <- c(queue, object_path)
    }
    if (!identical(group, handle)) try(group$close(), silent = TRUE)
  }
  if (!length(rows)) {
    return(data.frame(path = character(), object_type = character(), dimensions = character()))
  }
  do.call(rbind, rows)
}

baa_classify_hdf5 <- function(path) {
  baa_require_adapter_package("hdf5r", "hdf5_structure_router")
  handle <- hdf5r::h5file(path, mode = "r")
  on.exit(handle$close_all(), add = TRUE)
  path_exists <- function(candidate) {
    tryCatch(isTRUE(handle$exists(candidate)), error = function(e) FALSE)
  }
  has_all <- function(paths) all(vapply(paths, path_exists, logical(1)))
  if (has_all(c(
    "matrix/data", "matrix/indices", "matrix/indptr", "matrix/shape",
    "matrix/barcodes", "matrix/features/id", "matrix/features/name"
  ))) return("10x_h5")
  # AnnData permits X to be absent when assays are stored only in /layers.
  # obs + var plus either representation is therefore a valid H5AD shape.
  if (has_all(c("obs", "var")) && any(vapply(c("X", "layers"), path_exists, logical(1)))) {
    return("h5ad")
  }
  if (has_all(c("matrix", "row_attrs", "col_attrs"))) return("loom")
  if (has_all(c("assays", "meta.data"))) return("h5seurat")
  "generic_hdf5"
}

baa_validate_10x_dataset <- function(dataset, adapter_id = "tenx_h5_seurat") {
  if (is.matrix(dataset) || inherits(dataset, "Matrix")) {
    return(baa_tag_dataset(dataset, adapter_id))
  }
  if (!is.list(dataset) || !length(dataset) || is.null(names(dataset))) {
    stop("10x HDF5 loader did not return a matrix or named multimodal matrix list.", call. = FALSE)
  }
  valid <- vapply(dataset, function(value) is.matrix(value) || inherits(value, "Matrix"), logical(1))
  if (!all(valid)) stop("10x multimodal result contains a non-matrix assay.", call. = FALSE)
  column_counts <- vapply(dataset, ncol, integer(1))
  if (length(unique(column_counts)) != 1L) {
    stop("10x multimodal assays do not share a common cell axis.", call. = FALSE)
  }
  cell_names <- lapply(dataset, colnames)
  if (length(cell_names) > 1L && !all(vapply(cell_names[-1], identical, logical(1), cell_names[[1]]))) {
    stop("10x multimodal assays do not share identical cell barcodes.", call. = FALSE)
  }
  class(dataset) <- unique(c("baa_10x_multimodal", class(dataset)))
  baa_tag_dataset(dataset, adapter_id)
}

baa_read_spatial_coordinates <- function(asset) {
  first_connection <- baa_open_text_connection(asset$path)
  first_line <- tryCatch(
    readLines(first_connection, n = 1L, warn = FALSE),
    finally = close(first_connection)
  )
  if (length(first_line) != 1L || !nzchar(first_line[[1]])) {
    stop("Space Ranger coordinate sidecar is empty.", call. = FALSE)
  }
  connection <- baa_open_text_connection(asset$path)
  on.exit(close(connection), add = TRUE)
  separator <- if (
    identical(asset$format, "tsv") || grepl("\\.tsv(\\.gz)?$", tolower(asset$path))
  ) "\t" else ","
  first_fields <- strsplit(first_line[[1]], separator, fixed = TRUE)[[1]]
  normalized_fields <- tolower(trimws(first_fields))
  has_header <- any(normalized_fields %in% c("barcode", "cell", "cell_id"))
  table <- tryCatch(
    utils::read.table(
      connection,
      header = has_header,
      sep = separator,
      quote = "\"",
      comment.char = "",
      stringsAsFactors = FALSE,
      check.names = FALSE,
      fill = FALSE
    ),
    error = function(e) {
      stop(
        paste0("Unable to parse Space Ranger coordinate sidecar: ", conditionMessage(e)),
        call. = FALSE
      )
    }
  )
  if (!nrow(table) || !ncol(table)) {
    stop("Space Ranger coordinate sidecar contains no coordinate records.", call. = FALSE)
  }
  barcode_names <- c("barcode", "Barcode", "cell", "Cell", "cell_id")
  barcode_column <- intersect(barcode_names, colnames(table))
  barcode_index <- if (length(barcode_column)) {
    match(barcode_column[[1]], colnames(table))
  } else if (!has_header) {
    1L
  } else {
    stop("Space Ranger coordinate sidecar has no barcode column.", call. = FALSE)
  }
  barcodes <- trimws(as.character(table[[barcode_index]]))
  if (any(is.na(barcodes) | !nzchar(barcodes))) {
    stop("Space Ranger coordinate sidecar contains an empty barcode.", call. = FALSE)
  }
  if (anyDuplicated(barcodes)) {
    stop("Space Ranger coordinate sidecar contains duplicate barcodes.", call. = FALSE)
  }
  list(table = table, barcodes = barcodes)
}

baa_attach_10x_bundle_context <- function(dataset, assets, count_barcodes = NULL) {
  contextual_roles <- c(
    "spatial_coordinates", "spatial_metadata", "spatial_image",
    "quality_metrics", "molecule_info", "sample_metadata", "feature_metadata"
  )
  contextual_assets <- Filter(function(asset) asset$role %in% contextual_roles, assets)
  if (!length(contextual_assets)) return(dataset)
  summaries <- lapply(contextual_assets, baa_scientific_asset_summary)
  coordinate_assets <- Filter(function(asset) identical(asset$role, "spatial_coordinates"), contextual_assets)
  if (length(coordinate_assets) > 1L) {
    stop("Scientific dataset manifest contains ambiguous spatial coordinate sidecars.", call. = FALSE)
  }
  coordinate_profile <- data.frame()
  if (length(coordinate_assets) == 1L) {
    parsed_coordinates <- baa_read_spatial_coordinates(coordinate_assets[[1]])
    coordinate_profile <- parsed_coordinates$table
    if (is.null(count_barcodes)) {
      count_barcodes <- if (inherits(dataset, "baa_10x_multimodal")) {
        colnames(dataset[[1]])
      } else {
        colnames(dataset)
      }
    }
    count_barcodes <- trimws(as.character(count_barcodes %||% character()))
    if (!length(count_barcodes) || any(is.na(count_barcodes) | !nzchar(count_barcodes))) {
      stop(
        "Cannot prove Space Ranger coordinate alignment because the 10x count barcode axis is unavailable.",
        call. = FALSE
      )
    }
    missing_barcodes <- setdiff(unique(count_barcodes), parsed_coordinates$barcodes)
    if (length(missing_barcodes)) {
      stop(
        paste0(
          "Space Ranger coordinate barcodes do not cover the 10x count matrix cell axis; missing ",
          length(missing_barcodes), " barcode(s)."
        ),
        call. = FALSE
      )
    }
  }
  attr(dataset, "baa_10x_bundle_context") <- list(
    assets = summaries,
    coordinate_profile = utils::head(coordinate_profile, 256L),
    profile_bounded = TRUE,
    coordinate_rows_exact = nrow(coordinate_profile),
    barcode_alignment_proven = length(coordinate_assets) == 1L
  )
  dataset
}

baa_hdf5_handle <- function(path, format, adapter_id, assets, upstream_required) {
  structure_data <- baa_hdf5_bounded_structure(path)
  baa_new_scientific_handle(
    format = format,
    adapter_id = adapter_id,
    modality = if (format %in% c("h5ad", "10x_h5", "loom", "h5seurat")) "single_cell" else "scientific_hdf5",
    assets = assets,
    structure = list(nodes_profiled = nrow(structure_data), structure_bounded = TRUE),
    axes = list(objects = nrow(structure_data)),
    profile_data = structure_data,
    processing_stage = if (identical(format, "10x_h5")) "counts" else "raw",
    upstream_required = upstream_required,
    capabilities = c("bounded_structure", "explicit_format", "native_hdf5"),
    native = NULL
  )
}

baa_read_10x_hdf5_barcodes <- function(path) {
  file_handle <- hdf5r::h5file(path, mode = "r")
  on.exit(file_handle$close_all(), add = TRUE)
  dataset_handle <- tryCatch(file_handle[["matrix/barcodes"]], error = function(e) NULL)
  if (is.null(dataset_handle)) {
    stop("10x HDF5 structure has no readable matrix/barcodes axis.", call. = FALSE)
  }
  on.exit(try(dataset_handle$close(), silent = TRUE), add = TRUE)
  barcodes <- tryCatch(
    as.character(dataset_handle$read()),
    error = function(e) {
      stop(paste0("Unable to read 10x HDF5 barcode axis: ", conditionMessage(e)), call. = FALSE)
    }
  )
  if (!length(barcodes) || any(is.na(barcodes) | !nzchar(trimws(barcodes)))) {
    stop("10x HDF5 barcode axis is empty or invalid.", call. = FALSE)
  }
  barcodes
}

baa_read_hdf5_asset <- function(asset, assets, declared_format, load_profile = "analysis") {
  load_profile <- baa_normalize_load_profile(load_profile)
  detected_format <- baa_classify_hdf5(asset$path)
  requested_format <- if (identical(declared_format, "h5")) detected_format else declared_format
  if (!identical(requested_format, detected_format)) {
    stop(
      paste0(
        "Declared HDF5 format '", requested_format, "' does not match detected structure '",
        detected_format, "'."
      ),
      call. = FALSE
    )
  }
  if (identical(load_profile, "inspection")) {
    adapter_id <- switch(
      detected_format,
      `10x_h5` = "tenx_h5_structure_handle",
      h5ad = "h5ad_structure_handle",
      loom = "loom_hdf5_handle",
      h5seurat = "h5seurat_structure_handle",
      "generic_hdf5_handle"
    )
    upstream <- switch(
      detected_format,
      `10x_h5` = "reload with load_profile='analysis' to materialize the 10x count matrices",
      h5ad = "reload with load_profile='analysis' and anndataR/rhdf5 to materialize a SingleCellExperiment",
      loom = "use a Loom-aware importer after confirming assay and annotation conventions",
      h5seurat = "reload with load_profile='analysis' and SeuratDisk to materialize the Seurat object",
      "select a format-specific HDF5 adapter after reviewing the bounded structure"
    )
    handle <- baa_hdf5_handle(asset$path, detected_format, adapter_id, assets, upstream)
    if (identical(detected_format, "10x_h5")) {
      has_spatial_coordinates <- any(vapply(
        assets,
        function(candidate) identical(candidate$role, "spatial_coordinates"),
        logical(1)
      ))
      count_barcodes <- if (has_spatial_coordinates) {
        baa_read_10x_hdf5_barcodes(asset$path)
      } else NULL
      return(baa_attach_10x_bundle_context(
        handle,
        assets,
        count_barcodes = count_barcodes
      ))
    }
    return(handle)
  }
  if (identical(detected_format, "10x_h5")) {
    baa_require_adapter_package("Seurat", "tenx_h5_seurat")
    value <- Seurat::Read10X_h5(asset$path, use.names = TRUE, unique.features = TRUE)
    return(baa_attach_10x_bundle_context(
      baa_validate_10x_dataset(value, "tenx_h5_seurat"), assets
    ))
  }
  if (identical(detected_format, "h5ad")) {
    baa_check_adapter_materialization_dependencies(
      baa_scientific_adapter_registry()$h5ad
    )
    return(baa_tag_dataset(
      anndataR::read_h5ad(asset$path, as = "SingleCellExperiment"),
      "h5ad_anndatar"
    ))
  }
  if (identical(detected_format, "h5seurat") && requireNamespace("SeuratDisk", quietly = TRUE)) {
    return(baa_tag_dataset(SeuratDisk::LoadH5Seurat(asset$path), "h5seurat_seuratdisk"))
  }
  adapter_id <- switch(
    detected_format,
    h5ad = "h5ad_structure_handle",
    loom = "loom_hdf5_handle",
    h5seurat = "h5seurat_structure_handle",
    "generic_hdf5_handle"
  )
  upstream <- switch(
    detected_format,
    h5ad = "install/use anndataR with rhdf5 to materialize an analysis-ready SingleCellExperiment",
    loom = "use a Loom-aware importer after confirming assay and annotation conventions",
    h5seurat = "install/use SeuratDisk to materialize the Seurat object",
    "select a format-specific HDF5 adapter after reviewing the bounded structure"
  )
  baa_hdf5_handle(asset$path, detected_format, adapter_id, assets, upstream)
}

baa_manifest_asset_rows <- function(raw_assets) {
  if (is.null(raw_assets) || length(raw_assets) == 0) {
    return(list())
  }
  if (is.data.frame(raw_assets)) {
    return(lapply(seq_len(nrow(raw_assets)), function(index) {
      lapply(raw_assets, function(column) column[[index]])
    }))
  }
  if (!is.list(raw_assets)) {
    stop("Scientific dataset manifest assets must be a list.", call. = FALSE)
  }
  if (!is.null(raw_assets$path)) {
    nested <- baa_manifest_asset_rows(raw_assets$assets %||% raw_assets$children)
    raw_assets$assets <- NULL
    raw_assets$children <- NULL
    return(c(list(raw_assets), nested))
  }
  nested_container <- raw_assets$assets %||% raw_assets$children
  if (!is.null(nested_container)) {
    return(baa_manifest_asset_rows(nested_container))
  }
  rows <- lapply(raw_assets, baa_manifest_asset_rows)
  unlist(rows, recursive = FALSE, use.names = FALSE)
}

baa_normalize_scientific_asset <- function(asset, index, fallback_format = "") {
  if (!is.list(asset)) {
    stop(paste0("Scientific dataset asset ", index, " must be an object."), call. = FALSE)
  }
  path <- baa_scalar_text(asset$path)
  if (!nzchar(path)) {
    stop(paste0("Scientific data asset ", index, " path is required."), call. = FALSE)
  }
  if (!file.exists(path) && !dir.exists(path)) {
    stop(
      paste0("Scientific data asset does not exist: ", baa_scalar_text(asset$name, basename(path))),
      call. = FALSE
    )
  }
  actual_kind <- if (dir.exists(path)) "directory" else "file"
  kind <- tolower(trimws(baa_scalar_text(asset$kind, actual_kind)))
  if (!kind %in% c("file", "directory")) {
    stop(paste0("Scientific data asset ", index, " kind must be file or directory."), call. = FALSE)
  }
  if (!identical(kind, actual_kind)) {
    stop(
      paste0("Scientific data asset kind mismatch for: ", baa_scalar_text(asset$name, basename(path))),
      call. = FALSE
    )
  }
  format <- baa_normalize_scientific_format(asset$format %||% fallback_format, path)
  if (!nzchar(format)) {
    stop(
      paste0("No scientific data adapter is registered for: ", baa_scalar_text(asset$name, basename(path))),
      call. = FALSE
    )
  }
  list(
    path = path,
    format = format,
    role = tolower(trimws(baa_scalar_text(asset$role, if (index == 1) "primary" else "auxiliary"))),
    kind = kind,
    name = baa_scalar_text(asset$name, basename(path))
  )
}

baa_scientific_manifest_assets <- function(manifest) {
  if (!is.list(manifest)) {
    stop("Scientific dataset manifest must be an object.", call. = FALSE)
  }
  raw_assets <- baa_manifest_asset_rows(manifest$assets)
  if (!length(raw_assets)) {
    raw_assets <- list(list(
      path = manifest$path,
      format = manifest$format,
      role = "primary",
      kind = manifest$kind,
      name = manifest$name
    ))
  }
  fallback_format <- baa_scalar_text(manifest$format)
  lapply(seq_along(raw_assets), function(index) {
    raw_asset <- raw_assets[[index]]
    role <- tolower(trimws(baa_scalar_text(raw_asset$role, if (index == 1) "primary" else "auxiliary")))
    asset_fallback <- if (identical(role, "primary") || index == 1) fallback_format else ""
    baa_normalize_scientific_asset(raw_asset, index, asset_fallback)
  })
}

baa_primary_scientific_asset <- function(assets) {
  primary_indexes <- which(vapply(
    assets,
    function(asset) identical(asset$role, "primary"),
    logical(1)
  ))
  if (length(primary_indexes) > 1) {
    stop("Scientific dataset manifest has more than one primary asset.", call. = FALSE)
  }
  assets[[if (length(primary_indexes)) primary_indexes[[1]] else 1]]
}

baa_open_text_connection <- function(path) {
  if (endsWith(tolower(path), ".gz")) {
    return(gzfile(path, open = "rt"))
  }
  file(path, open = "rt")
}

baa_read_delimited_sidecar <- function(path) {
  connection <- baa_open_text_connection(path)
  on.exit(close(connection), add = TRUE)
  utils::read.delim(
    connection,
    header = FALSE,
    stringsAsFactors = FALSE,
    check.names = FALSE,
    quote = "",
    comment.char = ""
  )
}

baa_count_delimited_sidecar_rows <- function(path, chunk_size = 4096L) {
  connection <- baa_open_text_connection(path)
  on.exit(close(connection), add = TRUE)
  row_count <- 0
  repeat {
    lines <- readLines(connection, n = as.integer(chunk_size), warn = FALSE)
    if (!length(lines)) break
    if (any(nchar(lines, type = "bytes") > 65536L)) {
      stop("10x sidecar record exceeds the bounded inspection line limit.", call. = FALSE)
    }
    row_count <- row_count + sum(nzchar(lines))
  }
  as.numeric(row_count)
}

baa_find_bundle_asset <- function(assets, roles, basenames) {
  role_matches <- which(vapply(
    assets,
    function(asset) asset$role %in% roles,
    logical(1)
  ))
  if (length(role_matches)) {
    return(assets[[role_matches[[1]]]])
  }
  basename_matches <- which(vapply(assets, function(asset) {
    candidates <- tolower(c(asset$name, basename(asset$path)))
    any(candidates %in% tolower(basenames))
  }, logical(1)))
  if (length(basename_matches)) assets[[basename_matches[[1]]]] else NULL
}

baa_find_bundle_assets <- function(assets, formats = character(), roles = character()) {
  Filter(function(asset) {
    format_match <- !length(formats) || asset$format %in% formats
    role_match <- !length(roles) || asset$role %in% roles
    format_match && role_match
  }, assets)
}

baa_matching_sidecar <- function(primary_asset, sidecar_asset, sidecar_format) {
  primary_name <- tolower(basename(primary_asset$path))
  sidecar_name <- tolower(basename(sidecar_asset$path))
  primary_stem <- primary_name
  for (suffix in c(".gz", ".bgz", ".bam", ".cram", ".vcf", ".bcf")) {
    if (endsWith(primary_stem, suffix)) {
      primary_stem <- substr(primary_stem, 1L, nchar(primary_stem) - nchar(suffix))
    }
  }
  accepted <- switch(
    sidecar_format,
    bai = unique(c(paste0(primary_name, ".bai"), paste0(primary_stem, ".bai"))),
    crai = unique(c(paste0(primary_name, ".crai"), paste0(primary_stem, ".crai"))),
    tbi = unique(c(paste0(primary_name, ".tbi"), paste0(primary_stem, ".tbi"))),
    csi = unique(c(paste0(primary_name, ".csi"), paste0(primary_stem, ".csi"))),
    character()
  )
  sidecar_name %in% accepted
}

baa_validate_index_asset <- function(primary_asset, assets, formats) {
  indexes <- baa_find_bundle_assets(assets, formats = formats)
  if (!length(indexes)) return(NULL)
  matching <- Filter(
    function(asset) baa_matching_sidecar(primary_asset, asset, asset$format),
    indexes
  )
  if (!length(matching)) {
    stop(
      paste0(
        "Scientific index asset does not match primary asset '",
        basename(primary_asset$path), "'."
      ),
      call. = FALSE
    )
  }
  if (length(matching) > 1L) {
    stop("Scientific dataset manifest contains ambiguous matching index assets.", call. = FALSE)
  }
  matching[[1]]
}

baa_bounded_directory_files <- function(path, max_entries = 4096L, max_depth = 4L) {
  root <- normalizePath(path, winslash = "/", mustWork = TRUE)
  max_entries <- as.integer(max_entries)
  max_depth <- as.integer(max_depth)
  if (max_entries < 1L || max_depth < 0L) {
    stop("Scientific directory scan bounds are invalid.", call. = FALSE)
  }
  queue <- list(list(path = root, depth = 0L))
  visited <- root
  files <- character()
  entries_seen <- 0L
  while (length(queue)) {
    current <- queue[[1]]
    queue <- queue[-1]
    entries <- list.files(
      current$path,
      full.names = TRUE,
      recursive = FALSE,
      all.files = TRUE,
      no.. = TRUE
    )
    entries_seen <- entries_seen + length(entries)
    if (entries_seen > max_entries) {
      stop(
        paste0(
          "Scientific directory contains more than ", max_entries,
          " entries; narrow the imported bundle before inspection."
        ),
        call. = FALSE
      )
    }
    if (!length(entries)) next
    if (any(nzchar(Sys.readlink(entries)))) {
      stop("Scientific directory bundles must not contain symbolic links.", call. = FALSE)
    }
    info <- file.info(entries)
    directories <- entries[!is.na(info$isdir) & info$isdir]
    files <- c(files, entries[!is.na(info$isdir) & !info$isdir])
    if (length(directories)) {
      if (current$depth >= max_depth) {
        stop(
          paste0(
            "Scientific directory exceeds maximum inspection depth ", max_depth,
            "; narrow the imported bundle before inspection."
          ),
          call. = FALSE
        )
      }
      normalized_directories <- normalizePath(
        directories,
        winslash = "/",
        mustWork = TRUE
      )
      if (any(normalized_directories %in% visited)) {
        stop("Scientific directory bundle contains a directory cycle.", call. = FALSE)
      }
      visited <- c(visited, normalized_directories)
      queue <- c(
        queue,
        lapply(normalized_directories, function(directory) {
          list(path = directory, depth = current$depth + 1L)
        })
      )
    }
  }
  files
}

baa_10x_directory_assets <- function(directory_asset) {
  paths <- baa_bounded_directory_files(directory_asset$path)
  matrix_matches <- paths[grepl("^matrix\\.mtx(\\.gz)?$", tolower(basename(paths)))]
  if (length(matrix_matches) > 1L) {
    filtered <- matrix_matches[grepl("filtered_feature_bc_matrix", tolower(dirname(matrix_matches)), fixed = TRUE)]
    if (length(filtered) == 1L) matrix_matches <- filtered
  }
  if (length(matrix_matches) != 1L) {
    stop(
      "Scientific data adapter 'matrix_market_10x' requires one unambiguous matrix.mtx(.gz), preferring Space Ranger filtered_feature_bc_matrix.",
      call. = FALSE
    )
  }
  matrix_directory <- dirname(matrix_matches[[1]])
  companion_paths <- paths[dirname(paths) == matrix_directory]
  companion_names <- tolower(basename(companion_paths))
  select_one <- function(pattern, role, required = FALSE) {
    matches <- companion_paths[grepl(pattern, companion_names)]
    if (length(matches) > 1L) {
      stop(paste0("Scientific data adapter 'matrix_market_10x' found ambiguous ", role, " files."), call. = FALSE)
    }
    if (!length(matches)) {
      if (required) stop(paste0("Scientific data adapter 'matrix_market_10x' requires ", role, "."), call. = FALSE)
      return(NULL)
    }
    list(
      path = matches[[1]],
      format = if (identical(role, "primary")) "mtx" else "tsv",
      role = role,
      kind = "file",
      name = basename(matches[[1]])
    )
  }
  select_global <- function(pattern, format, role, multiple = FALSE) {
    matches <- paths[grepl(pattern, tolower(basename(paths)))]
    if (!multiple && length(matches) > 1L) {
      stop(paste0("Scientific data adapter 'matrix_market_10x' found ambiguous ", role, " files."), call. = FALSE)
    }
    if (!length(matches)) return(list())
    lapply(if (multiple) matches else matches[[1]], function(path) list(
      path = path, format = format, role = role, kind = "file", name = basename(path)
    ))
  }
  core_assets <- Filter(Negate(is.null), list(
    list(
      path = matrix_matches[[1]], format = "mtx", role = "primary", kind = "file",
      name = basename(matrix_matches[[1]])
    ),
    select_one("^(features|genes)\\.tsv(\\.gz)?$", "features"),
    select_one("^barcodes\\.tsv(\\.gz)?$", "barcodes")
  ))
  c(
    core_assets,
    select_global("^tissue_positions.*\\.(csv|tsv)(\\.gz)?$", "csv", "spatial_coordinates"),
    select_global("^scalefactors.*\\.json$", "json", "spatial_metadata"),
    select_global("^tissue_.*\\.(png|jpe?g|tiff?)$", "image", "spatial_image", multiple = TRUE),
    select_global("^metrics_summary\\.(csv|tsv)$", "csv", "quality_metrics"),
    select_global("^molecule_info\\.h5$", "generic_hdf5", "molecule_info")
  )
}

baa_matrix_market_header <- function(path, max_header_lines = 256L) {
  connection <- baa_open_text_connection(path)
  on.exit(close(connection), add = TRUE)
  banner <- readLines(connection, n = 1L, warn = FALSE)
  if (
    length(banner) != 1L ||
    !grepl("^%%MatrixMarket[[:space:]]+matrix[[:space:]]+coordinate[[:space:]]+", banner)
  ) {
    stop(
      "Scientific data adapter 'matrix_market_10x' requires a coordinate Matrix Market banner.",
      call. = FALSE
    )
  }
  if (nchar(banner, type = "bytes") > 8192L) {
    stop("Matrix Market banner exceeds the bounded inspection limit.", call. = FALSE)
  }
  dimension_line <- character()
  for (index in seq_len(as.integer(max_header_lines))) {
    candidate <- readLines(connection, n = 1L, warn = FALSE)
    if (!length(candidate)) break
    if (nchar(candidate[[1]], type = "bytes") > 8192L) {
      stop("Matrix Market header line exceeds the bounded inspection limit.", call. = FALSE)
    }
    trimmed <- trimws(candidate[[1]])
    if (nzchar(trimmed) && !startsWith(trimmed, "%")) {
      dimension_line <- trimmed
      break
    }
  }
  if (!length(dimension_line)) {
    stop("Matrix Market dimensions were not found within the bounded header.", call. = FALSE)
  }
  fields <- strsplit(dimension_line, "[[:space:]]+")[[1]]
  dimensions <- suppressWarnings(as.numeric(fields))
  if (
    length(dimensions) != 3L || any(!is.finite(dimensions)) ||
    any(dimensions < 0) || any(dimensions != floor(dimensions))
  ) {
    stop("Matrix Market dimensions must be three non-negative whole numbers.", call. = FALSE)
  }
  list(
    banner = banner[[1]],
    features = dimensions[[1]],
    samples = dimensions[[2]],
    nonzero_entries = dimensions[[3]]
  )
}

baa_read_matrix_market_inspection <- function(primary_asset, assets) {
  if (identical(primary_asset$kind, "directory")) {
    assets <- baa_10x_directory_assets(primary_asset)
    primary_asset <- baa_primary_scientific_asset(assets)
  }
  header <- baa_matrix_market_header(primary_asset$path)
  features <- baa_find_bundle_asset(
    assets,
    roles = c("features", "feature", "genes", "gene"),
    basenames = c("features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz")
  )
  barcodes <- baa_find_bundle_asset(
    assets,
    roles = c("barcodes", "barcode", "cells", "cell"),
    basenames = c("barcodes.tsv", "barcodes.tsv.gz")
  )
  feature_rows <- NA_real_
  barcode_rows <- NA_real_
  count_barcodes <- NULL
  if (!is.null(features)) {
    feature_rows <- baa_count_delimited_sidecar_rows(features$path)
    if (feature_rows != header$features) {
      stop("Scientific data adapter 'matrix_market_10x' found a feature-count mismatch.", call. = FALSE)
    }
  }
  if (!is.null(barcodes)) {
    barcode_rows <- baa_count_delimited_sidecar_rows(barcodes$path)
    if (barcode_rows != header$samples) {
      stop("Scientific data adapter 'matrix_market_10x' found a barcode-count mismatch.", call. = FALSE)
    }
    has_spatial_coordinates <- any(vapply(
      assets,
      function(asset) identical(asset$role, "spatial_coordinates"),
      logical(1)
    ))
    if (has_spatial_coordinates) {
      barcode_table <- baa_read_delimited_sidecar(barcodes$path)
      count_barcodes <- as.character(barcode_table[[1]])
    }
  }
  profile_data <- data.frame(
    features = header$features,
    samples = header$samples,
    nonzero_entries = header$nonzero_entries,
    stringsAsFactors = FALSE
  )
  handle <- baa_new_scientific_handle(
    format = "mtx",
    adapter_id = "matrix_market_structure_handle",
    modality = "omics_matrix",
    assets = assets,
    structure = list(
      banner = header$banner,
      nonzero_entries = header$nonzero_entries,
      feature_rows = feature_rows,
      barcode_rows = barcode_rows,
      header_bounded = TRUE
    ),
    axes = list(features = header$features, samples = header$samples),
    profile_data = profile_data,
    processing_stage = "counts",
    upstream_required = "reload with load_profile='analysis' to materialize the sparse count matrix",
    capabilities = c("bounded_structure", "sparse_counts")
  )
  baa_attach_10x_bundle_context(handle, assets, count_barcodes = count_barcodes)
}

baa_read_matrix_market_bundle <- function(primary_asset, assets) {
  if (identical(primary_asset$kind, "directory")) {
    assets <- baa_10x_directory_assets(primary_asset)
    primary_asset <- baa_primary_scientific_asset(assets)
  }
  connection <- baa_open_text_connection(primary_asset$path)
  on.exit(close(connection), add = TRUE)
  matrix <- Matrix::readMM(connection)

  features <- baa_find_bundle_asset(
    assets,
    roles = c("features", "feature", "genes", "gene"),
    basenames = c("features.tsv", "features.tsv.gz", "genes.tsv", "genes.tsv.gz")
  )
  feature_types <- NULL
  if (!is.null(features)) {
    feature_table <- baa_read_delimited_sidecar(features$path)
    if (nrow(feature_table) != nrow(matrix)) {
      stop("Scientific data adapter 'matrix_market_10x' found a feature-count mismatch.")
    }
    feature_column <- if (ncol(feature_table) >= 2) 2 else 1
    feature_names <- as.character(feature_table[[feature_column]])
    empty <- is.na(feature_names) | !nzchar(feature_names)
    if (any(empty)) feature_names[empty] <- as.character(feature_table[[1]])[empty]
    rownames(matrix) <- make.unique(feature_names)
    if (ncol(feature_table) >= 3L) {
      feature_types <- trimws(as.character(feature_table[[3L]]))
      feature_types[is.na(feature_types) | !nzchar(feature_types)] <- "Gene Expression"
    }
  }

  barcodes <- baa_find_bundle_asset(
    assets,
    roles = c("barcodes", "barcode", "cells", "cell"),
    basenames = c("barcodes.tsv", "barcodes.tsv.gz")
  )
  if (!is.null(barcodes)) {
    barcode_table <- baa_read_delimited_sidecar(barcodes$path)
    if (nrow(barcode_table) != ncol(matrix)) {
      stop("Scientific data adapter 'matrix_market_10x' found a barcode-count mismatch.")
    }
    colnames(matrix) <- make.unique(as.character(barcode_table[[1]]))
  }
  if (!is.null(feature_types) && length(unique(feature_types)) > 1L) {
    modality_names <- unique(feature_types)
    modalities <- lapply(modality_names, function(modality) {
      matrix[feature_types == modality, , drop = FALSE]
    })
    names(modalities) <- modality_names
    return(baa_attach_10x_bundle_context(
      baa_validate_10x_dataset(modalities, "matrix_market_10x"), assets
    ))
  }
  baa_attach_10x_bundle_context(baa_tag_dataset(matrix, "matrix_market_10x"), assets)
}

baa_read_rdata <- function(path) {
  environment <- new.env(parent = baseenv())
  object_names <- load(path, envir = environment)
  if (!length(object_names)) {
    stop("RData file contains no objects.")
  }
  objects <- mget(object_names, envir = environment, inherits = FALSE)
  if (length(objects) == 1) {
    return(objects[[1]])
  }
  structure(objects, class = c("baa_rdata_objects", "list"))
}

baa_configured_whole_number <- function(
  option_name,
  environment_name,
  default,
  minimum = 0,
  maximum = 2^53
) {
  option_value <- getOption(option_name, NULL)
  raw_value <- if (is.null(option_value)) {
    Sys.getenv(environment_name, unset = "")
  } else {
    option_value
  }
  if (length(raw_value) == 0 || is.na(raw_value[[1]]) || !nzchar(trimws(as.character(raw_value[[1]])))) {
    return(as.numeric(default))
  }
  value <- suppressWarnings(as.numeric(raw_value[[1]]))
  if (
    length(value) != 1 || !is.finite(value) || value != floor(value) ||
    value < minimum || value > maximum
  ) {
    stop(
      paste0(
        environment_name, " must be a whole number between ",
        format(minimum, scientific = FALSE), " and ", format(maximum, scientific = FALSE), "."
      ),
      call. = FALSE
    )
  }
  value
}

baa_workers <- function(tasks = NULL, memory_per_worker_bytes = NULL) {
  workers <- baa_configured_whole_number(
    "mc.cores",
    "BAA_R_WORKERS",
    1,
    minimum = 1,
    maximum = .Machine$integer.max
  )
  if (!is.null(tasks)) {
    task_count <- suppressWarnings(as.numeric(tasks))
    if (
      length(task_count) != 1L || !is.finite(task_count) ||
      task_count != floor(task_count) || task_count < 1
    ) {
      stop("tasks must be one positive whole number.", call. = FALSE)
    }
    workers <- min(workers, task_count)
  }
  if (!is.null(memory_per_worker_bytes)) {
    worker_bytes <- suppressWarnings(as.numeric(memory_per_worker_bytes))
    if (
      length(worker_bytes) != 1L || !is.finite(worker_bytes) ||
      worker_bytes <= 0
    ) {
      stop("memory_per_worker_bytes must be one positive number.", call. = FALSE)
    }
    memory_budget <- suppressWarnings(as.numeric(
      getOption(
        "baa.memory_budget_bytes",
        Sys.getenv("BAA_R_MEMORY_BUDGET_BYTES", unset = "0")
      )
    ))
    if (length(memory_budget) == 1L && is.finite(memory_budget) && memory_budget > 0) {
      workers <- min(workers, max(1, floor(memory_budget / worker_bytes)))
    }
  }
  as.integer(max(1, workers))
}

baa_parallel_lapply <- function(
  X,
  FUN,
  ...,
  workers = baa_workers(tasks = length(X)),
  seed = 1L
) {
  FUN <- match.fun(FUN)
  worker_count <- baa_workers(tasks = max(1L, length(X)))
  requested_workers <- suppressWarnings(as.numeric(workers))
  if (
    length(requested_workers) != 1L || !is.finite(requested_workers) ||
    requested_workers != floor(requested_workers) || requested_workers < 1
  ) {
    stop("workers must be one positive whole number.", call. = FALSE)
  }
  worker_count <- as.integer(min(worker_count, requested_workers))
  if (length(X) < 2L || worker_count < 2L) {
    return(lapply(X, FUN, ...))
  }

  normalized_seed <- NULL
  if (!is.null(seed)) {
    numeric_seed <- suppressWarnings(as.numeric(seed))
    if (
      length(numeric_seed) != 1L || !is.finite(numeric_seed) ||
      numeric_seed != floor(numeric_seed) || numeric_seed < 0 ||
      numeric_seed > (.Machine$integer.max - 1)
    ) {
      stop("seed must be NULL or one whole number between 0 and 2147483646.", call. = FALSE)
    }
    normalized_seed <- as.integer(numeric_seed)
  }

  dots <- list(...)
  worker_call <- function(value, fn, arguments) {
    # Explicit process parallelism owns the CPU budget. Keep native libraries
    # inside each child at one thread to avoid workers x BLAS oversubscription.
    Sys.setenv(
      OMP_NUM_THREADS = "1",
      OMP_THREAD_LIMIT = "1",
      OPENBLAS_NUM_THREADS = "1",
      MKL_NUM_THREADS = "1",
      VECLIB_MAXIMUM_THREADS = "1",
      RCPP_PARALLEL_NUM_THREADS = "1"
    )
    do.call(fn, c(list(value), arguments))
  }

  if (.Platform$OS.type == "windows") {
    cluster <- parallel::makePSOCKcluster(worker_count)
    on.exit(parallel::stopCluster(cluster), add = TRUE)
    if (!is.null(normalized_seed)) {
      parallel::clusterSetRNGStream(cluster, iseed = normalized_seed)
    }
    result <- parallel::parLapply(
      cluster,
      X,
      worker_call,
      fn = FUN,
      arguments = dots
    )
  } else {
    previous_kind <- RNGkind()
    had_seed <- exists(".Random.seed", envir = .GlobalEnv, inherits = FALSE)
    previous_seed <- if (had_seed) get(".Random.seed", envir = .GlobalEnv) else NULL
    if (!is.null(normalized_seed)) {
      RNGkind("L'Ecuyer-CMRG")
      set.seed(normalized_seed)
      on.exit({
        do.call(RNGkind, as.list(previous_kind))
        if (had_seed) {
          assign(".Random.seed", previous_seed, envir = .GlobalEnv)
        } else if (exists(".Random.seed", envir = .GlobalEnv, inherits = FALSE)) {
          rm(".Random.seed", envir = .GlobalEnv)
        }
      }, add = TRUE)
    }
    result <- parallel::mclapply(
      X,
      worker_call,
      fn = FUN,
      arguments = dots,
      mc.cores = worker_count,
      mc.preschedule = TRUE,
      mc.set.seed = !is.null(normalized_seed)
    )
  }

  failed <- vapply(result, inherits, logical(1), what = "try-error")
  if (any(failed)) {
    stop(
      paste0("Parallel worker failed: ", as.character(result[[which(failed)[[1L]]]])),
      call. = FALSE
    )
  }
  result
}

baa_lazy_dataset_threshold_bytes <- function() {
  baa_configured_whole_number(
    "baa.lazy_dataset_threshold_bytes",
    "BAA_LAZY_DATASET_THRESHOLD_BYTES",
    256 * 1024^2
  )
}

baa_lazy_collect_max_rows <- function() {
  baa_configured_whole_number(
    "baa.lazy_collect_max_rows",
    "BAA_LAZY_COLLECT_MAX_ROWS",
    100000,
    minimum = 1,
    maximum = .Machine$integer.max
  )
}

baa_lazy_sample_max_rows <- function() {
  baa_configured_whole_number(
    "baa.lazy_sample_max_rows",
    "BAA_LAZY_SAMPLE_MAX_ROWS",
    10000,
    minimum = 1,
    maximum = .Machine$integer.max
  )
}

baa_lazy_sample_scan_max_rows <- function() {
  baa_configured_whole_number(
    "baa.lazy_sample_scan_max_rows",
    "BAA_LAZY_SAMPLE_SCAN_MAX_ROWS",
    100000,
    minimum = 1,
    maximum = .Machine$integer.max
  )
}

baa_lazy_inspection_rows <- function() {
  configured <- baa_configured_whole_number(
    "baa.lazy_inspection_rows",
    "BAA_LAZY_INSPECTION_ROWS",
    2000,
    minimum = 1,
    maximum = .Machine$integer.max
  )
  as.integer(min(configured, baa_lazy_sample_max_rows(), baa_lazy_sample_scan_max_rows()))
}

baa_lazy_schema_sample_rows <- function() {
  baa_configured_whole_number(
    "baa.lazy_schema_sample_rows",
    "BAA_LAZY_SCHEMA_SAMPLE_ROWS",
    20480,
    minimum = 1,
    maximum = .Machine$integer.max
  )
}

baa_file_size_bytes <- function(path) {
  details <- file.info(path)
  if (nrow(details) != 1 || is.na(details$size[[1]])) {
    stop(paste0("Unable to determine scientific data asset size: ", basename(path)), call. = FALSE)
  }
  as.numeric(details$size[[1]])
}

baa_should_lazy_load <- function(path, format) {
  normalized_format <- baa_normalize_scientific_format(format, path)
  normalized_format %in% c("csv", "tsv", "txt", "parquet") &&
    file.exists(path) && !dir.exists(path) &&
    baa_file_size_bytes(path) > baa_lazy_dataset_threshold_bytes()
}

baa_require_lazy_dataset_packages <- function() {
  for (package in c("duckdb", "DBI", "dbplyr", "dplyr")) {
    baa_require_adapter_package(package, "tabular_duckdb_lazy")
  }
  invisible(TRUE)
}

baa_lazy_source_sql <- function(connection, path, format) {
  quoted_path <- as.character(DBI::dbQuoteString(connection, normalizePath(path, winslash = "/", mustWork = TRUE)))
  if (identical(format, "parquet")) {
    return(paste0("read_parquet(", quoted_path, ")"))
  }
  if (identical(format, "txt")) {
    return(paste0(
      "read_csv_auto(", quoted_path,
      ", header=true, sample_size=", base::format(baa_lazy_schema_sample_rows(), scientific = FALSE),
      ")"
    ))
  }
  delimiter <- if (identical(format, "tsv")) "\t" else ","
  paste0(
    "read_csv_auto(", quoted_path,
    ", delim=", as.character(DBI::dbQuoteString(connection, delimiter)),
    ", header=true, sample_size=", base::format(baa_lazy_schema_sample_rows(), scientific = FALSE),
    ")"
  )
}

baa_disconnect_duckdb <- function(connection) {
  if (!is.null(connection) && tryCatch(DBI::dbIsValid(connection), error = function(e) FALSE)) {
    try(DBI::dbDisconnect(connection, shutdown = TRUE), silent = TRUE)
  }
  invisible(NULL)
}

baa_open_lazy_dataset <- function(path, format) {
  baa_require_lazy_dataset_packages()
  normalized_format <- baa_normalize_scientific_format(format, path)
  if (!normalized_format %in% c("csv", "tsv", "txt", "parquet")) {
    stop("Lazy datasets support only CSV, TSV, TXT, and Parquet assets.", call. = FALSE)
  }
  if (normalized_format %in% c("csv", "tsv", "txt")) {
    raw_head <- tryCatch(readBin(path, what = "raw", n = 262144L), error = function(e) NULL)
    if (is.null(raw_head) || !length(raw_head)) {
      stop(paste0("无法读取大文件 '", basename(path), "' 的内容进行编码检查。"), call. = FALSE)
    }
    has_bom <- length(raw_head) >= 3 && raw_head[1] == as.raw(0xEF) && raw_head[2] == as.raw(0xBB) && raw_head[3] == as.raw(0xBF)
    decoded_head <- tryCatch(
      rawToChar(if (has_bom) raw_head[-c(1, 2, 3)] else raw_head),
      error = function(e) NULL
    )
    if (is.null(decoded_head) || !validUTF8(decoded_head)) {
      reason <- if (is.null(decoded_head)) "文件包含无法识别的非文本字节" else "它是其他编码（如 GB18030/GBK 中文导出）"
      stop(paste0(
        "大文件懒加载要求数据为 UTF-8 编码，检测到 '", basename(path), "'：", reason,
        "。请先转换编码：Excel 另存为「CSV UTF-8」，或终端执行 iconv -f GB18030 -t UTF-8 原文件 > 新文件。"
      ), call. = FALSE)
    }
  }

  connection <- DBI::dbConnect(duckdb::duckdb(), dbdir = ":memory:")
  keep_connection <- FALSE
  on.exit({
    if (!keep_connection) baa_disconnect_duckdb(connection)
  }, add = TRUE)

  relation_name <- "baa_dataset"
  create_view_sql <- paste0(
    "CREATE VIEW ", as.character(DBI::dbQuoteIdentifier(connection, relation_name)),
    " AS SELECT * FROM ", baa_lazy_source_sql(connection, path, normalized_format)
  )
  DBI::dbExecute(connection, create_view_sql)
  schema <- DBI::dbGetQuery(
    connection,
    paste0(
      "DESCRIBE SELECT * FROM ",
      as.character(DBI::dbQuoteIdentifier(connection, relation_name))
    )
  )

  dataset <- new.env(parent = emptyenv())
  dataset$adapter_id <- "tabular_duckdb_lazy"
  dataset$backend <- "duckdb"
  dataset$connection <- connection
  dataset$format <- normalized_format
  dataset$path <- normalizePath(path, winslash = "/", mustWork = TRUE)
  dataset$relation_name <- relation_name
  dataset$schema <- schema
  dataset$size_bytes <- baa_file_size_bytes(path)
  dataset$closed <- FALSE
  class(dataset) <- c("baa_lazy_dataset", "environment")
  lazy_table <- dplyr::tbl(connection, relation_name)
  attr(lazy_table, "baa_lazy_dataset_owner") <- dataset
  dataset$table <- lazy_table
  reg.finalizer(dataset, function(value) {
    baa_disconnect_duckdb(value$connection)
  }, onexit = TRUE)
  keep_connection <- TRUE
  dataset
}

baa_is_lazy_dataset <- function(dataset) {
  inherits(dataset, "baa_lazy_dataset")
}

baa_is_lazy_table <- function(dataset) {
  inherits(dataset, c("tbl_lazy", "tbl_sql"))
}

baa_assert_lazy_dataset_open <- function(dataset) {
  if (!baa_is_lazy_dataset(dataset)) {
    stop("Expected a baa_lazy_dataset.", call. = FALSE)
  }
  if (
    isTRUE(dataset$closed) || is.null(dataset$connection) ||
    !tryCatch(DBI::dbIsValid(dataset$connection), error = function(e) FALSE)
  ) {
    stop("The baa_lazy_dataset connection is closed.", call. = FALSE)
  }
  invisible(TRUE)
}

baa_lazy_table <- function(dataset) {
  baa_assert_lazy_dataset_open(dataset)
  dataset$table
}

baa_lazy_dataset_info <- function(dataset) {
  baa_assert_lazy_dataset_open(dataset)
  list(
    representation = "lazy_duckdb",
    backend = dataset$backend,
    format = dataset$format,
    size_bytes = dataset$size_bytes,
    columns = as.character(dataset$schema$column_name),
    column_types = stats::setNames(
      as.character(dataset$schema$column_type),
      as.character(dataset$schema$column_name)
    ),
    sample_strategy = "bounded_prefix_reservoir",
    row_count = NA_real_,
    row_count_exact = FALSE
  )
}

baa_close_lazy_dataset <- function(dataset) {
  if (!baa_is_lazy_dataset(dataset)) {
    stop("Expected a baa_lazy_dataset.", call. = FALSE)
  }
  if (!isTRUE(dataset$closed)) {
    baa_disconnect_duckdb(dataset$connection)
    dataset$closed <- TRUE
  }
  invisible(dataset)
}

names.baa_lazy_dataset <- function(x) {
  as.character(x$schema$column_name)
}

dim.baa_lazy_dataset <- function(x) {
  c(NA_integer_, length(names(x)))
}

length.baa_lazy_dataset <- function(x) {
  length(names(x))
}

print.baa_lazy_dataset <- function(x, ...) {
  info <- baa_lazy_dataset_info(x)
  cat(
    "<baa_lazy_dataset>", info$format, "via", info$backend, "\n",
    "Source bytes:", format(info$size_bytes, big.mark = ",", scientific = FALSE), "\n",
    "Columns:", length(info$columns), "\n",
    "Use baa_lazy_table(), baa_collect_dataset(), or baa_sample_dataset() for bounded access.\n"
  )
  invisible(x)
}

as.data.frame.baa_lazy_dataset <- function(x, ...) {
  stop(
    "A baa_lazy_dataset cannot be fully converted to a data.frame; use baa_collect_dataset() with an explicit row limit.",
    call. = FALSE
  )
}

baa_validate_lazy_row_limit <- function(value, maximum, argument_name, environment_name) {
  numeric_value <- suppressWarnings(as.numeric(value))
  if (
    length(numeric_value) != 1 || !is.finite(numeric_value) ||
    numeric_value != floor(numeric_value) || numeric_value < 1
  ) {
    stop(paste0(argument_name, " must be one positive whole number."), call. = FALSE)
  }
  if (numeric_value > maximum) {
    stop(
      paste0(
        argument_name, " exceeds the configured ", environment_name,
        " limit of ", format(maximum, scientific = FALSE), " rows."
      ),
      call. = FALSE
    )
  }
  as.integer(numeric_value)
}

baa_lazy_sample_scan_rows <- function(sample_size) {
  scan_maximum <- baa_lazy_sample_scan_max_rows()
  if (sample_size > scan_maximum) {
    stop(
      paste0(
        "size exceeds the configured BAA_LAZY_SAMPLE_SCAN_MAX_ROWS limit of ",
        base::format(scan_maximum, scientific = FALSE), " rows."
      ),
      call. = FALSE
    )
  }
  as.integer(min(scan_maximum, max(sample_size, sample_size * 10)))
}

baa_collect_dataset <- function(dataset, limit = 10000L) {
  row_limit <- baa_validate_lazy_row_limit(
    limit,
    baa_lazy_collect_max_rows(),
    "limit",
    "BAA_LAZY_COLLECT_MAX_ROWS"
  )
  if (baa_is_lazy_dataset(dataset)) {
    dataset <- baa_lazy_table(dataset)
  }
  if (baa_is_lazy_table(dataset)) {
    return(dplyr::collect(utils::head(dataset, row_limit)))
  }
  if (is.data.frame(dataset)) {
    return(utils::head(dataset, row_limit))
  }
  stop("baa_collect_dataset() expects a data.frame, lazy table, or baa_lazy_dataset.", call. = FALSE)
}

baa_sample_dataset <- function(dataset, size = 1000L, seed = 1L) {
  sample_size <- baa_validate_lazy_row_limit(
    size,
    baa_lazy_sample_max_rows(),
    "size",
    "BAA_LAZY_SAMPLE_MAX_ROWS"
  )
  numeric_seed <- suppressWarnings(as.numeric(seed))
  if (
    length(numeric_seed) != 1 || !is.finite(numeric_seed) ||
    numeric_seed != floor(numeric_seed) || numeric_seed < 0 ||
    numeric_seed > (.Machine$integer.max - 1)
  ) {
    stop("seed must be one whole number between 0 and 2147483646.", call. = FALSE)
  }
  sample_seed <- as.integer(numeric_seed)
  if (baa_is_lazy_dataset(dataset)) {
    baa_assert_lazy_dataset_open(dataset)
    scan_rows <- baa_lazy_sample_scan_rows(sample_size)
    query <- paste0(
      "SELECT * FROM (SELECT * FROM ",
      as.character(DBI::dbQuoteIdentifier(dataset$connection, dataset$relation_name)),
      " LIMIT ", scan_rows, ") AS baa_sample_source",
      " USING SAMPLE reservoir(", sample_size, " ROWS) REPEATABLE (", sample_seed, ")"
    )
    sampled <- DBI::dbGetQuery(dataset$connection, query)
    attr(sampled, "baa_sampling_scope") <- list(
      strategy = "bounded_prefix_reservoir",
      scan_rows = scan_rows,
      returned_rows = nrow(sampled)
    )
    return(sampled)
  }
  if (baa_is_lazy_table(dataset)) {
    scan_rows <- baa_lazy_sample_scan_rows(sample_size)
    bounded_prefix <- dplyr::collect(utils::head(dataset, scan_rows))
    set.seed(sample_seed)
    indexes <- sample.int(
      nrow(bounded_prefix),
      min(sample_size, nrow(bounded_prefix)),
      replace = FALSE
    )
    sampled <- bounded_prefix[indexes, , drop = FALSE]
    attr(sampled, "baa_sampling_scope") <- list(
      strategy = "bounded_prefix_reservoir",
      scan_rows = scan_rows,
      returned_rows = nrow(sampled)
    )
    return(sampled)
  }
  if (is.data.frame(dataset)) {
    if (!nrow(dataset)) return(utils::head(dataset, 0L))
    set.seed(sample_seed)
    indexes <- sample.int(nrow(dataset), min(sample_size, nrow(dataset)), replace = FALSE)
    return(dataset[indexes, , drop = FALSE])
  }
  stop("baa_sample_dataset() expects a data.frame, lazy table, or baa_lazy_dataset.", call. = FALSE)
}

baa_read_fcs_header <- function(path) {
  connection <- file(path, open = "rb")
  on.exit(close(connection), add = TRUE)
  header <- readBin(connection, what = "raw", n = 58L)
  if (length(header) < 58L) {
    stop("FCS asset is shorter than the required 58-byte header.", call. = FALSE)
  }
  version <- trimws(rawToChar(header[1:6]))
  if (!grepl("^FCS[0-9]", version)) {
    stop("FCS asset has an invalid header signature.", call. = FALSE)
  }
  offset <- function(start, end) {
    value <- trimws(rawToChar(header[start:end]))
    if (!nzchar(value)) return(NA_real_)
    parsed <- suppressWarnings(as.numeric(value))
    if (!is.finite(parsed) || parsed < 0 || parsed != floor(parsed)) {
      stop("FCS asset contains an invalid segment offset.", call. = FALSE)
    }
    parsed
  }
  size_bytes <- as.numeric(file.info(path)$size[[1]])
  text_start <- offset(11L, 18L)
  text_end <- offset(19L, 26L)
  data_start <- offset(27L, 34L)
  data_end <- offset(35L, 42L)
  validate_segment <- function(start, end, label) {
    if (is.na(start) != is.na(end)) {
      stop(paste0("FCS ", label, " segment has an incomplete offset pair."), call. = FALSE)
    }
    if (is.na(start) || (start == 0 && end == 0)) return(invisible(TRUE))
    if (start > end || end >= size_bytes) {
      stop(
        paste0("FCS ", label, " segment offsets are out of order or outside the file."),
        call. = FALSE
      )
    }
    invisible(TRUE)
  }
  validate_segment(text_start, text_end, "TEXT")
  validate_segment(data_start, data_end, "DATA")
  data.frame(
    version = version,
    text_start = text_start,
    text_end = text_end,
    data_start = data_start,
    data_end = data_end,
    size_bytes = size_bytes,
    stringsAsFactors = FALSE
  )
}

baa_read_fcs_inspection_handle <- function(primary_asset, assets) {
  fcs_assets <- baa_find_bundle_assets(assets, formats = "fcs")
  paths <- unique(vapply(fcs_assets, function(asset) asset$path, character(1)))
  if (!length(paths)) stop("FCS bundle contains no FCS assets.", call. = FALSE)
  profiled_paths <- utils::head(paths, 256L)
  rows <- lapply(profiled_paths, function(path) {
    row <- baa_read_fcs_header(path)
    row$file <- basename(path)
    row
  })
  profile_data <- do.call(rbind, rows)
  rownames(profile_data) <- NULL
  baa_new_scientific_handle(
    format = "fcs",
    adapter_id = if (length(paths) > 1L) "fcs_header_batch_handle" else "fcs_header_handle",
    modality = "flow_cytometry",
    assets = assets,
    structure = list(files_profiled = length(profiled_paths), profile_bounded = TRUE),
    axes = list(files = length(paths), events = NA_integer_),
    profile_data = profile_data,
    processing_stage = "raw",
    upstream_required = "reload with load_profile='analysis' to materialize flowFrame/flowSet objects",
    capabilities = "bounded_fcs_headers"
  )
}

baa_read_fcs_bundle <- function(primary_asset, assets) {
  fcs_assets <- baa_find_bundle_assets(assets, formats = "fcs")
  paths <- unique(vapply(fcs_assets, function(asset) asset$path, character(1)))
  if (!length(paths)) stop("FCS bundle contains no FCS assets.", call. = FALSE)
  if (length(paths) == 1L) {
    value <- flowCore::read.FCS(
      paths[[1]], transformation = FALSE, truncate_max_range = FALSE
    )
    return(baa_tag_dataset(value, "fcs_flowcore"))
  }
  value <- flowCore::read.flowSet(
    files = paths,
    transformation = FALSE,
    truncate_max_range = FALSE
  )
  baa_tag_dataset(value, "fcs_flowcore_batch")
}

baa_read_mass_spectrometry_bundle <- function(primary_asset, assets) {
  spectrum_assets <- baa_find_bundle_assets(assets, formats = c("mzml", "mzxml"))
  paths <- unique(vapply(spectrum_assets, function(asset) asset$path, character(1)))
  if (!length(paths)) stop("Mass-spectrometry bundle contains no mzML/mzXML assets.", call. = FALSE)
  value <- MSnbase::readMSData(files = paths, mode = "onDisk")
  baa_tag_dataset(
    value,
    if (length(paths) > 1L) "mass_spectrometry_msnbase_batch" else "mass_spectrometry_msnbase"
  )
}

baa_read_bam_handle <- function(primary_asset, assets) {
  index_asset <- baa_validate_index_asset(primary_asset, assets, "bai")
  bam_file <- Rsamtools::BamFile(
    primary_asset$path,
    index = if (is.null(index_asset)) character() else index_asset$path
  )
  header <- tryCatch(Rsamtools::scanBamHeader(bam_file), error = function(e) {
    stop(paste0("Unable to read BAM header: ", conditionMessage(e)), call. = FALSE)
  })
  if (is.null(header$targets) && length(header) == 1L && is.list(header[[1]])) {
    header <- header[[1]]
  }
  targets <- header$targets %||% integer()
  profile_data <- data.frame(
    sequence = utils::head(names(targets), 256L),
    length = as.numeric(utils::head(targets, 256L)),
    stringsAsFactors = FALSE
  )
  baa_new_scientific_handle(
    format = "bam",
    adapter_id = if (is.null(index_asset)) "bam_rsamtools_handle" else "bam_rsamtools_indexed_handle",
    modality = "aligned_reads",
    assets = assets,
    structure = list(indexed = !is.null(index_asset), sequences_profiled = nrow(profile_data)),
    axes = list(sequences = length(targets), alignments = NA_integer_),
    profile_data = profile_data,
    processing_stage = "aligned",
    upstream_required = if (is.null(index_asset)) "create and attach a matching BAM index before region queries" else character(),
    capabilities = c("bounded_header", if (!is.null(index_asset)) "indexed_region_queries"),
    native = bam_file
  )
}

baa_read_vcf_handle <- function(primary_asset, assets) {
  index_asset <- baa_validate_index_asset(primary_asset, assets, c("tbi", "csi"))
  if (is.null(index_asset)) {
    connection <- baa_open_text_connection(primary_asset$path)
    on.exit(close(connection), add = TRUE)
    header_lines <- readLines(connection, n = 256L, warn = FALSE)
    header_lines <- header_lines[startsWith(header_lines, "#")]
    return(baa_new_scientific_handle(
      format = "vcf",
      adapter_id = "vcf_raw_handle",
      modality = "genomics_variants",
      assets = assets,
      structure = list(indexed = FALSE, header_lines_profiled = length(header_lines)),
      axes = list(variants = NA_integer_, samples = NA_integer_),
      profile_data = data.frame(header = header_lines, stringsAsFactors = FALSE),
      processing_stage = "raw",
      upstream_required = "bgzip and index the VCF with a matching TBI/CSI for bounded region queries",
      capabilities = "bounded_header"
    ))
  }
  tabix_file <- Rsamtools::TabixFile(primary_asset$path, index = index_asset$path)
  header <- tryCatch({
    open(tabix_file)
    on.exit(close(tabix_file), add = TRUE)
    Rsamtools::headerTabix(tabix_file)
  }, error = function(e) {
    stop(paste0("Unable to read indexed VCF header: ", conditionMessage(e)), call. = FALSE)
  })
  seqnames <- utils::head(as.character(header$seqnames %||% character()), 256L)
  baa_new_scientific_handle(
    format = "vcf",
    adapter_id = "vcf_indexed_handle",
    modality = "genomics_variants",
    assets = assets,
    structure = list(indexed = TRUE),
    axes = list(sequences = length(header$seqnames %||% character()), variants = NA_integer_),
    profile_data = data.frame(sequence = seqnames, stringsAsFactors = FALSE),
    processing_stage = "variant_calls",
    capabilities = c("bounded_header", "indexed_region_queries"),
    native = tabix_file
  )
}

baa_read_bcf_handle <- function(primary_asset, assets) {
  index_asset <- baa_validate_index_asset(primary_asset, assets, "csi")
  bcf_file <- Rsamtools::BcfFile(
    primary_asset$path,
    index = if (is.null(index_asset)) character() else index_asset$path
  )
  header <- tryCatch(
    Rsamtools::scanBcfHeader(bcf_file),
    error = function(e) {
      stop(paste0("Unable to read BCF header: ", conditionMessage(e)), call. = FALSE)
    }
  )
  if (!all(c("Reference", "Sample") %in% names(header)) && length(header) == 1L) {
    header <- header[[1]]
  }
  references <- as.character(header$Reference %||% character())
  samples <- as.character(header$Sample %||% character())
  profile_data <- data.frame(
    record_type = c(rep("reference", length(references)), rep("sample", length(samples))),
    value = c(references, samples),
    stringsAsFactors = FALSE
  )
  profile_data <- utils::head(profile_data, 256L)
  baa_new_scientific_handle(
    format = "bcf",
    adapter_id = "bcf_rsamtools_handle",
    modality = "genomics_variants",
    assets = assets,
    structure = list(
      indexed = !is.null(index_asset),
      references_profiled = min(length(references), 256L),
      header_bounded = TRUE
    ),
    axes = list(
      references = length(references), samples = length(samples), variants = NA_integer_
    ),
    profile_data = profile_data,
    processing_stage = "variant_calls",
    upstream_required = if (is.null(index_asset)) {
      "attach a matching CSI index before bounded region queries"
    } else character(),
    capabilities = c("bounded_header", if (!is.null(index_asset)) "indexed_region_queries"),
    native = bcf_file
  )
}

baa_read_range_inspection_handle <- function(primary_asset, assets, format) {
  connection <- baa_open_text_connection(primary_asset$path)
  on.exit(close(connection), add = TRUE)
  lines <- readLines(connection, n = 512L, warn = FALSE)
  if (any(nchar(lines, type = "bytes") > 65536L)) {
    stop("Genomic range record exceeds the bounded inspection line limit.", call. = FALSE)
  }
  records <- lines[nzchar(trimws(lines)) & !startsWith(trimws(lines), "#")]
  records <- utils::head(records, 256L)
  minimum_fields <- if (format %in% c("gff", "gtf")) 9L else if (identical(format, "narrowpeak")) 10L else 3L
  split_records <- strsplit(records, "\t", fixed = TRUE)
  if (length(split_records) && any(lengths(split_records) < minimum_fields)) {
    stop(
      paste0("Scientific data adapter for '", format, "' found a malformed range record."),
      call. = FALSE
    )
  }
  field_count <- if (length(split_records)) min(max(lengths(split_records)), 12L) else minimum_fields
  profile_data <- if (!length(split_records)) {
    as.data.frame(setNames(replicate(field_count, character(), simplify = FALSE), paste0("V", seq_len(field_count))))
  } else {
    values <- lapply(split_records, function(fields) {
      fields <- utils::head(fields, field_count)
      length(fields) <- field_count
      fields[is.na(fields)] <- ""
      fields
    })
    result <- as.data.frame(do.call(rbind, values), stringsAsFactors = FALSE, check.names = FALSE)
    colnames(result) <- paste0("V", seq_len(ncol(result)))
    result
  }
  baa_new_scientific_handle(
    format = format,
    adapter_id = paste0(format, "_bounded_range_handle"),
    modality = "genomic_ranges",
    assets = assets,
    structure = list(records_profiled = nrow(profile_data), profile_bounded = TRUE),
    axes = list(ranges = NA_integer_),
    profile_data = profile_data,
    processing_stage = "ranges",
    upstream_required = "reload with load_profile='analysis' to materialize a genomic ranges object",
    capabilities = "bounded_range_profile"
  )
}

baa_read_fastq_handle <- function(primary_asset, assets) {
  streamer <- ShortRead::FastqStreamer(primary_asset$path, n = 256L)
  on.exit(close(streamer), add = TRUE)
  reads <- ShortRead::yield(streamer)
  profile_data <- data.frame(
    read_id = as.character(ShortRead::id(reads)),
    width = as.integer(Biostrings::width(ShortRead::sread(reads))),
    stringsAsFactors = FALSE
  )
  baa_new_scientific_handle(
    format = "fastq", adapter_id = "fastq_shortread_stream", modality = "sequencing_reads",
    assets = assets, structure = list(records_profiled = nrow(profile_data), profile_bounded = TRUE),
    axes = list(reads = NA_integer_), profile_data = profile_data, processing_stage = "raw",
    upstream_required = "perform quality control/trimming and alignment or quantification",
    capabilities = "bounded_read_profile"
  )
}

baa_read_fasta_handle <- function(primary_asset, assets) {
  sequences <- Biostrings::readDNAStringSet(primary_asset$path, nrec = 256L, use.names = TRUE)
  identifiers <- names(sequences)
  if (is.null(identifiers)) identifiers <- rep("", length(sequences))
  profile_data <- data.frame(
    sequence_id = as.character(identifiers),
    width = as.integer(Biostrings::width(sequences)),
    stringsAsFactors = FALSE
  )
  baa_new_scientific_handle(
    format = "fasta", adapter_id = "fasta_biostrings_bounded", modality = "sequences",
    assets = assets, structure = list(records_profiled = nrow(profile_data), profile_bounded = TRUE),
    axes = list(sequences = NA_integer_), profile_data = profile_data, processing_stage = "raw",
    upstream_required = "select sequence-specific indexing, alignment, annotation, or phylogenetic processing",
    capabilities = "bounded_sequence_profile"
  )
}

baa_read_sam_handle <- function(primary_asset, assets) {
  connection <- baa_open_text_connection(primary_asset$path)
  on.exit(close(connection), add = TRUE)
  lines <- readLines(connection, n = 256L, warn = FALSE)
  header <- lines[startsWith(lines, "@")]
  baa_new_scientific_handle(
    format = "sam", adapter_id = "sam_raw_handle", modality = "aligned_reads",
    assets = assets, structure = list(header_lines_profiled = length(header), profile_bounded = TRUE),
    axes = list(alignments = NA_integer_),
    profile_data = data.frame(header = header, stringsAsFactors = FALSE),
    processing_stage = "aligned",
    upstream_required = "convert to coordinate-sorted BAM/CRAM and create an index for bounded queries",
    capabilities = "bounded_header"
  )
}

baa_read_cram_handle <- function(primary_asset, assets) {
  index_asset <- baa_validate_index_asset(primary_asset, assets, "crai")
  cram_file <- Rsamtools::BamFile(
    primary_asset$path,
    index = if (is.null(index_asset)) character() else index_asset$path
  )
  baa_new_scientific_handle(
    format = "cram", adapter_id = "cram_rsamtools_handle", modality = "aligned_reads",
    assets = assets, structure = list(indexed = !is.null(index_asset)), axes = list(alignments = NA_integer_),
    processing_stage = "aligned",
    upstream_required = c(
      "provide the matching reference genome",
      if (is.null(index_asset)) "attach a matching CRAI index for region queries" else character()
    ),
    capabilities = "path_backed_alignment_handle", native = cram_file
  )
}

baa_read_bigwig_handle <- function(primary_asset, assets) {
  bigwig_file <- rtracklayer::BigWigFile(primary_asset$path)
  sequence_info <- tryCatch(GenomeInfoDb::seqinfo(bigwig_file), error = function(e) NULL)
  sequence_names <- if (is.null(sequence_info)) character() else GenomeInfoDb::seqnames(sequence_info)
  profile_data <- data.frame(sequence = utils::head(as.character(sequence_names), 256L), stringsAsFactors = FALSE)
  baa_new_scientific_handle(
    format = "bigwig", adapter_id = "bigwig_rtracklayer_handle", modality = "genomic_signal",
    assets = assets, structure = list(sequences_profiled = nrow(profile_data)),
    axes = list(sequences = length(sequence_names)), profile_data = profile_data,
    processing_stage = "processed_signal", capabilities = c("indexed_ranges", "bounded_sequence_profile"),
    native = bigwig_file
  )
}

baa_read_maf_handle <- function(primary_asset, assets) {
  profile_data <- as.data.frame(readr::read_tsv(
    primary_asset$path, n_max = 256L, comment = "#", show_col_types = FALSE,
    progress = FALSE
  ), check.names = FALSE)
  baa_new_scientific_handle(
    format = "maf", adapter_id = "maf_bounded_raw", modality = "somatic_variants",
    assets = assets,
    structure = list(records_profiled = nrow(profile_data), columns = colnames(profile_data), profile_bounded = TRUE),
    axes = list(variants = NA_integer_), profile_data = profile_data, processing_stage = "raw",
    upstream_required = "validate MAF schema and genome build before cohort-level mutation analysis",
    capabilities = "bounded_variant_profile"
  )
}

baa_read_qza_handle <- function(primary_asset, assets) {
  connection <- file(primary_asset$path, open = "rb")
  on.exit(close(connection), add = TRUE)
  signature <- readBin(connection, what = "raw", n = 4L)
  archive_signature <- length(signature) >= 2L && identical(as.integer(signature[1:2]), c(0x50L, 0x4bL))
  size_bytes <- as.numeric(file.info(primary_asset$path)$size[[1]])
  profile_data <- data.frame(
    size_bytes = size_bytes,
    zip_signature = archive_signature,
    stringsAsFactors = FALSE
  )
  baa_new_scientific_handle(
    format = "qza", adapter_id = "qza_archive_handle", modality = "microbiome_artifact",
    assets = assets,
    structure = list(archive_signature = archive_signature, structure_not_extracted = TRUE),
    axes = list(entries = NA_integer_),
    profile_data = profile_data, processing_stage = "raw",
    upstream_required = "use qiime2R/QIIME 2 after validating artifact provenance and semantic type",
    capabilities = "bounded_archive_profile"
  )
}

baa_read_plink_bundle <- function(primary_asset, assets) {
  bed_assets <- baa_find_bundle_assets(assets, formats = c("bed", "plink_bed"))
  bim_assets <- baa_find_bundle_assets(assets, formats = "bim")
  fam_assets <- baa_find_bundle_assets(assets, formats = "fam")
  if (length(bed_assets) != 1L || length(bim_assets) != 1L || length(fam_assets) != 1L) {
    stop("PLINK bundle requires exactly one BED, one BIM, and one FAM asset.", call. = FALSE)
  }
  stems <- c(
    tools::file_path_sans_ext(basename(bed_assets[[1]]$path)),
    tools::file_path_sans_ext(basename(bim_assets[[1]]$path)),
    tools::file_path_sans_ext(basename(fam_assets[[1]]$path))
  )
  if (length(unique(tolower(stems))) != 1L) {
    stop("PLINK BED/BIM/FAM asset names do not share the same stem.", call. = FALSE)
  }
  directories <- vapply(
    c(bed_assets, bim_assets, fam_assets),
    function(asset) normalizePath(dirname(asset$path), winslash = "/", mustWork = TRUE),
    character(1)
  )
  if (length(unique(directories)) != 1L) {
    stop("PLINK BED/BIM/FAM assets must be colocated for path-backed loading.", call. = FALSE)
  }
  baa_require_adapter_function("BEDMatrix::BEDMatrix", "plink_bedmatrix")
  native <- BEDMatrix::BEDMatrix(bed_assets[[1]]$path)
  baa_new_scientific_handle(
    format = "plink_bed", adapter_id = "plink_bedmatrix", modality = "genotypes",
    assets = assets, structure = list(bundle_complete = TRUE), axes = list(samples = nrow(native), variants = ncol(native)),
    processing_stage = "genotype_calls", capabilities = c("path_backed_genotypes", "bounded_matrix_access"),
    native = native
  )
}

baa_bundle_adapter_format <- function(primary_asset, assets) {
  if (
    identical(primary_asset$format, "bed") &&
    length(baa_find_bundle_assets(assets, formats = "bim")) &&
    length(baa_find_bundle_assets(assets, formats = "fam"))
  ) return("plink_bed")
  primary_asset$format
}

baa_read_scientific_adapter <- function(asset, assets = list(asset), load_profile = NULL) {
  load_profile <- baa_normalize_load_profile(load_profile)
  format <- baa_normalize_scientific_format(asset$format, asset$path)
  registry <- baa_scientific_adapter_registry()
  spec <- registry[[format]]
  if (is.null(spec)) {
    stop(
      paste0(
        "No scientific data adapter is registered for format '", format,
        "'. Supported formats: ", paste(names(registry), collapse = ", "), "."
      ),
      call. = FALSE
    )
  }
  asset_name <- baa_scalar_text(asset$name, basename(asset$path))
  if (format %in% c("h5ad", "h5", "generic_hdf5", "10x_h5", "loom", "h5seurat")) {
    return(baa_scientific_adapter_error(
      spec$adapter_id,
      asset_name,
      baa_read_hdf5_asset(asset, assets, format, load_profile = load_profile)
    ))
  }
  if (identical(load_profile, "inspection")) {
    if (identical(format, "mtx")) {
      return(baa_scientific_adapter_error(
        spec$adapter_id, asset_name, baa_read_matrix_market_inspection(asset, assets)
      ))
    }
    if (identical(format, "fcs")) {
      return(baa_scientific_adapter_error(
        spec$adapter_id, asset_name, baa_read_fcs_inspection_handle(asset, assets)
      ))
    }
    if (identical(format, "bcf")) {
      baa_require_adapter_function("Rsamtools::BcfFile", spec$adapter_id)
      baa_require_adapter_function("Rsamtools::scanBcfHeader", spec$adapter_id)
      return(baa_scientific_adapter_error(
        spec$adapter_id, asset_name, baa_read_bcf_handle(asset, assets)
      ))
    }
    if (format %in% c("bed", "narrowpeak", "gff", "gtf")) {
      return(baa_scientific_adapter_error(
        spec$adapter_id,
        asset_name,
        baa_read_range_inspection_handle(asset, assets, format)
      ))
    }
  }
  baa_check_adapter_dependencies(spec)
  if (baa_should_lazy_load(asset$path, format)) {
    return(baa_scientific_adapter_error(
      "tabular_duckdb_lazy",
      asset_name,
      baa_open_lazy_dataset(asset$path, format)
    ))
  }
  baa_scientific_adapter_error(spec$adapter_id, asset_name, switch(
    format,
    csv = baa_read_tabular_with_encoding(asset$path, "csv"),
    tsv = baa_read_tabular_with_encoding(asset$path, "tsv"),
    txt = baa_read_tabular_with_encoding(asset$path, "txt"),
    xlsx = baa_read_excel_first_sheet(asset$path),
    xls = baa_read_excel_first_sheet(asset$path),
    sav = baa_unlabel_haven_dataset(haven::read_sav(asset$path)),
    dta = baa_unlabel_haven_dataset(haven::read_dta(asset$path)),
    rds = readRDS(asset$path),
    parquet = arrow::read_parquet(asset$path),
    rdata = baa_read_rdata(asset$path),
    mtx = baa_read_matrix_market_bundle(asset, assets),
    h5ad = stop("Internal HDF5 routing error.", call. = FALSE),
    h5 = stop("Internal HDF5 routing error.", call. = FALSE),
    generic_hdf5 = stop("Internal HDF5 routing error.", call. = FALSE),
    `10x_h5` = stop("Internal HDF5 routing error.", call. = FALSE),
    loom = stop("Internal HDF5 routing error.", call. = FALSE),
    h5seurat = stop("Internal HDF5 routing error.", call. = FALSE),
    fcs = baa_read_fcs_bundle(asset, assets),
    vcf = baa_read_vcf_handle(asset, assets),
    bcf = Rsamtools::scanBcf(asset$path),
    bed = rtracklayer::import(asset$path, format = "BED"),
    narrowpeak = rtracklayer::import(asset$path, format = "narrowPeak"),
    gff = rtracklayer::import(asset$path, format = "GFF"),
    gtf = rtracklayer::import(asset$path, format = "GTF"),
    biom = biomformat::read_biom(asset$path),
    mzml = baa_read_mass_spectrometry_bundle(asset, assets),
    mzxml = baa_read_mass_spectrometry_bundle(asset, assets),
    bam = baa_read_bam_handle(asset, assets),
    fastq = baa_read_fastq_handle(asset, assets),
    fasta = baa_read_fasta_handle(asset, assets),
    sam = baa_read_sam_handle(asset, assets),
    cram = baa_read_cram_handle(asset, assets),
    bigwig = baa_read_bigwig_handle(asset, assets),
    maf = baa_read_maf_handle(asset, assets),
    newick = baa_tag_dataset(ape::read.tree(asset$path), "newick_ape"),
    qza = baa_read_qza_handle(asset, assets),
    plink_bed = baa_read_plink_bundle(asset, assets),
    stop(paste0("Scientific data adapter dispatcher is missing format '", format, "'."), call. = FALSE)
  ))
}

baa_unlabel_haven_dataset <- function(dataset) {
  # SPSS/Stata value labels are the analysis-ready encoding: a labelled
  # column WITH value labels becomes a factor carrying the labels (男/女,
  # not 1/2), so profiling, semantic typing and categorical candidates keep
  # Chinese-named questionnaire columns.  Labelled columns without value
  # labels are zapped back to their plain underlying type.
  if (!is.data.frame(dataset)) return(dataset)
  for (name in names(dataset)) {
    col <- dataset[[name]]
    if (!inherits(col, "haven_labelled")) next
    # haven does not export val_labels on all versions; the labels live on
    # the "labels" attribute for both labelled and labelled_spss.
    labels <- attr(col, "labels")
    converted <- NULL
    dataset[[name]] <- tryCatch({
      if (!is.null(labels) && length(labels) > 0) {
        converted <- haven::as_factor(col)
        # as_factor sets values missing from the label table to NA (SPSS
        # sheets commonly carry undeclared codes like 9=拒答).  Restore them
        # as their own levels instead of silently dropping those rows.
        uncovered <- is.na(converted) & !is.na(col)
        if (any(uncovered)) {
          baa_record_warning(paste0(
            "列 ", name, " 有 ", sum(uncovered),
            " 个取值未在值标签中声明，已按原始编码保留为独立水平。"
          ))
          text <- as.character(converted)
          text[uncovered] <- as.character(col[uncovered])
          factor(text)
        } else {
          converted
        }
      } else {
        haven::zap_labels(col)
      }
    }, error = function(e) col)
    # Keep the SPSS variable label (e.g. "手术方式（1=急诊）") so the
    # profiler and the model can see what each column means.
    var_label <- attr(col, "label", exact = TRUE)
    if (!is.null(var_label) && nzchar(var_label)) attr(dataset[[name]], "label") <- var_label
  }
  dataset
}

baa_text_replacement_score <- function(dataset) {
  # Signature of reading non-UTF-8 bytes (GBK/GB18030 Chinese CSVs from
  # SPSS/WPS/legacy Excel) as UTF-8: readr keeps the invalid bytes instead
  # of writing U+FFFD, so count invalid-UTF-8 strings AND replacement chars.
  if (!is.data.frame(dataset)) return(0L)
  replacement <- intToUtf8(65533L)
  total <- 0L
  for (col in Filter(is.character, dataset)) {
    valid <- suppressWarnings(validUTF8(col))
    total <- total + sum(!valid)
    if (any(valid)) {
      total <- total + sum(grepl(replacement, col[valid], fixed = TRUE))
    }
  }
  total
}

baa_read_tabular_with_encoding <- function(path, format) {
  # readr defaults to UTF-8. Chinese-locale exports are frequently GB18030;
  # read them with an explicit locale instead of failing or mojibaking.
  reader <- switch(
    format,
    csv = function(p, locale) readr::read_csv(p, show_col_types = FALSE, locale = locale),
    tsv = function(p, locale) readr::read_tsv(p, show_col_types = FALSE, locale = locale),
    txt = function(p, locale) readr::read_delim(p, delim = NULL, show_col_types = FALSE, locale = locale),
    stop(paste0("Unsupported tabular format '", format, "'."), call. = FALSE)
  )
  dataset <- tryCatch(
    reader(path, readr::locale(encoding = "UTF-8")),
    error = function(e) NULL
  )
  used_encoding <- "UTF-8"
  if (is.null(dataset) || baa_text_replacement_score(dataset) > 0L) {
    gb <- tryCatch(
      reader(path, readr::locale(encoding = "GB18030")),
      error = function(e) NULL
    )
    if (!is.null(gb)) {
      gb_score <- baa_text_replacement_score(gb)
      utf8_score <- if (is.null(dataset)) Inf else baa_text_replacement_score(dataset)
      if (gb_score < utf8_score) {
        baa_record_warning(paste0(
          "数据文件不是 UTF-8 编码，已按 GB18030（简体中文）成功读取：", basename(path)
        ))
        used_encoding <- "GB18030"
        dataset <- gb
      }
    }
  }
  if (is.null(dataset)) {
    stop(
      paste0(
        "无法读取表格文件 '", basename(path),
        "'：既不是有效的 UTF-8，也不是 GB18030（简体中文）编码，或文件已损坏。"
      ),
      call. = FALSE
    )
  }
  baa_resniff_single_column_csv(dataset, path, format, used_encoding)
}

baa_resniff_single_column_csv <- function(dataset, path, format, encoding = "UTF-8") {
  # A semicolon-delimited export parsed with the comma reader collapses to
  # one column; retry with the explicit delimiter — keeping the encoding
  # that already succeeded, or the retry re-mojibakes Chinese text.  The
  # delimiter probe runs on raw BYTES: readLines would translate non-UTF-8
  # bytes and can mangle the probe.
  if (!identical(format, "csv") || !is.data.frame(dataset) || ncol(dataset) != 1) {
    return(dataset)
  }
  head_bytes <- tryCatch(readBin(path, what = "raw", n = 4096L), error = function(e) NULL)
  if (is.null(head_bytes) || !length(head_bytes)) {
    return(dataset)
  }
  line_breaks <- which(head_bytes == as.raw(10L))
  first_line <- if (length(line_breaks)) head_bytes[seq_len(line_breaks[1] - 1L)] else head_bytes
  semicolons <- sum(first_line == as.raw(0x3B))
  commas <- sum(first_line == as.raw(0x2C))
  if (semicolons == 0L || semicolons <= commas) {
    return(dataset)
  }
  sniffed <- tryCatch(
    readr::read_delim(
      path, delim = ";", show_col_types = FALSE,
      locale = readr::locale(encoding = encoding)
    ),
    error = function(e) NULL
  )
  if (!is.null(sniffed) && ncol(sniffed) > 1) {
    baa_record_warning(paste0("CSV 文件使用分号分隔，已自动按分号解析：", basename(path)))
    return(as.data.frame(sniffed))
  }
  dataset
}

baa_read_excel_first_sheet <- function(path) {
  sheets <- tryCatch(readxl::excel_sheets(path), error = function(e) character(0))
  dataset <- readxl::read_excel(path)
  if (length(sheets) > 1) {
    baa_record_warning(paste0(
      "工作簿包含 ", length(sheets), " 个工作表，已读取第一个：'", sheets[1],
      "'。如需读取其他工作表，请在代码中用 readxl::read_excel(path, sheet = ...) 指定。"
    ))
  }
  dataset
}

baa_read_scientific_bundle <- function(manifest, load_profile = NULL) {
  load_profile <- baa_normalize_load_profile(load_profile)
  assets <- baa_scientific_manifest_assets(manifest)
  if (!length(assets)) {
    stop("Scientific dataset manifest has no assets.", call. = FALSE)
  }
  primary_asset <- baa_primary_scientific_asset(assets)
  adapter_format <- baa_bundle_adapter_format(primary_asset, assets)
  primary_asset$format <- adapter_format
  registry <- baa_scientific_adapter_registry()
  spec <- registry[[adapter_format]]
  if (is.null(spec)) {
    stop(
      paste0("No scientific data adapter is registered for format '", adapter_format, "'."),
      call. = FALSE
    )
  }
  dataset <- baa_read_scientific_adapter(
    primary_asset,
    assets,
    load_profile = load_profile
  )
  adapter_id <- baa_dataset_adapter_id(dataset, spec$adapter_id)
  structure(
    list(
      dataset = dataset,
      manifest = manifest,
      assets = assets,
      primary_asset = primary_asset,
      adapter_id = adapter_id,
      object_family = if (baa_is_scientific_handle(dataset)) "scientific_handle" else spec$object_family,
      requested_load_profile = load_profile,
      load_mode = if (baa_is_lazy_dataset(dataset)) {
        "lazy"
      } else if (baa_is_scientific_handle(dataset)) {
        "handle"
      } else {
        "eager"
      }
    ),
    class = c("baa_scientific_bundle", "list")
  )
}

baa_bundle_dataset <- function(bundle) {
  if (!inherits(bundle, "baa_scientific_bundle") || !"dataset" %in% names(bundle)) {
    stop("Expected a loaded baa_scientific_bundle.", call. = FALSE)
  }
  bundle$dataset
}

baa_read_dataset <- function(path, format, load_profile = NULL) {
  bundle <- baa_read_scientific_bundle(list(
    path = path,
    format = format,
    name = basename(path)
  ), load_profile = load_profile)
  baa_bundle_dataset(bundle)
}

baa_as_table_data <- function(data) {
  if (inherits(data, "gtsummary") && !is.null(data$table_body)) {
    data <- data$table_body
  } else if (inherits(data, "flextable") && !is.null(data$body$dataset)) {
    data <- data$body$dataset
  }

  out <- tryCatch(
    as.data.frame(data, stringsAsFactors = FALSE, check.names = FALSE),
    error = function(e) {
      tryCatch(as.data.frame(data), error = function(e2) e2)
    }
  )
  if (inherits(out, "error")) {
    stop(paste("Unable to convert table artifact to data.frame:", conditionMessage(out)))
  }

  rownames(out) <- NULL
  out[] <- lapply(out, function(column) {
    if (is.factor(column)) {
      return(as.character(column))
    }
    if (is.list(column)) {
      return(vapply(column, function(value) {
        if (length(value) == 0 || all(is.na(value))) {
          return("")
        }
        paste(as.character(unlist(value)), collapse = "; ")
      }, character(1)))
    }
    column
  })
  out
}

baa_artifact_metadata <- function(metadata = list()) {
  if (is.null(metadata)) {
    return(list())
  }
  if (is.list(metadata)) {
    return(metadata)
  }
  list(note = as.character(metadata))
}

baa_is_blank <- function(value) {
  is.null(value) || length(value) == 0 || is.na(value) || !nzchar(trimws(as.character(value)))
}

baa_first_existing_name <- function(names_vec, candidates) {
  matched <- names_vec[tolower(names_vec) %in% tolower(candidates)]
  if (length(matched)) matched[[1]] else ""
}

baa_table_one_like <- function(name, data, metadata = list()) {
  table_name <- tolower(as.character(name))
  role <- tolower(as.character(metadata$role %||% ""))
  method <- tolower(as.character(metadata$method_family %||% ""))
  title <- tolower(as.character(metadata$title %||% ""))
  any(grepl("table[_ -]?1|table[_ -]?one|baseline|characteristic", c(table_name, role, method, title)))
}

baa_split_level_stat <- function(value) {
  text <- trimws(as.character(value %||% ""))
  if (!nzchar(text) || !grepl(":\\s*", text) || !grepl(";", text, fixed = TRUE)) {
    return(NULL)
  }
  pieces <- strsplit(text, "\\s*;\\s*")[[1]]
  parsed <- list()
  for (piece in pieces) {
    match <- regexec("^\\s*([^:]+?)\\s*:\\s*(.+?)\\s*$", piece, perl = TRUE)
    parts <- regmatches(piece, match)[[1]]
    if (length(parts) != 3) {
      return(NULL)
    }
    parsed[[trimws(parts[[2]])]] <- trimws(parts[[3]])
  }
  parsed
}

baa_expand_collapsed_table_one <- function(data, name = "", metadata = list()) {
  if (!is.data.frame(data) || nrow(data) == 0 || ncol(data) < 4) {
    return(data)
  }
  if (!baa_table_one_like(name, data, metadata)) {
    return(data)
  }
  names_vec <- names(data)
  variable_col <- baa_first_existing_name(names_vec, c("Variable", "variable"))
  level_col <- baa_first_existing_name(names_vec, c("Level", "level"))
  if (!nzchar(variable_col)) {
    return(data)
  }
  if (!nzchar(level_col)) {
    data[["Level"]] <- ""
    level_col <- "Level"
  }
  stat_cols <- setdiff(names(data), c(variable_col, level_col, "P_value", "P value", "p_value", "p.value", "Missing", "SMD"))
  if (!length(stat_cols)) {
    return(data)
  }
  expanded <- list()
  changed <- FALSE
  for (i in seq_len(nrow(data))) {
    row <- data[i, , drop = FALSE]
    row_level <- row[[level_col]][[1]]
    parsed_by_col <- lapply(stat_cols, function(col) baa_split_level_stat(row[[col]][[1]]))
    names(parsed_by_col) <- stat_cols
    parsed_by_col <- parsed_by_col[!vapply(parsed_by_col, is.null, logical(1))]
    if (!baa_is_blank(row_level) || !length(parsed_by_col)) {
      expanded[[length(expanded) + 1]] <- row
      next
    }
    levels <- unique(unlist(lapply(parsed_by_col, names), use.names = FALSE))
    if (length(levels) < 2) {
      expanded[[length(expanded) + 1]] <- row
      next
    }
    changed <- TRUE
    first <- TRUE
    for (level in levels) {
      next_row <- row
      next_row[[variable_col]] <- if (first) row[[variable_col]][[1]] else ""
      next_row[[level_col]] <- level
      for (col in stat_cols) {
        parsed <- parsed_by_col[[col]]
        if (!is.null(parsed) && !is.null(parsed[[level]])) {
          next_row[[col]] <- parsed[[level]]
        }
      }
      for (col in setdiff(names(data), c(variable_col, level_col, stat_cols))) {
        if (!first) next_row[[col]] <- ""
      }
      expanded[[length(expanded) + 1]] <- next_row
      first <- FALSE
    }
  }
  if (!changed) {
    return(data)
  }
  out <- do.call(rbind, expanded)
  rownames(out) <- NULL
  out
}

baa_table <- function(name, data, metadata = list()) {
  artifact_metadata <- baa_artifact_metadata(metadata)
  table_data <- tryCatch(
    baa_as_table_data(data),
    error = function(e) {
      baa_record_warning(paste(
        "Unable to save table artifact",
        as.character(name),
        ":",
        conditionMessage(e)
      ))
      NULL
    }
  )
  if (is.null(table_data)) {
    return(invisible(NULL))
  }
  table_data <- baa_expand_collapsed_table_one(table_data, name = name, metadata = artifact_metadata)
  path <- baa_artifact_path(name, "csv")
  write_ok <- tryCatch(
    {
      readr::write_csv(table_data, path)
      TRUE
    },
    error = function(e) {
      baa_record_warning(paste(
        "Unable to write table artifact",
        as.character(name),
        ":",
        conditionMessage(e)
      ))
      FALSE
    }
  )
  if (!write_ok || !file.exists(path)) {
    return(invisible(NULL))
  }
  baa_register_artifact("tables", list(
    name = as.character(name),
    path = baa_normalize_path(path),
    kind = "table",
    mime_type = "text/csv",
    metadata = artifact_metadata
  ))
  invisible(path)
}

baa_format_p <- function(p, digits = 3) {
  if (is.null(p) || length(p) == 0 || is.na(p)) {
    return("")
  }
  if (p < 0.001) {
    return("<0.001")
  }
  formatC(p, format = "f", digits = digits)
}

baa_format_smd <- function(value, digits = 3) {
  if (is.null(value) || length(value) == 0 || is.na(value) || !is.finite(value)) {
    return("")
  }
  formatC(abs(value), format = "f", digits = digits)
}

baa_count_pct <- function(x, level) {
  denom <- sum(!is.na(x))
  n <- sum(!is.na(x) & x == level)
  if (denom == 0) {
    return("")
  }
  sprintf("%s (%.1f%%)", format(n, big.mark = ",", trim = TRUE), 100 * n / denom)
}

baa_table_one_numeric <- function(x) {
  source <- if (is.factor(x)) as.character(x) else x
  suppressWarnings(as.numeric(source))
}

baa_mean_sd <- function(x, digits = 1) {
  x <- baa_table_one_numeric(x)
  x <- x[!is.na(x)]
  if (!length(x)) {
    return("")
  }
  sprintf(paste0("%.", digits, "f (%.", digits, "f)"), mean(x), stats::sd(x))
}

baa_median_iqr <- function(x, digits = 1) {
  x <- baa_table_one_numeric(x)
  x <- x[is.finite(x)]
  if (!length(x)) {
    return("")
  }
  values <- stats::quantile(x, probs = c(0.25, 0.5, 0.75), names = FALSE, na.rm = TRUE)
  sprintf(
    paste0("%.", digits, "f (%.", digits, "f–%.", digits, "f)"),
    values[2], values[1], values[3]
  )
}

baa_table_one_continuous_style <- function(x, by, mode = "auto") {
  mode <- match.arg(mode, c("auto", "mean_sd", "median_iqr"))
  if (mode != "auto") {
    return(mode)
  }
  numeric_x <- baa_table_one_numeric(x)
  keep <- is.finite(numeric_x) & !is.na(by)
  x <- numeric_x[keep]
  by <- droplevels(factor(by[keep]))
  if (!length(x) || nlevels(by) < 1) {
    return("median_iqr")
  }
  approximately_normal <- function(values) {
    values <- values[is.finite(values)]
    if (length(values) < 3) {
      return(FALSE)
    }
    if (length(unique(values)) < 3) {
      return(FALSE)
    }
    if (length(values) > 200) {
      spread <- stats::sd(values)
      if (!is.finite(spread) || spread == 0) {
        return(FALSE)
      }
      standardized <- (values - mean(values)) / spread
      skewness <- mean(standardized^3)
      quartiles <- stats::quantile(
        values,
        probs = c(0.25, 0.5, 0.75),
        names = FALSE,
        na.rm = TRUE
      )
      iqr <- quartiles[3] - quartiles[1]
      bowley_skew <- if (is.finite(iqr) && iqr > 0) {
        (quartiles[3] + quartiles[1] - 2 * quartiles[2]) / iqr
      } else {
        Inf
      }
      return(
        is.finite(skewness)
        && abs(skewness) <= 1
        && is.finite(bowley_skew)
        && abs(bowley_skew) <= 0.3
      )
    }
    p <- tryCatch(stats::shapiro.test(values)$p.value, error = function(e) NA_real_)
    is.finite(p) && p >= 0.05
  }
  group_sizes <- vapply(levels(by), function(level) sum(by == level), integer(1))
  if (any(group_sizes < 3)) {
    return(if (approximately_normal(x)) "mean_sd" else "median_iqr")
  }
  group_is_approximately_normal <- vapply(
    levels(by),
    function(level) approximately_normal(x[by == level]),
    logical(1)
  )
  if (length(group_is_approximately_normal) && all(group_is_approximately_normal)) {
    "mean_sd"
  } else {
    "median_iqr"
  }
}

baa_table_one_p <- function(x, by, categorical = FALSE, continuous_method = "mean_sd") {
  keep <- !is.na(x) & !is.na(by)
  x <- x[keep]
  by <- by[keep]
  if (length(unique(by)) < 2 || !length(x)) {
    return(NA_real_)
  }
  tryCatch({
    if (categorical) {
      tab <- table(x, by)
      if (any(dim(tab) < 2)) {
        return(NA_real_)
      }
      chi <- suppressWarnings(stats::chisq.test(tab, correct = FALSE))
      if (any(chi$expected < 5) && prod(dim(tab)) <= 200) {
        return(stats::fisher.test(tab)$p.value)
      }
      return(chi$p.value)
    }
    x_num <- baa_table_one_numeric(x)
    if (identical(continuous_method, "median_iqr")) {
      if (length(unique(by)) == 2) {
        return(stats::wilcox.test(x_num ~ by, exact = FALSE)$p.value)
      }
      return(stats::kruskal.test(x_num ~ by)$p.value)
    }
    if (length(unique(by)) == 2) {
      return(stats::t.test(x_num ~ by)$p.value)
    }
    return(summary(stats::aov(x_num ~ by))[[1]][["Pr(>F)"]][1])
  }, error = function(e) NA_real_)
}

baa_is_table_one_categorical <- function(x) {
  if (is.factor(x) || is.character(x) || is.logical(x)) {
    return(TRUE)
  }
  if (is.numeric(x) || is.integer(x)) {
    return(length(unique(stats::na.omit(x))) <= 6)
  }
  TRUE
}

baa_table_one_smd_continuous <- function(x, by) {
  keep <- !is.na(x) & !is.na(by)
  x <- baa_table_one_numeric(x[keep])
  by <- droplevels(factor(by[keep]))
  levels_by <- levels(by)
  if (length(levels_by) < 2 || length(x) < 2) {
    return(NA_real_)
  }
  pairs <- utils::combn(levels_by, 2, simplify = FALSE)
  values <- vapply(pairs, function(pair) {
    x1 <- x[by == pair[1]]
    x2 <- x[by == pair[2]]
    if (length(x1) < 2 || length(x2) < 2) {
      return(NA_real_)
    }
    pooled <- sqrt((stats::var(x1, na.rm = TRUE) + stats::var(x2, na.rm = TRUE)) / 2)
    if (!is.finite(pooled) || pooled == 0) {
      return(NA_real_)
    }
    abs(mean(x1, na.rm = TRUE) - mean(x2, na.rm = TRUE)) / pooled
  }, numeric(1))
  values <- values[is.finite(values)]
  if (!length(values)) {
    return(NA_real_)
  }
  max(values)
}

baa_table_one_smd_level <- function(x, by, level) {
  keep <- !is.na(x) & !is.na(by)
  x <- x[keep]
  by <- droplevels(factor(by[keep]))
  levels_by <- levels(by)
  if (length(levels_by) < 2 || !length(x)) {
    return(NA_real_)
  }
  pairs <- utils::combn(levels_by, 2, simplify = FALSE)
  values <- vapply(pairs, function(pair) {
    p1 <- mean(x[by == pair[1]] == level, na.rm = TRUE)
    p2 <- mean(x[by == pair[2]] == level, na.rm = TRUE)
    pooled <- (p1 + p2) / 2
    denom <- sqrt(pooled * (1 - pooled))
    if (!is.finite(denom) || denom == 0) {
      return(NA_real_)
    }
    abs(p1 - p2) / denom
  }, numeric(1))
  values <- values[is.finite(values)]
  if (!length(values)) {
    return(NA_real_)
  }
  max(values)
}

baa_table_one <- function(
  name = "table_one",
  data = dataset,
  by = NULL,
  vars = NULL,
  categorical = NULL,
  continuous = NULL,
  labels = NULL,
  level_labels = NULL,
  digits = 1,
  p_digits = 3,
  continuous_summary = c("auto", "mean_sd", "median_iqr"),
  include_overall = TRUE,
  include_missing = TRUE,
  metadata = list()
) {
  continuous_summary <- match.arg(continuous_summary)
  df <- as.data.frame(data)
  grouped <- !is.null(by) && length(by) > 0L && nzchar(as.character(by[[1L]]))
  if (grouped && (length(by) != 1L || !by %in% names(df))) {
    stop(paste("Table One grouping variable not found:", by))
  }
  if (is.null(vars)) {
    vars <- setdiff(names(df), by)
  }
  vars <- vars[vars %in% names(df)]
  if (!length(vars)) {
    stop("Table One has no variables to summarize")
  }

  group <- if (grouped) df[[by]] else factor(rep("Overall", nrow(df)))
  if (!grouped) include_overall <- TRUE
  if (!is.factor(group)) {
    group <- factor(group)
  } else {
    group <- droplevels(group)
  }
  group_levels <- if (grouped) levels(group) else character(0)
  group_ns <- vapply(group_levels, function(level) sum(!is.na(group) & group == level), numeric(1))
  group_columns <- if (length(group_levels)) paste0(
    group_levels,
    " (n=",
    format(group_ns, big.mark = ",", trim = TRUE),
    ")"
  ) else character(0)
  overall_column <- paste0("Overall (N=", format(nrow(df), big.mark = ",", trim = TRUE), ")")
  include_smd <- length(group_levels) >= 2
  smd_column <- if (length(group_levels) > 2) "SMD (max pairwise)" else "SMD"

  rows <- list()
  continuous_styles <- character(0)
  append_row <- function(row) {
    rows[[length(rows) + 1]] <<- row
  }

  base_row <- c(
    list(Variable = "N", Level = ""),
    if (include_overall) stats::setNames(list(format(nrow(df), big.mark = ",", trim = TRUE)), overall_column) else list(),
    stats::setNames(as.list(format(group_ns, big.mark = ",", trim = TRUE)), group_columns),
    if (include_missing) list(Missing = "") else list(),
    if (include_smd) stats::setNames(list(""), smd_column) else list(),
    if (include_smd) list(`P value` = "") else list()
  )
  append_row(base_row)

  for (var in vars) {
    x <- df[[var]]
    label <- if (!is.null(labels) && !is.null(labels[[var]])) as.character(labels[[var]]) else var
    is_cat <- if (!is.null(categorical) && var %in% categorical) {
      TRUE
    } else if (!is.null(continuous) && var %in% continuous) {
      FALSE
    } else {
      baa_is_table_one_categorical(x)
    }
    continuous_style <- if (is_cat) {
      ""
    } else {
      baa_table_one_continuous_style(x, group, mode = continuous_summary)
    }
    if (!is_cat) {
      continuous_styles[var] <- continuous_style
    }
    p_value <- baa_format_p(
      baa_table_one_p(
        x,
        group,
        categorical = is_cat,
        continuous_method = continuous_style
      ),
      p_digits
    )
    missing_n <- sum(is.na(x))
    missing_text <- if (missing_n > 0) format(missing_n, big.mark = ",", trim = TRUE) else "0"

    if (is_cat) {
      xf <- if (is.factor(x)) droplevels(x) else factor(x)
      levels_x <- levels(xf)
      first <- TRUE
      for (level in levels_x) {
        display_level <- as.character(level)
        if (
          !is.null(level_labels)
          && !is.null(level_labels[[var]])
          && !is.null(level_labels[[var]][[display_level]])
        ) {
          display_level <- as.character(level_labels[[var]][[display_level]])
        }
        smd_value <- if (include_smd) baa_format_smd(baa_table_one_smd_level(xf, group, level)) else NULL
        row <- c(
          list(Variable = if (first) label else "", Level = display_level),
          if (include_overall) stats::setNames(list(baa_count_pct(xf, level)), overall_column) else list(),
          stats::setNames(
            lapply(group_levels, function(group_level) baa_count_pct(xf[group == group_level], level)),
            group_columns
          ),
          if (include_missing) list(Missing = if (first) missing_text else "") else list(),
          if (include_smd) stats::setNames(list(smd_value), smd_column) else list(),
          if (include_smd) list(`P value` = if (first) p_value else "") else list()
        )
        append_row(row)
        first <- FALSE
      }
    } else {
      smd_value <- if (include_smd) baa_format_smd(baa_table_one_smd_continuous(x, group)) else NULL
      summary_function <- if (identical(continuous_style, "median_iqr")) {
        baa_median_iqr
      } else {
        baa_mean_sd
      }
      summary_label <- if (identical(continuous_style, "median_iqr")) "Median (IQR)" else "Mean (SD)"
      row <- c(
        list(Variable = label, Level = summary_label),
        if (include_overall) stats::setNames(list(summary_function(x, digits)), overall_column) else list(),
        stats::setNames(
          lapply(
            group_levels,
            function(group_level) summary_function(x[group == group_level], digits)
          ),
          group_columns
        ),
        if (include_missing) list(Missing = missing_text) else list(),
        if (include_smd) stats::setNames(list(smd_value), smd_column) else list(),
        if (include_smd) list(`P value` = p_value) else list()
      )
      append_row(row)
    }
  }

  out <- do.call(
    rbind,
    lapply(rows, function(row) {
      as.data.frame(row, stringsAsFactors = FALSE, check.names = FALSE)
    })
  )
  rownames(out) <- NULL
  table_metadata <- baa_artifact_metadata(metadata)
  if (is.null(table_metadata$role)) {
    table_metadata$role <- "baseline"
  }
  if (is.null(table_metadata$semantic_key)) {
    table_metadata$semantic_key <- "baseline_table"
  }
  if (is.null(table_metadata$acceptance_evidence)) {
    table_metadata$acceptance_evidence <- if (include_smd) c(
      "总体与分组描述",
      "P 值",
      "SMD",
      "效应量或不确定性",
      "可由结果页导出为 CSV/XLSX/DOCX"
    ) else c("总体描述", "可由结果页导出为 CSV/XLSX/DOCX")
  }
  if (is.null(table_metadata$method_family)) {
    table_metadata$method_family <- "Table One"
  }
  if (is.null(table_metadata$title)) {
    table_metadata$title <- "Table One baseline characteristics"
  }
  if (is.null(table_metadata$paper_ready)) {
    table_metadata$paper_ready <- TRUE
  }
  baa_table(name, out, metadata = table_metadata)
  baa_text(
    paste0(name, "_note"),
    paste0(
      "Table One generated by ", if (grouped) by else "overall sample",
      ". Continuous-variable summaries were selected ",
      if (identical(continuous_summary, "auto")) "from group-wise distribution checks" else "from the requested rule",
      ": ",
      if (length(continuous_styles)) {
        paste0(names(continuous_styles), "=", gsub("_", " ", continuous_styles), collapse = "; ")
      } else {
        "no continuous variables"
      },
      if (include_smd) {
        paste0(". Mean (SD) uses t test/ANOVA; median (IQR) uses Wilcoxon/Kruskal-Wallis. ",
               "Categorical variables are shown as n (%) with chi-square or Fisher's exact test as applicable.")
      } else {
        ". Descriptive summaries only; no between-group tests, P values, or SMD were calculated. Categorical variables are shown as n (%)."
      },
      if (length(group_levels) == 2) {
        " Absolute SMD is reported for the two-group comparison."
      } else if (length(group_levels) > 2) {
        " Maximum absolute pairwise SMD is reported for multi-group comparisons."
      } else {
        ""
      }
    ),
    metadata = list(role = "method_note", method_family = "Table One", title = "Table One method note")
  )
  invisible(out)
}

baa_save_companion_plot <- function(path, plot, width, height, dpi = NULL, device = NULL, ...) {
  tryCatch({
    args <- list(
      filename = path,
      plot = plot,
      width = width,
      height = height,
      units = "in"
    )
    if (!is.null(dpi)) {
      args$dpi <- dpi
    }
    if (!is.null(device)) {
      args$device <- device
    }
    extras <- list(...)
    if (length(extras)) {
      args <- c(args, extras)
    }
    do.call(ggplot2::ggsave, args)
    TRUE
  }, error = function(e) {
    baa_record_warning(paste(
      "Unable to create companion plot artifact",
      basename(path),
      ":",
      conditionMessage(e)
    ))
    FALSE
  })
}

baa_cairo_available <- function() {
  isTRUE(tryCatch(grDevices::capabilities("cairo"), error = function(e) FALSE))
}

baa_plot <- function(name, plot, width = 8, height = 5, dpi = 300, metadata = list()) {
  # Clamps keep a single oversized request from producing hundreds-of-MB
  # artifacts that the vision channel silently skips.
  width <- if (is.numeric(width) && length(width) == 1 && is.finite(width)) min(max(width, 4), 20) else 8
  height <- if (is.numeric(height) && length(height) == 1 && is.finite(height)) min(max(height, 4), 20) else 5
  dpi <- if (is.numeric(dpi) && length(dpi) == 1 && is.finite(dpi)) min(max(dpi, 72), 600) else 300
  path <- baa_artifact_path(name, "png")
  artifact_metadata <- baa_artifact_metadata(metadata)
  # Cairo devices apply system font fallback, so Chinese axis labels and
  # titles render instead of collapsing to dots/tofu (grDevices::pdf()
  # cannot embed CJK at all — the PDF companion therefore uses cairo_pdf).
  png_args <- list(
    filename = path,
    plot = plot,
    width = width,
    height = height,
    units = "in",
    dpi = dpi,
    bg = "white"
  )
  if (baa_cairo_available()) {
    png_args$type <- "cairo"
  }
  do.call(ggplot2::ggsave, png_args)
  pdf_path <- baa_artifact_path(name, "pdf")
  pdf_saved <- baa_save_companion_plot(
    pdf_path,
    plot,
    width,
    height,
    device = if (baa_cairo_available()) "cairo_pdf" else "pdf"
  )
  tiff_path <- baa_artifact_path(name, "tiff")
  tiff_saved <- baa_save_companion_plot(
    tiff_path,
    plot,
    width,
    height,
    dpi = max(dpi, 600),
    device = "tiff",
    compression = "lzw",
    bg = "white",
    type = if (baa_cairo_available()) "cairo" else NULL
  )
  baa_register_artifact("figures", list(
    name = as.character(name),
    path = baa_normalize_path(path),
    kind = "figure",
    mime_type = "image/png",
    metadata = utils::modifyList(artifact_metadata, list(format_variant = "png_preview"))
  ))
  if (isTRUE(pdf_saved) && file.exists(pdf_path)) {
    baa_register_artifact("figures", list(
      name = paste0(as.character(name), "_pdf"),
      path = baa_normalize_path(pdf_path),
      kind = "figure",
      mime_type = "application/pdf",
      metadata = utils::modifyList(artifact_metadata, list(format_variant = "pdf_vector"))
    ))
  }
  if (isTRUE(tiff_saved) && file.exists(tiff_path)) {
    baa_register_artifact("figures", list(
      name = paste0(as.character(name), "_tiff"),
      path = baa_normalize_path(tiff_path),
      kind = "figure",
      mime_type = "image/tiff",
      metadata = utils::modifyList(artifact_metadata, list(format_variant = "tiff_print"))
    ))
  }
  invisible(path)
}

baa_text <- function(name, text, metadata = list()) {
  path <- baa_artifact_path(name, "txt")
  writeLines(as.character(text), path, useBytes = TRUE)
  baa_register_artifact("texts", list(
    name = as.character(name),
    path = baa_normalize_path(path),
    kind = "text",
    mime_type = "text/plain",
    metadata = baa_artifact_metadata(metadata)
  ))
  invisible(path)
}

baa_model_summary <- function(name, model, metadata = list()) {
  text <- paste(capture.output(summary(model)), collapse = "\n")
  model_metadata <- baa_artifact_metadata(metadata)
  if (is.null(model_metadata$role)) {
    model_metadata$role <- "model_summary"
  }
  path <- baa_artifact_path(name, "txt")
  writeLines(text, path, useBytes = TRUE)
  baa_register_artifact("models", list(
    name = as.character(name),
    path = baa_normalize_path(path),
    kind = "model",
    mime_type = "text/plain",
    metadata = model_metadata
  ))
  invisible(path)
}

baa_path_is_within_output <- function(path) {
  resolved <- baa_normalize_path(path)
  root <- paste0(sub("/+$", "", baa_normalize_path(baa_output_dir)), "/")
  identical(resolved, sub("/+$", "", baa_normalize_path(baa_output_dir))) || startsWith(resolved, root)
}

baa_file <- function(
  name,
  path,
  kind = "artifact",
  mime_type = "application/octet-stream",
  metadata = list(),
  copy = TRUE
) {
  source <- normalizePath(path, winslash = "/", mustWork = TRUE)
  if (!file.info(source)$isdir && file.exists(source)) {
    extension <- tools::file_ext(source)
    target <- if (baa_path_is_within_output(source) && !isTRUE(copy)) {
      source
    } else {
      baa_artifact_path(name, if (nzchar(extension)) extension else "bin")
    }
    if (!identical(source, target)) {
      copied <- file.copy(source, target, overwrite = TRUE, copy.mode = FALSE, copy.date = FALSE)
      if (!isTRUE(copied)) stop(paste("Unable to copy scientific artifact:", source))
    }
    if (!baa_path_is_within_output(target)) stop("Scientific artifact must be inside the analysis output directory")
    artifact_metadata <- baa_artifact_metadata(metadata)
    artifact_metadata$artifact_size_bytes <- unname(file.info(target)$size)
    baa_register_artifact("artifacts", list(
      name = as.character(name),
      path = baa_normalize_path(target),
      kind = as.character(kind),
      mime_type = as.character(mime_type),
      metadata = artifact_metadata
    ))
    return(invisible(target))
  }
  stop("Scientific artifact must be an existing regular file")
}

baa_save_object <- function(name, object, metadata = list(), compress = FALSE) {
  path <- baa_artifact_path(name, "rds")
  saveRDS(object, path, compress = compress)
  baa_file(
    name,
    path,
    kind = "scientific_object",
    mime_type = "application/x-r-rds",
    metadata = metadata,
    copy = FALSE
  )
}

baa_validate_checkpoint_name <- function(name) {
  value <- baa_scalar_text(name)
  if (!nzchar(value) || !grepl("^[A-Za-z0-9_-]+$", value)) {
    stop(
      "Workflow checkpoint name must contain only letters, numbers, underscore, or hyphen.",
      call. = FALSE
    )
  }
  value
}

baa_checkpoint_filename <- function(name) {
  paste0("checkpoint_", baa_validate_checkpoint_name(name), ".rds")
}

baa_checkpoint <- function(name, object, metadata = list(), compress = FALSE) {
  name <- baa_validate_checkpoint_name(name)
  checkpoint_metadata <- baa_artifact_metadata(metadata)
  checkpoint_metadata$checkpoint <- TRUE
  checkpoint_metadata$role <- checkpoint_metadata$role %||% "workflow_checkpoint"
  path <- baa_artifact_path(paste0("checkpoint_", name), "rds")
  saveRDS(object, path, compress = compress)
  baa_file(
    paste0("checkpoint_", name),
    path,
    kind = "checkpoint",
    mime_type = "application/x-r-rds",
    metadata = checkpoint_metadata,
    copy = FALSE
  )
}

baa_load_checkpoint <- function(name) {
  name <- baa_validate_checkpoint_name(name)
  filename <- baa_checkpoint_filename(name)
  checkpoint_root <- trimws(Sys.getenv("BAA_CHECKPOINT_DIR", unset = ""))
  candidates <- character()
  if (nzchar(checkpoint_root) && dir.exists(checkpoint_root)) {
    read_only_root <- normalizePath(checkpoint_root, winslash = "/", mustWork = TRUE)
    read_only_candidate <- normalizePath(
      file.path(read_only_root, filename), winslash = "/", mustWork = FALSE
    )
    root_prefix <- paste0(sub("/+$", "", read_only_root), "/")
    if (!startsWith(read_only_candidate, root_prefix)) {
      stop("Workflow checkpoint path escaped BAA_CHECKPOINT_DIR.", call. = FALSE)
    }
    candidates <- c(candidates, read_only_candidate)
  }
  candidates <- c(candidates, baa_artifact_path(paste0("checkpoint_", name), "rds"))
  existing <- candidates[file.exists(candidates)]
  if (!length(existing)) stop(paste("Workflow checkpoint not found:", name), call. = FALSE)
  readRDS(existing[[1]])
}

baa_flush_artifacts <- function() {
  jsonlite::write_json(
    list(
      tables = baa_artifacts$tables,
      figures = baa_artifacts$figures,
      texts = baa_artifacts$texts,
      models = baa_artifacts$models,
      artifacts = baa_artifacts$artifacts,
      warnings = baa_artifacts$warnings
    ),
    file.path(baa_output_dir, "artifacts.json"),
    auto_unbox = TRUE,
    pretty = TRUE
  )
  invisible(file.path(baa_output_dir, "artifacts.json"))
}
