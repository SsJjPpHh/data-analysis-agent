"""Scientific history remains classifiable across real session/compactor caps."""

from __future__ import annotations

import copy
import csv
import io
import json

import pytest

from agent.analysis.model_egress import ModelEgressGuard
from agent.compaction import _messages_to_text, _strip_heavy_content, trim_oversized_tool_results
from agent.tools.results import (
    ToolResultEnvelope,
    extract_tool_result_references,
    truncate_tool_result_preserving_refs,
)
from data.session import ChatSession


REF = "artifact://tool-result/tr_0123456789abcdef0123456789abcdef"
COEFFICIENTS = 'term,estimate,p_value\n"exposure, main",1.2345,0.0042\n'
TRUNCATED_NOTICE = "result truncated for history"


def _read(text):
    body = text.split("\n", 1)[1] if text.startswith("[TOOL_RESULT]") else text
    envelope = json.loads(body)
    data = envelope.get("data")
    if isinstance(data, str):
        data = json.loads(data)
    return envelope, data


def _message(payload, *, tool="run_r_code", ok=True, error="", refs=True):
    return ToolResultEnvelope(
        tool=tool,
        ok=ok,
        error=error,
        summary="old summary " + "x" * 1000,
        data=json.dumps(payload, ensure_ascii=False),
        artifacts=[{"uri": REF, "name": "full result"}] if refs else [],
    ).to_model_text()


def _allowance(text, cap):
    suffix = f"\n…[result truncated for history, {len(text):,} chars total]"
    refs = extract_tool_result_references(text)
    if refs:
        suffix += "\n[RECOVERABLE_ARTIFACTS] " + ", ".join(refs)
    return cap + len(suffix)


@pytest.mark.parametrize("tool", ["run_r_code", "inspect_dataset"])
@pytest.mark.parametrize("cap", [500, 600, 800, 2000])
def test_exact_statistical_row_and_refs_survive_as_parseable_bounded_json(tool, cap):
    payload = {
        "ok": True,
        "job_id": "job-science",
        "artifact_previews": {"tables": [{"name": "effects", "preview_csv": COEFFICIENTS * 1}]},
        "result": {"ok": True, "warnings": ["warning " + "x" * 1000] * 20},
        "dataset": {
            "profile": {"variables": [{"name": f"x{i}", "description": "x" * 1000} for i in range(50)]}
        },
    }
    before = copy.deepcopy(payload)
    raw = _message(payload, tool=tool)
    trimmed = truncate_tool_result_preserving_refs(raw, cap)
    envelope, data = _read(trimmed)
    assert envelope["tool"] == tool and envelope["ok"] is True and envelope["error"] == ""
    assert envelope["truncated"] is True
    assert data["artifact_previews"]["tables"][0]["preview_csv"] == COEFFICIENTS.rstrip("\n")
    assert len(trimmed) <= _allowance(raw, cap)
    assert extract_tool_result_references(trimmed) == [REF]
    assert truncate_tool_result_preserving_refs(trimmed, cap) == trimmed
    assert payload == before


def test_csv_is_trimmed_by_whole_rows_without_shortening_sensitive_headers_or_values():
    header = "subject_id,clinical_note,prediction"
    note = 'private, quoted "note"'
    source = io.StringIO()
    writer = csv.writer(source, lineterminator="\n")
    writer.writerow(header.split(","))
    for index in range(100):
        writer.writerow([f"SUBJECT_{index:04}", note, "0.721928"])
    raw = _message(
        {
            "artifact_previews": {
                "tables": [{"name": "individual_prediction", "preview_csv": source.getvalue()}]
            }
        }
    )
    trimmed = truncate_tool_result_preserving_refs(raw, 800)
    _, data = _read(trimmed)
    preview = data["artifact_previews"]["tables"][0]["preview_csv"]
    rows = list(csv.reader(io.StringIO(preview), strict=True))
    assert rows[0] == header.split(",")
    assert 1 < len(rows) < 101
    assert all(row[1:] == [note, "0.721928"] for row in rows[1:])
    guard = ModelEgressGuard({"result_mode": "deidentified", "sensitive_columns": ["clinical_note"]})
    clean = guard.messages([{"role": "tool", "tool_call_id": "call-1", "content": trimmed}])
    wire = json.dumps(clean, ensure_ascii=False)
    assert note not in wire and "SUBJECT_0000" not in wire
    assert "0.721928" in wire
    summary = ModelEgressGuard().messages([{"role": "tool", "tool_call_id": "call-1", "content": trimmed}])
    assert "0.721928" not in json.dumps(summary, ensure_ascii=False)


@pytest.mark.parametrize("cap", [500, 600, 800, 2000])
def test_failure_status_taxonomy_and_decisive_r_tail_remain_visible(cap):
    decisive = "Error in glm.fit: algorithm did not converge"
    raw = _message(
        {
            "ok": False,
            "result": {"ok": False, "error": "prefix " * 1000 + decisive},
            "execution": {"status": "failed"},
            "stderr": "harmless warning\n" * 1000 + decisive,
        },
        ok=False,
        error="scientific_execution_failed",
    )
    trimmed = truncate_tool_result_preserving_refs(raw, cap)
    envelope, data = _read(trimmed)
    assert envelope["ok"] is False and envelope["error"] == "scientific_execution_failed"
    assert data["ok"] is False
    assert decisive in trimmed
    assert len(trimmed) <= _allowance(raw, cap)
    wire = ModelEgressGuard().messages([{"role": "tool", "tool_call_id": "failed-call", "content": trimmed}])
    assert decisive in json.dumps(wire)


