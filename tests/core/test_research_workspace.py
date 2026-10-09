from pathlib import Path
from unittest.mock import Mock

import pytest

from data.research_dataset import ResearchDatasetStore
from data.research_workspace import ResearchWorkspace


def write_csv(root: Path, name: str) -> Path:
    path = root / name
    path.write_text("arm,score\nA,1\nB,2\n", encoding="utf-8")
    return path


def test_workspace_replace_keeps_old_dataset_on_failure_and_one_on_success(tmp_path):
    results = Mock()
    dataset_store = ResearchDatasetStore(tmp_path / "datasets")
    workspace = ResearchWorkspace(
        datasets=dataset_store,
        results_for_session=lambda _session_id: results,
        active_root=tmp_path / "active",
        validator=lambda _dataset: None,
    )
    first = workspace.replace("research-session", write_csv(tmp_path, "first.csv"), "first.csv")
    workspace.validator = lambda _dataset: (_ for _ in ()).throw(ValueError("invalid"))
    with pytest.raises(ValueError):
        workspace.replace("research-session", write_csv(tmp_path, "bad.csv"), "bad.csv")
    assert workspace.current("research-session").dataset_id == first.dataset_id
    workspace.validator = lambda _dataset: None
    second = workspace.replace("research-session", write_csv(tmp_path, "second.csv"), "second.csv")
    assert workspace.current("research-session").dataset_id == second.dataset_id
    assert len(dataset_store.list("research-session")) == 1


def test_dataset_delete_rejects_non_identifier_targets(tmp_path):
    store = ResearchDatasetStore(tmp_path / "datasets")

    for invalid in ("", "*", "../abcdef123456", "/tmp/abcdef123456"):
        with pytest.raises(ValueError):
            store.delete("research-session", invalid)


def test_workspace_reference_replacement_and_delete_never_remove_sources(tmp_path):
    results = Mock()
    dataset_store = ResearchDatasetStore(tmp_path / "datasets")
    workspace = ResearchWorkspace(
        datasets=dataset_store,
        results_for_session=lambda _session_id: results,
        active_root=tmp_path / "active",
        validator=lambda _dataset: None,
    )
    first_source = write_csv(tmp_path, "external-first.csv")
    second_source = write_csv(tmp_path, "external-second.csv")

    first = workspace.replace_reference("research-session", first_source)
    second = workspace.replace_reference("research-session", second_source)

    assert workspace.current("research-session").dataset_id == second.dataset_id
    assert first.dataset_id != second.dataset_id
    assert len(dataset_store.list("research-session")) == 1
    assert first_source.read_text(encoding="utf-8").startswith("arm,score")
    assert second_source.read_text(encoding="utf-8").startswith("arm,score")

    workspace.delete_current("research-session")

    assert first_source.is_file()
    assert second_source.is_file()
    assert workspace.current("research-session") is None


def test_failed_reference_replacement_keeps_previous_bundle_and_source(tmp_path):
    results = Mock()
    dataset_store = ResearchDatasetStore(tmp_path / "datasets")
    workspace = ResearchWorkspace(
        datasets=dataset_store,
        results_for_session=lambda _session_id: results,
        active_root=tmp_path / "active",
        validator=lambda _dataset: None,
    )
    first_source = write_csv(tmp_path, "first.csv")
    rejected_source = write_csv(tmp_path, "rejected.csv")
    first = workspace.replace_reference("research-session", first_source)
    workspace.validator = lambda _dataset: (_ for _ in ()).throw(ValueError("invalid"))

    with pytest.raises(ValueError, match="invalid"):
        workspace.replace_reference("research-session", rejected_source)

    assert workspace.current("research-session").dataset_id == first.dataset_id
    assert len(dataset_store.list("research-session")) == 1
    assert first_source.is_file()
    assert rejected_source.is_file()


def test_workspace_can_activate_one_multi_asset_bundle(tmp_path):
    results = Mock()
    dataset_store = ResearchDatasetStore(tmp_path / "datasets")
    workspace = ResearchWorkspace(
        datasets=dataset_store,
        results_for_session=lambda _session_id: results,
        active_root=tmp_path / "active",
        validator=lambda _dataset: None,
    )
    matrix = tmp_path / "matrix.mtx"
    matrix.write_text("%%MatrixMarket\n", encoding="utf-8")
    metadata = tmp_path / "metadata.tsv"
    metadata.write_text("sample\nS1\n", encoding="utf-8")

    dataset = workspace.replace_assets(
        "research-session",
        [
            {"path": matrix, "role": "primary"},
            {"path": metadata, "role": "sample_metadata"},
        ],
        name="coordinated bundle",
    )

    assert workspace.current("research-session").dataset_id == dataset.dataset_id
    assert len(dataset.assets) == 2
    assert len(dataset_store.list("research-session")) == 1


def test_reference_storage_usage_does_not_count_external_source_tree(tmp_path, monkeypatch):
    results = Mock()
    dataset_store = ResearchDatasetStore(tmp_path / "datasets")
    workspace = ResearchWorkspace(
        datasets=dataset_store,
        results_for_session=lambda _session_id: results,
        active_root=tmp_path / "active",
        validator=lambda _dataset: None,
    )
    external_dir = tmp_path / "large-external-tree"
    external_dir.mkdir()
    source = external_dir / "clinical.csv"
    source.write_bytes(b"x" * 100_000)
    monkeypatch.setattr(
        "data.research_workspace.data_path",
        lambda *parts: tmp_path.joinpath("app-data", *parts),
    )
    workspace.replace_reference("research-session", source)

    usage = workspace.storage_usage("research-session")

    assert usage["dataset_bytes"] < source.stat().st_size
