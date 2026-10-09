from pathlib import Path
import errno
import json

import pytest

from data.research_dataset import (
    ResearchDataset,
    ResearchDatasetStore,
    detect_research_format,
    register_research_format,
)


def test_save_manifest(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_upload("session/one", source)

    loaded = store.get("session/one", dataset.dataset_id)
    assert loaded is not None
    assert len(loaded.dataset_id) == 12
    assert loaded.name == "clinical.csv"
    assert loaded.format == "csv"
    assert Path(loaded.stored_path).read_text(encoding="utf-8") == "id,value\n1,42\n"
    assert [item.dataset_id for item in store.list("session/one")] == [dataset.dataset_id]
    assert "stored_path" not in dataset.to_dict()
    assert dataset.to_storage_dict()["stored_path"] == dataset.stored_path


def test_staged_upload_is_atomically_adopted_without_copy(tmp_path, monkeypatch):
    store = ResearchDatasetStore(tmp_path / "research")
    staging_dir = store.upload_staging_root() / "request-one"
    staging_dir.mkdir()
    staged = staging_dir / "clinical.csv"
    staged.write_text("id,value\n1,42\n", encoding="utf-8")

    def unexpected_copy(*_args, **_kwargs):
        raise AssertionError("a browser-staged upload must be adopted, not copied")

    monkeypatch.setattr("data.research_dataset.shutil.copy2", unexpected_copy)

    dataset = store.add_staged_upload("session-one", staged, "clinical.csv")

    adopted = Path(dataset.stored_path)
    assert not staged.exists()
    assert adopted.read_text(encoding="utf-8") == "id,value\n1,42\n"
    assert adopted.stat().st_dev == store.root.stat().st_dev


def test_staged_upload_cross_device_failure_is_fail_closed(tmp_path, monkeypatch):
    store = ResearchDatasetStore(tmp_path / "research")
    staging_dir = store.upload_staging_root() / "request-one"
    staging_dir.mkdir()
    staged = staging_dir / "clinical.csv"
    staged.write_text("id,value\n1,42\n", encoding="utf-8")

    def cross_device(_source, _destination):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr("data.research_dataset._atomic_replace", cross_device)

    with pytest.raises(ValueError, match="same filesystem"):
        store.add_staged_upload("session-one", staged, "clinical.csv")

    assert staged.is_file()
    assert store.list("session-one") == []


def test_public_dataset_profile_omits_nested_runtime_paths(tmp_path):
    internal_path = str(tmp_path / "outputs" / "research" / "profile.csv")
    dataset = ResearchDataset(
        dataset_id="a" * 12,
        session_id="session-one",
        name="clinical.csv",
        format="csv",
        stored_path=str(tmp_path / "clinical.csv"),
        created_at="2026-06-29T00:00:00",
        profile={
            "summary": {
                "rows": 2,
                "local_semantics": {"levels": {"group": ["control", "treatment"]}},
            },
            "analysis_context": {"role_memory": {"private-key": "group"}},
            "inspection": {
                "ok": True,
                "tables": [{"name": "dataset_profile", "path": internal_path}],
                "stderr": f"read {internal_path}",
            },
        },
    )

    serialized = json.dumps(dataset.to_dict(), ensure_ascii=False)

    assert internal_path not in serialized
    assert dataset.to_dict()["profile"]["summary"]["rows"] == 2
    assert "local_semantics" not in dataset.to_dict()["profile"]["summary"]
    assert "analysis_context" not in dataset.to_dict()["profile"]


def test_registers_txt_as_upstream_required_instead_of_executable(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("not tabular", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_upload("session-one", source)

    assert dataset.format == "txt"
    assert dataset.primary_asset.support == "upstream_required"


def test_list_ignores_corrupt_manifests(tmp_path):
    source = tmp_path / "clinical.tsv"
    source.write_text("id\tvalue\n1\t42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")
    dataset = store.add_upload("session-one", source)

    corrupt_dir = store._session_dir("session-one") / "bad"
    corrupt_dir.mkdir(parents=True)
    (corrupt_dir / "manifest.json").write_text("{", encoding="utf-8")

    assert [item.dataset_id for item in store.list("session-one")] == [dataset.dataset_id]


def test_session_paths_are_collision_resistant(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    first = store.add_upload("a/b", source)
    second = store.add_upload("a_b", source)
    blank = store.add_upload("", source)

    assert Path(first.stored_path).parents[1] != Path(second.stored_path).parents[1]
    assert Path(blank.stored_path).parents[1] == store._session_dir("")


def test_invalid_dataset_id_is_rejected(tmp_path):
    store = ResearchDatasetStore(tmp_path / "research")
    dataset = ResearchDataset(
        dataset_id="../bad",
        session_id="session-one",
        name="clinical.csv",
        format="csv",
        stored_path=str(tmp_path / "clinical.csv"),
        created_at="2026-06-29T00:00:00",
    )

    with pytest.raises(ValueError, match="Invalid dataset_id"):
        store.save(dataset)

    with pytest.raises(ValueError, match="Invalid dataset_id"):
        store.get("session-one", "../bad")


def test_datasets_are_listed_newest_first(tmp_path, monkeypatch):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    timestamps = iter(["2026-01-01T00:00:00", "2026-01-02T00:00:00"])
    monkeypatch.setattr("data.research_dataset._now_iso", lambda: next(timestamps))
    older = store.add_upload("session-one", source, "older.csv")
    newer = store.add_upload("session-one", source, "newer.csv")

    assert [item.dataset_id for item in store.list("session-one")] == [
        newer.dataset_id,
        older.dataset_id,
    ]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("table.csv", "csv"),
        ("table.tsv", "tsv"),
        ("workbook.xlsx", "xlsx"),
        ("legacy.xls", "xls"),
        ("survey.sav", "sav"),
        ("stata.dta", "dta"),
        ("object.rds", "rds"),
        ("table.parquet", "parquet"),
        ("workspace.RData", "rdata"),
        ("matrix.mtx.gz", "mtx"),
        ("assay.h5", "generic_hdf5"),
        ("cells.h5ad", "h5ad"),
        ("events.fcs", "fcs"),
        ("variants.vcf.gz", "vcf"),
        ("variants.bcf", "bcf"),
        ("regions.bed.gz", "bed"),
        ("peaks.narrowPeak", "narrowpeak"),
        ("reads.bam", "bam"),
        ("genes.gff3.gz", "gff"),
        ("genes.gtf", "gtf"),
        ("community.biom", "biom"),
        ("run.mzML", "mzml"),
        ("run.mzXML", "mzxml"),
    ],
)
def test_detects_legacy_and_scientific_formats(filename, expected):
    assert detect_research_format(filename) == expected


def test_format_registry_is_extensible():
    register_research_format("example-science", ".example-science")

    assert detect_research_format("cohort.example-science") == "example-science"


def test_reference_import_is_zero_copy_and_keeps_real_path_private(tmp_path, monkeypatch):
    source = tmp_path / "external" / "cells.h5ad"
    source.parent.mkdir()
    source.write_bytes(b"\x89HDF\r\n\x1a\nencoding-type\x00anndata\x00obs\x00var\x00X")
    store = ResearchDatasetStore(tmp_path / "research")

    def unexpected_copy(*_args, **_kwargs):
        raise AssertionError("reference import must not copy source data")

    monkeypatch.setattr("data.research_dataset.shutil.copy2", unexpected_copy)
    dataset = store.add_reference("session-one", source, role="counts")

    assert dataset.stored_path == str(source.resolve())
    assert len(dataset.assets) == 1
    assert dataset.assets[0].access_mode == "reference"
    assert dataset.assets[0].role == "counts"
    assert dataset.assets[0].format == "h5ad"
    assert dataset.assets[0].kind == "file"
    assert dataset.assets[0].size_bytes == source.stat().st_size
    assert dataset.assets[0].relative_mount_name == "cells.h5ad"
    assert str(source.resolve()) not in json.dumps(dataset.to_dict())
    assert "path" not in dataset.to_dict()["assets"][0]
    assert dataset.to_storage_dict()["assets"][0]["path"] == str(source.resolve())

    loaded = store.get("session-one", dataset.dataset_id)
    assert loaded is not None
    assert loaded.assets == dataset.assets


def test_reference_import_requires_an_exact_existing_absolute_path(tmp_path, monkeypatch):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="absolute"):
        store.add_reference("relative", Path("clinical.csv"))
    with pytest.raises(ValueError, match="does not exist"):
        store.add_reference("missing", tmp_path / "missing.csv")
    with pytest.raises(ValueError, match="exact"):
        store.add_reference("ambiguous", tmp_path / "nested" / ".." / "clinical.csv")

    assert store.list("relative") == []
    assert store.list("missing") == []
    assert store.list("ambiguous") == []


def test_multi_asset_manifest_roundtrips_roles_and_mount_names(tmp_path):
    matrix = tmp_path / "matrix.mtx"
    matrix.write_text("%%MatrixMarket matrix coordinate integer general\n", encoding="utf-8")
    samples = tmp_path / "samples.tsv"
    samples.write_text("sample\nS1\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_local_assets(
        "session-one",
        [
            {
                "path": matrix,
                "role": "primary",
                "relative_mount_name": "counts/matrix.mtx",
            },
            {
                "path": samples,
                "role": "sample_metadata",
                "relative_mount_name": "metadata/samples.tsv",
            },
        ],
        name="single-cell bundle",
    )

    assert dataset.name == "single-cell bundle"
    assert dataset.format == "mtx"
    assert dataset.stored_path == str(matrix.resolve())
    assert [asset.role for asset in dataset.assets] == ["primary", "sample_metadata"]
    assert [asset.relative_mount_name for asset in dataset.assets] == [
        "counts/matrix.mtx",
        "metadata/samples.tsv",
    ]
    assert all(asset.access_mode == "reference" for asset in dataset.assets)
    assert store.get("session-one", dataset.dataset_id).assets == dataset.assets


def test_explicit_copy_mode_supports_directory_assets(tmp_path):
    source = tmp_path / "matrix-directory"
    source.mkdir()
    (source / "matrix.mtx").write_bytes(b"original")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_local_assets(
        "session-one",
        [{"path": source, "format": "mtx", "relative_mount_name": "counts"}],
        access_mode="copy",
    )

    copied = Path(dataset.assets[0].path)
    assert dataset.assets[0].access_mode == "copy"
    assert dataset.assets[0].kind == "directory"
    assert copied != source.resolve()
    assert (copied / "matrix.mtx").read_bytes() == b"original"

    (source / "matrix.mtx").write_bytes(b"changed")
    store.delete("session-one", dataset.dataset_id)

    assert (source / "matrix.mtx").read_bytes() == b"changed"
    assert not copied.exists()


def test_reference_directory_is_supported_but_symlinks_are_rejected(tmp_path):
    source = tmp_path / "matrix-directory"
    source.mkdir()
    (source / "matrix.mtx").write_bytes(b"12345")
    (source / "barcodes.tsv").write_bytes(b"abc")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = store.add_reference("session-one", source)

    assert dataset.assets[0].kind == "directory"
    assert dataset.assets[0].size_bytes == 8

    outside = tmp_path / "outside.tsv"
    outside.write_text("private", encoding="utf-8")
    (source / "unsafe.tsv").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        store.add_reference("session-two", source, format="mtx")

    linked_source = tmp_path / "linked-matrix"
    linked_source.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.add_reference("session-three", linked_source, format="mtx")

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        store.add_reference("session-four", linked_parent / "matrix.mtx")


@pytest.mark.parametrize("mount_name", ["", ".", "../escape.csv", "/absolute.csv", "a/../../b.csv"])
def test_reference_import_rejects_unsafe_relative_mount_names(tmp_path, mount_name):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    with pytest.raises(ValueError, match="mount"):
        store.add_reference("session-one", source, relative_mount_name=mount_name)


def test_legacy_manifest_without_assets_loads_as_one_copy_asset(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")
    dataset = store.add_upload("session-one", source)
    manifest_path = store._manifest_path("session-one", dataset.dataset_id)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("assets")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = store.get("session-one", dataset.dataset_id)

    assert loaded is not None
    assert len(loaded.assets) == 1
    assert loaded.assets[0].access_mode == "copy"
    assert loaded.assets[0].path == loaded.stored_path
