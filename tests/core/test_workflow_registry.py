from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.format_contracts import accepted_template_formats
from agent.analysis.models import ConversationPlanDraft, RequiredOutput, ResolvedInput
from agent.analysis.preparation import AnalysisPreparer
from agent.analysis.workflows import (
    CatalogWorkflowRegistry,
    ScientificWorkflowExecutor,
    ScientificWorkflowLifecycle,
    WorkflowRunSpec,
    validate_workflow_run_spec,
    workflow_run_spec_projection,
    workflow_system_section,
)


def test_registry_compiles_all_213_templates_into_shared_executable_workflows():
    catalog = AnalysisCatalog()
    registry = CatalogWorkflowRegistry(catalog)
    templates = tuple(item for item in catalog.snapshot().templates if item.visibility == "user")
    definitions = registry.definitions()

    assert len(templates) == 213
    assert len(definitions) == 213
    assert {item.template_id for item in definitions} == {item.id for item in templates}
    assert len({item.workflow_id for item in definitions}) < len(definitions)
    assert len({(item.workflow_id, item.variant) for item in definitions}) == 213

    for definition in definitions:
        assert definition.workflow_id
        assert definition.variant == definition.template_id
        assert tuple(step.phase for step in definition.steps) == (
            "preflight",
            "prepare",
            "analyze",
            "diagnose",
            "publish",
        )
        assert definition.data_contract.formats
        assert definition.artifacts
        assert definition.resource_hints
        assert "resource_policy=adaptive_local" in definition.resource_hints
        assert "timeout_seconds=0" in definition.resource_hints
        assert definition.package_requirement.candidates
        assert definition.package_requirement.primary


def test_static_capability_validation_covers_baselines_packages_and_object_semantics():
    catalog = AnalysisCatalog()
    registry = CatalogWorkflowRegistry(catalog)

    report = registry.validate()

    assert report.valid, report.issues
    assert report.checked_templates == 213
    assert report.checked_definitions == 213

    for template in catalog.snapshot().templates:
        if template.visibility != "user":
            continue
        definition = registry.get(template.id)
        assert definition.data_contract.formats == accepted_template_formats(template)
        addressable = {
            role for artifact in definition.artifacts for role in (artifact.role, *artifact.legacy_roles)
        }
        assert {output.role for output in template.required_outputs if output.tier == "core"} <= addressable
        if template.packages:
            assert definition.package_requirement.primary == template.packages[0]

        object_ids = {item.id for item in template.input_specs if item.kind == "object"}
        assert object_ids <= set(definition.data_contract.object_inputs)
        assert not object_ids & set(definition.data_contract.column_inputs)
        if template.input_shape == "r_object":
            preflight = definition.steps[0]
            assert "r_object_class" in preflight.checks
            assert object_ids <= set(definition.data_contract.asset_inputs)
        if template.category == "组学与生物信息":
            assert object_ids <= set(definition.data_contract.asset_inputs)


def test_template_and_free_form_requests_compile_to_the_same_run_spec_contract():
    registry = CatalogWorkflowRegistry(AnalysisCatalog())
    profile = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "columns": [
            {"name": "score", "semantic_type": "numeric"},
            {"name": "arm", "semantic_type": "categorical"},
        ],
    }
    template_spec = registry.compile_template(
        "group_tests",
        dataset_profile=profile,
    )
    free_form_spec = registry.compile_free_form(
        ConversationPlanDraft(
            intent="比较两组评分",
            category="基础统计",
            method_family="classical_statistics_and_mixed_models",
            scenario="两独立组比较",
            analysis_inputs=(
                ResolvedInput("outcome", "结局", "column", "score", "user"),
                ResolvedInput("group", "分组", "column", "arm", "user"),
            ),
            package_queries=("group comparison",),
            required_outputs=(RequiredOutput("group_comparison_table", "table", label="组间比较表"),),
        ),
        dataset_profile=profile,
        resolved_packages=("stats", "effectsize"),
    )

    assert isinstance(template_spec, WorkflowRunSpec)
    assert isinstance(free_form_spec, WorkflowRunSpec)
    assert template_spec.source == "template"
    assert free_form_spec.source == "free_form"
    assert template_spec.dataset_id == free_form_spec.dataset_id == "dataset-1"
    assert tuple(step.phase for step in template_spec.steps) == tuple(
        step.phase for step in free_form_spec.steps
    )
    assert free_form_spec.package_requirement.primary == "stats"
    assert free_form_spec.package_requirement.alternatives == ("effectsize",)
    free_form_roles = {item.role for item in free_form_spec.artifacts}
    assert "group_comparison_table" in free_form_roles
    assert {
        "effect_estimate_table",
        "model_assumption_diagnostics",
        "manuscript_results",
    } <= free_form_roles


