"""Cross-check the method modules against the independently owned catalog."""

import pytest

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.method_policy import REFERENCE_CASES, template_method_status
from agent.analysis.script_library import scripted_template_ids
from agent.analysis.skeleton_cores import FIXED_METHOD_MODULES
from agent.analysis.models import ResolvedInput
from agent.analysis.output_portfolio import ScientificOutputPortfolioCompiler
from agent.analysis.script_library import _WIRING


METHODS = [(module, tid) for module in FIXED_METHOD_MODULES for tid in module.CORES]


@pytest.mark.parametrize("module,template_id", METHODS, ids=[tid for _, tid in METHODS])
def test_fixed_implementation_preserves_catalog_inputs_and_output_roles(module, template_id):
    template = AnalysisCatalog().get(template_id)
    inputs = {spec.id for spec in template.input_specs}
    critical = {spec.id for spec in template.input_specs if spec.requirement == "critical"}
    outputs = {item.role for item in template.required_outputs if item.required}
    assert set(module.INPUTS[template_id]) <= inputs
    assert critical <= set(module.INPUTS[template_id])
    assert outputs <= set(module.ROLE_MAPS[template_id])
    assert template_id in scripted_template_ids()
    assert template_id in REFERENCE_CASES


def test_registered_coverage_keeps_execution_and_scientific_review_separate():
    catalog_ids = {item.id for item in AnalysisCatalog().snapshot().templates}
    fixed = set(scripted_template_ids())
    assert fixed <= catalog_ids
    assert fixed <= set(REFERENCE_CASES)
    for tid in fixed:
        status = template_method_status(tid)
        assert status["execution_mode"] == "fixed_script"
        assert status["verification_status"] == "reference_cases"
        assert status["scientific_review_status"] == "not_reviewed"


@pytest.mark.parametrize("template_id", [t.id for t in AnalysisCatalog().snapshot().templates])
def test_compiled_production_portfolio_has_unambiguous_fixed_output_slots(template_id):
    """Catch production renaming/additions that raw catalog role tests miss.

    This is a structural contract check with declared inputs present. Actual
    values, numerical methods and imported profiles are tested by native R
    cases separately; this check never claims a scientific execution.
    """
    template = AnalysisCatalog().get(template_id)
    resolved = {}
    for spec in template.input_specs:
        value = spec.default if spec.default is not None else (
            spec.choices[0] if spec.choices else [spec.id] if spec.kind == "columns"
            else 1 if spec.kind == "parameter" else "{}" if "json" in spec.accepts else spec.id
        )
        resolved[spec.id] = ResolvedInput(spec.id, spec.label, spec.kind, value, "user")
    profile = {"format": "csv", "rows": 200, "columns": [
        {"name": key, "semantic_type": "numeric", "unique_count": 200} for key in resolved
    ]}
    portfolio = ScientificOutputPortfolioCompiler().compile(
        source="template", template_id=template_id, intent=template.title,
        method_family=template.family, scenario=template.scenario,
        baseline_outputs=template.required_outputs, dataset_profile=profile,
        resolved_inputs=resolved, packages=template.packages,
    )
    roles = _WIRING[template_id][1]
    for output in portfolio.outputs:
        if not output.required or output.role in roles:
            continue
        slots = {roles[alias] for alias in output.legacy_roles if alias in roles}
        assert len(slots) == 1, (template_id, output.role, output.kind, output.legacy_roles, slots)
