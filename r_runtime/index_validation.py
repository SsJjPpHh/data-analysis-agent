"""Bounded operational validation for scientific index sidecars.

BAI/TBI/CSI files do not embed a checksum of their owner.  Filename matching
is therefore only routing metadata, never proof that an index belongs to the
selected data.  This R preflight cross-checks index metadata and bounded real
queries against records read directly from the primary file before loaders may
advertise indexed capabilities.
"""

from __future__ import annotations


INDEX_CONTENT_VALIDATION_R_CODE = r'''
.baa_index_format <- function(asset) {
  fmt <- tolower(trimws(as.character(asset$format %||% "")))
  path <- tolower(as.character(asset$path %||% ""))
  if (fmt %in% c("bam_index", "bai") || endsWith(path, ".bai")) return("bai")
  if (fmt %in% c("vcf_index", "tbi") && endsWith(path, ".tbi")) return("tbi")
  if (fmt %in% c("vcf_index", "csi") && endsWith(path, ".csi")) return("csi")
  ""
}

.baa_index_owner_names <- function(index_path, index_format) {
  name <- tolower(basename(index_path))
  suffix <- paste0(".", index_format)
  if (!endsWith(name, suffix)) return(character())
  stripped <- substr(name, 1L, nchar(name) - nchar(suffix))
  if (identical(index_format, "bai")) {
    return(unique(c(stripped, paste0(stripped, ".bam"))))
  }
  unique(c(
    stripped,
    paste0(stripped, ".vcf"),
    paste0(stripped, ".vcf.gz"),
    paste0(stripped, ".vcf.bgz")
  ))
}

.baa_direct_bam_sample <- function(path, limit = 65L) {
  handle <- Rsamtools::BamFile(path, yieldSize = as.integer(limit))
  open(handle)
  on.exit(close(handle), add = TRUE)
  value <- Rsamtools::scanBam(
    handle,
    param = Rsamtools::ScanBamParam(what = c("qname", "rname", "pos"))
  )[[1]]
  data.frame(
    qname = as.character(value$qname),
    rname = as.character(value$rname),
    pos = as.integer(value$pos),
    stringsAsFactors = FALSE
  )
}

.baa_validate_bai_content <- function(primary, index) {
  if (!requireNamespace("Rsamtools", quietly = TRUE) ||
      !requireNamespace("GenomicRanges", quietly = TRUE) ||
      !requireNamespace("IRanges", quietly = TRUE)) {
    stop("BAI content validation requires Rsamtools, GenomicRanges, and IRanges.", call. = FALSE)
  }
  direct_header <- tryCatch(
    Rsamtools::scanBamHeader(primary$path)[[1]]$targets,
    error = function(e) stop(paste0("Unable to inspect BAM content: ", conditionMessage(e)), call. = FALSE)
  )
  indexed <- Rsamtools::BamFile(primary$path, index = index$path)
  stats <- tryCatch(
    Rsamtools::idxstatsBam(indexed),
    error = function(e) stop(paste0("BAI content does not match BAM: ", conditionMessage(e)), call. = FALSE)
  )
  reference_stats <- stats[as.character(stats$seqnames) != "*", , drop = FALSE]
  if (!identical(as.character(reference_stats$seqnames), as.character(names(direct_header))) ||
      !identical(as.numeric(reference_stats$seqlength), as.numeric(direct_header))) {
    stop("BAI content does not match the BAM reference dictionary.", call. = FALSE)
  }

  direct <- .baa_direct_bam_sample(primary$path)
  indexed_count <- sum(as.numeric(stats$mapped) + as.numeric(stats$unmapped), na.rm = TRUE)
  if (nrow(direct) < 65L && !identical(as.numeric(nrow(direct)), as.numeric(indexed_count))) {
    stop("BAI content record counts do not match the BAM content.", call. = FALSE)
  }
  candidates <- direct[!is.na(direct$pos) & direct$pos > 0L & nzchar(direct$rname), , drop = FALSE]
  if (nrow(candidates)) {
    selected <- unique(round(seq(1, nrow(candidates), length.out = min(8L, nrow(candidates)))))
    for (row_index in selected) {
      record <- candidates[row_index, , drop = FALSE]
      region <- GenomicRanges::GRanges(
        record$rname[[1]],
        IRanges::IRanges(record$pos[[1]], record$pos[[1]])
      )
      queried <- tryCatch(
        Rsamtools::scanBam(
          indexed,
          param = Rsamtools::ScanBamParam(which = region, what = c("qname", "rname", "pos"))
        )[[1]],
        error = function(e) stop(paste0("BAI indexed query failed: ", conditionMessage(e)), call. = FALSE)
      )
      matching <- as.character(queried$qname) == record$qname[[1]] &
        as.integer(queried$pos) == record$pos[[1]] &
        as.character(queried$rname) == record$rname[[1]]
      if (!any(matching, na.rm = TRUE)) {
        stop("BAI indexed query does not resolve records from the BAM content.", call. = FALSE)
      }
    }
  }
  invisible(TRUE)
}

.baa_direct_vcf_sample <- function(path, limit = 65L) {
  connection <- gzfile(path, open = "rt")
  on.exit(close(connection), add = TRUE)
  records <- character()
  while (length(records) < limit) {
    lines <- readLines(connection, n = 256L, warn = FALSE)
    if (!length(lines)) break
    records <- c(records, lines[!startsWith(lines, "#") & nzchar(lines)])
  }
  records[seq_len(min(length(records), limit))]
}

.baa_validate_tabix_content <- function(primary, index) {
  if (!requireNamespace("Rsamtools", quietly = TRUE) ||
      !requireNamespace("GenomicRanges", quietly = TRUE) ||
      !requireNamespace("IRanges", quietly = TRUE)) {
    stop("TBI/CSI content validation requires Rsamtools, GenomicRanges, and IRanges.", call. = FALSE)
  }
  tabix <- Rsamtools::TabixFile(primary$path, index = index$path)
  header <- tryCatch(
    Rsamtools::headerTabix(tabix),
    error = function(e) stop(paste0("TBI/CSI content does not match VCF: ", conditionMessage(e)), call. = FALSE)
  )
  direct <- .baa_direct_vcf_sample(primary$path)
  if (length(direct)) {
    fields <- strsplit(direct, "\\t", fixed = FALSE)
    usable <- which(vapply(fields, length, integer(1)) >= 2L)
    selected <- unique(round(seq(1, length(usable), length.out = min(8L, length(usable)))))
    for (sample_index in selected) {
      record <- fields[[usable[[sample_index]]]]
      position <- suppressWarnings(as.integer(record[[2]]))
      if (is.na(position) || position < 1L) next
      if (!record[[1]] %in% as.character(header$seqnames %||% character())) {
        stop("TBI/CSI sequence dictionary does not match VCF content.", call. = FALSE)
      }
      region <- GenomicRanges::GRanges(record[[1]], IRanges::IRanges(position, position))
      queried <- tryCatch(
        unlist(Rsamtools::scanTabix(tabix, param = region), use.names = FALSE),
        error = function(e) stop(paste0("TBI/CSI indexed query failed: ", conditionMessage(e)), call. = FALSE)
      )
      prefix <- paste0(record[[1]], "\t", position, "\t")
      if (!any(startsWith(queried, prefix))) {
        stop("TBI/CSI indexed query does not resolve records from the VCF content.", call. = FALSE)
      }
    }
  }
  invisible(TRUE)
}

baa_validate_dataset_index_content <- function(dataset_manifest) {
  assets <- dataset_manifest$assets %||% list()
  if (!length(assets)) return(invisible(TRUE))
  index_formats <- vapply(assets, .baa_index_format, character(1))
  for (index_number in which(nzchar(index_formats))) {
    index <- assets[[index_number]]
    fmt <- index_formats[[index_number]]
    owner_format <- if (identical(fmt, "bai")) "bam" else "vcf"
    accepted <- .baa_index_owner_names(index$path, fmt)
    owners <- Filter(function(asset) {
      identical(tolower(as.character(asset$format %||% "")), owner_format) &&
        tolower(basename(asset$path)) %in% accepted
    }, assets)
    if (length(owners) != 1L) {
      stop("Scientific index content validation requires exactly one matching primary asset.", call. = FALSE)
    }
    if (identical(fmt, "bai")) {
      .baa_validate_bai_content(owners[[1]], index)
    } else {
      .baa_validate_tabix_content(owners[[1]], index)
    }
  }
  invisible(TRUE)
}
'''


__all__ = ["INDEX_CONTENT_VALIDATION_R_CODE"]
