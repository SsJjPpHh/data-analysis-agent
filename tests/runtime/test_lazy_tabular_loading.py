import csv
import json
import os
import subprocess
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.inspection import inspection_summary
from r_runtime.inspection_template import DATASET_PROFILE_R_CODE
from r_runtime.protocol import DatasetRef
from r_runtime.environment import HostREnvironment, REnvironmentError


host_environment = HostREnvironment.locate()
try:
    host_verified = host_environment.prepare()
    rscript_bin = str(host_verified.rscript)
    host_r_env = host_environment.process_environment(verified=host_verified)
except REnvironmentError:
    rscript_bin = None
    host_r_env = {}
lazy_r_required = pytest.mark.skipif(
    rscript_bin is None,
    reason="Local R is unavailable",
)


def _run_r(code: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    assert rscript_bin is not None
    env = host_r_env.copy()
    env["BAA_OUTPUT_DIR"] = str(tmp_path / "artifacts")
    script_path = tmp_path / "lazy_loading_test.R"
    script_path.write_text(code, encoding="utf-8")
    return subprocess.run(
        [rscript_bin, "--vanilla", str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )


@pytest.mark.parametrize(
    ("format_name", "delimiter"),
    [("csv", ","), ("tsv", "\t")],
)
@lazy_r_required
def test_delimited_loading_switches_at_the_size_threshold_without_changing_small_files(
    tmp_path, format_name, delimiter
):
    data_path = tmp_path / f"clinical.{format_name}"
    data_path.write_text(
        delimiter.join(("id", "group"))
        + "\n"
        + "\n".join(delimiter.join((str(index), group)) for index, group in ((1, "A"), (2, "B"), (3, "A")))
        + "\n",
        encoding="utf-8",
    )
    helper_path = Path("r_runtime/helpers/baa_helpers.R").resolve()
    code = f"""
source({json.dumps(str(helper_path))})
Sys.setenv(BAA_LAZY_DATASET_THRESHOLD_BYTES = "1048576")
small <- baa_read_dataset({json.dumps(str(data_path))}, {json.dumps(format_name)})
stopifnot(is.data.frame(small), !baa_is_lazy_dataset(small), nrow(small) == 3L)

Sys.setenv(
  BAA_LAZY_DATASET_THRESHOLD_BYTES = "1",
  BAA_LAZY_COLLECT_MAX_ROWS = "2",
  BAA_LAZY_SAMPLE_MAX_ROWS = "2"
)
lazy <- baa_read_dataset({json.dumps(str(data_path))}, {json.dumps(format_name)})
stopifnot(baa_is_lazy_dataset(lazy), inherits(lazy, "baa_lazy_dataset"))
stopifnot(identical(lazy$backend, "duckdb"), inherits(baa_lazy_table(lazy), "tbl_lazy"))
stopifnot(identical(names(lazy), c("id", "group")))

collected <- baa_collect_dataset(lazy, limit = 2L)
stopifnot(is.data.frame(collected), nrow(collected) == 2L, identical(collected$id, c(1, 2)))
sampled <- baa_sample_dataset(lazy, size = 2L, seed = 7L)
stopifnot(is.data.frame(sampled), nrow(sampled) == 2L)
sampling_scope <- attr(sampled, "baa_sampling_scope")
stopifnot(
  identical(sampling_scope$strategy, "bounded_prefix_reservoir"),
  identical(sampling_scope$scan_rows, 20L)
)
lazy_table <- baa_lazy_table(lazy)
table_sample <- baa_sample_dataset(lazy_table, size = 2L, seed = 7L)
repeated_table_sample <- baa_sample_dataset(lazy_table, size = 2L, seed = 7L)
stopifnot(
  identical(table_sample, repeated_table_sample),
  identical(attr(table_sample, "baa_sampling_scope")$strategy, "bounded_prefix_reservoir")
)

info <- baa_lazy_dataset_info(lazy)
stopifnot(
  identical(info$representation, "lazy_duckdb"),
  identical(info$sample_strategy, "bounded_prefix_reservoir"),
  identical(info$format, {json.dumps(format_name)}),
  identical(info$columns, c("id", "group")),
  identical(info$row_count_exact, FALSE)
)
collect_error <- tryCatch(baa_collect_dataset(lazy, limit = 3L), error = identity)
sample_error <- tryCatch(baa_sample_dataset(lazy, size = 3L), error = identity)
stopifnot(
  inherits(collect_error, "error"), grepl("BAA_LAZY_COLLECT_MAX_ROWS", conditionMessage(collect_error)),
  inherits(sample_error, "error"), grepl("BAA_LAZY_SAMPLE_MAX_ROWS", conditionMessage(sample_error))
)
owner <- attr(lazy_table, "baa_lazy_dataset_owner")
stopifnot(identical(owner, lazy))
rm(lazy)
invisible(gc())
stopifnot(nrow(baa_collect_dataset(lazy_table, limit = 1L)) == 1L)
baa_close_lazy_dataset(owner)
"""

    completed = _run_r(code, tmp_path)

    assert completed.returncode == 0, completed.stderr


@lazy_r_required
def test_parquet_loading_is_lazy_only_above_the_threshold(tmp_path):
    data_path = tmp_path / "clinical.parquet"
    helper_path = Path("r_runtime/helpers/baa_helpers.R").resolve()
    code = f"""
source({json.dumps(str(helper_path))})
arrow::write_parquet(data.frame(id = 1:4, value = c(2.5, 3.5, 4.5, 5.5)), {json.dumps(str(data_path))})
Sys.setenv(BAA_LAZY_DATASET_THRESHOLD_BYTES = "1000000000000")
small <- baa_read_dataset({json.dumps(str(data_path))}, "parquet")
stopifnot(is.data.frame(small), !baa_is_lazy_dataset(small), nrow(small) == 4L)

Sys.setenv(BAA_LAZY_DATASET_THRESHOLD_BYTES = "1")
lazy <- baa_read_dataset({json.dumps(str(data_path))}, "parquet")
stopifnot(baa_is_lazy_dataset(lazy), identical(lazy$format, "parquet"))
collected <- baa_collect_dataset(lazy, limit = 3L)
stopifnot(nrow(collected) == 3L, identical(collected$id, 1:3))
baa_close_lazy_dataset(lazy)
"""

    completed = _run_r(code, tmp_path)

    assert completed.returncode == 0, completed.stderr


@lazy_r_required
def test_lazy_inspection_profiles_only_a_bounded_sample(tmp_path, monkeypatch):
    data_path = tmp_path / "large.csv"
    with data_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("id", "group", "value"))
        for index in range(1, 51):
            writer.writerow((index, "A" if index % 2 else "B", index * 10))

    monkeypatch.setenv("BAA_LAZY_DATASET_THRESHOLD_BYTES", "1")
    monkeypatch.setenv("BAA_LAZY_INSPECTION_ROWS", "5")
    monkeypatch.setenv("BAA_LAZY_SAMPLE_MAX_ROWS", "5")
    monkeypatch.setenv("BAA_LAZY_SAMPLE_SCAN_MAX_ROWS", "3")
    runtime_env = {
        **host_r_env,
        "BAA_LAZY_DATASET_THRESHOLD_BYTES": "1",
        "BAA_LAZY_INSPECTION_ROWS": "5",
        "BAA_LAZY_SAMPLE_MAX_ROWS": "5",
        "BAA_LAZY_SAMPLE_SCAN_MAX_ROWS": "3",
    }
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).inspect_dataset(
        DatasetRef(
            dataset_id="large",
            path=str(data_path),
            format="csv",
            name="large.csv",
        ),
        timeout_seconds=60,
    )

    assert result.ok is True, f"error={result.error}\nstderr={result.stderr}"
    summary = inspection_summary(result)
    assert summary["rows"] is None
    assert summary["columns"] == 3
    assert summary["representation"] == "lazy_duckdb"
    assert summary["scientific"]["axes"] == {"observations": None, "variables": 3}
    assert summary["scientific"]["inspection"] == {
        "mode": "bounded_sample",
        "sample_rows": 3,
        "row_count_exact": False,
    }
    assert "bounded_collect" in summary["scientific"]["capabilities"]
    assert "Rows: unknown" in summary["overview"]
    assert "Profile rows: 3" in summary["overview"]
    assert all(variable["non_missing"] <= 3 for variable in summary["variables"])


