from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.format_contracts import accepted_template_formats, dataset_asset_formats
from data.research_dataset import ResearchDatasetStore
from data.scientific_dataset import (
    detect_scientific_format,
    infer_scientific_asset_specs,
    inspect_local_asset_path,
    scientific_format_support,
)


HDF5_SIGNATURE = b"\x89HDF\r\n\x1a\n"


def _write_hdf5_probe(path: Path, *markers: str) -> Path:
    path.write_bytes(HDF5_SIGNATURE + b"\x00" * 64 + b"\x00".join(item.encode() for item in markers))
    return path


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("cohort.csv.gz", "csv"),
        ("cohort.tsv.gz", "tsv"),
        ("notes.txt", "txt"),
        ("notes.txt.gz", "txt"),
        ("reads.fastq.gz", "fastq"),
        ("reads.fq", "fastq"),
        ("reference.fasta.gz", "fasta"),
        ("reference.fa", "fasta"),
        ("alignments.sam", "sam"),
        ("alignments.cram", "cram"),
        ("alignments.cram.crai", "cram_index"),
        ("signal.bigWig", "bigwig"),
        ("signal.bw", "bigwig"),
        ("mutations.maf", "maf"),
        ("tree.newick", "newick"),
        ("tree.nwk", "newick"),
        ("taxonomy.qza", "qza"),
        ("cells.loom", "loom"),
        ("cells.h5Seurat", "h5seurat"),
    ],
)
def test_extended_scientific_suffix_contracts(filename, expected):
    assert detect_scientific_format(filename) == expected


def test_hdf5_probe_distinguishes_tenx_and_generic_content(tmp_path):
    tenx = _write_hdf5_probe(
        tmp_path / "matrix.h5",
        "matrix",
        "barcodes",
        "features",
        "data",
        "indices",
        "indptr",
        "shape",
    )
    generic = _write_hdf5_probe(tmp_path / "assay.h5", "measurements", "temperature")

    assert detect_scientific_format(tenx) == "10x_h5"
    assert detect_scientific_format(generic) == "generic_hdf5"


def test_hdf5_probe_recognizes_h5ad_loom_and_h5seurat_even_with_h5_suffix(tmp_path):
    h5ad = _write_hdf5_probe(tmp_path / "anndata.h5", "encoding-type", "anndata", "obs", "var", "X")
    loom = _write_hdf5_probe(
        tmp_path / "matrix.h5", "LOOM_SPEC_VERSION", "row_attrs", "col_attrs", "layers"
    )
    h5seurat = _write_hdf5_probe(
        tmp_path / "seurat.h5", "h5seurat", "assays", "meta.data", "reductions"
    )

    assert detect_scientific_format(h5ad) == "h5ad"
    assert detect_scientific_format(loom) == "loom"
    assert detect_scientific_format(h5seurat) == "h5seurat"


def test_existing_hdf5_suffix_requires_hdf5_content(tmp_path):
    invalid = tmp_path / "not-really.h5"
    invalid.write_bytes(b"not hdf5")

    with pytest.raises(ValueError, match="HDF5"):
        detect_scientific_format(invalid)


def test_bed_probe_does_not_confuse_plink_binary_with_genomic_bed(tmp_path):
    plink = tmp_path / "cohort.bed"
    plink.write_bytes(b"\x6c\x1b\x01\x00\x01")
    genomic = tmp_path / "regions.bed"
    genomic.write_text("chr1\t10\t20\n", encoding="utf-8")

    assert detect_scientific_format(plink) == "plink_bed"
    assert detect_scientific_format(genomic) == "bed"


def test_compressed_genomic_bed_is_not_misprobed_as_binary_plink(tmp_path):
    compressed = tmp_path / "regions.bed.gz"
    compressed.write_bytes(b"\x1f\x8bcompressed")

    assert detect_scientific_format(compressed) == "bed"


def test_nested_tenx_directory_is_expanded_with_semantic_roles(tmp_path):
    matrix_dir = tmp_path / "cell-ranger" / "outs" / "filtered_feature_bc_matrix"
    matrix_dir.mkdir(parents=True)
    (matrix_dir / "matrix.mtx.gz").write_bytes(b"matrix")
    (matrix_dir / "barcodes.tsv.gz").write_bytes(b"barcodes")
    (matrix_dir / "features.tsv.gz").write_bytes(b"features")
    spatial = matrix_dir.parent.parent / "spatial"
    spatial.mkdir()
    (spatial / "tissue_positions.csv").write_text("barcode,in_tissue\n", encoding="utf-8")
    (spatial / "scalefactors_json.json").write_text("{}", encoding="utf-8")
    (spatial / "tissue_lowres_image.png").write_bytes(b"png")

    specs = infer_scientific_asset_specs([tmp_path / "cell-ranger"])

    assert [item["role"] for item in specs[:3]] == ["primary", "barcodes", "features"]
    assert specs[0]["format"] == "mtx"
    assert {item["role"] for item in specs} >= {
        "primary",
        "barcodes",
        "features",
        "spatial_coordinates",
        "spatial_metadata",
        "spatial_image",
    }
    assert all(Path(item["path"]).is_file() for item in specs)


