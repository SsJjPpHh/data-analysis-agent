import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.input_validation import normalize_parameter_value, normalize_setting_value
from agent.analysis.preparation import AnalysisPreparer


def test_json_vector_is_normalized_before_plan_freezing():
    profile = {
        "dataset_id": "vector-fixture", "format": "csv", "rows": 120,
        "columns": [
            {"name": "y", "semantic_type": "categorical", "unique_count": 2},
            {"name": "p", "semantic_type": "numeric", "unique_count": 120},
        ],
    }
    prepared = AnalysisPreparer(AnalysisCatalog()).prepare(
        "external_validation_model", profile,
        provided_inputs={"outcome": "y", "prediction": "p", "threshold": "[0.1, 0.2]", "positive_class": "1"},
    )
    assert prepared.plan is not None
    assert prepared.plan.resolved_inputs["threshold"].value == [.1, .2]


@pytest.mark.parametrize("value", [[[]], [{"code": "anything"}], [None], [float("inf")], list(range(257))])
def test_vectors_reject_nested_unbounded_or_nonfinite_values(value):
    with pytest.raises(ValueError):
        normalize_setting_value(value)


def test_known_scalar_domains_still_reject_vectors():
    with pytest.raises(ValueError):
        normalize_parameter_value(input_id="alpha", label="alpha", value=[.01, .05])
    assert normalize_setting_value('["healthy->ill", "ill->dead"]') == ["healthy->ill", "ill->dead"]


def test_named_treatment_levels_request_direction_before_execution():
    from agent.analysis.input_validation import joint_input_issues
    values = {"exposure":"arm", "outcome":"y"}
    profile = {"columns":[{"name":"arm", "levels":["placebo", "drug"]}]}
    assert "treatment_level" in joint_input_issues(values, template_id="propensity_clinical", dataset_profile=profile)
    assert "treatment_level" not in joint_input_issues(
        dict(values, reference_group="placebo"), template_id="propensity_clinical", dataset_profile=profile)
    profile["columns"][0]["levels"] = ["0", "1"]
    assert "treatment_level" not in joint_input_issues(values, template_id="propensity_clinical", dataset_profile=profile)


def test_explicit_json_documents_preserve_rows_without_becoming_vectors():
    text = '[{"sample":"s1","group":"A"},{"sample":"s2","group":"B"}]'
    assert normalize_setting_value(text, json_document=True) == text
    assert normalize_setting_value({"prior":{"mu":0,"sd":2}}, json_document=True) == '{"prior":{"mu":0,"sd":2}}'
    with pytest.raises(ValueError):
        normalize_setting_value('{"sd":NaN}', json_document=True)


def test_confirmed_mapping_accepts_json_rows_and_literal_tab_without_code_evaluation():
    document = '[{"term":"pathway1","gene":"g1"}]'
    assert normalize_setting_value(document, json_or_text=True) == document
    assert normalize_setting_value("term\tgene\npathway1\tg1", json_or_text=True) == "term\tgene\npathway1\tg1"
    assert normalize_setting_value(["pathway1\tg1", "pathway1\tg2"], json_or_text=True) == ["pathway1\tg1", "pathway1\tg2"]
    with pytest.raises(ValueError):
        normalize_setting_value('[{"term":NaN}]', json_or_text=True)
