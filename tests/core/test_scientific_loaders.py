import json
import subprocess
from pathlib import Path

import pytest

from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.environment import HostREnvironment, REnvironmentError
from r_runtime.protocol import DatasetAssetRef, DatasetRef, RRunRequest


def _local_runtime():
    environment = HostREnvironment.locate()
    try:
        verified = environment.prepare()
        return str(verified.rscript), environment.process_environment(verified=verified)
    except REnvironmentError:
        return None, {}


rscript_bin, runtime_env = _local_runtime()
rscript_required = pytest.mark.skipif(rscript_bin is None, reason="Local R is unavailable")


def test_r_adapter_registry_declares_all_required_routes_and_never_flattens_high_dimensional_data():
    helper = Path("r_runtime/helpers/baa_helpers.R").read_text(encoding="utf-8")
    for adapter_id in (
        "tabular_readr",
        "spreadsheet_readxl",
        "labelled_haven",
        "rds_base",
        "parquet_arrow",
        "rdata_base",
        "matrix_market_10x",
        "h5ad_structure_router",
        "tenx_h5_seurat",
        "fcs_flowcore",
        "vcf_variantannotation",
        "bcf_rsamtools",
        "genomic_ranges_rtracklayer",
        "biom_biomformat",
        "mass_spectrometry_msnbase",
        "bam_genomicalignments",
    ):
        assert f'adapter_id = "{adapter_id}"' in helper
    assert "anndataR::read_h5ad" in helper
    assert "baa_read_scientific_bundle <- function(" in helper
    assert "baa_read_scientific_adapter <- function(" in helper
    assert "as.data.frame(baa_read_scientific_adapter" not in helper


def _run_r(code: str) -> subprocess.CompletedProcess[str]:
    assert rscript_bin is not None
    return subprocess.run(
        [rscript_bin, "--vanilla", "-e", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=runtime_env,
    )


@rscript_required
def test_plink_registry_matches_the_only_implemented_loader_branch():
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    completed = _run_r(
        f"""
source({json.dumps(str(helper))})
spec <- baa_scientific_adapter_registry()$plink_bed
stopifnot(identical(spec$required_packages, "BEDMatrix"))
stopifnot(identical(spec$required_functions, "BEDMatrix::BEDMatrix"))
"""
    )
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_r_adapter_reads_rdata_and_matrix_market_as_native_objects(tmp_path):
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    rdata_path = tmp_path / "native.RData"
    matrix_path = tmp_path / "matrix.mtx"
    matrix_path.write_text(
        "%%MatrixMarket matrix coordinate integer general\n2 3 3\n1 1 1\n2 2 2\n1 3 3\n",
        encoding="utf-8",
    )
    features_path = tmp_path / "features.tsv"
    features_path.write_text("g1\tGene A\ng2\tGene B\n", encoding="utf-8")
    barcodes_path = tmp_path / "barcodes.tsv"
    barcodes_path.write_text("Cell1\nCell2\nCell3\n", encoding="utf-8")
    code = f"""
source({json.dumps(str(helper))})
original <- matrix(1:6, nrow = 2)
save(original, file = {json.dumps(str(rdata_path))})
rdata_bundle <- baa_read_scientific_bundle(list(path={json.dumps(str(rdata_path))}, format="rdata"))
stopifnot(is.matrix(rdata_bundle$dataset), identical(rdata_bundle$dataset, original))
matrix_bundle <- baa_read_scientific_bundle(list(assets=list(
  list(path={json.dumps(str(matrix_path))}, format="mtx", role="primary", kind="file"),
  list(path={json.dumps(str(features_path))}, format="tsv", role="features", kind="file"),
  list(path={json.dumps(str(barcodes_path))}, format="tsv", role="barcodes", kind="file")
)))
stopifnot(inherits(matrix_bundle$dataset, "Matrix"), identical(dim(matrix_bundle$dataset), c(2L, 3L)))
"""
    completed = _run_r(code)
    assert completed.returncode == 0, completed.stderr


@rscript_required
def test_local_backend_executes_an_asset_manifest_end_to_end(tmp_path):
    data_path = tmp_path / 'clinical "quoted".csv'
    data_path.write_text("x,y\n1,2\n3,4\n", encoding="utf-8")
    output = tmp_path / "output"
    result = LocalRscriptBackend(rscript_bin=rscript_bin, runtime_env=runtime_env).run_code(
        RRunRequest(
            job_id="scientific-loader",
            dataset=DatasetRef(
                dataset_id="clinical",
                path=str(data_path),
                format="csv",
                assets=(DatasetAssetRef(path=str(data_path), format="csv", role="primary"),),
            ),
            artifacts_dir=str(output),
            code='stopifnot(nrow(dataset) == 2); baa_text("loaded_adapter", dataset_bundle$adapter_id)',
            timeout_seconds=60,
        )
    )
    assert result.ok is True, result.error
    assert Path(result.texts[0].path).read_text(encoding="utf-8").strip() == "tabular_readr"


@rscript_required
def test_r_adapter_missing_dependency_error_names_package_and_adapter():
    helper = Path("r_runtime/helpers/baa_helpers.R").resolve()
    completed = _run_r(
        f"""
source({json.dumps(str(helper))})
message <- tryCatch(baa_require_adapter_package("baaDefinitelyMissing", "example_adapter"), error=function(e) conditionMessage(e))
stopifnot(grepl("example_adapter", message, fixed=TRUE), grepl("baaDefinitelyMissing", message, fixed=TRUE))
"""
    )
    assert completed.returncode == 0, completed.stderr
