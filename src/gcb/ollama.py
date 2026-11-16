"""Explicit loopback Ollama adapter; never pulls models, reads credentials, or retries.

Only public benchmark input/schema is sent. Gold answers and scorer internals stay
inside the runner. Metadata is sanitized before it reaches durable storage.
"""
from __future__ import annotations

from datetime import datetime, timezone
import http.client
import json
import math
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from .profile_backend import NativeToolCall, ProfileResponse, RateLimited, UnknownCompletion
from .validation import ValidationError, canonical, digest, identifier, integer, native_settings, text

ADAPTER_REVISION = "ollama-loopback-2"
SOURCE = "https://docs.ollama.com/api-reference/show-model-details"
SOURCES = [SOURCE, "https://docs.ollama.com/api/chat", "https://docs.ollama.com/capabilities/thinking",
           "https://docs.ollama.com/modelfile", "https://docs.ollama.com/capabilities/tool-calling"]
# Public response formats, authored independently of the private expected values.
RESPONSE_PROPERTIES = {
    "coding.guard": {"missing_guard": {"type": "string"}},
    "review.risks": {"risks": {"type": "array", "items": {"type": "string"}}},
    "research.source-pack": {"hour": {"type": "integer"}, "sources": {"type": "array", "items": {"type": "string"}}},
    "planning.dependencies": {"steps": {"type": "array", "items": {"type": "string"}}},
    "structured.invoice": {"invoice": {"type": "string"}, "count": {"type": "integer"}, "unit": {"type": "string"}},
    "orchestration.choice": {"next_step": {"type": "string"}, "arguments": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}},
}
TOOL = {"type": "function", "function": {"name": "fixture_lookup", "description": "Read one harmless benchmark fixture by key.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}}


def loopback_endpoint(value):
    if not isinstance(value, str):
        raise ValidationError("explicit_loopback_endpoint_required")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValidationError("explicit_loopback_endpoint_required") from None
    if (url.scheme != "http" or url.hostname != "127.0.0.1" or port is None or not 1 <= port <= 65535
            or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment
            or value not in {f"http://127.0.0.1:{port}", f"http://127.0.0.1:{port}/"}):
        raise ValidationError("explicit_loopback_endpoint_required")
    return f"http://127.0.0.1:{port}"


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValidationError("duplicate_backend_json_key")
            result[key] = value
        return result
    try:
        result = json.loads(data, object_pairs_hook=pairs)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValidationError("invalid_backend_json") from None


def _artifact_details(response):
    """Retain only public deployment labels, exactly as reported by each API.

    Format identifies the artifact; Ollama owns decoding and runner selection.
    Absent/empty optional labels remain unknown, not inferred from architecture.
    """
    details = response.get("details", {})
    if not isinstance(details, dict):
        raise ValidationError("invalid_model_details")
    result = {}
    for key in ("format", "quantization_level", "runner"):
        value = details.get(key)
        result[key] = value if value is None or value == "" else text(value, key, 128)
    return result


class LoopbackTransport:
    """Direct HTTP without environment proxies, redirects, authentication or retries."""
    def __init__(self, endpoint):
        self.endpoint = loopback_endpoint(endpoint)
        self.port = urlsplit(self.endpoint).port
        self._lock = threading.Lock()
        self._cancelled = False
        self._connections = {}

    @staticmethod
    def _interrupt(connection, owned_socket):
        # Keep the socket reference even when getresponse() clears connection.sock
        # for a Connection: close response: its file object may still be reading.
        if owned_socket is not None:
            try:
                owned_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        try:
            connection.close()
        except OSError:
            pass

    def cancel(self):
        """Permanently stop this transport and interrupt only its own requests.

        This is best-effort client cancellation, not proof the server stopped.
        A cancelled attempt must retain unknown completion until independently
        reconciled; cancellation never authorizes another request or a retry.
        """
        with self._lock:
            self._cancelled = True
            active = tuple(self._connections.items())
        for connection, owned_socket in active:
            self._interrupt(connection, owned_socket)

    def _check_cancelled(self):
        with self._lock:
            if self._cancelled:
                raise UnknownCompletion("ollama_transport_cancelled")

    def __call__(self, method, path, body, *, timeout):
        if (method, path) not in {("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/show"), ("POST", "/api/chat")}:
            raise ValidationError("unsupported_ollama_operation")
        if type(timeout) not in {float, int} or not math.isfinite(timeout) or timeout <= 0:
            raise UnknownCompletion("request_deadline")
        with self._lock:
            if self._cancelled:
                raise UnknownCompletion("ollama_transport_cancelled")
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
            # Connect explicitly, then forbid http.client from reconnecting if a
            # simultaneous cancel closes the socket before request() sends bytes.
            connection.auto_open = 0
            self._connections[connection] = None
        response = None
        try:
            connection.connect()
            with self._lock:
                self._connections[connection] = connection.sock
            self._check_cancelled()
            connection.request(method, path, None if body is None else canonical(body).encode("utf-8"),
                               {"Content-Type": "application/json", "Accept": "application/json"})
            response = connection.getresponse()
            self._check_cancelled()
            if response.status == 429:
                try:
                    retry = float(response.getheader("Retry-After", "60"))
                except ValueError:
                    retry = 60
                raise RateLimited(retry)
            if response.status != 200:
                raise ValidationError("ollama_http_error")
            # Enough for bounded non-streaming output/thinking, without unbounded downloads.
            data = response.read(1048577)
            self._check_cancelled()
            if len(data) > 1048576:
                raise ValidationError("backend_response_byte_limit")
            result = _json(data)
            with self._lock:
                if self._cancelled:
                    raise UnknownCompletion("ollama_transport_cancelled")
                return result
        except (TimeoutError, ConnectionError, http.client.HTTPException, OSError):
            raise UnknownCompletion("ollama_transport_uncertain") from None
        finally:
            with self._lock:
                owned_socket = self._connections.pop(connection, None)
                cancelled = self._cancelled
            if cancelled:
                self._interrupt(connection, owned_socket)
            if response is not None:
                response.close()
            connection.close()


class OllamaBackend:
    """One explicit already-installed local model on an explicit loopback endpoint.

    context_tokens/output_cap are operator request bounds, not intrinsic model
    limits. Metadata must report thinking controls; missing controls are unknown.
    Injected transports default to transport_fixture evidence, never real evidence.
    """
    def __init__(self, endpoint, native_model_id, *, context_tokens=4096, output_cap=512, transport=None):
        self.endpoint = loopback_endpoint(endpoint)
        self.native_model_id = identifier(native_model_id, "native_model_id")
        if "cloud" in native_model_id.lower():
            raise ValidationError("local_model_required")
        self.context_tokens = integer(context_tokens, "context_tokens", 1024, 32768)
        self.output_cap = integer(output_cap, "output_cap", 1, 8192)
        if self.output_cap >= self.context_tokens:
            raise ValidationError("output_cap_must_fit_context")
        self.transport = transport if transport is not None else LoopbackTransport(self.endpoint)
        self.evidence_kind = "transport_fixture" if transport is not None else "local_model"
        self._metadata = None
        self._previous = None

    def _get(self, method, path, body=None, *, deadline=None):
        timeout = 5 if deadline is None else min(5 if path != "/api/chat" else 60, deadline - time.monotonic())
        if timeout <= 0:
            raise UnknownCompletion("request_deadline")
        result = self.transport(method, path, body, timeout=timeout)
        if not isinstance(result, dict) or "error" in result:
            raise ValidationError("ollama_response_error")
        return result

    def _snapshot(self, *, deadline=None):
        version = self._get("GET", "/api/version", deadline=deadline).get("version")
        identifier(version, "ollama_version")
        tags = self._get("GET", "/api/tags", deadline=deadline).get("models")
        if not isinstance(tags, list) or len(tags) > 1024:
            raise ValidationError("invalid_ollama_catalog")
        rows = [row for row in tags if isinstance(row, dict) and row.get("name") == self.native_model_id]
        if len(rows) != 1:
            raise ValidationError("exact_installed_model_required")
        tag = rows[0]
        model_digest = tag.get("digest")
        if not isinstance(model_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", model_digest):
            raise ValidationError("model_digest_required")
        if tag.get("remote_model") or tag.get("remote_host") or type(tag.get("size")) is not int or tag["size"] <= 0:
            raise ValidationError("local_model_required")
        catalog_details = _artifact_details(tag)
        if not catalog_details["format"]:
            raise ValidationError("model_format_required")
        shown = self._get("POST", "/api/show", {"model": self.native_model_id, "verbose": False}, deadline=deadline)
        if shown.get("remote_model") or shown.get("remote_host"):
            raise ValidationError("local_model_required")
        show_details = _artifact_details(shown)
        for key, value in catalog_details.items():
            if value and show_details[key] and value != show_details[key]:
                raise ValidationError("inconsistent_model_details")
        capabilities = shown.get("capabilities")
        if not isinstance(capabilities, list) or any(type(x) is not str for x in capabilities) or "completion" not in capabilities:
            raise ValidationError("completion_capability_required")
        info = shown.get("model_info", {})
        architecture = info.get("general.architecture")
        identifier(architecture, "model_architecture")
        intrinsic_context = integer(info.get(architecture + ".context_length"), "intrinsic_context", 1)
        if self.context_tokens > intrinsic_context:
            raise ValidationError("context_exceeds_documented_model_limit")
        thinking = shown.get("thinking")
        if not isinstance(thinking, dict) or set(thinking) != {"values", "default"}:
            raise ValidationError("thinking_metadata_required")
        values = thinking["values"]
        if (not isinstance(values, list) or not 1 <= len(values) <= 8
                or any(type(x) not in {bool, str} for x in values)
                or any(isinstance(x, str) and (not x or len(x) > 32) for x in values)
                or len({canonical(x) for x in values}) != len(values)
                or canonical(thinking["default"]) not in {canonical(x) for x in values}):
            raise ValidationError("invalid_exact_thinking_values")
        # Only numeric runtime defaults are retained. Never save system/template/license text.
        defaults = {}
        parameters = shown.get("parameters", "")
        if not isinstance(parameters, str) or len(parameters) > 32768:
            raise ValidationError("invalid_model_parameters")
        for line in parameters.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in {"num_ctx", "num_predict", "temperature", "top_p", "top_k", "min_p", "seed", "repeat_penalty", "presence_penalty"}:
                try:
                    value = json.loads(parts[1])
                    if type(value) in {int, float} and math.isfinite(value):
                        defaults[parts[0]] = value
                except ValueError:
                    pass
        return {"server_version": version, "digest": model_digest, "architecture": architecture,
                "artifact": {"size_bytes": tag["size"], "catalog_details": catalog_details,
                             "show_details": show_details},
                "intrinsic_context_window": intrinsic_context, "capabilities": sorted(set(capabilities)),
                "thinking": thinking, "numeric_model_defaults": defaults}

    def metadata(self, model_id):
        identifier(model_id, "model_id")
        snapshot = self._snapshot()
        options = {"num_ctx": self.context_tokens, "num_predict": self.output_cap}
        native = [{"think": value, "options": options} for value in snapshot["thinking"]["values"]]
        default = {"think": snapshot["thinking"]["default"], "options": options}
        backend_id = "ollama-" + digest({"endpoint": self.endpoint, "version": snapshot["server_version"], "adapter": ADAPTER_REVISION})[:24]
        checked = datetime.now(timezone.utc).isoformat()
        body = {"metadata_version": ADAPTER_REVISION, "evidence_kind": self.evidence_kind,
                "provider": {"id": "ollama-local", "name": "Ollama local runtime"},
                "backend": {"id": backend_id, "provider_id": "ollama-local", "api_family": "ollama-chat",
                            "revision": snapshot["server_version"] + "/" + ADAPTER_REVISION,
                            "locality": "local", "authorization": "allowed", "availability": "ready", "endpoint_ref": self.endpoint},
                "model": {"id": model_id, "native_id": self.native_model_id, "revision": snapshot["digest"],
                          "context_window": self.context_tokens, "max_output": self.output_cap, "modalities": ["text"],
                          "native_tools": "tools" in snapshot["capabilities"],
                          "features": ["native_tools", "error_recovery"] if "tools" in snapshot["capabilities"] else [],
                          "reasoning": {"semantics": "fixed" if len(native) == 1 else ("toggle" if all(type(value) is bool for value in snapshot["thinking"]["values"]) else "effort"), "native_configurations": native},
                          "default_native": default,
                          "default_behavior": "Exact /api/show thinking default, explicitly sent; context and output are operator request caps. Other sampling settings use model/server defaults; effective settings are not echoed.",
                          "source": {"url": SOURCE, "title": "Ollama show API and sanitized local model metadata", "checked_at": checked},
                          "execution_limits": {"context_window_scope": "configured_request_cap", "max_output_scope": "configured_request_cap",
                                               "intrinsic_context_window": snapshot["intrinsic_context_window"], "intrinsic_max_output": None},
                          "backend_snapshot": snapshot, "documentation_urls": SOURCES}}
        self._metadata = json.loads(canonical(body))
        return json.loads(canonical(body))

    def bind_metadata(self, body):
        """Bind the persisted plan snapshot; live identity is checked before each inference."""
        row = body["model"]
        if (body.get("evidence_kind") != self.evidence_kind or body["backend"]["endpoint_ref"] != self.endpoint
                or row["native_id"] != self.native_model_id or row["context_window"] != self.context_tokens
                or row["max_output"] != self.output_cap or body["metadata_version"] != ADAPTER_REVISION):
            raise ValidationError("ollama_backend_plan_mismatch")
        self._metadata = json.loads(canonical(body))

    def complete(self, request, case, *, deadline, tool_feedback=None):
        if self._metadata is None:
            raise ValidationError("research_required_before_inference")
        metadata = self._metadata
        row = metadata["model"]
        if (request.provider_id != metadata["provider"]["id"] or request.backend_id != metadata["backend"]["id"]
                or request.native_model_id != self.native_model_id or request.case_id != case.id
                or request.max_output_tokens != self.output_cap
                or request.native_json not in {native_settings(value) for value in row["reasoning"]["native_configurations"]}
                or not set(case.requires) <= set(row["features"])):
            raise ValidationError("ollama_request_identity_mismatch")
        if self._snapshot(deadline=deadline) != row["backend_snapshot"]:
            raise ValidationError("ollama_metadata_drift")
        # Deliberately no access to expected_json, score_text, or private evaluator state.
        payload = {"model": self.native_model_id, "stream": False, **json.loads(request.native_json)}
        messages = [{"role": "user", "content": case.input_json}]
        if case.requires:
            payload["tools"] = [TOOL]
            if case.category == "error_recovery":
                messages[0]["content"] += "\nFor this error-recovery exercise, first call fixture_lookup with key missing. Then repair the key after the tool error."
            if tool_feedback is not None:
                if self._previous is None or self._previous[0] != (request.configuration_id, case.id):
                    raise ValidationError("native_feedback_context_required")
                messages.append(self._previous[1])
                self._previous = None
                messages.append({"role": "tool", "tool_name": "fixture_lookup", "content": canonical(tool_feedback)})
        else:
            if case.id not in RESPONSE_PROPERTIES:
                raise ValidationError("unsupported_public_case_schema")
            properties = RESPONSE_PROPERTIES[case.id]
            schema = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
            payload["format"] = schema
            messages[0]["content"] += "\nReturn only JSON matching this public response schema: " + canonical(schema)
        payload["messages"] = messages
        if len(canonical(messages).encode("utf-8")) + self.output_cap > self.context_tokens:
            raise ValidationError("bounded_prompt_exceeds_context")
        result = self._get("POST", "/api/chat", payload, deadline=deadline)
        if result.get("done") is not True:
            raise UnknownCompletion("incomplete_nonstreaming_response")
        message = result.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise ValidationError("invalid_chat_message")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > 1:
            raise ValidationError("unsupported_native_call_count")
        native_calls = []
        safe_calls = []
        for call in calls:
            function = call.get("function", {}) if isinstance(call, dict) else {}
            if not isinstance(function.get("arguments"), dict):
                raise ValidationError("native_arguments_object_required")
            native_calls.append(NativeToolCall(function.get("name"), canonical(function["arguments"])))
            safe_calls.append({"function": {"name": function["name"], "arguments": function["arguments"]}})
        # Ollama tool continuations require the original assistant fields. Keep
        # bounded content/thinking only ephemerally; never return or persist them.
        history = {"role": "assistant", "content": message.get("content", ""), "tool_calls": safe_calls}
        if "thinking" in message:
            history["thinking"] = message["thinking"]
        for field in ("content", "thinking"):
            if field in history and (not isinstance(history[field], str) or len(history[field].encode("utf-8")) > 32768):
                raise ValidationError("assistant_history_bound_exceeded")
        canonical(history)
        self._previous = ((request.configuration_id, case.id), history) if safe_calls and tool_feedback is None and case.category=="error_recovery" else None
        finish = result.get("done_reason")
        if finish not in {"stop", "length"}:
            raise ValidationError("unsupported_ollama_finish_reason")
        if native_calls and finish == "stop":
            finish = "tool_calls"
        durations = {key: integer(result[key], key, 0, 10**15) for key in ("total_duration", "load_duration", "prompt_eval_duration", "eval_duration") if key in result}
        return ProfileResponse(request.provider_id, request.backend_id, result.get("model"), request.native_json,
                               message.get("content", ""), tuple(native_calls), finish,
                               input_tokens=result.get("prompt_eval_count"), output_tokens=result.get("eval_count"),
                               evidence_kind=self.evidence_kind, native_confirmation="request_only_not_echoed",
                               backend_metrics_json=canonical(durations))
