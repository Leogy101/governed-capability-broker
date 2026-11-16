"""Cited immutable facts, including exact backend-native reasoning combinations."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .validation import (ValidationError, boolean, canonical, choice, digest, identifier,
                         integer, native_settings, object_fields, source_url, text, utc_time)

PROPERTIES = frozenset({"context_window", "max_output", "modalities", "native_tools", "reasoning"})
USABLE_VERIFICATIONS = frozenset({"documented", "verified", "synthetic"})


@dataclass(frozen=True)
class SourceCitation:
    url: str
    title: str
    checked_at: str

    def __post_init__(self):
        source_url(self.url)
        text(self.title, "source_title")
        utc_time(self.checked_at)


def validate_fact_value(property: str, value: Any) -> None:
    choice(property, set(PROPERTIES), "research_property")
    if property in {"context_window", "max_output"}:
        if value is not None:
            integer(value, property, 1)
    elif property == "native_tools":
        if value is not None:
            boolean(value, "native_tools")
    elif property == "modalities":
        if value is not None:
            if not isinstance(value, list) or not value or len(value) > 4:
                raise ValidationError("invalid_research_modalities")
            if any(type(x) is not str or x not in {"text", "image", "audio", "video"} for x in value):
                raise ValidationError("invalid_research_modalities")
            if len(value) != len(set(value)):
                raise ValidationError("invalid_research_modalities")
    else:
        object_fields(value, {"semantics", "native_configurations"})
        choice(value["semantics"], {"none", "fixed", "toggle", "effort", "token_budget", "adaptive", "unknown"}, "reasoning_semantics")
        configurations = value["native_configurations"]
        if not isinstance(configurations, list) or len(configurations) > 32:
            raise ValidationError("bounded_native_configurations_required")
        encoded = [native_settings(config) for config in configurations]
        if len(set(encoded)) != len(encoded):
            raise ValidationError("duplicate_native_configuration")
        if value["semantics"] == "unknown" and configurations:
            raise ValidationError("unknown_reasoning_cannot_assert_configurations")
        if value["semantics"] != "unknown" and not configurations:
            raise ValidationError("native_configuration_required")
        if value["semantics"] == "none" and configurations != [{}]:
            raise ValidationError("no_reasoning_requires_empty_native_configuration")


@dataclass(frozen=True)
class ResearchFact:
    model_id: str
    property: str
    value_json: str
    verification: str
    sources: tuple[SourceCitation, ...]

    def __post_init__(self):
        identifier(self.model_id, "model_id")
        choice(self.verification, {"unverified", "documented", "verified", "synthetic", "unsupported"}, "verification")
        if not isinstance(self.value_json, str) or len(self.value_json) > 65536:
            raise ValidationError("invalid_fact_json")
        try:
            value = json.loads(self.value_json)
        except (ValueError, RecursionError):
            raise ValidationError("invalid_fact_json") from None
        encoded = canonical(value)
        validate_fact_value(self.property, value)
        object.__setattr__(self, "value_json", encoded)
        if not isinstance(self.sources, (list, tuple)) or not 1 <= len(self.sources) <= 16:
            raise ValidationError("bounded_sources_required")
        if not all(isinstance(x, SourceCitation) for x in self.sources):
            raise ValidationError("typed_sources_required")
        if self.verification != "synthetic" and any(x.url.startswith("fixture:") for x in self.sources):
            raise ValidationError("fixture_source_requires_synthetic_verification")
        object.__setattr__(self, "sources", tuple(self.sources))

    @classmethod
    def create(cls, model_id: str, property: str, value: Any, verification: str,
               sources: tuple[SourceCitation, ...]) -> ResearchFact:
        return cls(model_id, property, canonical(value), verification, sources)

    @property
    def id(self) -> str:
        return digest(asdict(self))

    @property
    def value(self) -> Any:
        return json.loads(self.value_json)


@dataclass(frozen=True)
class ResolvedFact:
    property: str
    status: str
    value_json: str | None
    fact_ids: tuple[str, ...]
    verifications: tuple[str, ...]

    @property
    def value(self) -> Any:
        return None if self.value_json is None else json.loads(self.value_json)


def resolve_facts(facts: tuple[ResearchFact, ...]) -> dict[str, ResolvedFact]:
    result = {}
    for property in sorted(PROPERTIES):
        entries = sorted((fact for fact in facts if fact.property == property), key=lambda fact: fact.id)
        usable = [fact for fact in entries if fact.verification in USABLE_VERIFICATIONS]
        distinct = {fact.value_json for fact in usable}
        if len(distinct) > 1:
            status, value = "conflict", None
        elif len(distinct) == 1:
            status, value = "resolved", next(iter(distinct))
            decoded = json.loads(value)
            if decoded is None or (property == "reasoning" and decoded["semantics"] == "unknown"):
                status = "unknown"
        else:
            status, value = ("unsupported" if any(f.verification == "unsupported" for f in entries) else "unknown"), None
        result[property] = ResolvedFact(property, status, value, tuple(f.id for f in entries),
                                        tuple(sorted({f.verification for f in usable})))
    return result
