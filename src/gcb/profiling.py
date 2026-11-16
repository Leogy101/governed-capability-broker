"""Minimal profiler boundary only. No job runner, scores, retries, or paid backend."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .inventory import Inventory
from .models import RoutingPolicy, TaskRequirements
from .routing import preview_route
from .validation import ValidationError, canonical, identifier, integer, native_settings


@dataclass(frozen=True)
class ProbeRequest:
    configuration_id: str
    provider_id: str
    backend_id: str
    native_model_id: str
    native_json: str
    case_id: str
    max_output_tokens: int

    def __post_init__(self):
        for item in (self.configuration_id, self.provider_id, self.backend_id, self.native_model_id, self.case_id):
            identifier(item)
        integer(self.max_output_tokens, "max_output_tokens", 1, 65536)
        try:
            native = json.loads(self.native_json)
        except (ValueError, TypeError, RecursionError):
            raise ValidationError("invalid_native_json") from None
        object.__setattr__(self, "native_json", native_settings(native))

    def payload(self) -> dict:
        # G0 regression: native fields are validated AND authorized identity is assigned last.
        native = json.loads(native_settings(json.loads(self.native_json)))
        return {**native, "provider":self.provider_id, "backend":self.backend_id,
                "model":self.native_model_id, "case_id":self.case_id,
                "max_output_tokens":self.max_output_tokens}


@dataclass(frozen=True)
class ProbeResult:
    served_model: str
    backend_id: str
    effective_native_json: str
    evidence_kind: str
    output: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    billed_cost_usd: float | None = None


class ProfilingBackend(Protocol):
    def complete(self, request: ProbeRequest) -> ProbeResult:
        """Future implementations must preserve identity and usage presence, not guessed zeros."""


def prepare_probe(inventory: Inventory, configuration_id: str, *, case_id: str,
                  max_output_tokens: int = 64) -> ProbeRequest:
    identifier(case_id, "case_id")
    integer(max_output_tokens, "max_output_tokens", 1, 65536)
    config = inventory.configuration(configuration_id)
    decision = preview_route(inventory, TaskRequirements(output_tokens=max_output_tokens),
                             RoutingPolicy(allowed_configurations=(configuration_id,)))
    if decision.selected_configuration_id != configuration_id:
        raise ValidationError("configuration_not_eligible_for_local_probe")
    model = inventory.model(config.model_id)
    backend = inventory.backend(model.backend_id)
    return ProbeRequest(config.id, backend.provider_id, backend.id, model.native_id,
                        config.native_json, case_id, max_output_tokens)


class SyntheticBackend:
    """Only the included fixture identity/case. It produces no model performance evidence."""

    def complete(self, request: ProbeRequest) -> ProbeResult:
        if not isinstance(request, ProbeRequest):
            raise ValidationError("typed_probe_request_required")
        if (request.provider_id, request.backend_id, request.native_model_id, request.case_id) != (
                "synthetic", "synthetic-local-v1", "fixture-model", "synthetic.identity"):
            raise ValidationError("synthetic_fixture_scope_only")
        if request.native_json not in {canonical({"reasoning_effort":"low"}), canonical({"reasoning_effort":"high"})}:
            raise ValidationError("unsupported_synthetic_native_configuration")
        request.payload()
        return ProbeResult(request.native_model_id, request.backend_id, request.native_json,
                           "synthetic_fixture_not_model_measurement", "synthetic identity fixture")
