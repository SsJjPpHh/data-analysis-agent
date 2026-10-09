"""Small, explicit applicability rules shared by preparation and execution.

Catalog conditions are input presence (``input:group``), an exact branch
(``input:analysis_method=gee``), or a portfolio feature such as ``numeric``.
There is no expression evaluation or inference from display labels.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.analysis.models import InputSpec, ResolvedInput


def input_value(value: Any) -> Any:
    return value.value if isinstance(value, ResolvedInput) else value


def has_value(value: Any) -> bool:
    value = input_value(value)
    return value is not None and value != "" and value != [] and value != ()


def conditions_match(
    conditions: Sequence[str],
    inputs: Mapping[str, Any],
    features: Sequence[str] = (),
) -> bool:
    for condition in conditions:
        if not condition.startswith("input:"):
            if condition not in features:
                return False
            continue
        key, separator, expected = condition[6:].partition("=")
        value = input_value(inputs.get(key))
        if not has_value(value) or (separator and str(value) != expected):
            return False
    return True


def missing_contract_inputs(
    specs: Sequence[InputSpec],
    alternatives: Sequence[Sequence[str]],
    inputs: Mapping[str, Any],
) -> tuple[str, ...]:
    required = {
        spec.id
        for spec in specs
        if spec.requirement == "critical"
        or (spec.required_when and conditions_match(spec.required_when, inputs))
    }
    missing = {key for key in required if not has_value(inputs.get(key))}
    for group in alternatives:
        if not any(has_value(inputs.get(key)) for key in group):
            missing.update(group)
    for spec in specs:
        value = input_value(inputs.get(spec.id))
        if has_value(value):
            if spec.kind == "columns" and (not isinstance(value, (list, tuple)) or len(value) < spec.min_items):
                missing.add(spec.id)
            if spec.choices and value not in spec.choices:
                missing.add(spec.id)
    return tuple(spec.id for spec in specs if spec.id in missing)
