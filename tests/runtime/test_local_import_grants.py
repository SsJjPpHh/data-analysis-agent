import os
from pathlib import Path

import pytest

from desktop.local_import import LocalImportGrantStore


def test_native_import_grant_is_exact_and_one_use(tmp_path):
    source = tmp_path / "large.h5ad"
    source.write_bytes(b"scientific-data")
    grants = LocalImportGrantStore(ttl_seconds=60)

    selection = grants.issue([source])
    grant = grants.consume(selection.token)

    assert grant.paths == (str(source.resolve()),)
    assert selection.name == "large.h5ad"
    assert selection.size_bytes == len(b"scientific-data")
    with pytest.raises(ValueError, match="失效"):
        grants.consume(selection.token)


def test_native_import_grant_rejects_selected_symlink(tmp_path):
    source = tmp_path / "counts.tsv"
    source.write_text("gene\ts1\nA\t1\n", encoding="utf-8")
    link = tmp_path / "linked.tsv"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("filesystem does not permit symlink creation")

    with pytest.raises(ValueError, match="符号链接"):
        LocalImportGrantStore().issue([link])


def test_native_directory_grant_returns_without_recursively_sizing_or_exposing_paths(tmp_path):
    bundle = tmp_path / "tenx"
    bundle.mkdir()
    (bundle / "matrix.mtx").write_bytes(b"1234")
    (bundle / "barcodes.tsv").write_bytes(b"12")
    selection = LocalImportGrantStore().issue([bundle])

    assert selection.kind == "directory"
    assert selection.size_bytes == 0
    assert str(Path(bundle).parent) not in selection.to_json()


def test_native_import_grant_rejects_same_path_replacement(tmp_path):
    source = tmp_path / "counts.tsv"
    source.write_bytes(b"first")
    replacement = tmp_path / "replacement.tsv"
    replacement.write_bytes(b"other")
    grants = LocalImportGrantStore(ttl_seconds=60)

    selection = grants.issue([source])
    replacement.replace(source)

    with pytest.raises(ValueError, match="发生变化"):
        grants.consume(selection.token)


def test_native_import_grant_rejects_same_size_rewrite_with_restored_mtime(tmp_path):
    source = tmp_path / "counts.tsv"
    source.write_bytes(b"first")
    original_mtime_ns = source.stat().st_mtime_ns
    grants = LocalImportGrantStore(ttl_seconds=60)

    selection = grants.issue([source])
    source.write_bytes(b"other")
    os.utime(source, ns=(original_mtime_ns, original_mtime_ns))

    with pytest.raises(ValueError, match="发生变化"):
        grants.consume(selection.token)


def test_native_directory_grant_rejects_descendant_rewrite_with_restored_mtime(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    nested = bundle / "matrix.mtx"
    nested.write_bytes(b"first")
    original_mtime_ns = nested.stat().st_mtime_ns
    grants = LocalImportGrantStore(ttl_seconds=60)

    selection = grants.issue([bundle])
    nested.write_bytes(b"other")
    os.utime(nested, ns=(original_mtime_ns, original_mtime_ns))

    with pytest.raises(ValueError, match="发生变化"):
        grants.consume(selection.token)
