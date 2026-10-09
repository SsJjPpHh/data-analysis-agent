"""Inspect actual provider kwargs across tool, vision, replay and compaction."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent.agent import BusinessAgent
from agent.analysis.model_egress import ModelEgressGuard
from agent.vision import build_r_artifact_review
from tests.core.test_agent_r_figure_vision import _Client, _FigureAgent, _Completions, _FinalStream
from tests.core.test_deepseek_vision import PNG_1X1


PRIVATE_ID = "SUBJECT_PRIVATE_20261008"
PRIVATE_NAME = "CONFIRMED_PRIVATE_NAME"

# Synthetic coefficient table from the local live-API pioneer. Do not depend
# on ignored acceptance receipts or make a model request in this regression.
PIONEER_EFFECT_CSV = (
    "变量,效应,CI下限,CI上限,P值\n"
    "(Intercept),0.8562,0.5921,1.1203,<0.001\n"
    "x,1.8707,1.6905,2.0509,<0.001\n"
    "z,-0.0962,-0.2908,0.0985,0.330\n"
    "armB,1.2307,0.8577,1.6037,<0.001"
)


@pytest.mark.parametrize("csv_text,role", [
    (PIONEER_EFFECT_CSV, "effect_estimate_table"),
    ("变量,稳健效应,稳健CI下限,稳健CI上限\nx,1.8707,1.6849,2.0566", "robustness_comparison"),
    ("变量名,回归系数,标准误,P值\nx,-0.0962,0.0983,0.330", "coefficient_table"),
    ("变量,OR,95%CI下限,95%CI上限,校正P值\nx,1.87,1.69,2.05,≤0.001", "effect_estimate_table"),
])
def test_summary_mode_retains_chinese_statistical_tables_and_original_artifacts(csv_text, role):
    payload = {"name": "table_1", "metadata": {"role": role, "semantic_key": role}, "preview_csv": csv_text}
    before = copy.deepcopy(payload)
    guard = ModelEgressGuard()
    clean = guard.project(payload)
    assert clean["preview_csv"] == csv_text
    assert guard.project(clean) == clean
    assert payload == before


@pytest.mark.parametrize("csv_text,metadata", [
    (f"subject_id,效应,P值\n{PRIVATE_ID},0.721928,0.0042", {"role": "effect_estimate_table"}),
    ("变量,age,bmi\nx,37.58213,24.19627", {"role": "effect_estimate_table"}),
    (f"变量,效应,P值\n{PRIVATE_ID},0.721928,0.0042", {"role": "effect_estimate_table"}),
    ("变量,效应,P值\nSYNTHETIC_SUBJECT_001,0.721928,0.0042", {"role": "effect_estimate_table"}),
    ("变量,效应,P值\n1,0.721928,0.0042", {"role": "effect_estimate_table"}),
    ("变量,效应,P值\nx,0.721928,0.0042", {"semantic_key": "per_patient_predictions"}),
    ("变量,效应,P值\nx,0.721928,0.0042", {"role": "effect_estimate_table", "title": "个体风险"}),
    (f"变量,效应,P值\nx,{PRIVATE_NAME},0.0042", {"role": "effect_estimate_table"}),
    ("变量,效应,P值\nx,0.721928,27", {"role": "effect_estimate_table"}),
    ("变量,效应,CI下限,CI上限\nx,0.721928,1.2,0.2", {"role": "effect_estimate_table"}),
    ("变量,效应,P值\nx,1e9999,0.0042", {"role": "effect_estimate_table"}),
    ("变量,效应,效应\nx,0.721928,0.2", {"role": "effect_estimate_table"}),
])
def test_summary_role_cannot_authorize_individual_or_invalid_chinese_rows(csv_text, metadata):
    clean = ModelEgressGuard().project({"name": "table_1", "metadata": metadata, "preview_csv": csv_text})
    assert clean["preview_csv"] == "[个体或未分类结果表未发送；完整结果保留在本机]"
    assert PRIVATE_ID not in clean["preview_csv"] and PRIVATE_NAME not in clean["preview_csv"]


def test_chinese_structured_statistics_keep_estimates_but_not_relabeled_subject_rows():
    good = {"变量": "x", "效应": 1.8707, "CI下限": 1.6905, "CI上限": 2.0509, "P值": "<0.001"}
    payload = {"coefficients": [good], "other_rows": [{**good, "变量": "SYNTHETIC_SUBJECT_001"}]}
    clean = ModelEgressGuard().project(payload)
    assert clean["coefficients"] == [good]
    assert "SYNTHETIC_SUBJECT_001" not in json.dumps(clean)


def test_subject_marker_in_fake_statistical_label_is_hidden_in_repeated_caption():
    payload = {"caption": "SYNTHETIC_SUBJECT_001 risk", "preview_csv":
               "变量,效应,P值\nSYNTHETIC_SUBJECT_001,0.721928,0.0042"}
    assert "SYNTHETIC_SUBJECT_001" not in json.dumps(ModelEgressGuard().project(payload))


def test_actual_provider_receives_pioneer_coefficients_but_not_subject_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    payload = _payload()
    payload["artifact_previews"]["tables"].append({
        "name": "table_1", "metadata": {"role": "effect_estimate_table"}, "preview_csv": PIONEER_EFFECT_CSV,
    })
    client = _Client()
    agent = _FigureAgent(client, "deepseek-flash", session_id="sharing-chinese-effects", workspace_id="",
                         figure_payload=json.dumps(payload, ensure_ascii=False), model_sharing_policy={})
    list(agent.run("解释回归系数", []))
    wire = json.dumps([call["messages"] for call in client.chat.completions.calls], ensure_ascii=False)
    assert "变量,效应,CI下限,CI上限,P值" in wire
    assert "x,1.8707,1.6905,2.0509,<0.001" in wire
    assert PRIVATE_ID not in wire and PRIVATE_NAME not in wire and "0.72" not in wire


def _payload(figure=None):
    return {
        "artifact_previews": {
            "tables": [
                {"name": "individual_prediction", "preview_csv": f"subject_id,patient_name,prediction\n{PRIVATE_ID},{PRIVATE_NAME},0.72\n"},
                {"name": "effects", "preview_csv": "term,estimate,p_value\nexposure,1.2345,0.0042\n"},
            ],
            "texts": [{"name": "note", "preview_text": f"{PRIVATE_NAME}: {PRIVATE_ID}, risk=0.72"}],
        },
        "result": {
            "ok": True,
            "figures": [] if figure is None else [{"name": "subject_plot", "path": str(figure), "mime_type": "image/png", "kind": "figure"}],
        },
    }


def test_summary_view_drops_individual_rows_retains_effects_and_local_result():
    payload = _payload(); before = copy.deepcopy(payload)
    clean = ModelEgressGuard({"sensitive_columns": ["patient_name"]}).project(payload)
    blob = json.dumps(clean)
    assert PRIVATE_ID not in blob and PRIVATE_NAME not in blob
    assert "0.72" not in blob
    assert "1.2345" in blob and "0.0042" in blob
    assert payload == before


def test_individual_mode_keeps_numbers_masks_selected_column_and_aliases_id():
    clean = ModelEgressGuard({"result_mode": "deidentified", "sensitive_columns": ["patient_name"]}).project(_payload())
    text = clean["artifact_previews"]["tables"][0]["preview_csv"]
    assert "subject_id,prediction" in text
    assert "[个体_1],0.72" in text
    assert PRIVATE_ID not in json.dumps(clean) and PRIVATE_NAME not in json.dumps(clean)


def test_csv_quoting_and_short_numeric_identity_do_not_mask_statistical_numbers():
    guard = ModelEgressGuard({"result_mode": "deidentified", "sensitive_columns": ["private note"]})
    clean = guard.project({"preview_csv": 'subject_id,private note,estimate\n1,"secret,with comma",1.5\n'})
    assert clean["preview_csv"] == 'subject_id,estimate\n[个体_1],1.5'
    assert guard.project(clean) == clean


def test_selected_column_uses_exact_name_including_spaces_and_case():
    guard = ModelEgressGuard({"result_mode": "deidentified", "sensitive_columns": [" Clinical Note "]})
    clean = guard.project({"preview_csv": " Clinical Note ,clinical_note,estimate\nPRIVATE_EXACT,public category,1.5"})
    assert "PRIVATE_EXACT" not in clean["preview_csv"]
    assert clean["preview_csv"] == "clinical_note,estimate\npublic category,1.5"


def test_importance_summary_is_shared_without_individual_shap_rows():
    guard = ModelEgressGuard()
    clean = guard.project({"name": "shap_summary", "preview_csv": "feature,mean_abs_shap\nx,0.31\n"})
    assert "x,0.31" in clean["preview_csv"]


def test_unknown_tables_are_withheld_in_summary_mode():
    clean = ModelEgressGuard().project({"preview_csv": "x,y\n42,27\n"})
    assert "42" not in clean["preview_csv"]


@pytest.mark.parametrize("csv_text", ['term,estimate,p_value\n"PRIVATE_BROKEN_TABLE,1.2', "term,estimate,p_value\nPRIVATE_BROKEN_TABLE,1.2"])
def test_malformed_table_is_withheld_in_both_modes(csv_text):
    for mode in ("summary_only", "deidentified"):
        clean = ModelEgressGuard({"result_mode": mode}).project({"preview_csv": csv_text})
        assert "PRIVATE_BROKEN_TABLE" not in clean["preview_csv"]


def test_sensitive_inspection_levels_are_not_forwarded_but_column_is_known():
    clean = ModelEgressGuard({"sensitive_columns": ["patient_name"]}).project({"variables": [
        {"name": "patient_name", "type": "character", "unique_count": 2, "levels": [PRIVATE_NAME]},
    ]})
    assert clean["variables"][0]["name"] == "patient_name"
    assert clean["variables"][0]["unique_count"] == 2
    assert PRIVATE_NAME not in json.dumps(clean)


@pytest.mark.parametrize("send_images", [False, True])
def test_real_agent_provider_requests_apply_policy_to_tools_and_image_text(tmp_path, monkeypatch, send_images):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path
    sid = "sharing-egress"
    path = data_path("outputs", "research", sid, "plot.png")
    path.parent.mkdir(parents=True);path.write_bytes(PNG_1X1)
    payload = _payload(path); client = _Client()
    agent = _FigureAgent(client=client, model="deepseek-flash", session_id=sid, workspace_id="",
                         figure_payload=json.dumps(payload), model_sharing_policy={"send_result_images": send_images, "sensitive_columns": ["patient_name"]})
    list(agent.run("解释本次结果", []))
    calls = json.dumps([call["messages"] for call in client.chat.completions.calls], ensure_ascii=False)
    assert PRIVATE_ID not in calls and PRIVATE_NAME not in calls
    assert "1.2345" in calls and "0.0042" in calls
    assert ("data:image/png;base64," in calls) is send_images
    assert path.read_bytes() == PNG_1X1


def test_tighter_policy_refilters_raw_historical_envelope_and_automatic_review(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    from infrastructure.paths import data_path
    sid = "sharing-replay"; path = data_path("outputs", "research", sid, "plot.png")
    path.parent.mkdir(parents=True);path.write_bytes(PNG_1X1)
    old_payload = _payload(path)
    old_review = build_r_artifact_review(old_payload, sid, sharing_guard=ModelEgressGuard({"result_mode": "deidentified", "send_result_images": True}))
    # Simulate unfiltered history saved by the version before this fix.
    envelope = {"tool": "run_r_code", "ok": True, "summary": PRIVATE_ID, "data": old_payload}
    history = [{"role": "assistant", "tool_calls": [{"id": "past", "type": "function", "function": {"name": "run_r_code", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "past", "content": '[TOOL_RESULT] run_r_code OK: '+PRIVATE_ID+'\n'+json.dumps(envelope)},
               old_review.history_message]
    before = copy.deepcopy(history)
    client = _Client();client.chat.completions = _Completions(first_stream=_FinalStream())
    agent = BusinessAgent(client, "deepseek-flash", session_id=sid, workspace_id="", model_sharing_policy={"sensitive_columns": ["patient_name"]})
    list(agent.run("继续解释", history))
    wire = json.dumps([call["messages"] for call in client.chat.completions.calls])
    assert PRIVATE_ID not in wire and PRIVATE_NAME not in wire
    assert "data:image" not in wire and "1.2345" in wire
    assert history == before


def test_disabled_attached_images_are_not_replayed():
    messages = [{"role": "user", "content": [{"type": "text", "text": "图片问题"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATE"}}]}]
    guard = ModelEgressGuard({"send_attached_images": False})
    assert "base64" not in json.dumps(guard.messages(messages))
    assert "图片问题" in json.dumps(guard.messages(messages), ensure_ascii=False)


def test_compactor_receives_filtered_history_and_invalidates_old_policy(monkeypatch):
    from agent import agent as module
    calls = []
    def compact(**kwargs):
        calls.append(kwargs["history"])
        return kwargs["history"][-2:], True, {"summary": "safe aggregate", "covered": 1, "boundary": "x"}
    monkeypatch.setattr(module, "should_compact_history", lambda **_kwargs: True)
    monkeypatch.setattr(module, "compact_history_rolling", compact)
    client = _Client();client.chat.completions = _Completions(first_stream=_FinalStream())
    agent = BusinessAgent(client, "deepseek-flash", workspace_id="")
    history = [{"role": "tool", "tool_call_id": "past", "content": json.dumps(_payload())},
               {"role": "user", "content": "继续"}]
    events = list(agent.run("解释", history, compaction_state={"summary": PRIVATE_ID, "covered": 1, "boundary": "old"}))
    assert calls and PRIVATE_ID not in json.dumps(calls) and PRIVATE_NAME not in json.dumps(calls)
    assert PRIVATE_ID not in json.dumps([call["messages"] for call in client.chat.completions.calls])
    state = next(e for e in events if e["type"] == "compaction_state")
    assert state["sharing_fingerprint"] == agent._model_egress.fingerprint


def test_short_names_are_hidden_in_text_and_metadata_as_well_as_table():
    payload = {"artifact_previews": {
        "tables": [{"name": "patient_predictions", "preview_csv": "patient_name,prediction\n张三,0.72\nAl,0.31"}],
        "texts": [{"name": "note", "preview_text": "张三风险；Al risk; Calibration"}],
    }, "metadata": {"caption": "张三风险"}}
    clean = ModelEgressGuard({"result_mode": "deidentified", "sensitive_columns": ["patient_name"]}).project(payload)
    blob = json.dumps(clean, ensure_ascii=False)
    assert "张三" not in blob and "Al risk" not in blob
    assert "Calibration" in blob and "0.72" in blob


def test_group_label_does_not_make_raw_demographic_rows_a_summary():
    payload = {"name": "baseline", "preview_csv": "group,age,bmi\nA,37,24.1\nB,58,27.8"}
    assert "37" not in ModelEgressGuard().project(payload)["preview_csv"]
    assert "A,37,24.1" in ModelEgressGuard({"result_mode": "deidentified"}).project(payload)["preview_csv"]
    assert "A,42" in ModelEgressGuard().project({"preview_csv": "arm,n\nA,42"})["preview_csv"]


def test_summary_mode_blocks_structured_individual_values_outside_csv():
    payload = {"metadata": {"predicted_probability": 0.721928, "coordinates": [[31.2322, 121.4671]],
                             "rows": [{"group": "A", "age": 37, "bmi": 24.1}]},
               "summary": {"rows": [{"term": "x", "estimate": 1.2345, "p_value": 0.0042}]}}
    clean = ModelEgressGuard().project(payload)
    assert "0.721928" not in json.dumps(clean) and "31.2322" not in json.dumps(clean)
    assert "24.1" not in json.dumps(clean)
    assert clean["summary"]["rows"] == payload["summary"]["rows"]


@pytest.mark.parametrize("metadata", [
    {"prediction": {"SYNTHETIC_SUBJECT_001": 0.721928}, "residual": {"SYNTHETIC_SUBJECT_001": 8.123456}},
    {"predicted_probabilities": [0.721928, 0.312789], "factor_scores": [8.123456, 3.246813]},
    {"individual_result": [{"subject_id": "SYNTHETIC_SUBJECT_001", "age": 37.58213, "bmi": 24.19627}]},
    {"custom_vector": [0.721928, 8.123456], "custom_rows": [{"age": 37.58213, "bmi": 24.19627}]},
    {"custom_mapping": {"SYNTHETIC_SUBJECT_001": 0.721928, "SYNTHETIC_SUBJECT_002": 8.123456}},
    {"results": [{"subject_id": "SYNTHETIC_SUBJECT_001", "age": 37.58213, "bmi": 24.19627}]},
])
def test_summary_mode_blocks_individual_shapes_in_actual_provider_kwargs(tmp_path, monkeypatch, metadata):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    payload = _payload(); payload["result"]["metadata"] = metadata
    client = _Client()
    agent = _FigureAgent(client, "deepseek-flash", session_id="sharing-shapes", workspace_id="",
                         figure_payload=json.dumps(payload), model_sharing_policy={})
    list(agent.run("解释", []))
    wire = json.dumps([call["messages"] for call in client.chat.completions.calls])
    assert "SYNTHETIC_SUBJECT_001" not in wire
    for number in ("0.721928", "8.123456", "37.58213", "24.19627"):
        assert number not in wire
    assert "1.2345" in wire and "0.0042" in wire


def test_summary_mode_preserves_numeric_summary_dict_and_confidence_interval():
    payload = {"summary": {"estimate": 1.2345, "p_value": 0.0042},
               "ci": [1.01, 1.48], "residual": {"mean": 0.0, "sd": 1.2, "n": 120},
               "rows": [{"term": "x", "estimate": 1.2345, "p_value": 0.0042}]}
    assert ModelEgressGuard().project(payload) == payload


@pytest.mark.parametrize("mode", ["summary_only", "deidentified"])
def test_numeric_identity_is_protected_by_field_without_changing_scientific_estimates(mode):
    payload = {"metadata": {"subject_id": 1}, "artifact_previews": {"tables": [
        {"name": "individual", "preview_csv": "subject_id,prediction\n1e+05,0.72"},
        {"name": "effects", "preview_csv": "term,estimate,p_value\nexposure,1e+05,1e-05"},
    ]}}
    clean = ModelEgressGuard({"result_mode": mode}).project(payload)
    assert '"subject_id": 1' not in json.dumps(clean["metadata"])
    assert "exposure,1e+05,1e-05" in clean["artifact_previews"]["tables"][1]["preview_csv"]


def test_name_sensitive_column_preserves_real_provider_tool_call_protocol(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    client = _Client()
    agent = _FigureAgent(client, "deepseek-flash", session_id="sharing-name", workspace_id="",
                         figure_payload=json.dumps({"artifact_previews": {"tables": [{
                             "name": "individual_predictions", "preview_csv": "name,prediction\n张三,0.72"}]}}),
                         model_sharing_policy={"result_mode": "deidentified", "sensitive_columns": ["name"]})
    list(agent.run("解释", []))
    second = client.chat.completions.calls[1]
    call = next(message for message in second["messages"] if message.get("tool_calls"))["tool_calls"][0]
    assert call["function"]["name"] == "run_r_code"
    assert json.loads(call["function"]["arguments"]) == {"code": "baa_plot('enrollment', p)"}
    assert any(tool["function"]["name"] == "run_r_code" for tool in second["tools"])
    assert "张三" not in json.dumps(second["messages"], ensure_ascii=False)


@pytest.mark.parametrize("truncate_outer", [False, True])
def test_persisted_truncated_scientific_history_is_withheld_before_compaction_and_provider(tmp_path, monkeypatch, truncate_outer):
    from agent.tools.results import make_tool_result, truncate_tool_result_preserving_refs
    from agent import agent as module
    raw = json.dumps({"artifact_previews": {"tables": [{"name": "baseline", "preview_csv":
        "clinical_note,prediction\nPRIVATE_HISTORICAL_NOTE,0.721928"}]}, "padding": "x" * 6000})
    envelope = make_tool_result("run_r_code", raw, session_id="sharing-trimmed",
                                runtime=SimpleNamespace(cache_dir=tmp_path), result_char_budget=200)
    with pytest.raises(ValueError):
        json.loads(envelope.data)
    content = envelope.to_model_text()
    if truncate_outer:
        content = truncate_tool_result_preserving_refs(content, 400)
    history = [{"role": "assistant", "tool_calls": [{"id": "past", "type": "function", "function": {"name": "run_r_code", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "past", "content": content}, {"role": "user", "content": "继续"}]
    before = copy.deepcopy(history)
    compacted_inputs = []
    def compact(**kwargs):
        compacted_inputs.append(kwargs["history"])
        return kwargs["history"], False, None
    monkeypatch.setattr(module, "should_compact_history", lambda **_kwargs: True)
    monkeypatch.setattr(module, "compact_history_rolling", compact)
    client = _Client();client.chat.completions = _Completions(first_stream=_FinalStream())
    agent = BusinessAgent(client, "deepseek-flash", workspace_id="", model_sharing_policy={"sensitive_columns": ["clinical_note"]})
    list(agent.run("解释", history))
    blob = json.dumps([compacted_inputs, [call["messages"] for call in client.chat.completions.calls]])
    assert "PRIVATE_HISTORICAL_NOTE" not in blob and "0.721928" not in blob
    assert history == before


def test_redacted_r_failure_tail_is_retained_for_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    payload = _payload()
    payload.update(ok=False, stderr="warning\n" * 1000 + f"{PRIVATE_NAME}: Error in glm.fit: algorithm did not converge")
    payload["result"]["ok"] = False
    clean = ModelEgressGuard({"sensitive_columns": ["patient_name"]}).project(payload)
    assert len(clean["stderr_tail"]) <= 4000
    assert "algorithm did not converge" in clean["stderr_tail"] and PRIVATE_NAME not in clean["stderr_tail"]
    client = _Client()
    agent = _FigureAgent(client, "deepseek-flash", session_id="sharing-diagnostics", workspace_id="",
                         figure_payload=json.dumps(payload), model_sharing_policy={"sensitive_columns": ["patient_name"]})
    list(agent.run("修正", []))
    wire = json.dumps([call["messages"] for call in client.chat.completions.calls])
    assert "algorithm did not converge" in wire and PRIVATE_NAME not in wire
