"""Item-level EFA summaries may be shared; observation rows remain local."""

import copy
import json

import pytest

from agent.analysis.model_egress import ModelEgressGuard
from tests.core.test_agent_r_figure_vision import _Client, _FigureAgent


# Exact synthetic primary_table from category-release/behavior's successful
# fixed R execution. Six item variables, rather than six research participants.
EFA_CSV = (
    "条目,PA1,共同度,整体alpha,外部总分相关\n"
    "i1,0.8694494956139901,0.7559424254234218,0.9419461103753997,0.6715883069713663\n"
    "i2,0.887155850042984,0.7870455022654895,0.9419461103753997,0.6715883069713663\n"
    "i3,0.8561613119300068,0.7330121920457103,0.9419461103753997,0.6715883069713663\n"
    "i4,0.8471963955212849,0.7177417325842574,0.9419461103753997,0.6715883069713663\n"
    "i5,0.8091952357721367,0.6547969295963239,0.9419461103753997,0.6715883069713663\n"
    "i6,0.8572205150953056,0.7348270115002611,0.9419461103753997,0.6715883069713663"
)
PRIVATE_ITEM = "PRIVATE_EFA_SUBJECT_987654"


def _table(csv_text):
    # Metadata supplies no proof of aggregation, including for negative cases.
    return {"name": "primary_table", "metadata": {"role": "primary_table", "title": "EFA summary"},
            "preview_csv": csv_text}


def test_real_synthetic_efa_item_summary_survives_without_metadata_and_without_local_mutation():
    payload = {"preview_csv": EFA_CSV}
    before = copy.deepcopy(payload)
    guard = ModelEgressGuard()

    clean = guard.project(payload)

    assert clean == payload
    assert guard.project(clean) == clean
    assert payload == before


@pytest.mark.parametrize("csv_text", [
    "条目,PA1,PA2,PA12,共同度\ni1,0.7,-0.2,0.01,0.5301\ni2,0.8,0.1,-0.05,0.6525",
    "item,PA1,PA2,communality,overall_alpha,external_total_correlation\ni1,0.7,-0.2,0.53,0.91,-0.43",
    "题项,PA1,共同度,整体alpha,外部总分相关\n自我效能条目1,0.7,0.49,0.88,NA",
])
def test_dynamic_pa_columns_are_allowed_only_with_strict_item_aggregate_columns(csv_text):
    assert ModelEgressGuard().project(_table(csv_text))["preview_csv"] == csv_text


@pytest.mark.parametrize("csv_text", [
    "条目,PA1\ni1,0.73123456789",  # missing aggregate communality evidence
    "term,PA1,estimate\nx,0.73123456789,1.2",  # PA is not a global summary column
    "条目,PA1,age,bmi\ni1,0.73123456789,37.2,24.5",  # raw observation measures
    "条目,PA1,共同度,PC1\ni1,0.7,0.49,0.73123456789",  # individual coordinate
    "subject_id,PA1,共同度\nSUBJECT_PRIVATE_01,0.73123456789,0.49",
    "条目,PA0,共同度\ni1,0.73123456789,0.49",
    "条目,PA1,PA1,共同度\ni1,0.7,0.73123456789,0.49",
    "条目,PA1,共同度\ni1,0.7,0.49\ni1,0.73123456789,0.49",  # repeated item is not item aggregation
])
def test_claimed_efa_metadata_cannot_authorize_raw_or_coordinate_schema(csv_text):
    clean = ModelEgressGuard().project(_table(csv_text))["preview_csv"]

    assert clean == "[个体或未分类结果表未发送；完整结果保留在本机]"
    assert "0.73123456789" not in clean


@pytest.mark.parametrize("item_label", [
    "", "314159", "SYNTHETIC_SUBJECT_001", "patient_private_001",
    "受试者编号001", "[个体_1]", "[敏感值已隐藏]", "/Users/research/cohort.csv",
])
def test_scale_item_labels_reject_empty_numeric_identified_and_hidden_subject_values(item_label):
    csv_text = f"条目,PA1,共同度,整体alpha,外部总分相关\n{item_label},0.73123456789,0.49,0.88,0.6"

    clean = ModelEgressGuard().project(_table(csv_text))["preview_csv"]

    assert clean == "[个体或未分类结果表未发送；完整结果保留在本机]"


