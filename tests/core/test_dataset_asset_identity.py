import json
import os
import stat
from pathlib import Path

import pytest

from agent.tools.research import RToolsMixin
from data.research_dataset import ResearchDatasetStore
from data.scientific_dataset import (
    REFERENCE_ACCESS,
    ScientificDataAsset,
    inspect_local_asset_path,
    validate_scientific_data_asset,
)
from r_runtime.backends.local_rscript import LocalRscriptBackend
from r_runtime.protocol import (
    DatasetAssetRef,
    DatasetRef,
    RRunRequest,
    validate_dataset_asset_mount,
)


def _identity_ref(path: Path, *, kind: str = "file") -> DatasetAssetRef:
    details = path.lstat()
    inspection = inspect_local_asset_path(path)
    return DatasetAssetRef(
        path=str(path),
        format="csv" if kind == "file" else "mtx",
        role="primary",
        relative_mount_name=path.name,
        kind=kind,
        name=path.name,
        size_bytes=int(details.st_size),
        device=int(details.st_dev),
        inode=int(details.st_ino),
        object_type=int(stat.S_IFMT(details.st_mode)),
        mtime_ns=int(details.st_mtime_ns),
        ctime_ns=inspection.ctime_ns,
        fingerprint_sha256=inspection.fingerprint_sha256,
    )


def _dataset_ref(asset: DatasetAssetRef) -> DatasetRef:
    return DatasetRef(
        dataset_id="dataset-identity",
        path=asset.path,
        format=asset.format,
        name=asset.name,
        assets=(asset,),
    )


def _write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("access_mode", ["reference", "copy"])
def test_import_records_private_host_identity_for_files(tmp_path, access_mode):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    store = ResearchDatasetStore(tmp_path / "research")

    dataset = (
        store.add_reference("session-one", source)
        if access_mode == "reference"
        else store.add_upload("session-one", source)
    )

    asset = dataset.assets[0]
    details = Path(asset.path).lstat()
    inspected = inspect_local_asset_path(asset.path)
    assert (
        asset.device,
        asset.inode,
        asset.object_type,
        asset.mtime_ns,
        asset.ctime_ns,
        asset.fingerprint_sha256,
    ) == (
        int(details.st_dev),
        int(details.st_ino),
        int(stat.S_IFMT(details.st_mode)),
        int(details.st_mtime_ns),
        inspected.ctime_ns,
        inspected.fingerprint_sha256,
    )
    stored_asset = dataset.to_storage_dict()["assets"][0]
    assert stored_asset["identity"] == {
        "device": int(details.st_dev),
        "inode": int(details.st_ino),
        "type": int(stat.S_IFMT(details.st_mode)),
        "mtime_ns": int(details.st_mtime_ns),
        "ctime_ns": inspected.ctime_ns,
        "fingerprint_sha256": inspected.fingerprint_sha256,
    }
    public_asset = dataset.to_dict()["assets"][0]
    assert "identity" not in public_asset
    assert "size_bytes" not in public_asset
    assert not (
        {
            "device",
            "inode",
            "type",
            "mtime_ns",
            "ctime_ns",
            "fingerprint_sha256",
        }
        & set(public_asset)
    )
    assert str(details.st_ino) not in json.dumps(public_asset)

    loaded = store.get("session-one", dataset.dataset_id)
    assert loaded is not None
    assert loaded.assets[0] == asset