@lazy_r_required
def test_inspection_has_bounded_profiles_for_supported_scientific_objects(tmp_path):
    data_path = tmp_path / "lazy.csv"
    data_path.write_text("id,value\n1,10\n2,20\n3,30\n4,40\n", encoding="utf-8")
    helper_path = Path("r_runtime/helpers/baa_helpers.R").resolve()
    code = f"""
suppressPackageStartupMessages({{
  library(GenomicRanges)
  library(GenomicAlignments)
  library(flowCore)
  library(MSnbase)
}})
source({json.dumps(str(helper_path))})
Sys.setenv(
  BAA_LAZY_DATASET_THRESHOLD_BYTES = "1",
  BAA_LAZY_INSPECTION_ROWS = "3",
  BAA_LAZY_SAMPLE_MAX_ROWS = "3"
)
profile_code <- {json.dumps(DATASET_PROFILE_R_CODE)}
profile_object <- function(value) {{
  dataset <- value
  eval(parse(text = profile_code), envir = environment())
  list(structure = structure_profile, profile_rows = nrow(profile_data))
}}

lazy_wrapper <- baa_read_dataset({json.dumps(str(data_path))}, "csv")
lazy_table <- baa_lazy_table(lazy_wrapper)

genomic_ranges <- GenomicRanges::GRanges(
  seqnames = rep("chr1", 8),
  ranges = IRanges::IRanges(start = 1:8, width = 2),
  strand = "*"
)
S4Vectors::mcols(genomic_ranges)$score <- 1:8

alignments <- GenomicAlignments::GAlignments(
  seqnames = S4Vectors::Rle(factor(rep("chr1", 5))),
  pos = 1:5,
  cigar = rep("2M", 5),
  strand = S4Vectors::Rle(GenomicRanges::strand(rep("+", 5)))
)
flow_frame <- flowCore::flowFrame(
  exprs = matrix(1:20, nrow = 10, dimnames = list(NULL, c("FSC", "SSC")))
)
mass_spectrometry <- MSnbase::MSnSet(
  exprs = matrix(
    1:12,
    nrow = 3,
    dimnames = list(paste0("feature", 1:3), paste0("sample", 1:4))
  )
)

if (!methods::isClass("VCF")) {{
  methods::setClass("VCF", slots = c(dimensions = "integer"))
  methods::setMethod("dim", "VCF", function(x) x@dimensions)
}}
variant_calls <- methods::new("VCF", dimensions = c(9L, 4L))
dim.biom <- function(x) c(12L, 4L)
biom <- structure(list(), class = "biom")

profiles <- list(
  lazy_table = profile_object(lazy_table),
  matrix = profile_object(matrix(1:100, nrow = 10)),
  genomic_ranges = profile_object(genomic_ranges),
  variant_calls = profile_object(variant_calls),
  flow_frame = profile_object(flow_frame),
  mass_spectrometry = profile_object(mass_spectrometry),
  alignments = profile_object(alignments),
  biom = profile_object(biom)
)
baa_close_lazy_dataset(lazy_wrapper)
profiles$closed_lazy_table <- profile_object(lazy_table)
cat(jsonlite::toJSON(profiles, auto_unbox = TRUE, null = "null", na = "null"))
"""

    completed = _run_r(code, tmp_path)

    assert completed.returncode == 0, completed.stderr
    profiles = json.loads(completed.stdout)
    assert profiles["lazy_table"]["structure"]["representation"] == "lazy_duckdb"
    assert profiles["lazy_table"]["profile_rows"] == 3
    assert profiles["closed_lazy_table"]["structure"]["representation"] == "lazy_duckdb"
    assert profiles["closed_lazy_table"]["profile_rows"] == 0
    assert profiles["closed_lazy_table"]["structure"]["inspection_warning"]

    assert profiles["matrix"]["structure"]["modality"] == "matrix"
    assert profiles["matrix"]["structure"]["axes"] == {"features": 10, "samples": 10}
    assert profiles["matrix"]["profile_rows"] == 3

    assert profiles["genomic_ranges"]["structure"]["modality"] == "genomic_ranges"
    assert profiles["genomic_ranges"]["structure"]["axes"] == {"ranges": 8}
    assert profiles["genomic_ranges"]["profile_rows"] == 3
    assert profiles["genomic_ranges"]["structure"]["feature_metadata"] == "score"

    assert profiles["variant_calls"]["structure"]["modality"] == "genomics_variants"
    assert profiles["variant_calls"]["structure"]["axes"] == {"variants": 9, "samples": 4}

    assert profiles["flow_frame"]["structure"]["modality"] == "flow_cytometry"
    assert profiles["flow_frame"]["structure"]["axes"] == {"events": 10, "channels": 2}
    assert profiles["flow_frame"]["profile_rows"] == 3

    assert profiles["mass_spectrometry"]["structure"]["modality"] == "mass_spectrometry"
    assert profiles["mass_spectrometry"]["structure"]["axes"] == {"features": 3, "samples": 4}
    assert profiles["mass_spectrometry"]["profile_rows"] <= 3

    assert profiles["alignments"]["structure"]["modality"] == "aligned_reads"
    assert profiles["alignments"]["structure"]["axes"] == {"alignments": 5}
    assert profiles["alignments"]["profile_rows"] == 3

    assert profiles["biom"]["structure"]["modality"] == "microbiome"
    assert profiles["biom"]["structure"]["axes"] == {"observations": 12, "samples": 4}