def test_inspection_variable_names_and_sensitive_levels_stay_structured_for_refiltering():
    raw = _message(
        {
            "ok": True,
            "variables": [
                {"name": "patient_name", "type": "character", "unique_count": 2, "levels": ["张三", "李四"]},
                {"name": "age", "type": "numeric", "missing_count": 0},
            ],
            "padding": "x" * 8000,
        },
        tool="inspect_dataset",
    )
    trimmed = truncate_tool_result_preserving_refs(raw, 800)
    _, data = _read(trimmed)
    assert data["variables"][0]["name"] == "patient_name"
    wire = ModelEgressGuard({"sensitive_columns": ["patient_name"]}).messages(
        [{"role": "tool", "tool_call_id": "inspect-call", "content": trimmed}]
    )
    blob = json.dumps(wire, ensure_ascii=False)
    assert "patient_name" in blob and "张三" not in blob and "李四" not in blob


def test_wide_header_is_withheld_instead_of_turning_partial_fields_into_valid_numbers():
    header = ",".join(["term", "estimate", "p_value", *("private_header_" + "x" * 160 for _ in range(30))])
    raw = _message(
        {
            "artifact_previews": {
                "tables": [
                    {"name": "wide", "preview_csv": header + "\nx,1.2345,0.0042," + ",".join(["7"] * 30)}
                ]
            }
        }
    )
    trimmed = truncate_tool_result_preserving_refs(raw, 500)
    envelope, data = _read(trimmed)
    assert envelope["truncated"] is True
    assert "表格预览未发送" in json.dumps(data, ensure_ascii=False)
    assert "1.2345" not in trimmed
    assert len(trimmed) <= _allowance(raw, 500)


def test_invalid_legacy_inner_json_is_not_reconstructed_or_loaded_from_artifact(monkeypatch):
    from agent.tools import results

    monkeypatch.setattr(
        results,
        "load_tool_result_artifact",
        lambda *_a, **_k: pytest.fail("never reload local data into model history"),
    )
    raw = ToolResultEnvelope(
        tool="run_r_code",
        data='{"preview_csv":"clinical_note\\nPRIVATE_BROKEN_NOTE' + "x" * 1000,
        artifacts=[{"uri": REF}],
    ).to_model_text()
    trimmed = truncate_tool_result_preserving_refs(raw, 500)
    envelope, data = _read(trimmed)
    assert envelope["truncated"] is True and data["truncated"] is True
    assert "PRIVATE_BROKEN_NOTE" not in trimmed and "不是完整 JSON" in data["message"]
    assert extract_tool_result_references(trimmed) == [REF]


def test_invalid_outer_envelope_uses_existing_behavior_without_attempting_recovery():
    raw = '[TOOL_RESULT] run_r_code OK\n{"tool":"run_r_code","data":' + "x" * 1000
    actual = truncate_tool_result_preserving_refs(raw, 100)
    assert actual == raw[:100] + f"\n…[result truncated for history, {len(raw):,} chars total]"


@pytest.mark.parametrize(
    "raw",
    [
        "ordinary text " * 200,
        ToolResultEnvelope(tool="run_sql", data="x" * 3000, artifacts=[{"uri": REF}]).to_model_text(),
    ],
)
def test_non_scientific_tools_keep_previous_prefix_suffix_behavior(raw):
    suffix = f"\n…[result truncated for history, {len(raw):,} chars total]"
    refs = extract_tool_result_references(raw)
    if refs:
        suffix += "\n[RECOVERABLE_ARTIFACTS] " + ", ".join(refs)
    assert truncate_tool_result_preserving_refs(raw, 600) == raw[:600] + suffix


def test_real_session_and_both_compaction_paths_keep_tool_protocol_and_statistical_values():
    raw = _message(
        {
            "ok": True,
            "artifact_previews": {"tables": [{"name": "effects", "preview_csv": COEFFICIENTS}]},
            "padding": "x" * 10000,
        }
    )
    assistant = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "scientific-call",
                "type": "function",
                "function": {"name": "run_r_code", "arguments": "{}"},
            }
        ],
        "reasoning_content": "protocol-required",
    }
    message = {"role": "tool", "tool_call_id": "scientific-call", "content": raw}
    before = copy.deepcopy(message)
    sess = ChatSession()
    sess.add_tool_messages([assistant, message])
    assert sess.history[0]["tool_calls"] == assistant["tool_calls"]
    assert sess.history[0]["reasoning_content"] == "protocol-required"
    stored = sess.history[1]
    assert stored["tool_call_id"] == "scientific-call"
    _read(stored["content"])
    trimmed, n_trimmed = trim_oversized_tool_results([message])
    assert n_trimmed == 1
    _read(trimmed[0]["content"])
    heavy = _strip_heavy_content(stored)
    _read(heavy["content"])
    prompt = _messages_to_text([stored])
    assert "1.2345" in prompt and "0.0042" in prompt
    assert message == before
