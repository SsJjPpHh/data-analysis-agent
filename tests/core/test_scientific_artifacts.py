from pathlib import Path

from r_runtime.backends.local_rscript import LocalRscriptBackend


def test_local_backend_discovers_unregistered_scientific_artifacts(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "model.rds").write_bytes(b"rds")
    (output / "cells.h5ad").write_bytes(b"h5ad")
    (output / "variants.vcf").write_text("##fileformat=VCFv4.3\n", encoding="utf-8")
    backend = LocalRscriptBackend(rscript_bin="private-r")

    manifest = backend._augment_manifest_with_discovered_artifacts(
        backend._empty_manifest(),
        output,
        preexisting_files=set(),
    )

    artifacts = {Path(item["path"]).name: item for item in manifest["artifacts"]}
    assert artifacts["model.rds"]["mime_type"] == "application/x-r-rds"
    assert artifacts["cells.h5ad"]["mime_type"] == "application/x-hdf5"
    assert artifacts["variants.vcf"]["mime_type"] == "text/vcf"


def test_r_helpers_expose_generic_file_object_and_checkpoint_interfaces():
    helper = Path("r_runtime/helpers/baa_helpers.R").read_text(encoding="utf-8")
    assert "baa_file <- function(" in helper
    assert "baa_save_object <- function(" in helper
    assert "baa_checkpoint <- function(" in helper
    assert "baa_load_checkpoint <- function(" in helper
