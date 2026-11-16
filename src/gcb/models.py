"""Validated immutable identity and routing inputs. No credential or prompt fields."""
from __future__ import annotations

from dataclasses import dataclass

from .validation import boolean, choice, identifier, ids, integer, text


@dataclass(frozen=True)
class Provider:
    id: str
    name: str

    def __post_init__(self):
        identifier(self.id)
        text(self.name, "provider_name")


@dataclass(frozen=True)
class Backend:
    id: str
    provider_id: str
    api_family: str
    revision: str
    locality: str = "unknown"
    authorization: str = "unknown"
    availability: str = "unknown"
    endpoint_ref: str | None = None

    def __post_init__(self):
        for item in (self.id, self.provider_id, self.api_family, self.revision):
            identifier(item)
        choice(self.locality, {"local", "remote", "unknown"}, "locality")
        choice(self.authorization, {"allowed", "denied", "unknown"}, "authorization")
        choice(self.availability, {"ready", "unavailable", "unknown"}, "availability")
        if self.endpoint_ref is not None:
            identifier(self.endpoint_ref, "endpoint_ref")


@dataclass(frozen=True)
class Model:
    id: str
    backend_id: str
    native_id: str
    revision: str = "unknown"

    def __post_init__(self):
        for item in (self.id, self.backend_id, self.native_id, self.revision):
            identifier(item)


@dataclass(frozen=True)
class TaskRequirements:
    category: str = "general"
    context_tokens: int = 0
    output_tokens: int = 256
    modalities: tuple[str, ...] = ("text",)
    require_tools: bool = False
    local_only: bool = False

    def __post_init__(self):
        identifier(self.category, "category")
        integer(self.context_tokens, "context_tokens")
        integer(self.output_tokens, "output_tokens", 1)
        values = ids(self.modalities, "modalities")
        if not values or not set(values) <= {"text", "image", "audio", "video"}:
            from .validation import ValidationError
            raise ValidationError("invalid_modalities")
        object.__setattr__(self, "modalities", values)
        boolean(self.require_tools, "require_tools")
        boolean(self.local_only, "local_only")


@dataclass(frozen=True)
class RoutingPolicy:
    policy_id: str = "default"
    revision: int = 1
    allowed_providers: tuple[str, ...] | None = None
    allowed_backends: tuple[str, ...] | None = None
    allowed_configurations: tuple[str, ...] | None = None
    allow_remote: bool = False
    require_verified_research: bool = False

    def __post_init__(self):
        identifier(self.policy_id, "policy_id")
        integer(self.revision, "policy_revision", 1)
        for field in ("allowed_providers", "allowed_backends", "allowed_configurations"):
            object.__setattr__(self, field, ids(getattr(self, field), field, optional=True))
        boolean(self.allow_remote, "allow_remote")
        boolean(self.require_verified_research, "require_verified_research")
