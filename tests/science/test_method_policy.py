from dataclasses import asdict

from agent.analysis.method_policy import REFERENCE_CASES, WITHDRAWN_FIXED_METHODS
from agent.analysis.result_validation import ResultAssessment
from agent.r_analysis_templates import enriched_analysis_templates


def test_catalog_exposes_method_evidence_without_claiming_scientific_acceptance():
    items = enriched_analysis_templates()["templates"]
    assert len(items) == 213
    by_id = {item["id"]: item["method_validation"] for item in items}
    assert {key for key, value in by_id.items() if value["verification_status"] == "reference_cases"} == set(REFERENCE_CASES)
    for template_id in WITHDRAWN_FIXED_METHODS:
        assert by_id[template_id]["execution_mode"] == "llm_generated"
        assert by_id[template_id]["verification_status"] == "method_review_pending"
        assert "已撤下" in by_id[template_id]["note"]
    assert all(item["scientific_review_status"] == "not_reviewed" for item in by_id.values())


def test_complete_assessment_only_certifies_artifact_structure():
    result = ResultAssessment(True, True, (), (), (), diagnostic_quality="passed")
    payload = asdict(result)
    assert payload["complete"] is True
    assert payload["validation_scope"] == "artifact_structure"
    assert payload["scientific_review_status"] == "not_reviewed"


def test_unassessed_diagnosis_survives_merge_without_becoming_passed():
    from agent.analysis.result_validation import merge_diagnostic_statuses
    assert merge_diagnostic_statuses({"test": "passed"}, {"test": "not_assessed"}) == {"test": "not_assessed"}
    assert merge_diagnostic_statuses({"test": "not_assessed"}, {"test": "warning"}) == {"test": "warning"}


def test_design_scope_is_checked_again_at_execution():
    from agent.analysis.input_validation import joint_input_issues
    from agent.analysis.method_policy import SUPPORTED_DESIGNS
    for tid, scope in SUPPORTED_DESIGNS.items():
        assert "design_scope" in joint_input_issues({}, template_id=tid)
        assert "design_scope" in joint_input_issues({"design_scope": "unsupported"}, template_id=tid)
        assert "design_scope" not in joint_input_issues({"design_scope": scope}, template_id=tid)


def test_longitudinal_script_only_requires_packages_for_selected_method():
    from agent.analysis.models import ResolvedInput
    from agent.analysis.script_library import render_script
    from r_runtime.package_references import referenced_r_packages
    from tests.science.test_script_library import _plan

    for method, required, excluded in [("gee", "geepack", "lme4"), ("mixed", "lme4", "geepack")]:
        plan = _plan("mixed_repeated", {
            "analysis_method": ResolvedInput("analysis_method", "方法", "specification", method, "user"),
        })
        packages = referenced_r_packages(render_script(plan))
        assert required in packages
        assert excluded not in packages
