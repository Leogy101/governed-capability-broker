"""Explicit research/execution protocol and bundled fictional implementation only."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
import time
from typing import Protocol

from .cases import BenchmarkCase
from .profiling import ProbeRequest
from .validation import ValidationError, canonical, digest, identifier, integer, native_settings


class RateLimited(Exception):
    def __init__(self, retry_after: float = 1):
        self.retry_after = float(retry_after) if type(retry_after) in {int,float} and 0 <= retry_after <= 3600 else 60.0
        super().__init__("rate_limited")


class UnknownCompletion(Exception):
    """The request may have completed. Never retry it automatically."""


@dataclass(frozen=True)
class NativeToolCall:
    name: str
    arguments_json: str

    def __post_init__(self):
        identifier(self.name)
        object.__setattr__(self,"arguments_json",canonical(json.loads(self.arguments_json)))


@dataclass(frozen=True)
class ProfileResponse:
    provider_id: str
    backend_id: str
    served_model: str
    effective_native_json: str
    output: str = ""
    native_calls: tuple[NativeToolCall,...] = ()
    finish_reason: str = "stop"
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_cost_usd: float | None = None
    billed_cost_usd: float | None = None
    evidence_kind: str = "synthetic_fixture"
    native_confirmation: str = "fixture_echo"
    backend_metrics_json: str = "{}"
    # Host-normalized usage is kept separate from provider billing and Ollama timings.
    host_usage_json: str = "{}"


class ResearchBackend(Protocol):
    def metadata(self, model_id: str) -> dict:
        """Return nonsecret documented identity, limits and exact native configurations."""

    def complete(self, request: ProbeRequest, case: BenchmarkCase, *, deadline: float,
                 tool_feedback: dict | None = None) -> ProfileResponse:
        """One adapter invocation; no adapter retries/fallbacks. Host-internal attempts
        may be opaque and must be reported as unknown. Deadline is monotonic."""


class FictionalBackend:
    """Two intentionally uneven fixtures; never accesses environment, network or live models."""
    def __init__(self):
        self._bundle = json.loads(files("gcb").joinpath("fixtures/profile-models-v1.json").read_text(encoding="utf-8"))
        self.calls = 0
        self.research_calls = 0

    def metadata(self, model_id: str) -> dict:
        self.research_calls += 1
        row = next((row for row in self._bundle["models"] if row["id"] == model_id),None)
        if row is None:
            raise ValidationError("unknown_fictional_model")
        return json.loads(canonical({"metadata_version":self._bundle["metadata_version"],
                                    "provider":self._bundle["provider"],"backend":self._bundle["backend"],"model":row}))

    def complete(self, request: ProbeRequest, case: BenchmarkCase, *, deadline: float,
                 tool_feedback: dict | None = None) -> ProfileResponse:
        if time.monotonic() >= deadline:
            raise UnknownCompletion("deadline_elapsed")
        row = next((row for row in self._bundle["models"] if row["native_id"] == request.native_model_id),None)
        if row is None or request.provider_id != "fictional" or request.backend_id != "fictional-local-v1":
            raise ValidationError("fictional_identity_required")
        if request.native_json not in {native_settings(value) for value in row["reasoning"]["native_configurations"]}:
            raise ValidationError("fictional_native_configuration_required")
        if request.case_id != case.id or not set(case.requires) <= set(row["features"]):
            raise ValidationError("unsupported_fictional_case")
        request.payload()  # validates reserved keys and exact identity rails again
        self.calls += 1
        strengths = {"forge":{"coding","code_review","orchestration","tool_use","error_recovery"},
                     "scribe":{"research","planning","structured_data"}}[row["id"]]
        # Additional native configurations improve one narrow fixture, not every category.
        if request.native_json == native_settings(row["reasoning"]["native_configurations"][1]):
            strengths = strengths | ({"structured_data"} if row["id"] == "forge" else {"coding"})
        calls = ()
        if case.requires:
            key = "wrong" if case.category == "error_recovery" and tool_feedback is None else "alpha"
            calls = (NativeToolCall("fixture_lookup",canonical({"key":key})),)
            output = ""
        else:
            output = case.expected_json if case.category in strengths else canonical({"fixture_wrong_answer":True})
        return ProfileResponse(request.provider_id,request.backend_id,request.native_model_id,request.native_json,
                               output,calls,"tool_calls" if calls else "stop")