def test_cell_ranger_root_ignores_duplicate_raw_matrix_and_derived_analysis_tables(tmp_path):
    outs = tmp_path / "cell-ranger" / "outs"
    filtered = outs / "filtered_feature_bc_matrix"
    raw = outs / "raw_feature_bc_matrix"
    analysis = outs / "analysis" / "clustering"
    for folder in (filtered, raw, analysis):
        folder.mkdir(parents=True)
    for folder in (filtered, raw):
        (folder / "matrix.mtx.gz").write_bytes(b"matrix")
        (folder / "barcodes.tsv.gz").write_bytes(b"barcodes")
        (folder / "features.tsv.gz").write_bytes(b"features")
    (analysis / "clusters.csv").write_text("barcode,cluster\n", encoding="utf-8")
    (outs / "metrics_summary.csv").write_text("metric,value\n", encoding="utf-8")
    _write_hdf5_probe(outs / "molecule_info.h5", "molecule_info", "metrics")

    specs = infer_scientific_asset_specs([tmp_path / "cell-ranger"])

    assert next(item for item in specs if item["role"] == "primary")["path"].startswith(str(filtered))
    assert sum(item["role"] == "barcodes" for item in specs) == 1
    assert sum(item["role"] == "features" for item in specs) == 1
    assert {item["role"] for item in specs} >= {"quality_metrics", "molecule_info"}
    assert all(item["name"] != "clusters.csv" for item in specs)


def test_selected_bam_and_index_are_role_inferred_without_order_dependence(tmp_path):
    bam = tmp_path / "sample.bam"
    bam.write_bytes(b"BAM")
    bai = tmp_path / "sample.bam.bai"
    bai.write_bytes(b"index")

    specs = infer_scientific_asset_specs([bai, bam])

    assert [(item["format"], item["role"]) for item in specs] == [
        ("bam", "primary"),
        ("bam_index", "alignment_index"),
    ]


def test_plink_bundle_requires_bim_and_fam_and_assigns_roles(tmp_path):
    bed = tmp_path / "cohort.bed"
    bed.write_bytes(b"\x6c\x1b\x01\x00")
    bim = tmp_path / "cohort.bim"
    bim.write_text("1 rs1 0 1 A G\n", encoding="utf-8")
    fam = tmp_path / "cohort.fam"
    fam.write_text("F1 I1 0 0 1 -9\n", encoding="utf-8")

    specs = infer_scientific_asset_specs([fam, bed, bim])

    assert [(item["format"], item["role"]) for item in specs] == [
        ("plink_bed", "primary"),
        ("plink_bim", "feature_metadata"),
        ("plink_fam", "sample_metadata"),
    ]
    with pytest.raises(ValueError, match="BIM.*FAM"):
        infer_scientific_asset_specs([bed])


def test_batch_fcs_directory_has_one_primary_and_stable_sample_roles(tmp_path):
    folder = tmp_path / "flow-batch"
    folder.mkdir()
    for name in ("z.fcs", "a.fcs", "m.fcs"):
        (folder / name).write_bytes(name.encode())

    specs = infer_scientific_asset_specs([folder])

    assert [item["name"] for item in specs] == ["a.fcs", "m.fcs", "z.fcs"]
    assert [item["role"] for item in specs] == ["primary", "sample_002", "sample_003"]


