import base64
import hashlib
import zlib

import pytest

from r_runtime.replay_snapshot import capture_replay_snapshot, unpack_replay_snapshot


def test_exact_helper_and_checkpoint_are_immutable_copies(tmp_path):
    helper = tmp_path / "helper.R"
    helper.write_text("original helper")
    checkpoint = tmp_path / "checkpoints"
    checkpoint.mkdir()
    (checkpoint / "input.rds").write_bytes(b"original checkpoint")
    snapshot = capture_replay_snapshot(helper, checkpoint)
    helper.write_text("changed helper")
    (checkpoint / "input.rds").write_bytes(b"changed checkpoint")
    assert snapshot["status"] == "complete"
    assert unpack_replay_snapshot(snapshot) == {
        "baa_helpers.R": b"original helper", "checkpoints/input.rds": b"original checkpoint",
    }


def test_snapshot_rejects_corruption_and_archive_traversal(tmp_path):
    helper = tmp_path / "helper.R"
    helper.write_text("helper")
    snapshot = capture_replay_snapshot(helper, None)
    snapshot["files"]["baa_helpers.R"]["sha256"] = "wrong"
    with pytest.raises(ValueError, match="fingerprint"):
        unpack_replay_snapshot(snapshot)
    snapshot["files"]["../secret"] = snapshot["files"].pop("baa_helpers.R")
    with pytest.raises(ValueError, match="filename"):
        unpack_replay_snapshot(snapshot)


def test_snapshot_rejects_compression_bomb(monkeypatch):
    import r_runtime.replay_snapshot as module
    monkeypatch.setattr(module, "MAX_SNAPSHOT_BYTES", 100)
    raw = b"x" * 1000
    record = {"size": 1000, "sha256": hashlib.sha256(raw).hexdigest(),
              "zlib_base64": base64.b64encode(zlib.compress(raw)).decode()}
    with pytest.raises(ValueError, match="oversized"):
        unpack_replay_snapshot({"files": {"baa_helpers.R": record}})


def test_browser_autosave_preserves_server_execution_snapshot(tmp_path, monkeypatch):
    from data.reproducibility import without_replay_snapshots
    from data.research_results import ResearchResultStore
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    store = ResearchResultStore("snapshot-autosave")
    state = store.append_r_payload(turn_id="one", question="test", template_context={}, payload={
        "job_id": "job-one", "result": {"ok": True, "code": "analysis()",
        "reproducibility": {"execution_snapshot": {"status": "complete", "files": {}}}},
    })
    public = without_replay_snapshots(state)
    assert "execution_snapshot" not in str(public)
    store.save(public)
    restored, _, _ = store.load()
    assert restored["analysis_turns"][0]["execution_refs"][0]["reproducibility"]["execution_snapshot"]["status"] == "complete"