def test_rtools_propagates_identity_only_inside_dataset_asset_ref(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_text("id,value\n1,42\n", encoding="utf-8")
    dataset = ResearchDatasetStore(tmp_path / "research").add_reference("session-one", source)

    class Agent(RToolsMixin):
        pass

    reference = Agent()._dataset_ref(dataset)
    source_asset = dataset.assets[0]
    runtime_asset = reference.assets[0]

    assert (
        runtime_asset.size_bytes,
        runtime_asset.device,
        runtime_asset.inode,
        runtime_asset.object_type,
        runtime_asset.mtime_ns,
        runtime_asset.ctime_ns,
        runtime_asset.fingerprint_sha256,
    ) == (
        source_asset.size_bytes,
        source_asset.device,
        source_asset.inode,
        source_asset.object_type,
        source_asset.mtime_ns,
        source_asset.ctime_ns,
        source_asset.fingerprint_sha256,
    )
    assert "identity" not in runtime_asset.to_dict()
    assert "inode" not in runtime_asset.to_dict()
    assert "ctime_ns" not in runtime_asset.to_dict()
    assert "fingerprint_sha256" not in runtime_asset.to_dict()
    assert "size_bytes" not in runtime_asset.to_dict()


def test_directory_import_records_root_and_descendant_manifest_identity(tmp_path):
    source = tmp_path / "matrix-directory"
    source.mkdir()
    (source / "matrix.mtx").write_bytes(b"12345")
    dataset = ResearchDatasetStore(tmp_path / "research").add_reference(
        "session-one",
        source,
        format="mtx",
    )

    asset = dataset.assets[0]
    root_details = source.lstat()
    assert asset.kind == "directory"
    assert (asset.device, asset.inode, asset.object_type, asset.mtime_ns) == (
        int(root_details.st_dev),
        int(root_details.st_ino),
        int(stat.S_IFMT(root_details.st_mode)),
        int(root_details.st_mtime_ns),
    )
    assert dataset.to_storage_dict()["assets"][0]["identity"]["inode"] == int(root_details.st_ino)
    assert asset.ctime_ns is not None
    assert asset.fingerprint_sha256 is not None


def test_legacy_manifest_without_identity_is_sealed_on_first_activation(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"12345")
    store = ResearchDatasetStore(tmp_path / "research")
    dataset = store.add_reference("session-one", source)
    manifest_path = store._manifest_path("session-one", dataset.dataset_id)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0].pop("identity")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded = store.get("session-one", dataset.dataset_id)
    assert loaded is not None
    asset = loaded.assets[0]
    details = source.lstat()
    inspected = inspect_local_asset_path(source)
    assert (
        asset.device,
        asset.inode,
        asset.object_type,
        asset.mtime_ns,
        asset.ctime_ns,
        asset.fingerprint_sha256,
    ) == (
        int(details.st_dev),
        int(details.st_ino),
        int(stat.S_IFMT(details.st_mode)),
        int(details.st_mtime_ns),
        inspected.ctime_ns,
        inspected.fingerprint_sha256,
    )
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["assets"][0]["identity"] == {
        "device": int(details.st_dev),
        "inode": int(details.st_ino),
        "type": int(stat.S_IFMT(details.st_mode)),
        "mtime_ns": int(details.st_mtime_ns),
        "ctime_ns": inspected.ctime_ns,
        "fingerprint_sha256": inspected.fingerprint_sha256,
    }

    class Agent(RToolsMixin):
        pass

    runtime_asset = Agent()._dataset_ref(loaded).assets[0]
    assert runtime_asset.size_bytes == 5
    assert validate_dataset_asset_mount(runtime_asset) == source.resolve()

    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(b"abcde")
    os.utime(replacement, ns=(asset.mtime_ns, asset.mtime_ns))
    replacement.replace(source)

    reloaded = store.get("session-one", dataset.dataset_id)
    assert reloaded is not None
    replaced_asset = Agent()._dataset_ref(reloaded).assets[0]
    with pytest.raises(ValueError, match="identity changed"):
        validate_dataset_asset_mount(replaced_asset)


def test_runtime_rejects_same_path_same_size_object_replacement(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"original")
    asset = _identity_ref(source)
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(b"replaced")
    os.utime(replacement, ns=(asset.mtime_ns, asset.mtime_ns))
    replacement.replace(source)

    with pytest.raises(ValueError, match="identity changed"):
        validate_dataset_asset_mount(asset)


def test_strong_file_identity_rejects_in_place_same_size_rewrite_with_restored_mtime(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"original")
    asset = _identity_ref(source)

    source.write_bytes(b"replaced")
    os.utime(source, ns=(asset.mtime_ns, asset.mtime_ns))

    with pytest.raises(ValueError, match="identity changed"):
        validate_dataset_asset_mount(asset)


def test_scientific_asset_storage_round_trips_strong_private_identity(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"original")
    inspected = inspect_local_asset_path(source)
    asset = ScientificDataAsset(
        path=str(source),
        format="csv",
        role="primary",
        size_bytes=inspected.size_bytes,
        access_mode=REFERENCE_ACCESS,
        relative_mount_name=source.name,
        device=inspected.device,
        inode=inspected.inode,
        object_type=inspected.object_type,
        mtime_ns=inspected.mtime_ns,
        ctime_ns=inspected.ctime_ns,
        fingerprint_sha256=inspected.fingerprint_sha256,
    )

    stored = asset.to_storage_dict()
    loaded = ScientificDataAsset.from_dict(stored)

    assert stored["identity"]["ctime_ns"] == inspected.ctime_ns
    assert stored["identity"]["fingerprint_sha256"] == inspected.fingerprint_sha256
    assert loaded == asset
    source.write_bytes(b"replaced")
    os.utime(source, ns=(asset.mtime_ns, asset.mtime_ns))
    with pytest.raises(ValueError, match="identity changed"):
        validate_scientific_data_asset(asset)