def test_ambiguous_mixed_directory_is_rejected_instead_of_using_first_file(tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    (folder / "sample.bam").write_bytes(b"BAM")
    (folder / "variants.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="ambiguous"):
        infer_scientific_asset_specs([folder])


@pytest.mark.parametrize(
    "filename",
    ["clinical.csv", "metadata.csv", "samples.tsv", "covariates.csv"],
)
def test_single_tabular_file_is_primary_even_when_its_name_looks_like_a_sidecar(
    tmp_path,
    filename,
):
    source = tmp_path / filename
    source.write_text("id,value\n1,42\n", encoding="utf-8")

    specs = infer_scientific_asset_specs([source])

    assert [(item["name"], item["role"]) for item in specs] == [(filename, "primary")]


def test_directory_inspection_stops_at_configured_entry_limit(tmp_path, monkeypatch):
    folder = tmp_path / "too-many"
    folder.mkdir()
    for index in range(6):
        (folder / f"{index}.csv").write_text("x\n1\n", encoding="utf-8")
    monkeypatch.setenv("BAA_SCIENTIFIC_DIRECTORY_SCAN_MAX_ENTRIES", "3")

    with pytest.raises(ValueError, match="configured inspection limit"):
        inspect_local_asset_path(folder)


def test_directory_inspection_stops_at_resource_derived_limit_with_clear_error(
    tmp_path,
    monkeypatch,
):
    import data.scientific_dataset as scientific_dataset

    folder = tmp_path / "resource-bound"
    folder.mkdir()
    for index in range(4):
        (folder / f"{index}.csv").write_text("x\n1\n", encoding="utf-8")
    monkeypatch.delenv("BAA_SCIENTIFIC_DIRECTORY_SCAN_MAX_ENTRIES", raising=False)
    monkeypatch.setattr(scientific_dataset, "_resource_directory_scan_max_entries", lambda _root: 2)

    with pytest.raises(ValueError, match="resource-derived safety limit.*2 entries"):
        inspect_local_asset_path(folder)


def test_directory_inspection_can_be_cancelled_explicitly(tmp_path):
    folder = tmp_path / "cancelled"
    folder.mkdir()
    (folder / "data.csv").write_text("x\n1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cancelled"):
        inspect_local_asset_path(folder, cancel_check=lambda: True)


def test_directory_inspection_has_no_default_depth_limit(tmp_path):
    folder = tmp_path / "deep-bundle"
    current = folder
    for index in range(12):
        current = current / f"level-{index}"
    current.mkdir(parents=True)
    (current / "data.csv").write_text("x\n1\n", encoding="utf-8")

    inspected = inspect_local_asset_path(folder)

    assert inspected.kind == "directory"
    assert inspected.size_bytes == len("x\n1\n".encode())


def test_store_infers_primary_and_index_for_untyped_assets(tmp_path):
    bam = tmp_path / "sample.bam"
    bam.write_bytes(b"BAM")
    bai = tmp_path / "sample.bai"
    bai.write_bytes(b"index")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_local_assets("session", [bai, bam])

    assert dataset.format == "bam"
    assert [(asset.format, asset.role) for asset in dataset.assets] == [
        ("bam", "primary"),
        ("bam_index", "alignment_index"),
    ]


def test_format_contracts_use_only_primary_loader_backed_formats():
    catalog = AnalysisCatalog()
    single_cell = catalog.get("single_cell")
    publication = catalog.get("publication_table")

    assert "10x_h5" in accepted_template_formats(single_cell)
    assert "generic_hdf5" not in accepted_template_formats(single_cell)
    assert "h5seurat" not in accepted_template_formats(single_cell)
    assert "rdata" not in accepted_template_formats(publication)
    assert "fastq" not in accepted_template_formats(single_cell)
    assert dataset_asset_formats(
        {
            "format": "bam",
            "assets": [
                {"format": "bam", "role": "primary"},
                {"format": "csv", "role": "sample_metadata"},
            ],
        }
    ) == ("bam",)


@pytest.mark.parametrize(
    "format_name",
    [
        "generic_hdf5",
        "loom",
        "h5seurat",
        "fastq",
        "fasta",
        "sam",
        "cram",
        "maf",
        "qza",
    ],
)
def test_non_loader_formats_are_truthfully_marked_upstream_required(format_name):
    assert scientific_format_support(format_name) == "upstream_required"


@pytest.mark.parametrize("format_name", ["bigwig", "newick", "plink_bed"])
def test_native_handle_formats_are_marked_loader_backed(format_name):
    assert scientific_format_support(format_name) == "loader_backed"


def test_desktop_api_infers_primary_from_exact_grant_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("BAA_DESKTOP_MODE", "1")
    duckdb = types.ModuleType("duckdb")
    duckdb.DuckDBPyConnection = object
    duckdb.connect = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)

    from api import create_app
    from desktop.local_import import default_local_import_grants

    bam = tmp_path / "sample.bam"
    bam.write_bytes(b"BAM")
    bai = tmp_path / "sample.bam.bai"
    bai.write_bytes(b"index")
    grant = default_local_import_grants().issue([bai, bam])

    response = create_app().test_client().post(
        "/api/session/inferred-assets/research/import-local",
        json={"grant_token": grant.token},
    )

    assert response.status_code == 200, response.get_json()
    assets = response.get_json()["dataset"]["assets"]
    assert [(item["format"], item["role"]) for item in assets] == [
        ("bam", "primary"),
        ("bam_index", "alignment_index"),
    ]
    assert all(item["access_mode"] == "copy" for item in assets)
    assert str(tmp_path) not in json.dumps(response.get_json(), ensure_ascii=False)

    from data.research_workspace import default_research_workspace

    active = default_research_workspace().current("inferred-assets")
    assert active is not None
    copied_paths = [Path(item.path) for item in active.assets]
    bam.unlink()
    bai.unlink()
    assert [path.read_bytes() for path in copied_paths] == [b"BAM", b"index"]
