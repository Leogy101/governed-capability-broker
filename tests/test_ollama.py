"""Offline fake transport and controlled loopback tests; never contact a model server."""
from contextlib import contextmanager, redirect_stderr
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import io
import threading
import time
import unittest
from unittest.mock import patch

from gcb import Inventory, RoutingPolicy, TaskRequirements, preview_route
from gcb.cases import CASES
from gcb.ollama import OllamaBackend, LoopbackTransport
from gcb.profile_backend import RateLimited, UnknownCompletion
from gcb.profiler import ProfileManager
from gcb.profiling import ProbeRequest
from gcb.validation import ValidationError, canonical


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.chat = []
        self.model = "test-model:1b"
        self.revision = "a" * 64
        self.thinking = {"values": [False, True], "default": True}
        self.capabilities = ["completion", "tools", "thinking", "vision"]
        self.details = {"format": "gguf", "quantization_level": "Q4_K_M", "runner": "ggml"}
        self.show_details = {"format": "gguf", "quantization_level": "Q4_K_M"}
        self.override = {}
        self.response = None

    def __call__(self, method, path, body, *, timeout):
        self.calls.append((method, path, body, timeout))
        if path in self.override:
            return self.override[path]
        if path == "/api/version":
            return {"version": "0.40.15"}
        if path == "/api/tags":
            return {"models": [{"name": self.model, "digest": self.revision, "size": 100,
                                "details": dict(self.details)}]}
        if path == "/api/show":
            return {"capabilities": self.capabilities, "thinking": self.thinking,
                    "details": dict(self.show_details),
                    "model_info": {"general.architecture": "test", "test.context_length": 32768},
                    "parameters": "temperature 0.8\nnum_ctx 2048\nmin_p 0\npresence_penalty 1.5\nstop SECRET_CANARY",
                    "template": "PRIVATE_TEMPLATE_CANARY", "system": "PRIVATE_SYSTEM_CANARY"}
        if path == "/api/chat":
            self.chat.append(body)
            if isinstance(self.response, Exception):
                raise self.response
            if self.response is not None:
                return self.response
            return {"model": self.model, "done": True, "done_reason": "stop", "prompt_eval_count": 33,
                    "eval_count": 9, "total_duration": 1000000, "load_duration": 100,
                    "message": {"role": "assistant", "content": '{"missing_guard":"empty_items"}',
                                "thinking": "THINKING_CANARY"}}
        raise AssertionError("unexpected endpoint")