def test_local_backend_rejects_replaced_object_before_starting_r(tmp_path):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"original")
    asset = _identity_ref(source)
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(b"replaced")
    os.utime(replacement, ns=(asset.mtime_ns, asset.mtime_ns))
    replacement.replace(source)
    marker = tmp_path / "r-started"
    fake = _write_executable(
        tmp_path / "fake-rscript",
        f"#!/bin/sh\ntouch '{marker}'\n",
    )
    request = RRunRequest(
        job_id="identity-local",
        dataset=_dataset_ref(asset),
        code="summary(dataset)",
        artifacts_dir=str(tmp_path / "out"),
    )

    result = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R").run_code(request)

    assert result.ok is False
    assert "identity changed" in result.error
    assert not marker.exists()


def test_local_backend_revalidates_after_script_preparation_before_starting_r(tmp_path, monkeypatch):
    source = tmp_path / "clinical.csv"
    source.write_bytes(b"original")
    asset = _identity_ref(source)
    replacement = tmp_path / "replacement.csv"
    replacement.write_bytes(b"replaced")
    os.utime(replacement, ns=(asset.mtime_ns, asset.mtime_ns))
    marker = tmp_path / "r-started"
    fake = _write_executable(tmp_path / "fake-rscript", f"#!/bin/sh\ntouch '{marker}'\n")
    backend = LocalRscriptBackend(rscript_bin=str(fake), helper_path=tmp_path / "missing.R")
    original_build_script = backend._build_script

    def replace_after_build(request, *, dataset_path=None, helper_path=None):
        script = original_build_script(
            request,
            dataset_path=dataset_path,
            helper_path=helper_path,
        )
        replacement.replace(source)
        return script

    monkeypatch.setattr(backend, "_build_script", replace_after_build)
    request = RRunRequest(
        job_id="identity-race-local",
        dataset=_dataset_ref(asset),
        code="summary(dataset)",
        artifacts_dir=str(tmp_path / "out"),
    )

    result = backend.run_code(request)

    assert result.ok is False
    assert "identity changed" in result.error
    assert not marker.exists()


def test_legacy_asset_without_identity_still_rejects_symlink_and_size_change(tmp_path):
    selected = tmp_path / "selected"
    selected.mkdir()
    source = selected / "clinical.csv"
    source.write_bytes(b"12345")
    legacy = DatasetAssetRef(
        path=str(source),
        format="csv",
        role="primary",
        relative_mount_name="clinical.csv",
        kind="file",
        size_bytes=5,
    )
    assert validate_dataset_asset_mount(legacy) == source.resolve()

    source.write_bytes(b"123456")
    with pytest.raises(ValueError, match="size changed"):
        validate_dataset_asset_mount(legacy)

    source.write_bytes(b"12345")
    original = tmp_path / "original-selected"
    selected.rename(original)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "clinical.csv").write_bytes(b"12345")
    selected.symlink_to(attacker, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink|reparse"):
        validate_dataset_asset_mount(legacy)


def test_directory_mount_rejects_nested_rewrite_even_when_root_and_file_mtime_are_restored(
    tmp_path,
):
    source = tmp_path / "matrix-directory"
    source.mkdir()
    nested = source / "matrix.mtx"
    nested.write_bytes(b"12345")
    asset = _identity_ref(source, kind="directory")
    nested_mtime_ns = nested.stat().st_mtime_ns
    nested.write_bytes(b"abcde")
    os.utime(nested, ns=(nested_mtime_ns, nested_mtime_ns))
    os.utime(source, ns=(asset.mtime_ns, asset.mtime_ns))

    with pytest.raises(ValueError, match="identity changed"):
        validate_dataset_asset_mount(asset)


def test_directory_mount_rejects_replaced_root_object(tmp_path):
    source = tmp_path / "matrix-directory"
    source.mkdir()
    (source / "matrix.mtx").write_bytes(b"12345")
    asset = _identity_ref(source, kind="directory")
    original = tmp_path / "original-directory"
    source.rename(original)
    source.mkdir()
    os.utime(source, ns=(asset.mtime_ns, asset.mtime_ns))
    with pytest.raises(ValueError, match="identity changed"):
        validate_dataset_asset_mount(asset)