@pytest.mark.parametrize("column,value", [
    ("PA1", PRIVATE_ITEM), ("PA1", "nan"), ("PA1", "1e9999"),
    ("共同度", PRIVATE_ITEM), ("共同度", "inf"),
    ("整体alpha", PRIVATE_ITEM), ("整体alpha", "nan"),
    ("外部总分相关", PRIVATE_ITEM), ("外部总分相关", "1.4"),
])
def test_every_scale_measure_must_be_numeric_and_finite_or_explicit_absent_criterion(column, value):
    values = {"条目": "i1", "PA1": "0.7", "共同度": "0.49", "整体alpha": "0.88", "外部总分相关": "0.6"}
    values[column] = value
    csv_text = ",".join(values) + "\n" + ",".join(values.values())

    clean = ModelEgressGuard().project(_table(csv_text))["preview_csv"]

    assert clean == "[个体或未分类结果表未发送；完整结果保留在本机]"
    assert PRIVATE_ITEM not in clean


@pytest.mark.parametrize("mode", ["summary_only", "deidentified"])
def test_scale_sensitive_columns_are_never_sent(mode):
    guard = ModelEgressGuard({"result_mode": mode, "sensitive_columns": ["外部总分相关"]})

    clean = guard.project(_table(EFA_CSV))["preview_csv"]

    assert "外部总分相关" not in clean
    assert "0.6715883069713663" not in clean


def test_learned_sensitive_item_value_withholds_fake_statistics_and_masks_repeated_caption():
    guard = ModelEgressGuard({"sensitive_columns": ["clinical_note"]})
    guard.project({"clinical_note": PRIVATE_ITEM})
    payload = {**_table(f"条目,PA1,共同度\n{PRIVATE_ITEM},0.73123456789,0.49"), "caption": PRIVATE_ITEM}

    clean = guard.project(payload)

    assert PRIVATE_ITEM not in json.dumps(clean)
    assert "0.73123456789" not in json.dumps(clean)


@pytest.mark.parametrize("parent", ["factor_summary", "results"])
@pytest.mark.parametrize("label", [PRIVATE_ITEM, "SYNTHETIC_SUBJECT_001", "314159", "[个体_1]"])
def test_structured_scale_rows_apply_identical_known_private_item_checks(parent, label):
    payload = {"metadata": {"subject_id": PRIVATE_ITEM}, parent: [
        {"条目": label, "PA1": 0.73123456789, "共同度": 0.49},
    ]}

    clean = ModelEgressGuard().project(payload)

    assert PRIVATE_ITEM not in json.dumps(clean)
    assert "0.73123456789" not in json.dumps(clean)


def test_structured_item_summaries_preserve_valid_rows_but_withhold_repeated_item_rows():
    rows = [{"条目": "i1", "PA1": 0.7, "共同度": 0.49}, {"条目": "i2", "PA1": 0.8, "共同度": 0.64}]
    assert ModelEgressGuard().project({"results": rows}) == {"results": rows}
    assert "0.7" not in json.dumps(ModelEgressGuard().project({"results": [rows[0], rows[0]]}))


def test_actual_provider_receives_real_efa_summary_and_withholds_fake_subject_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("BAA_DATA_DIR", str(tmp_path))
    payload = {"metadata": {"subject_id": PRIVATE_ITEM}, "artifact_previews": {"tables": [
        _table(EFA_CSV),
        {**_table(f"条目,PA1,共同度\n{PRIVATE_ITEM},0.73123456789,0.49"), "name": "claimed_efa"},
    ]}, "result": {"ok": True, "figures": []}}
    before = copy.deepcopy(payload)
    client = _Client()
    agent = _FigureAgent(client, "deepseek-flash", session_id="scale-summary-egress", workspace_id="",
                         figure_payload=json.dumps(payload, ensure_ascii=False), model_sharing_policy={})

    list(agent.run("解释已经生成的量表汇总", []))

    wire = json.dumps([call["messages"] for call in client.chat.completions.calls], ensure_ascii=False)
    assert "条目,PA1,共同度,整体alpha,外部总分相关" in wire
    assert "0.8694494956139901" in wire and "0.6715883069713663" in wire
    assert PRIVATE_ITEM not in wire and "0.73123456789" not in wire
    assert payload == before