def test_executor_dispatches_shared_step_implementations_in_dependency_order():
    registry = CatalogWorkflowRegistry(AnalysisCatalog())
    run_spec = registry.compile_template(
        "data_quality",
        dataset_profile={"dataset_id": "dataset-1", "format": "csv"},
    )
    observed = []

    def execute_step(step, spec, state):
        observed.append((step.phase, spec.workflow_id, tuple(state)))
        return {"phase": step.phase}

    result = ScientificWorkflowExecutor(execute_step).execute(run_spec)

    assert result.completed_steps == tuple(step.step_id for step in run_spec.steps)
    assert [item[0] for item in observed] == [
        "preflight",
        "prepare",
        "analyze",
        "diagnose",
        "publish",
    ]
    assert result.step_outputs["publish"]["phase"] == "publish"


def test_production_lifecycle_never_marks_post_r_phases_without_validator_evidence():
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "data_quality",
        dataset_profile={"dataset_id": "dataset-1", "format": "csv"},
    )
    lifecycle = ScientificWorkflowLifecycle(spec)

    lifecycle.begin("preflight")
    lifecycle.complete("preflight")
    lifecycle.mark_runtime_result(ok=True, execution_stage="complete")

    assert lifecycle.status == {
        "preflight": "completed",
        "prepare": "completed",
        "analyze": "completed",
        "diagnose": "not_started",
        "publish": "not_started",
    }

    lifecycle.apply_assessment(diagnostics_complete=False, portfolio_complete=False)
    assert lifecycle.status["diagnose"] == "incomplete"
    assert lifecycle.status["publish"] == "not_started"


def test_production_lifecycle_stops_after_the_actual_failed_worker_stage():
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "data_quality",
        dataset_profile={"dataset_id": "dataset-1", "format": "csv"},
    )
    lifecycle = ScientificWorkflowLifecycle(spec)

    lifecycle.begin("preflight")
    lifecycle.complete("preflight")
    lifecycle.mark_runtime_result(ok=False, execution_stage="analyze")

    assert lifecycle.status["prepare"] == "completed"
    assert lifecycle.status["analyze"] == "failed"
    assert lifecycle.status["diagnose"] == "not_started"
    assert lifecycle.status["publish"] == "not_started"


def test_workflow_projection_and_model_contract_expose_all_five_phases():
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "data_quality",
        dataset_profile={"dataset_id": "dataset-1", "format": "csv"},
    )

    projection = workflow_run_spec_projection(spec)
    section = workflow_system_section(spec)

    assert [item["phase"] for item in projection["steps"]] == [
        "preflight",
        "prepare",
        "analyze",
        "diagnose",
        "publish",
    ]
    assert projection["package_requirement"]["primary"]
    assert "[SCIENTIFIC WORKFLOW RUN SPEC]" in section
    assert "Do not skip preflight" in section


def test_runtime_preflight_accepts_a_resolved_plan_for_the_active_dataset():
    catalog = AnalysisCatalog()
    profile = {
        "dataset_id": "dataset-1",
        "format": "csv",
        "rows": 100,
        "columns": [
            {"name": "arm", "semantic_type": "categorical", "unique_count": 2},
            {"name": "score", "semantic_type": "numeric", "unique_count": 80},
        ],
    }
    prepared = AnalysisPreparer(catalog).prepare("group_tests", profile)
    assert prepared.plan is not None
    spec = CatalogWorkflowRegistry(catalog).compile_template(
        "group_tests", plan=prepared.plan, dataset_profile=profile
    )

    report = validate_workflow_run_spec(spec, profile)

    assert report.ready, report.issues


def test_runtime_preflight_fails_closed_on_dataset_or_format_drift():
    spec = CatalogWorkflowRegistry(AnalysisCatalog()).compile_template(
        "group_tests",
        dataset_profile={"dataset_id": "dataset-1", "format": "csv"},
    )

    report = validate_workflow_run_spec(
        spec,
        {"dataset_id": "dataset-2", "format": "fcs", "assets": [{"format": "fcs"}]},
    )

    assert report.ready is False
    assert {issue.code for issue in report.issues} >= {
        "dataset_mismatch",
        "unsupported_dataset_format",
        "unresolved_critical_inputs",
    }
