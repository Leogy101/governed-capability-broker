"""Small strict validators. Errors intentionally omit rejected input values."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit


class ValidationError(ValueError):
    """Invalid public input (message contains a code, never the rejected body)."""


def identifier(value: Any, field: str = "identifier") -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}", value):
        raise ValidationError(f"invalid_{field}")
    return value


def text(value: Any, field: str, limit: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValidationError(f"invalid_{field}")
    return value


def integer(value: Any, field: str, minimum: int = 0, maximum: int = 10**9) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"invalid_{field}")
    return value


def boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ValidationError(f"invalid_{field}")
    return value


def choice(value: Any, allowed: set[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"invalid_{field}")
    return value


def ids(value: Any, field: str, *, optional: bool = False) -> tuple[str, ...] | None:
    if optional and value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) > 128:
        raise ValidationError(f"invalid_{field}")
    result = tuple(identifier(x, field) for x in value)
    if len(set(result)) != len(result):
        raise ValidationError(f"duplicate_{field}")
    return result


def utc_time(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 40:
        raise ValidationError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("invalid_timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValidationError("utc_timestamp_required")
    return value


def source_url(value: Any) -> str:
    text(value, "source_url", 2048)
    url = urlsplit(value)
    if url.scheme not in {"https", "fixture"} or not url.netloc or url.username or url.password or url.query:
        raise ValidationError("public_source_url_without_credentials_or_query_required")
    return value


def canonical(value: Any) -> str:
    def check(item: Any, depth: int = 0) -> None:
        if depth > 12:
            raise ValidationError("json_nesting_limit")
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if item.bit_length() > 4096:
                raise ValidationError("json_integer_limit")
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValidationError("finite_json_required")
            return
        if isinstance(item, str):
            if len(item) > 65536:
                raise ValidationError("json_string_limit")
            return
        if isinstance(item, (list, tuple)):
            if len(item) > 1024:
                raise ValidationError("json_array_limit")
            for child in item:
                check(child, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > 128 or any(not isinstance(k, str) or len(k) > 128 for k in item):
                raise ValidationError("json_object_limit")
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValidationError("json_type_required")
    check(value)
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(result.encode("utf-8")) > 65536:
            raise ValidationError("json_byte_limit")
        return result
    except (ValueError, TypeError, UnicodeError):
        raise ValidationError("invalid_json") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def object_fields(value: Any, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ValidationError("invalid_object_fields")
    canonical(value)
    return value


RESERVED_NATIVE_KEYS = frozenset({
    "provider", "backend", "model", "model_id", "configuration_id", "case_id", "fixture_id",
    "endpoint", "base_url", "url", "api_key", "authorization", "headers", "credentials", "token",
    "messages", "input", "prompt", "system", "tools", "tool_choice", "stream", "max_tokens",
    "max_output_tokens", "max_completion_tokens", "timeout", "approved", "policy", "permissions",
    "password", "secret", "access_token", "refresh_token", "cookie", "cookies", "credential_ref",
    "api_base", "host", "proxy",
    "auth", "auth_token", "bearer_token", "access_key", "credential", "endpoint_url", "api_url",
})
_RESERVED_NATIVE_NORMALIZED = frozenset(re.sub(r"[^a-z0-9]", "", key.lower()) for key in RESERVED_NATIVE_KEYS)


def native_settings(value: Any) -> str:
    if not isinstance(value, dict) or len(value) > 16:
        raise ValidationError("native_settings_object_required")
    encoded = canonical(value)
    def check(item: Any):
        if isinstance(item, dict):
            for key, child in item.items():
                identifier(key, "native_key")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _RESERVED_NATIVE_NORMALIZED:
                    raise ValidationError("reserved_native_key")
                check(child)
        elif isinstance(item, (tuple, list)):
            for child in item:
                check(child)
    check(value)
    return encoded
