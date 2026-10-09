"""Sharing choices survive reload without bypassing conversation mutation leases."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from agent.analysis.model_sharing import ModelSharingPolicy
from data.session import ChatSession


DEFAULT = ModelSharingPolicy().to_dict()
CHOSEN = dict(result_mode="deidentified", sensitive_columns=["patient_id", "姓名"], send_result_images=True, send_attached_images=False, version=1)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BAA_DESKTOP_TOKEN", raising=False)
    from api import create_app, models, saved_sessions
    from api.state import session_manager

    directory = tmp_path / "sessions"
    directory.mkdir()
    monkeypatch.setattr(saved_sessions, "SAVE_DIR", directory)
    monkeypatch.setattr(saved_sessions, "config_manager", SimpleNamespace(get_config=lambda _p: object(), get_default_provider=lambda: "deepseek"))
    # These settings must never touch credentials or probe the provider.
    monkeypatch.setattr(models, "config_manager", object())
    client = create_app().test_client()
    sid = client.post("/api/session/new").get_json()["session_id"]
    sess = session_manager.get(sid)
    sess.model_provider = "deepseek"
    yield client, sess, directory, saved_sessions
    session_manager.remove(sid)


def test_policy_preserves_exact_columns_deduplicates_and_is_an_immutable_snapshot():
    source = dict(CHOSEN, sensitive_columns=[" patient_id ", "姓名", "patient_id", "Patient_id", " patient_id "])
    policy = ModelSharingPolicy.from_dict(source, strict=True)
    source["sensitive_columns"].append("later_mutation")
    assert policy.sensitive_columns == (" patient_id ", "姓名", "patient_id", "Patient_id")
    exported = policy.to_dict()
    exported["sensitive_columns"].clear()
    assert policy.sensitive_columns == (" patient_id ", "姓名", "patient_id", "Patient_id")
    with pytest.raises(FrozenInstanceError):
        policy.result_mode = "summary_only"


INVALID = [
    None, [], {"unexpected": True}, {"result_mode": "raw"}, {"result_mode": 1},
    {"send_result_images": 1}, {"send_attached_images": "false"},
    {"version": True}, {"version": 2}, {"sensitive_columns": "patient_id"},
    {"sensitive_columns": [1]}, {"sensitive_columns": [" "]},
    {"sensitive_columns": ["x\ny"]}, {"sensitive_columns": ["x" * 257]},
    {"sensitive_columns": [f"c{i}" for i in range(257)]},
]


@pytest.mark.parametrize("payload", INVALID)
def test_invalid_policy_is_rejected_at_api_but_saved_state_falls_back_safely(settings, payload):
    client, sess, directory, _saved = settings
    sess.model_sharing_policy = dict(CHOSEN)
    response = client.post(f"/api/session/{sess.session_id}/model-sharing", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 400
    assert sess.model_sharing_policy == CHOSEN
    assert list(directory.iterdir()) == []
    assert ModelSharingPolicy.from_dict(payload).to_dict() == DEFAULT


def test_default_and_hot_reload_backfill_do_not_share_mutable_state():
    first, second = ChatSession(), ChatSession()
    first.model_sharing_policy["sensitive_columns"].append("first_only")
    assert second.model_sharing_policy == DEFAULT
    del first.model_sharing_policy
    first._ensure_fields()
    assert first.model_sharing_policy == DEFAULT
    assert first.model_sharing_policy is not second.model_sharing_policy


def test_api_persists_settings_before_the_first_message_and_invalidates_old_summary(settings):
    client, sess, directory, _saved = settings
    assert client.get(f"/api/session/{sess.session_id}/model-sharing").get_json() == {"ok": True, "policy": DEFAULT}
    sess.compaction = {"summary": "OLD_DISCLOSURE", "covered": 2, "boundary": "old"}
    sess.last_prompt_tokens = 888
    sess.last_reasoning = "OLD_INDIVIDUAL_VALUE"
    response = client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN)
    assert response.status_code == 200 and response.get_json()["policy"] == CHOSEN
    assert sess.compaction is None and sess.last_prompt_tokens == 0
    assert sess.last_reasoning == ""
    assert sess.model_provider == "deepseek"
    saved = json.loads((directory / f"autosave_{sess.session_id}.json").read_text())
    assert saved["history"] == [] and saved["model_sharing_policy"] == CHOSEN


@pytest.mark.parametrize("lease", ["conversation", "mutation"])
def test_busy_session_rejects_policy_change_without_touching_saved_state(settings, lease):
    client, sess, directory, _saved = settings
    begin = sess.begin_conversation if lease == "conversation" else sess.begin_session_mutation
    end = sess.end_conversation if lease == "conversation" else sess.end_session_mutation
    assert begin()
    try:
        response = client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN)
        assert response.status_code == 409
        assert sess.model_sharing_policy == DEFAULT
        assert list(directory.iterdir()) == []
        assert client.get(f"/api/session/{sess.session_id}/model-sharing").get_json()["policy"] == DEFAULT
    finally:
        end()
    assert client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN).status_code == 200


def test_persistence_error_keeps_previous_settings_and_releases_mutation_lease(settings, monkeypatch):
    client, sess, _directory, saved = settings
    compaction = {"summary": "previous"}
    sess.compaction = compaction
    sess.last_prompt_tokens = 123
    sess.last_reasoning = "previous reasoning"
    monkeypatch.setattr(saved, "persist_local_autosave", lambda _s: (_ for _ in ()).throw(OSError("disk unavailable")))
    assert client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN).status_code == 503
    assert sess.model_sharing_policy == DEFAULT
    assert sess.compaction == compaction and sess.last_prompt_tokens == 123
    assert sess.last_reasoning == "previous reasoning"
    assert sess.begin_conversation()
    sess.end_conversation()


def test_manual_save_load_restores_policy_and_drops_preexisting_compaction(settings):
    client, sess, directory, _saved = settings
    sess.add_user("研究问题")
    sess.history.append({"role": "assistant", "content": "旧结论", "reasoning": "OLD_INDIVIDUAL_VALUE"})
    sess.last_reasoning = "OLD_INDIVIDUAL_VALUE"
    sess.model_sharing_policy = ModelSharingPolicy.from_dict(CHOSEN, strict=True).to_dict()
    sess.compaction = {"summary": "OLD_DISCLOSURE", "covered": 1, "boundary": "old"}
    response = client.post(f"/api/session/{sess.session_id}/save", json={"name": "sharing"})
    assert response.status_code == 200
    filename = response.get_json()["filename"]
    assert json.loads((directory / filename).read_text())["model_sharing_policy"] == CHOSEN
    sess.model_sharing_policy = DEFAULT.copy()
    loaded = client.post(f"/api/session/{sess.session_id}/load", json={"filename": filename})
    assert loaded.status_code == 200 and loaded.get_json()["model_sharing_policy"] == CHOSEN
    assert sess.model_sharing_policy == CHOSEN and sess.compaction is None
    assert sess.last_reasoning == ""
    assert any(message.get("content") == "旧结论" for message in sess.history)


def test_empty_history_settings_restore_after_process_restart(settings):
    client, sess, _directory, saved = settings
    from api.state import session_manager

    sid = sess.session_id
    assert client.post(f"/api/session/{sid}/model-sharing", json=CHOSEN).status_code == 200
    sess.compaction = {"summary": "OLD_DISCLOSURE", "covered": 1, "boundary": "old"}
    saved.persist_local_autosave(sess)
    session_manager.remove(sid)
    response = client.post(f"/api/session/{sid}/resume")
    assert response.status_code == 200 and response.get_json()["restored"] is True
    restored = session_manager.get(sid)
    assert restored.history == [] and restored.model_sharing_policy == CHOSEN
    assert restored.compaction is None and restored.last_prompt_tokens == 0
    assert restored.last_reasoning == ""


@pytest.mark.parametrize("restorer", ["manual", "autosave"])
@pytest.mark.parametrize("old_policy", ["missing", {"result_mode": "raw", "send_result_images": True}])
def test_legacy_or_invalid_save_overwrites_current_permissive_policy_with_default(settings, restorer, old_policy):
    client, sess, directory, saved = settings
    record = {"history": [{"role": "user", "content": "旧会话"}, {"role": "assistant", "content": "旧结论", "reasoning": "OLD_INDIVIDUAL_VALUE"}], "compaction": {"summary": "OLD_DISCLOSURE"}}
    if old_policy != "missing":
        record["model_sharing_policy"] = old_policy
    filename = "legacy.json" if restorer == "manual" else f"autosave_{sess.session_id}.json"
    (directory / filename).write_text(json.dumps(record))
    sess.model_sharing_policy = dict(CHOSEN)
    if restorer == "manual":
        assert client.post(f"/api/session/{sess.session_id}/load", json={"filename": filename}).status_code == 200
    else:
        with client.application.test_request_context():
            restored, ok = saved.restore_local_autosave(sess.session_id)
        assert ok and restored is sess
    assert sess.model_sharing_policy == DEFAULT and sess.compaction is None
    assert sess.last_reasoning == ""


@pytest.mark.parametrize("lease", ["conversation", "mutation"])
def test_autosave_restore_cannot_replace_a_running_session(settings, lease):
    client, sess, directory, saved = settings
    (directory / f"autosave_{sess.session_id}.json").write_text(json.dumps({"history": [], "model_sharing_policy": CHOSEN}))
    sess.add_user("当前问题")
    previous_history = list(sess.history)
    begin = sess.begin_conversation if lease == "conversation" else sess.begin_session_mutation
    end = sess.end_conversation if lease == "conversation" else sess.end_session_mutation
    assert begin()
    try:
        with client.application.test_request_context():
            restored, ok = saved.restore_local_autosave(sess.session_id)
        assert not ok and restored is sess
        assert sess.history == previous_history and sess.model_sharing_policy == DEFAULT
    finally:
        end()


def test_invalid_autosave_never_partially_applies_a_more_permissive_policy(settings):
    client, sess, directory, saved = settings
    sess.add_user("当前问题")
    original = list(sess.history)
    (directory / f"autosave_{sess.session_id}.json").write_text(json.dumps({"history": [], "model_sharing_policy": CHOSEN, "total_input_tokens": "invalid"}))
    with client.application.test_request_context():
        restored, ok = saved.restore_local_autosave(sess.session_id)
    assert not ok and restored.history == original and restored.model_sharing_policy == DEFAULT
    assert sess.begin_session_mutation()
    sess.end_session_mutation()


def test_history_clear_and_rewind_do_not_restore_a_wider_sharing_policy(settings):
    client, sess, directory, _saved = settings
    sess.add_user("研究问题")
    earlier = sess.capture_rewind_state()
    assert client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN).status_code == 200
    sess.restore_rewind_state(earlier)
    assert sess.model_sharing_policy == CHOSEN
    assert client.post(f"/api/session/{sess.session_id}/clear").status_code == 200
    assert sess.history == [] and sess.model_sharing_policy == CHOSEN
    assert (directory / f"autosave_{sess.session_id}.json").is_file()


def test_disabled_attachments_are_rejected_before_loading_images_or_building_model(settings, monkeypatch):
    client, sess, _directory, _saved = settings
    from api import chat

    assert client.post(f"/api/session/{sess.session_id}/model-sharing", json=CHOSEN).status_code == 200
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("disabled attachments must be rejected before image/model construction")

    monkeypatch.setattr(chat, "_build_agent", forbidden)
    monkeypatch.setattr(chat, "load_conversation_images", forbidden)
    response = client.post(f"/api/session/{sess.session_id}/chat", json={"message": "检查附件", "image_ids": ["synthetic-image"]})
    assert response.status_code == 400
    assert response.get_json()["code"] == "attached_images_disabled"
    assert calls == []
    assert sess.history == []
    assert sess.begin_conversation()
    sess.end_conversation()
