"""Explicit bounded deterministic operations. Registration is trusted developer code."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import json
from typing import Any, Callable

from .inventory import Inventory
from .validation import ValidationError, canonical, digest, identifier, integer, object_fields


@dataclass(frozen=True)
class GateContext:
    permissions: frozenset[str] = frozenset()
    inventory: Inventory | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not isinstance(self.permissions, frozenset) or len(self.permissions) > 32:
            raise ValidationError("trusted_permission_set_required")
        for permission in self.permissions:
            identifier(permission, "permission")
        if self.inventory is not None and not isinstance(self.inventory, Inventory):
            raise ValidationError("typed_inventory_required")


@dataclass(frozen=True)
class HandlerSpec:
    operation: str
    version: str
    validate: Callable[[dict], Any] = field(repr=False)
    execute: Callable[[Any, GateContext], Any] = field(repr=False)
    required_permissions: frozenset[str] = frozenset()

    def __post_init__(self):
        identifier(self.operation, "operation")
        identifier(self.version, "handler_version")
        if not callable(self.validate) or not callable(self.execute):
            raise ValidationError("callable_handler_required")
        GateContext(self.required_permissions)


@dataclass(frozen=True)
class GateResult:
    status: str  # handled, declined, denied
    operation: str | None
    reason: str
    value: Any = None
    handler_version: str | None = None
    result_sha256: str | None = None


class Gate0Registry:
    def __init__(self):
        self._handlers: dict[str, HandlerSpec] = {}

    def register(self, handler: HandlerSpec, *, approved: bool = False) -> None:
        if approved is not True or not isinstance(handler, HandlerSpec):
            raise ValidationError("explicit_developer_approval_required")
        if handler.operation in self._handlers:
            raise ValidationError("duplicate_handler")
        self._handlers[handler.operation] = handler

    def operations(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def run(self, operation: str | None, arguments: Any, context: GateContext | None = None) -> GateResult:
        if operation is None:
            return GateResult("declined", None, "no_explicit_operation")
        try:
            identifier(operation, "operation")
        except ValidationError:
            return GateResult("denied", None, "invalid_operation")
        handler = self._handlers.get(operation)
        if handler is None:
            return GateResult("declined", operation, "unsupported_operation")
        if context is None:
            context = GateContext()
        if not isinstance(context, GateContext) or not handler.required_permissions <= context.permissions:
            return GateResult("denied", operation, "permission_required")
        try:
            if not isinstance(arguments, dict):
                raise ValidationError("object_arguments_required")
            # Copy through bounded JSON: a handler cannot mutate the caller's object.
            prepared = handler.validate(json.loads(canonical(arguments)))
        except Exception:
            return GateResult("denied", operation, "invalid_arguments", handler_version=handler.version)
        try:
            output = json.loads(canonical(handler.execute(prepared, context)))
            return GateResult("handled", operation, "approved_deterministic_handler", output,
                              handler.version, digest(output))
        except Exception:
            # Developer handler failures cannot turn into an implicit model fallback or leak data.
            return GateResult("denied", operation, "handler_failed", handler_version=handler.version)


def _arithmetic_validate(arguments: dict):
    object_fields(arguments, {"operator", "operands"})
    operator = arguments["operator"]
    if not isinstance(operator, str) or operator not in {"add", "subtract", "multiply", "divide"}:
        raise ValidationError("unsupported_arithmetic")
    operands = arguments["operands"]
    if not isinstance(operands, list) or not 2 <= len(operands) <= 32:
        raise ValidationError("bounded_operands_required")
    if operator in {"subtract", "divide"} and len(operands) != 2:
        raise ValidationError("two_operands_required")
    for value in operands:
        integer(value, "operand", -(10**9), 10**9)
    if operator == "divide" and operands[1] == 0:
        raise ValidationError("zero_denominator")
    return operator, operands


def _arithmetic_execute(prepared, _context):
    operator, operands = prepared
    result = Fraction(operands[0])
    for value in operands[1:]:
        if operator == "add":
            result += value
        elif operator == "subtract":
            result -= value
        elif operator == "multiply":
            result *= value
        else:
            result /= value
    return {"numerator": result.numerator, "denominator": result.denominator}


def _select_validate(arguments):
    object_fields(arguments, {"records", "fields"})
    rows, fields = arguments["records"], arguments["fields"]
    if not isinstance(rows, list) or len(rows) > 256 or not isinstance(fields, list) or not 1 <= len(fields) <= 32:
        raise ValidationError("bounded_transform_required")
    for key in fields:
        identifier(key, "field")
    if len(set(fields)) != len(fields):
        raise ValidationError("duplicate_field")
    if any(not isinstance(row, dict) or not set(fields) <= row.keys() for row in rows):
        raise ValidationError("missing_transform_field")
    return rows, fields


def _select_execute(prepared, _context):
    rows, fields = prepared
    return [{key: row[key] for key in fields} for row in rows]


def _metadata_validate(arguments):
    object_fields(arguments, {"model_id"})
    return identifier(arguments["model_id"], "model_id")


def _metadata_execute(model_id, context):
    if context.inventory is None:
        raise ValidationError("inventory_required")
    model = context.inventory.model(model_id)
    return {"id":model.id, "backend_id":model.backend_id, "native_id":model.native_id,
            "revision":model.revision,
            "research":{key:fact.status for key,fact in context.inventory.research_summary(model_id).items()}}


def builtin_registry() -> Gate0Registry:
    registry = Gate0Registry()
    registry.register(HandlerSpec("math.calculate", "1", _arithmetic_validate, _arithmetic_execute), approved=True)
    registry.register(HandlerSpec("data.select", "1", _select_validate, _select_execute), approved=True)
    registry.register(HandlerSpec("inventory.describe", "1", _metadata_validate, _metadata_execute,
                                  frozenset({"inventory.read"})), approved=True)
    return registry