@contextmanager
def local_server(callback):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(None)
        def do_POST(self):
            self.respond(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        def respond(self, body):
            status, headers, data = callback(self.command, self.path, body)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTransport()
        self.backend = OllamaBackend("http://127.0.0.1:11434", self.fake.model, transport=self.fake)
        self.inventory = Inventory(":memory:")
        self.addCleanup(self.inventory.close)
        self.manager = ProfileManager(self.inventory, self.backend)

    def plan(self, **kwargs):
        plan = self.manager.plan("local-test", **kwargs)
        return plan, self.manager.authorize(plan["plan_id"])

    def request(self, case=None):
        case = case or CASES[0]
        metadata = self.manager.research("local-test")
        config = self.inventory.configuration(metadata["configuration_ids"][0])
        return ProbeRequest(config.id, "ollama-local", metadata["backend"]["id"], self.fake.model,
                            config.native_json, case.id, 512)

    def test_sanitized_exact_metadata_cached_without_inference(self):
        first = self.manager.research("local-test")
        cached = self.manager.research("local-test")
        self.assertTrue(cached["cached"])
        self.assertEqual(len(self.fake.calls), 3)
        self.assertEqual(self.fake.chat, [])
        self.assertEqual(first["model"]["reasoning"]["semantics"], "toggle")
        self.assertIs(first["model"]["default_native"]["think"], True)
        self.assertEqual(first["model"]["execution_limits"]["intrinsic_max_output"], None)
        self.assertEqual(first["model"]["execution_limits"]["max_output_scope"], "configured_request_cap")
        self.assertEqual(first["model"]["modalities"], ["text"])  # Only supported adapter input.
        self.assertEqual(first["evidence_kind"], "transport_fixture")
        defaults = first["model"]["backend_snapshot"]["numeric_model_defaults"]
        self.assertEqual(defaults["min_p"], 0)
        self.assertEqual(defaults["presence_penalty"], 1.5)
        self.assertNotIn("CANARY", "\n".join(self.inventory.db.iterdump()))

    def test_missing_or_invented_thinking_metadata_fails_before_model_call(self):
        for thinking in (None, {}, {"values": [False, True], "default": "high"},
                         {"values": [0, 1], "default": 1}, {"values": [False, False], "default": False}):
            with self.subTest(thinking=thinking):
                self.fake.thinking = thinking
                with self.assertRaises(ValidationError):
                    self.manager.research("local-test")
        self.assertEqual(self.fake.chat, [])

    def test_api_managed_formats_preserve_deployment_and_use_same_chat_path(self):
        for format_name, quantization, runner in (("gguf", "Q4_K_M", "ggml"),
                                                  ("safetensors", "nvfp4", "mlx")):
            with self.subTest(format=format_name), Inventory(":memory:") as inventory:
                fake = FakeTransport()
                fake.details = {"format": format_name, "quantization_level": quantization, "runner": runner,
                                "family": "", "parameter_size": "", "private_path": "PRIVATE_CANARY"}
                fake.show_details = {"format": format_name, "quantization_level": quantization,
                                     "family": "populated_family", "parameter_size": "36.0B"}
                backend = OllamaBackend("http://127.0.0.1:11434", fake.model, transport=fake)
                manager = ProfileManager(inventory, backend)
                metadata = manager.research("format-test")
                artifact = metadata["model"]["backend_snapshot"]["artifact"]
                self.assertEqual(artifact["catalog_details"], {"format": format_name,
                                 "quantization_level": quantization, "runner": runner})
                self.assertEqual(artifact["show_details"]["runner"], None)
                self.assertEqual(artifact["size_bytes"], 100)
                self.assertEqual(metadata["model"]["revision"], fake.revision)
                self.assertFalse(fake.chat)
                config = next(c for c in metadata["configuration_ids"] if inventory.configuration(c).native["think"] is False)
                plan = manager.plan("format-test", contrast=False, max_requests=9, baseline_configuration_id=config)
                grant = manager.authorize(plan["plan_id"])
                manager.start(plan["plan_id"], grant, max_cells=1)
                self.assertEqual(len(fake.chat), 1)
                self.assertEqual(fake.chat[0]["model"], fake.model)
                self.assertIs(fake.chat[0]["think"], False)
                self.assertEqual(fake.chat[0]["options"], {"num_ctx": 4096, "num_predict": 512})
                self.assertIsInstance(fake.chat[0]["format"], dict)
                self.assertEqual(manager.bio("format-test")["backend_snapshot"]["artifact"], artifact)
                self.assertNotIn("CANARY", "\n".join(inventory.db.iterdump()))

    def test_format_is_metadata_not_a_second_runner_allowlist(self):
        self.fake.details["format"] = "api-managed-format"
        self.fake.show_details["format"] = "api-managed-format"
        self.fake.details.pop("quantization_level")
        self.fake.details.pop("runner")
        self.fake.show_details = {}
        snapshot = self.backend.metadata("future-format")["model"]["backend_snapshot"]
        self.assertEqual(snapshot["artifact"]["catalog_details"],
                         {"format": "api-managed-format", "quantization_level": None, "runner": None})
        self.assertEqual(snapshot["artifact"]["show_details"]["format"], None)
        self.assertFalse(self.fake.chat)

    def test_malformed_artifacts_and_remote_aliases_fail_before_inference(self):
        good = {"name": self.fake.model, "digest": self.fake.revision, "size": 100,
                "details": {"format": "safetensors", "quantization_level": "nvfp4", "runner": "mlx"}}
        bad_rows = [{**good, "details": value} for value in (None, [], {}, {"format": ""}, {"format": 7},
                    {"format": "safetensors", "runner": ["mlx"]})]
        bad_rows += [{**good, "size": value} for value in (0, -1, True, None)]
        bad_rows += [{**good, key: "remote.example"} for key in ("remote_model", "remote_host")]
        for row in bad_rows:
            with self.subTest(row=row):
                self.fake.override["/api/tags"] = {"models": [row]}
                with self.assertRaises(ValidationError):
                    self.backend.metadata("bad")
        self.fake.override.clear()
        for field in ("remote_model", "remote_host"):
            self.fake.override["/api/show"] = {field: "remote.example"}
            with self.assertRaisesRegex(ValidationError, "local_model_required"):
                self.backend.metadata("bad-show")
        self.assertFalse(self.fake.chat)

    def test_catalog_show_disagreement_and_artifact_drift_fail_closed(self):
        for key, value in (("format", "safetensors"), ("quantization_level", "nvfp4"), ("runner", "mlx")):
            with self.subTest(key=key):
                fake = FakeTransport()
                fake.show_details[key] = value
                backend = OllamaBackend("http://127.0.0.1:11434", fake.model, transport=fake)
                with self.assertRaisesRegex(ValidationError, "inconsistent_model_details"):
                    backend.metadata("mismatch")
                self.assertFalse(fake.chat)
        for key, value in (("format", "safetensors"), ("quantization_level", "nvfp4"), ("runner", "mlx")):
            with self.subTest(drift=key), Inventory(":memory:") as inventory:
                fake = FakeTransport()
                manager = ProfileManager(inventory, OllamaBackend("http://127.0.0.1:11434", fake.model, transport=fake))
                plan = manager.plan("drift", contrast=False)
                fake.details[key] = fake.show_details[key] = value
                status = manager.start(plan["plan_id"], manager.authorize(plan["plan_id"]), max_cells=1)
                self.assertEqual(status["cells"]["technical_error"], 1)
                self.assertEqual(manager.evidence(plan["plan_id"])["attempts"][0]["summary_json"]["error_code"],
                                 "backend_or_response_error")
                self.assertFalse(fake.chat)

    def test_old_adapter_plans_require_fresh_research_without_rewriting_history(self):
        metadata = self.backend.metadata("old")
        metadata["metadata_version"] = "ollama-loopback-1"
        with self.assertRaisesRegex(ValidationError, "ollama_backend_plan_mismatch"):
            self.backend.bind_metadata(metadata)
        self.assertFalse(self.fake.chat)

    def test_mlx_partial_cancel_resume_preserves_poor_observations(self):
        self.fake.details = {"format": "safetensors", "quantization_level": "nvfp4", "runner": "mlx"}
        self.fake.show_details = dict(self.fake.details)
        self.fake.thinking = {"values": [False], "default": False}
        plan, grant = self.plan(contrast=False, max_requests=9)
        self.manager.start(plan["plan_id"], grant, max_cells=2)
        self.manager.cancel(plan["plan_id"])
        self.assertEqual(self.manager.start(plan["plan_id"], grant)["status"], "cancelled")
        self.assertEqual(len(self.fake.chat), 2)
        result = self.manager.resume(plan["plan_id"], grant)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.fake.chat), 8)
        bio = self.manager.bio("local-test")
        self.assertTrue(bio["configurations"][0]["discoverable"])
        self.assertTrue(bio["configurations"][0]["weaknesses"])
        self.assertEqual(bio["backend_snapshot"]["artifact"]["catalog_details"]["format"], "safetensors")

    def test_named_native_levels_and_fixed_false_are_not_invented(self):
        self.fake.thinking = {"values": ["minimal", "balanced"], "default": "balanced"}
        metadata = self.backend.metadata("named")
        self.assertEqual([x["think"] for x in metadata["model"]["reasoning"]["native_configurations"]], ["minimal", "balanced"])
        self.fake.thinking = {"values": [False], "default": False}
        self.assertEqual(self.backend.metadata("fixed")["model"]["reasoning"]["semantics"], "fixed")

    def test_explicit_loopback_only_no_cloud_or_credentials(self):
        for endpoint in ("http://localhost:11434", "http://127.0.0.1", "https://127.0.0.1:443", "http://192.168.1.2:11434",
                         "http://u:p@127.0.0.1:11434", "http://127.0.0.1:11434/?secret=x", "http://127.1:11434"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValidationError):
                OllamaBackend(endpoint, "model:1b")
        with self.assertRaises(ValidationError):
            OllamaBackend("http://127.0.0.1:11434", "model:cloud")
        self.fake.override["/api/tags"] = {"models": [{"name": self.fake.model, "digest": "a"*64, "size": 100,
                                                        "details": {"format": "gguf"}, "remote_host": "https://cloud.example"}]}
        with self.assertRaises(ValidationError):
            self.plan()
        self.assertFalse(self.fake.chat)

    def test_public_payload_excludes_gold_and_thinking_is_not_retained(self):
        request = self.request()
        poisoned_case = replace(CASES[0], expected_json='{"GOLD_CANARY":"SCORER_CANARY"}')
        response = self.backend.complete(request, poisoned_case, deadline=time.monotonic()+5)
        payload = self.fake.chat[-1]
        self.assertNotIn("CANARY", canonical(payload))
        self.assertEqual(payload["options"], {"num_ctx": 4096, "num_predict": 512})
        self.assertIs(payload["think"], True)
        self.assertIs(payload["stream"], False)
        self.assertEqual(response.native_confirmation, "request_only_not_echoed")
        plan, grant = self.plan()
        self.manager.start(plan["plan_id"], grant, max_cells=1)
        evidence = self.manager.evidence(plan["plan_id"])
        summary = evidence["attempts"][0]["summary_json"]
        self.assertEqual(summary["usage"]["input_tokens"], 33)
        self.assertEqual(summary["usage"]["output_tokens"], 9)
        self.assertIsNone(summary["usage"]["reasoning_tokens"])
        self.assertIsNone(summary["usage"]["billed_cost_usd"])
        self.assertFalse(summary["effective_native_settings_known"])
        self.assertEqual(summary["backend_metrics_ns"]["total_duration"], 1000000)
        self.assertNotIn("CANARY", "\n".join(self.inventory.db.iterdump()))

    def test_operational_bounds_change_configuration_identity(self):
        first = self.manager.research("local-test")
        with Inventory(":memory:") as inventory:
            other = ProfileManager(inventory, OllamaBackend("http://127.0.0.1:11434", self.fake.model,
                                                           output_cap=256, transport=FakeTransport())).research("local-test")
        self.assertNotEqual(first["configuration_ids"], other["configuration_ids"])

    def test_nondefault_baseline_is_explicit_and_research_default_unchanged(self):
        metadata = self.manager.research("local-test")
        chosen = next(c for c in metadata["configuration_ids"] if self.inventory.configuration(c).native["think"] is False)
        plan, grant = self.plan(baseline_configuration_id=chosen)
        self.assertEqual(plan["configuration_ids"][0], chosen)
        self.assertEqual(plan["max_output_tokens"], 512)
        self.manager.start(plan["plan_id"], grant, max_cells=1)
        self.assertIs(self.fake.chat[0]["think"], False)
        self.assertIs(self.manager.bio("local-test")["documented_capabilities"]["default_native"]["think"], True)
        with self.assertRaises(ValidationError):
            self.plan(baseline_configuration_id="unresearched")

    def test_drift_and_exact_response_identity_fail_without_fallback(self):
        plan, grant = self.plan()
        self.fake.revision = "b"*64
        status = self.manager.start(plan["plan_id"], grant, max_cells=1)
        self.assertEqual(status["cells"]["technical_error"], 1)
        self.assertFalse(self.fake.chat)
        self.fake.revision = "a"*64
        self.fake.response = {"model": "fallback-model", "done": True, "done_reason": "stop",
                              "message": {"role": "assistant", "content": "{}"}}
        self.manager.resume(plan["plan_id"], grant, max_cells=1)
        self.assertEqual(self.manager.evidence(plan["plan_id"])["attempts"][-1]["summary_json"]["error_code"], "effective_identity_mismatch")
        self.assertEqual(len(self.fake.chat), 1)

    def test_native_tool_is_structured_and_recovery_passes_tool_error(self):
        case = next(c for c in CASES if c.id == "recovery.native")
        request = self.request(case)
        self.fake.response = {"model": self.fake.model, "done": True, "done_reason": "stop",
                              "message": {"role": "assistant", "content": "", "thinking": "PRIVATE_REASONING",
                                          "tool_calls": [{"function": {"name": "fixture_lookup", "arguments": {"key": "missing"}}}]}}
        response = self.backend.complete(request, case, deadline=time.monotonic()+5)
        self.assertEqual(response.finish_reason, "tool_calls")
        self.assertEqual(len(response.native_calls), 1)
        self.backend.complete(request, case, deadline=time.monotonic()+5,
                              tool_feedback={"error": "invalid_fixture_key", "required_key": "alpha"})
        messages = self.fake.chat[-1]["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-2]["tool_calls"][0]["function"]["arguments"], {"key": "missing"})
        self.assertEqual(messages[-2]["thinking"], "PRIVATE_REASONING")
        self.assertIsNone(self.backend._previous)
        self.assertNotIn("PRIVATE_REASONING", "\n".join(self.inventory.db.iterdump()))
        self.assertIn("first call fixture_lookup with key missing", messages[0]["content"])

    def test_rate_limit_unknown_and_truncation_remain_distinct(self):
        from gcb.cli import main
        for error in (RateLimited(12), UnknownCompletion("PRIVATE_ERROR_CANARY")):
            output=io.StringIO()
            with patch("gcb.cli._run",side_effect=error), redirect_stderr(output):
                self.assertEqual(main(["doctor"]),2)
            self.assertNotIn("CANARY",output.getvalue())
        for response, expected in ((RateLimited(30), "rate_limited"), (UnknownCompletion(), "partial_unknown"),
                                   ({"model": self.fake.model, "done": False}, "partial_unknown"),
                                   ({"model": self.fake.model, "done": True, "done_reason": "length", "message": {"role": "assistant", "content": "{}"}}, "paused")):
            with self.subTest(expected=expected), Inventory(":memory:") as inventory:
                fake = FakeTransport(); fake.response = response
                manager = ProfileManager(inventory, OllamaBackend("http://127.0.0.1:11434", fake.model, transport=fake))
                plan = manager.plan("test"); grant = manager.authorize(plan["plan_id"])
                result = manager.start(plan["plan_id"], grant, max_cells=1)
                self.assertEqual(result["status"], expected)
                if expected == "partial_unknown":
                    manager.resume(plan["plan_id"], grant)
                    self.assertEqual(len(fake.chat), 1)
                if expected == "paused":
                    self.assertEqual(result["cells"]["resource_limited"], 1)

    def test_partial_resume_poor_bio_and_preview_use_real_adapter_path(self):
        plan, grant = self.plan(contrast=False)
        self.manager.start(plan["plan_id"], grant, max_cells=2)
        self.assertEqual(self.manager.bio("local-test")["evidence_kind"], "transport_fixture")
        count = len(self.fake.chat)
        self.manager.cancel(plan["plan_id"])
        self.manager.start(plan["plan_id"], grant)
        self.assertEqual(len(self.fake.chat), count)
        result = self.manager.resume(plan["plan_id"], grant)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.fake.chat), 8)
        bio = self.manager.bio("local-test")
        self.assertTrue(bio["configurations"][0]["discoverable"])
        self.assertTrue(any(c["weaknesses"] for c in bio["configurations"]))
        preview = preview_route(self.inventory, TaskRequirements(category="coding"), RoutingPolicy())
        self.assertEqual(preview.enforcement, "preview_only")
        self.assertIsNotNone(preview.selected_configuration_id)

    def test_loopback_transport_runs_http_and_ignores_proxy_environment(self):
        def callback(method, path, body):
            return 200, {"Content-Type": "application/json"}, canonical(self.fake(method, path, body, timeout=5)).encode()
        with local_server(callback) as endpoint:
            with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1"}):
                backend = OllamaBackend(endpoint, self.fake.model, transport=LoopbackTransport(endpoint))
                manager = ProfileManager(self.inventory, backend)
                plan = manager.plan("http-fixture")
                result = manager.start(plan["plan_id"], manager.authorize(plan["plan_id"]), max_cells=1)
                self.assertEqual(result["cells"]["observed"], 1)
                self.assertEqual(manager.bio("http-fixture")["evidence_kind"], "transport_fixture")

    def test_http_redirect_rate_limit_invalid_json_and_oversize_are_bounded(self):
        for status, headers, data, error in ((302, {"Location": "https://example.invalid"}, b"", ValidationError),
             (429, {"Retry-After": "17"}, b"RAW_ERROR_CANARY", RateLimited),
             (200, {}, b'{"a":1,"a":2}', ValidationError), (200, {}, b"x"*1048577, ValidationError),
             (500, {}, b"RAW_ERROR_CANARY", ValidationError)):
            with self.subTest(status=status, size=len(data)), local_server(lambda *_: (status, headers, data)) as endpoint:
                with self.assertRaises(error) as caught:
                    LoopbackTransport(endpoint)("GET", "/api/version", None, timeout=1)
                self.assertNotIn("CANARY", str(caught.exception))
                if isinstance(caught.exception, RateLimited):
                    self.assertEqual(caught.exception.retry_after, 17)
