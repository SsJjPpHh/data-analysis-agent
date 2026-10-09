"""Catalog-backed registry for explicit scientific workflow variants."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Iterator, Mapping, Sequence

from agent.analysis.catalog import AnalysisCatalog
from agent.analysis.models import AnalysisPlan, ConversationPlanDraft
from agent.analysis.workflows.compiler import ScientificWorkflowCompiler
from agent.analysis.workflows.contracts import WorkflowDefinition, WorkflowRunSpec
from agent.analysis.workflows.validation import (
    WorkflowValidationReport,
    validate_workflow_definitions,
)


class CatalogWorkflowRegistry:
    """Compile every user template once and expose shared workflow variants."""

    def __init__(
        self,
        catalog: AnalysisCatalog | None = None,
        compiler: ScientificWorkflowCompiler | None = None,
    ) -> None:
        self._catalog = catalog or AnalysisCatalog()
        self._compiler = compiler or ScientificWorkflowCompiler()
        self._definitions = tuple(
            self._compiler.definition_for_template(template)
            for template in self._catalog.snapshot().templates
            if template.visibility == "user"
        )
        self._by_template = {definition.template_id: definition for definition in self._definitions}
        self._by_key = {
            (definition.workflow_id, definition.variant): definition for definition in self._definitions
        }

    def __len__(self) -> int:
        return len(self._definitions)

    def __iter__(self) -> Iterator[WorkflowDefinition]:
        return iter(self._definitions)

    def definitions(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._definitions)

    def workflow_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.workflow_id for item in self._definitions))

    def variants(self, workflow_id: str) -> tuple[WorkflowDefinition, ...]:
        requested = str(workflow_id or "").strip()
        return tuple(item for item in self._definitions if item.workflow_id == requested)

    def get(self, template_id: str) -> WorkflowDefinition:
        template = self._catalog.get(template_id)
        try:
            return self._by_template[template.id]
        except KeyError:
            raise KeyError(f"template has no user workflow definition: {template.id}") from None

    def get_variant(self, workflow_id: str, variant: str) -> WorkflowDefinition:
        key = (str(workflow_id or "").strip(), str(variant or "").strip())
        try:
            return self._by_key[key]
        except KeyError:
            raise KeyError(f"unknown workflow variant: {key[0]}/{key[1]}") from None

    def compile_template(
        self,
        template_id: str,
        *,
        plan: AnalysisPlan | None = None,
        dataset_profile: Mapping[str, Any] | None = None,
    ) -> WorkflowRunSpec:
        definition = self.get(template_id)
        return self._compiler.run_spec_from_template(
            definition,
            plan=plan,
            dataset_profile=dataset_profile,
        )

    def compile_free_form(
        self,
        request: ConversationPlanDraft | AnalysisPlan,
        *,
        dataset_profile: Mapping[str, Any] | None = None,
        resolved_packages: Sequence[str] = (),
    ) -> WorkflowRunSpec:
        return self._compiler.run_spec_from_free_form(
            request,
            dataset_profile=dataset_profile,
            resolved_packages=resolved_packages,
        )

    def compile(
        self,
        request: str | ConversationPlanDraft | AnalysisPlan,
        *,
        dataset_profile: Mapping[str, Any] | None = None,
        resolved_packages: Sequence[str] = (),
    ) -> WorkflowRunSpec:
        """Compile either entry path without exposing two execution models."""

        if isinstance(request, str):
            return self.compile_template(request, dataset_profile=dataset_profile)
        if isinstance(request, AnalysisPlan) and request.source == "template":
            if not request.template_id:
                raise ValueError("template AnalysisPlan must include template_id")
            return self.compile_template(
                request.template_id,
                plan=request,
                dataset_profile=dataset_profile,
            )
        return self.compile_free_form(
            request,
            dataset_profile=dataset_profile,
            resolved_packages=resolved_packages,
        )

    def validate(self) -> WorkflowValidationReport:
        return validate_workflow_definitions(self._catalog, self._definitions)

    def assert_valid(self) -> None:
        self.validate().raise_for_errors()


@lru_cache(maxsize=1)
def default_workflow_registry() -> CatalogWorkflowRegistry:
    """Return the immutable process-local registry for the bundled catalog."""

    registry = CatalogWorkflowRegistry()
    registry.assert_valid()
    return registry
