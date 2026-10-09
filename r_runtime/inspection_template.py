"""Canonical bounded dataset inspection used by every R execution adapter."""

from __future__ import annotations


DATASET_PROFILE_R_CODE = r'''
object_classes <- class(dataset)
object_kind <- paste(object_classes, collapse = "/")

safe_names <- function(value) tryCatch(as.character(names(value)), error = function(e) character())
safe_dim <- function(value) {
  dims <- tryCatch(dim(value), error = function(e) NULL)
  if (is.null(dims)) integer() else as.integer(dims)
}
safe_axis <- function(dimensions, index) {
  if (length(dimensions) < index || is.na(dimensions[[index]])) NA_integer_ else dimensions[[index]]
}
safe_metadata_names <- function(value) {
  if (!requireNamespace("S4Vectors", quietly = TRUE)) return(character())
  tryCatch(as.character(colnames(S4Vectors::mcols(value))), error = function(e) character())
}
bounded_data_frame <- function(value, limit) {
  tryCatch(
    as.data.frame(utils::head(value, limit)),
    error = function(e) data.frame()
  )
}
inspection_row_limit <- if (exists("baa_lazy_inspection_rows", mode = "function")) {
  baa_lazy_inspection_rows()
} else {
  2000L
}
inspection_column_limit <- 256L
overview_rows <- NULL
overview_columns <- NULL

structure_profile <- list(
  schema_version = "2.0",
  object_classes = as.character(object_classes),
  object_kind = object_kind,
  modality = "tabular",
  representation = "data_frame",
  processing_stage = "analysis_ready",
  dimensions = as.list(safe_dim(dataset)),
  assays = list(),
  axes = list(),
  sample_metadata = character(),
  feature_metadata = character(),
  reductions = character(),
  alternate_experiments = character(),
  spatial = list(),
  sample_map = list(),
  indexes = list(),
  capabilities = character(),
  upstream_required = character()
)

profile_data <- tryCatch({
  if (inherits(dataset, "baa_scientific_handle")) {
    structure_profile$modality <- tryCatch(as.character(dataset$modality), error = function(e) "scientific_object")
    structure_profile$representation <- paste0(tryCatch(as.character(dataset$format), error = function(e) "scientific"), "_handle")
    structure_profile$processing_stage <- tryCatch(as.character(dataset$processing_stage), error = function(e) "raw")
    structure_profile$axes <- tryCatch(dataset$axes, error = function(e) list())
    structure_profile$indexes <- tryCatch(dataset$structure, error = function(e) list())
    structure_profile$capabilities <- tryCatch(as.character(dataset$capabilities), error = function(e) character())
    structure_profile$upstream_required <- tryCatch(as.character(dataset$upstream_required), error = function(e) character())
    structure_profile$assets <- tryCatch(lapply(dataset$assets, function(asset) {
      list(
        name = as.character(asset$name %||% ""),
        format = as.character(asset$format %||% ""),
        role = as.character(asset$role %||% ""),
        kind = as.character(asset$kind %||% "")
      )
    }), error = function(e) list())
    bounded <- tryCatch(utils::head(dataset$profile_data, inspection_row_limit), error = function(e) data.frame())
    structure_profile$inspection <- list(
      mode = "bounded_scientific_handle_profile",
      sample_rows = nrow(bounded),
      row_count_exact = FALSE
    )
    overview_rows <- NA_integer_
    overview_columns <- ncol(bounded)
    bounded
  } else if (inherits(dataset, "baa_10x_multimodal")) {
    modality_names <- as.character(names(dataset))
    assay_dimensions <- lapply(dataset, safe_dim)
    cell_counts <- vapply(assay_dimensions, function(value) safe_axis(value, 2), integer(1))
    structure_profile$modality <- "multi_omics"
    structure_profile$representation <- "10x_multimodal"
    structure_profile$processing_stage <- "counts"
    structure_profile$axes <- list(
      modalities = length(dataset),
      cells = if (length(cell_counts) && length(unique(cell_counts)) == 1L) cell_counts[[1]] else NA_integer_
    )
    structure_profile$assays <- lapply(seq_along(dataset), function(index) {
      list(name = modality_names[[index]], dimensions = as.list(assay_dimensions[[index]]))
    })
    tenx_context <- tryCatch(attr(dataset, "baa_10x_bundle_context", exact = TRUE), error = function(e) NULL)
    if (!is.null(tenx_context)) {
      structure_profile$spatial <- list(
        assets = tenx_context$assets,
        coordinate_rows_profiled = nrow(tenx_context$coordinate_profile %||% data.frame()),
        profile_bounded = TRUE
      )
    }
    structure_profile$capabilities <- c("multiple_modalities", "sparse_counts", "shared_cell_axis")
    structure_profile$inspection <- list(
      mode = "bounded_modality_summary",
      sample_rows = length(dataset),
      row_count_exact = TRUE
    )
    overview_rows <- length(dataset)
    overview_columns <- 3L
    data.frame(
      modality = modality_names,
      features = vapply(assay_dimensions, function(value) safe_axis(value, 1), integer(1)),
      cells = cell_counts,
      stringsAsFactors = FALSE
    )
  } else if (
    (exists("baa_is_lazy_dataset", mode = "function") && baa_is_lazy_dataset(dataset)) ||
    (exists("baa_is_lazy_table", mode = "function") && baa_is_lazy_table(dataset))
  ) {
    is_wrapper <- exists("baa_is_lazy_dataset", mode = "function") && baa_is_lazy_dataset(dataset)
    lazy_columns <- if (is_wrapper) {
      baa_lazy_dataset_info(dataset)$columns
    } else {
      tryCatch(as.character(colnames(dataset)), error = function(e) character())
    }
    structure_profile$representation <- if (
      is_wrapper || inherits(dataset, "tbl_duckdb_connection")
    ) "lazy_duckdb" else "lazy_table"
    structure_profile$dimensions <- list(NULL, length(lazy_columns))
    structure_profile$axes <- list(observations = NA_integer_, variables = length(lazy_columns))
    structure_profile$inspection <- list(
      mode = "bounded_sample",
      sample_rows = 0L,
      row_count_exact = FALSE
    )
    structure_profile$capabilities <- c(
      "lazy_query", "bounded_collect", "bounded_sample", "column_schema"
    )
    overview_rows <- NA_integer_
    overview_columns <- length(lazy_columns)
    sampled <- baa_sample_dataset(dataset, size = inspection_row_limit, seed = 1L)
    structure_profile$inspection$sample_rows <- nrow(sampled)
    sampled
  } else if (is.list(dataset) && !is.data.frame(dataset) &&
             !is.null(dataset$model) && is.data.frame(dataset$data) &&
             is.data.frame(dataset$training_data)) {
    # Inspect the observation table while retaining the original fitted model
    # bundle as the frozen object. Never fit or replace a model during preview.
    structure_profile$modality <- "prediction_model"
    structure_profile$representation <- "model_bundle"
    structure_profile$model_classes <- as.character(class(dataset$model))
    structure_profile$axes <- list(observations = nrow(dataset$data), variables = ncol(dataset$data))
    structure_profile$training_axes <- list(observations = nrow(dataset$training_data), variables = ncol(dataset$training_data))
    structure_profile$sample_metadata <- as.character(colnames(dataset$data))
    structure_profile$capabilities <- c("fitted_model", "observation_data", "training_background")
    overview_rows <- nrow(dataset$data)
    overview_columns <- ncol(dataset$data)
    structure_profile$inspection <- list(
      mode = "bounded_model_observations", sample_rows = min(nrow(dataset$data), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(dataset$data[, seq_len(min(ncol(dataset$data), inspection_column_limit)), drop = FALSE], inspection_row_limit)
  } else if (is.data.frame(dataset)) {
    structure_profile$axes <- list(observations = nrow(dataset), variables = ncol(dataset))
    overview_rows <- nrow(dataset)
    overview_columns <- ncol(dataset)
    sampled <- baa_sample_dataset(dataset, size = inspection_row_limit, seed = 1L)
    structure_profile$inspection <- list(
      mode = if (nrow(sampled) < nrow(dataset)) "bounded_row_sample" else "full",
      sample_rows = nrow(sampled),
      row_count_exact = TRUE
    )
    sampled
  } else if (is.matrix(dataset) || inherits(dataset, "Matrix")) {
    dimensions <- safe_dim(dataset)
    structure_profile$modality <- "matrix"
    structure_profile$representation <- if (inherits(dataset, "sparseMatrix")) "sparse_matrix" else "matrix"
    structure_profile$axes <- list(
      features = safe_axis(dimensions, 1),
      samples = safe_axis(dimensions, 2)
    )
    structure_profile$assays <- list(list(name = "matrix", dimensions = as.list(dimensions)))
    tenx_context <- tryCatch(attr(dataset, "baa_10x_bundle_context", exact = TRUE), error = function(e) NULL)
    if (!is.null(tenx_context)) {
      structure_profile$spatial <- list(
        assets = tenx_context$assets,
        coordinate_rows_profiled = nrow(tenx_context$coordinate_profile %||% data.frame()),
        profile_bounded = TRUE
      )
    }
    selected_rows <- seq_len(min(nrow(dataset), inspection_row_limit))
    selected_columns <- seq_len(min(ncol(dataset), inspection_column_limit))
    limited_matrix <- dataset[selected_rows, selected_columns, drop = FALSE]
    if (inherits(limited_matrix, "Matrix")) limited_matrix <- as.matrix(limited_matrix)
    matrix_profile <- as.data.frame(limited_matrix, check.names = FALSE)
    structure_profile$inspection <- list(
      mode = "bounded_matrix_slice",
      sample_rows = nrow(matrix_profile),
      sample_columns = ncol(matrix_profile),
      row_count_exact = TRUE
    )
    overview_rows <- nrow(dataset)
    overview_columns <- ncol(dataset)
    matrix_profile
  } else if (methods::is(dataset, "VCF") || inherits(dataset, "VCF")) {
    dimensions <- safe_dim(dataset)
    structure_profile$modality <- "genomics_variants"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- list(
      variants = safe_axis(dimensions, 1),
      samples = safe_axis(dimensions, 2)
    )
    row_ranges <- tryCatch(
      if (
        requireNamespace("SummarizedExperiment", quietly = TRUE) &&
        methods::is(dataset, "RangedSummarizedExperiment")
      ) SummarizedExperiment::rowRanges(dataset) else NULL,
      error = function(e) NULL
    )
    sample_data <- tryCatch(
      if (
        requireNamespace("SummarizedExperiment", quietly = TRUE) &&
        methods::is(dataset, "SummarizedExperiment")
      ) SummarizedExperiment::colData(dataset) else NULL,
      error = function(e) NULL
    )
    genotype_names <- tryCatch(
      if (requireNamespace("VariantAnnotation", quietly = TRUE)) {
        names(VariantAnnotation::geno(dataset))
      } else character(),
      error = function(e) character()
    )
    structure_profile$assays <- lapply(genotype_names, function(name) list(name = name))
    structure_profile$sample_metadata <- tryCatch(as.character(colnames(sample_data)), error = function(e) character())
    structure_profile$feature_metadata <- if (is.null(row_ranges)) character() else safe_metadata_names(row_ranges)
    structure_profile$capabilities <- c(
      "variant_ranges", "variant_annotations", "genotypes", "sample_metadata"
    )
    overview_rows <- safe_axis(dimensions, 1)
    structure_profile$inspection <- list(
      mode = "bounded_variant_metadata",
      sample_rows = if (is.null(row_ranges)) 0L else min(length(row_ranges), inspection_row_limit),
      row_count_exact = !is.na(safe_axis(dimensions, 1))
    )
    if (is.null(row_ranges)) data.frame() else bounded_data_frame(row_ranges, inspection_row_limit)
  } else if (methods::is(dataset, "GAlignments") || inherits(dataset, "GAlignments")) {
    structure_profile$modality <- "aligned_reads"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- list(alignments = length(dataset))
    structure_profile$feature_metadata <- safe_metadata_names(dataset)
    structure_profile$indexes <- list(
      seqlevels = tryCatch(
        utils::head(as.character(GenomeInfoDb::seqlevels(dataset)), 256L),
        error = function(e) character()
      )
    )
    structure_profile$capabilities <- c("alignment_ranges", "cigar", "read_metadata")
    overview_rows <- length(dataset)
    structure_profile$inspection <- list(
      mode = "bounded_alignment_metadata",
      sample_rows = min(length(dataset), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(dataset, inspection_row_limit)
  } else if (
    methods::is(dataset, "GenomicRanges") || methods::is(dataset, "GRanges") ||
    inherits(dataset, c("GenomicRanges", "GRanges"))
  ) {
    structure_profile$modality <- "genomic_ranges"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- list(ranges = length(dataset))
    structure_profile$feature_metadata <- safe_metadata_names(dataset)
    structure_profile$indexes <- list(
      seqlevels = tryCatch(
        utils::head(as.character(GenomeInfoDb::seqlevels(dataset)), 256L),
        error = function(e) character()
      )
    )
    structure_profile$capabilities <- c("ranges", "sequence_indexes", "range_metadata")
    overview_rows <- length(dataset)
    structure_profile$inspection <- list(
      mode = "bounded_range_metadata",
      sample_rows = min(length(dataset), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(dataset, inspection_row_limit)
  } else if (inherits(dataset, "Seurat")) {
    structure_profile$modality <- "single_cell"
    structure_profile$representation <- "Seurat"
    structure_profile$axes <- list(features = nrow(dataset), cells = ncol(dataset))
    cell_metadata <- tryCatch(dataset[[]], error = function(e) data.frame())
    assay_names <- tryCatch(names(dataset@assays), error = function(e) character())
    structure_profile$assays <- lapply(assay_names, function(name) {
      value <- tryCatch(dataset[[name]], error = function(e) NULL)
      layers <- tryCatch(
        if (requireNamespace("SeuratObject", quietly = TRUE)) SeuratObject::Layers(value) else character(),
        error = function(e) character()
      )
      list(name = name, dimensions = as.list(safe_dim(value)), layers = as.character(layers))
    })
    structure_profile$sample_metadata <- colnames(cell_metadata)
    structure_profile$reductions <- tryCatch(names(dataset@reductions), error = function(e) character())
    structure_profile$capabilities <- c("assays", "cell_metadata", "reductions")
    overview_rows <- ncol(dataset)
    structure_profile$inspection <- list(
      mode = "bounded_cell_metadata",
      sample_rows = min(nrow(cell_metadata), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(cell_metadata, inspection_row_limit)
  } else if (
    methods::is(dataset, "MultiAssayExperiment") &&
    requireNamespace("MultiAssayExperiment", quietly = TRUE)
  ) {
    structure_profile$modality <- "multi_omics"
    structure_profile$representation <- "MultiAssayExperiment"
    experiment_names <- names(MultiAssayExperiment::experiments(dataset))
    structure_profile$assays <- lapply(experiment_names, function(name) {
      value <- MultiAssayExperiment::experiments(dataset)[[name]]
      list(name = name, classes = class(value), dimensions = as.list(safe_dim(value)))
    })
    sample_map_value <- tryCatch(MultiAssayExperiment::sampleMap(dataset), error = function(e) data.frame())
    sample_map <- bounded_data_frame(sample_map_value, inspection_row_limit)
    column_data <- MultiAssayExperiment::colData(dataset)
    structure_profile$sample_map <- list(
      rows = nrow(sample_map_value),
      profiled_rows = nrow(sample_map),
      columns = colnames(sample_map_value),
      experiments = experiment_names
    )
    structure_profile$sample_metadata <- colnames(column_data)
    structure_profile$axes <- list(
      experiments = length(experiment_names), samples = nrow(column_data)
    )
    structure_profile$capabilities <- c("multiple_assays", "sample_map", "sample_metadata")
    overview_rows <- nrow(column_data)
    structure_profile$inspection <- list(
      mode = "bounded_sample_metadata",
      sample_rows = min(nrow(column_data), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(column_data, inspection_row_limit)
  } else if (
    methods::is(dataset, "SummarizedExperiment") &&
    requireNamespace("SummarizedExperiment", quietly = TRUE)
  ) {
    is_spatial <- methods::is(dataset, "SpatialExperiment") && requireNamespace("SpatialExperiment", quietly = TRUE)
    is_single_cell <- methods::is(dataset, "SingleCellExperiment") && requireNamespace("SingleCellExperiment", quietly = TRUE)
    structure_profile$modality <- if (is_spatial) "spatial" else if (is_single_cell) "single_cell" else "omics_matrix"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- list(features = nrow(dataset), samples = ncol(dataset))
    column_data <- SummarizedExperiment::colData(dataset)
    row_data <- SummarizedExperiment::rowData(dataset)
    assay_names <- SummarizedExperiment::assayNames(dataset)
    structure_profile$assays <- lapply(assay_names, function(name) {
      value <- SummarizedExperiment::assay(dataset, name)
      list(name = name, classes = class(value), dimensions = as.list(safe_dim(value)))
    })
    structure_profile$sample_metadata <- colnames(column_data)
    structure_profile$feature_metadata <- colnames(row_data)
    structure_profile$capabilities <- c("assays", "sample_metadata", "feature_metadata")
    if (is_single_cell) {
      structure_profile$reductions <- SingleCellExperiment::reducedDimNames(dataset)
      structure_profile$alternate_experiments <- SingleCellExperiment::altExpNames(dataset)
      structure_profile$capabilities <- c(structure_profile$capabilities, "reductions", "alternate_experiments")
    }
    if (is_spatial) {
      coordinates <- tryCatch(SpatialExperiment::spatialCoords(dataset), error = function(e) NULL)
      images <- tryCatch(SpatialExperiment::imgData(dataset), error = function(e) NULL)
      structure_profile$spatial <- list(
        coordinate_dimensions = as.list(safe_dim(coordinates)),
        coordinate_names = colnames(coordinates),
        image_rows = if (is.null(images)) 0L else nrow(images),
        image_columns = if (is.null(images)) character() else colnames(images)
      )
      structure_profile$capabilities <- c(structure_profile$capabilities, "spatial_coordinates", "spatial_images")
    }
    overview_rows <- ncol(dataset)
    structure_profile$inspection <- list(
      mode = "bounded_sample_metadata",
      sample_rows = min(nrow(column_data), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(column_data, inspection_row_limit)
  } else if (methods::is(dataset, "flowFrame") || inherits(dataset, "flowFrame")) {
    dimensions <- safe_dim(dataset)
    expressions <- tryCatch(
      if (requireNamespace("flowCore", quietly = TRUE)) flowCore::exprs(dataset) else NULL,
      error = function(e) NULL
    )
    channels <- if (is.null(expressions)) character() else as.character(colnames(expressions))
    structure_profile$modality <- "flow_cytometry"
    structure_profile$representation <- "flowFrame"
    structure_profile$axes <- list(
      events = safe_axis(dimensions, 1),
      channels = safe_axis(dimensions, 2)
    )
    structure_profile$assays <- list(list(name = "events", channels = channels))
    structure_profile$feature_metadata <- channels
    structure_profile$capabilities <- c("events", "channels", "parameter_metadata")
    overview_rows <- safe_axis(dimensions, 1)
    structure_profile$inspection <- list(
      mode = "bounded_events",
      sample_rows = if (is.null(expressions)) 0L else min(nrow(expressions), inspection_row_limit),
      row_count_exact = !is.na(safe_axis(dimensions, 1))
    )
    if (is.null(expressions)) data.frame() else {
      as.data.frame(utils::head(expressions, inspection_row_limit), check.names = FALSE)
    }
  } else if (methods::is(dataset, "flowSet")) {
    structure_profile$modality <- "flow_cytometry"
    structure_profile$representation <- "flowSet"
    sample_names <- safe_names(dataset)
    parameters <- tryCatch({
      first <- dataset[[1]]
      if (requireNamespace("flowCore", quietly = TRUE)) flowCore::colnames(first) else colnames(first)
    }, error = function(e) character())
    structure_profile$axes <- list(samples = length(dataset), channels = length(parameters))
    structure_profile$assays <- list(list(name = "events", channels = parameters))
    structure_profile$capabilities <- c("events", "channels", "sample_metadata")
    overview_rows <- length(dataset)
    if (requireNamespace("Biobase", quietly = TRUE)) {
      bounded_data_frame(Biobase::pData(dataset), inspection_row_limit)
    } else {
      data.frame(sample_id = utils::head(sample_names, inspection_row_limit), check.names = FALSE)
    }
  } else if (inherits(dataset, c("MSnExp", "OnDiskMSnExp", "MSnSet"))) {
    dimensions <- safe_dim(dataset)
    is_spectra_collection <- inherits(dataset, c("MSnExp", "OnDiskMSnExp"))
    structure_profile$modality <- "mass_spectrometry"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- if (is_spectra_collection) {
      list(spectra = length(dataset), samples = safe_axis(dimensions, 2))
    } else {
      list(features = safe_axis(dimensions, 1), samples = safe_axis(dimensions, 2))
    }
    sample_data <- tryCatch(
      if (requireNamespace("Biobase", quietly = TRUE)) Biobase::pData(dataset) else data.frame(),
      error = function(e) data.frame()
    )
    feature_data <- tryCatch(
      if (requireNamespace("Biobase", quietly = TRUE)) Biobase::fData(dataset) else data.frame(),
      error = function(e) data.frame()
    )
    structure_profile$sample_metadata <- as.character(colnames(sample_data))
    structure_profile$feature_metadata <- as.character(colnames(feature_data))
    structure_profile$capabilities <- c(
      "spectra", "spectrum_metadata", "sample_metadata",
      if (inherits(dataset, "OnDiskMSnExp")) "on_disk" else "in_memory"
    )
    overview_rows <- if (is_spectra_collection) length(dataset) else safe_axis(dimensions, 1)
    profile_value <- if (ncol(sample_data)) {
      sample_data
    } else {
      sample_ids <- tryCatch(
        if (requireNamespace("Biobase", quietly = TRUE)) Biobase::sampleNames(dataset) else character(),
        error = function(e) character()
      )
      data.frame(sample_id = sample_ids, check.names = FALSE)
    }
    structure_profile$inspection <- list(
      mode = "bounded_spectrum_metadata",
      sample_rows = min(nrow(profile_value), inspection_row_limit),
      row_count_exact = !is.na(overview_rows)
    )
    bounded_data_frame(profile_value, inspection_row_limit)
  } else if (inherits(dataset, "Spectra") && requireNamespace("Spectra", quietly = TRUE)) {
    spectrum_variables <- tryCatch(Spectra::spectraVariables(dataset), error = function(e) character())
    structure_profile$modality <- "mass_spectrometry"
    structure_profile$representation <- "Spectra"
    structure_profile$axes <- list(spectra = length(dataset))
    structure_profile$feature_metadata <- as.character(spectrum_variables)
    structure_profile$capabilities <- c("spectra", "spectrum_metadata", "backend_aware")
    overview_rows <- length(dataset)
    limited <- if (length(dataset)) dataset[seq_len(min(length(dataset), inspection_row_limit))] else dataset
    spectrum_data <- tryCatch(Spectra::spectraData(limited), error = function(e) data.frame())
    structure_profile$inspection <- list(
      mode = "bounded_spectrum_metadata",
      sample_rows = min(length(dataset), inspection_row_limit),
      row_count_exact = TRUE
    )
    bounded_data_frame(spectrum_data, inspection_row_limit)
  } else if (inherits(dataset, "phyloseq") && requireNamespace("phyloseq", quietly = TRUE)) {
    structure_profile$modality <- "microbiome"
    structure_profile$representation <- "phyloseq"
    structure_profile$axes <- list(taxa = phyloseq::ntaxa(dataset), samples = phyloseq::nsamples(dataset))
    structure_profile$assays <- list(list(name = "otu_table"))
    structure_profile$capabilities <- c("abundance", "taxonomy", "sample_metadata")
    overview_rows <- phyloseq::ntaxa(dataset)
    tryCatch(
      bounded_data_frame(phyloseq::sample_data(dataset), inspection_row_limit),
      error = function(e) data.frame()
    )
  } else if (inherits(dataset, "biom") || methods::is(dataset, "biom")) {
    dimensions <- safe_dim(dataset)
    observation_names <- tryCatch(
      if (requireNamespace("biomformat", quietly = TRUE)) {
        biomformat::observation_names(dataset)
      } else character(),
      error = function(e) character()
    )
    sample_names <- tryCatch(
      if (requireNamespace("biomformat", quietly = TRUE)) {
        biomformat::sample_names(dataset)
      } else character(),
      error = function(e) character()
    )
    structure_profile$modality <- "microbiome"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$axes <- list(
      observations = safe_axis(dimensions, 1),
      samples = safe_axis(dimensions, 2)
    )
    structure_profile$assays <- list(list(name = "abundance", dimensions = as.list(dimensions)))
    structure_profile$sample_metadata <- utils::head(as.character(sample_names), 256L)
    structure_profile$feature_metadata <- utils::head(as.character(observation_names), 256L)
    structure_profile$capabilities <- c("abundance_matrix", "observation_metadata", "sample_metadata")
    overview_rows <- safe_axis(dimensions, 1)
    limited_observations <- utils::head(as.character(observation_names), inspection_row_limit)
    structure_profile$inspection <- list(
      mode = "bounded_observation_metadata",
      sample_rows = length(limited_observations),
      row_count_exact = !is.na(safe_axis(dimensions, 1))
    )
    if (length(limited_observations)) {
      data.frame(observation_id = limited_observations, check.names = FALSE)
    } else {
      data.frame()
    }
  } else if (inherits(dataset, "phylo")) {
    tip_labels <- utils::head(as.character(dataset$tip.label %||% character()), inspection_row_limit)
    node_count <- tryCatch(as.integer(dataset$Nnode), error = function(e) NA_integer_)
    structure_profile$modality <- "phylogenetics"
    structure_profile$representation <- "phylo"
    structure_profile$processing_stage <- "tree"
    structure_profile$axes <- list(tips = length(dataset$tip.label %||% character()), internal_nodes = node_count)
    structure_profile$capabilities <- c("tree_topology", "tip_labels", "branch_lengths")
    structure_profile$inspection <- list(
      mode = "bounded_tip_labels",
      sample_rows = length(tip_labels),
      row_count_exact = TRUE
    )
    overview_rows <- length(dataset$tip.label %||% character())
    data.frame(tip_label = tip_labels, stringsAsFactors = FALSE)
  } else if (inherits(dataset, "multiPhylo")) {
    structure_profile$modality <- "phylogenetics"
    structure_profile$representation <- "multiPhylo"
    structure_profile$processing_stage <- "tree_collection"
    structure_profile$axes <- list(trees = length(dataset))
    structure_profile$capabilities <- c("multiple_trees", "tree_topology")
    overview_rows <- length(dataset)
    data.frame(tree_index = seq_len(min(length(dataset), inspection_row_limit)))
  } else {
    structure_profile$modality <- "scientific_object"
    structure_profile$representation <- object_classes[[1]]
    structure_profile$processing_stage <- "unknown"
    data.frame()
  }
}, error = function(e) {
  structure_profile$inspection_warning <<- conditionMessage(e)
  data.frame()
})
if (is.null(overview_rows)) overview_rows <- if (nrow(profile_data)) NA_integer_ else 0L
if (is.null(overview_columns)) overview_columns <- if (ncol(profile_data)) NA_integer_ else 0L

safe_unique_count <- function(x) tryCatch(length(unique(x[!is.na(x)])), error = function(e) NA_integer_)
profile_defaults <- list(example = "")
  column_label <- function(x) {
    lab <- attr(x, "label", exact = TRUE)
    if (is.character(lab) && length(lab) >= 1 && nzchar(lab[1])) lab[1] else ""
  }
profile <- data.frame(
  column = names(profile_data),
  type = vapply(profile_data, function(x) paste(class(x), collapse = "/"), character(1)),
  missing = vapply(profile_data, function(x) sum(is.na(x)), numeric(1)),
  missing_pct = vapply(profile_data, function(x) if (length(x)) round(mean(is.na(x)) * 100, 2) else 0, numeric(1)),
  non_missing = vapply(profile_data, function(x) sum(!is.na(x)), numeric(1)),
  unique_count = vapply(profile_data, safe_unique_count, numeric(1)),
  example = rep(profile_defaults$example, ncol(profile_data)),
  label = vapply(profile_data, column_label, character(1))
)

level_rows <- lapply(seq_along(profile_data), function(column_index) {
  column_name <- names(profile_data)[column_index]
  # Address columns by POSITION: `df[[name]]` on duplicated names silently
  # returns only the first match, mislabelling the second column's levels.
  values <- profile_data[[column_index]]
  count <- safe_unique_count(values)
  if (!is.finite(count) || count < 1 || count > 32 || is.list(values)) return(NULL)
  levels <- unique(as.character(values[!is.na(values)]))
  levels <- levels[nzchar(levels)]
  if (!length(levels)) return(NULL)
  data.frame(column = column_name, level = head(levels, 32), stringsAsFactors = FALSE)
})
level_rows <- Filter(Negate(is.null), level_rows)
level_profile <- if (length(level_rows)) do.call(rbind, level_rows) else {
  data.frame(column = character(), level = character(), stringsAsFactors = FALSE)
}

baa_table("dataset_profile", profile)
baa_table("dataset_levels", level_profile)
baa_text("dataset_structure", jsonlite::toJSON(structure_profile, auto_unbox = TRUE, null = "null", na = "null"))
baa_text(
  "dataset_overview",
  paste(
    "Rows:", if (length(overview_rows) == 0 || is.na(overview_rows)) "unknown" else overview_rows,
    "\nProfile rows:", nrow(profile_data),
    "\nColumns:", overview_columns,
    "\nObject kind:", object_kind,
    "\nModality:", structure_profile$modality,
    "\nRepresentation:", structure_profile$representation
  )
)
'''
