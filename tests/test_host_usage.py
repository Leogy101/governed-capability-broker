"""Offline host-normalized telemetry; no host, account, or provider is contacted."""
from dataclasses import replace
import json
import threading
import time
import unittest
from unittest.mock import patch

from gcb import Inventory
from gcb.cases import CASES
from gcb.profile_backend import FictionalBackend, ProfileResponse, UnknownCompletion
from gcb.profiler import ProfileManager
from gcb.profiling import ProbeRequest
from gcb.validation import ValidationError, canonical


def telemetry(**changes):
    return {"provenance": "openclaw_normalized", "uncached_input_tokens": 50,
            "cache_read_tokens": 100, "cache_write_tokens": 0,
            "reported_total_tokens": 170, "reported_cost_usd_unclassified": 0.002,
            "provider_request_count": None, "coverage": "partial",
            "finish_reason_known": False, **changes}


class HostFixture(FictionalBackend):
    def __init__(self):
        super().__init__()
        self.wrong = False
        self.fail_after = None
        self.telemetry = telemetry()

    def metadata(self, model_id):
        result = super().metadata(model_id)
        result["evidence_kind"] = "transport_fixture"
        result["model"]["native_tools"] = False
        result["model"]["features"] = []
        return result

    def complete(self, request, case, *, deadline, tool_feedback=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise UnknownCompletion("fixture_timeout")
        parts = [self.telemetry[key] for key in ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens")]
        return ProfileResponse(request.provider_id, request.backend_id, request.native_model_id,
                               request.native_json, output='{"wrong":true}' if self.wrong else case.expected_json,
                               input_tokens=sum(parts) if all(value is not None for value in parts) else None,
                               output_tokens=20, evidence_kind="transport_fixture",
                               native_confirmation="request_only_not_echoed", host_usage_json=canonical(self.telemetry))


class RemoteHostFixture(HostFixture):
    def metadata(self, model_id):
        result = super().metadata(model_id)
        result["provider"]["id"] = "openai-host"
        result["backend"].update(provider_id="openai-host", locality="remote", api_family="openclaw-isolated-codex")
        return result


class HostUsageTests(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory(":memory:")
        self.addCleanup(self.inventory.close)
        self.backend = HostFixture()
        self.manager = ProfileManager(self.inventory, self.backend)

    def run_cells(self, count=1):
        plan = self.manager.plan("forge", contrast=False)
        self.manager.start(plan["plan_id"], self.manager.authorize(plan["plan_id"]), max_cells=count)
        return plan

    def response(self, **changes):
        self.manager.research("forge")
        config = self.inventory.configurations()[0]
        request = ProbeRequest(config.id, "fictional", "fictional-local-v1", "forge-v1",
                               config.native_json, CASES[0].id, 256)
        response = ProfileResponse(request.provider_id, request.backend_id, request.native_model_id,
                                   request.native_json, input_tokens=150, output_tokens=20,
                                   evidence_kind="host_runtime", native_confirmation="request_only_not_echoed",
                                   host_usage_json=canonical(telemetry()))
        return request, replace(response, **changes)

    def summary(self, **changes):
        request, response = self.response(**changes)
        return self.manager._response(response, request, 0.1, "host_runtime")

    def test_cache_is_part_of_input_and_not_added_to_reported_total(self):
        summary = self.summary()
        self.assertEqual(summary["usage"]["input_tokens"], 150)
        self.assertEqual(summary["host_usage"]["reported_total_tokens"], 170)
        self.assertEqual(summary["host_usage"]["cache_read_tokens"], 100)
        self.assertIsNone(summary["usage"]["billed_cost_usd"])
        self.assertIsNone(summary["usage"]["estimated_cost_usd"])
        self.assertEqual(summary["host_usage"]["reported_cost_usd_unclassified"], 0.002)
        self.assertIsNone(summary["host_usage"]["provider_request_count"])
        self.assertFalse(summary["effective_native_settings_known"])
        self.assertFalse(summary["host_usage"]["finish_reason_known"])

    def test_missing_cache_stays_unknown_while_reported_total_survives(self):
        summary = self.summary(input_tokens=None, host_usage_json=canonical(telemetry(cache_write_tokens=None)))
        self.assertIsNone(summary["usage"]["input_tokens"])
        self.assertEqual(summary["host_usage"]["reported_total_tokens"], 170)
        with self.assertRaisesRegex(ValidationError, "host_prompt_input_attribution_mismatch"):
            self.summary(input_tokens=150, host_usage_json=canonical(telemetry(cache_write_tokens=None)))

    def test_missing_all_usage_is_unknown_not_zero(self):
        empty = telemetry(**{key: None for key in ("uncached_input_tokens", "cache_read_tokens",
                          "cache_write_tokens", "reported_total_tokens", "reported_cost_usd_unclassified")},
                          coverage="unknown")
        summary = self.summary(input_tokens=None, output_tokens=None, host_usage_json=canonical(empty))
        self.assertTrue(all(value is None for value in summary["usage"].values()))
        self.assertEqual(summary["host_usage"]["coverage"], "unknown")

    def test_no_double_count_or_invented_billing_reasoning_and_request_count(self):
        for change in ({"input_tokens": 250}, {"input_tokens": 50}, {"billed_cost_usd": 0},
                       {"estimated_cost_usd": 0.002}, {"reasoning_tokens": 0},
                       {"native_confirmation": "fixture_echo"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.summary(**change)
        for change in ({"provider_request_count": 1}, {"coverage": "complete"},
                       {"coverage": "unknown"}, {"finish_reason_known": True},
                       {"cache_read_tokens": True}, {"reported_total_tokens": -1},
                       {"provenance": "provider_billed"}, {"secret": "CANARY"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.summary(host_usage_json=canonical(telemetry(**change)))

    def test_host_evidence_requires_closed_telemetry(self):
        for value in ("{}", "[]", '"CANARY"', "null", "not json"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.summary(host_usage_json=value)
        self.assertNotIn("CANARY", "\n".join(self.inventory.db.iterdump()))

    def test_multiple_requests_aggregate_actual_categories_without_cache_duplication(self):
        self.run_cells(2)
        bio = self.manager.bio("forge")["configurations"][0]
        self.assertEqual(bio["attempt_count"], 2)
        self.assertEqual(bio["usage"]["input_tokens"], 300)
        self.assertEqual(bio["usage"]["output_tokens"], 40)
        self.assertEqual(bio["host_usage"]["reported_total_tokens"], 340)
        self.assertEqual(bio["host_usage"]["cache_read_tokens"], 200)
        self.assertIsNone(bio["host_usage"]["provider_request_count"])
        self.assertEqual(bio["host_usage"]["coverage"], "partial")

    def test_uncertain_attempt_makes_aggregate_unknown_but_preserves_observed_usage(self):
        self.backend.fail_after = 1
        plan = self.run_cells(2)
        evidence = self.manager.evidence(plan["plan_id"])
        self.assertEqual([row["status"] for row in evidence["attempts"]], ["completed", "unknown_completion"])
        self.assertEqual(evidence["attempts"][0]["summary_json"]["host_usage"]["reported_total_tokens"], 170)
        bio = self.manager.bio("forge")["configurations"][0]
        self.assertIsNone(bio["usage"]["input_tokens"])
        self.assertIsNone(bio["host_usage"]["reported_total_tokens"])
        self.assertEqual(bio["host_usage"]["profile_attempt_count"], 2)
        self.assertEqual(bio["host_usage"]["attempts_with_host_usage"], 1)
        self.assertEqual(self.manager.resume(plan["plan_id"], self.manager.authorize(plan["plan_id"]))["status"], "partial_unknown")
        self.assertEqual(self.backend.calls, 2)

    def test_weak_response_still_produces_descriptive_discoverable_bio(self):
        self.backend.wrong = True
        self.run_cells(1)
        bio = self.manager.bio("forge")["configurations"][0]
        self.assertTrue(bio["discoverable"])
        self.assertEqual(bio["categories"]["coding"]["observed"], 1)
        self.assertEqual(bio["categories"]["coding"]["correct"], 0)
        self.assertEqual(bio["categories"]["coding"]["technical_errors"], 0)
        self.assertTrue(bio["weaknesses"])
        self.assertEqual(bio["host_usage"]["reported_total_tokens"], 170)

    def test_remote_host_is_read_only_without_explicit_opt_in(self):
        self.backend = RemoteHostFixture()
        manager = ProfileManager(self.inventory, self.backend)
        plan = manager.plan("forge", contrast=False)
        grant = manager.authorize(plan["plan_id"])
        with self.assertRaisesRegex(ValidationError, "profile_target_not_eligible"):
            manager.start(plan["plan_id"], grant, max_cells=1)
        self.assertEqual(self.backend.calls, 0)
        allowed = ProfileManager(self.inventory, self.backend, allow_host_runtime=True)
        allowed.start(plan["plan_id"], grant, max_cells=1)
        self.assertEqual(self.backend.calls, 1)

    def test_host_opt_in_is_not_generic_remote_authority(self):
        for change in ({"provider_id": "other-provider"}, {"api_family": "openai-direct"},
                       {"locality": "unknown"}, {"authorization": "denied"}):
            with self.subTest(change=change), Inventory(":memory:") as inventory:
                backend = RemoteHostFixture()
                original = backend.metadata
                def metadata(model_id):
                    body = original(model_id)
                    body["backend"].update(change)
                    body["provider"]["id"] = body["backend"]["provider_id"]
                    return body
                backend.metadata = metadata
                manager = ProfileManager(inventory, backend, allow_host_runtime=True)
                plan = manager.plan("forge", contrast=False)
                with self.assertRaises(ValidationError):
                    manager.start(plan["plan_id"], manager.authorize(plan["plan_id"]), max_cells=1)
                self.assertEqual(backend.calls, 0)

    def test_host_opt_in_still_requires_plan_grant(self):
        backend = RemoteHostFixture()
        manager = ProfileManager(self.inventory, backend, allow_host_runtime=True)
        plan = manager.plan("forge", contrast=False)
        with self.assertRaises(ValidationError):
            manager.start(plan["plan_id"], None, max_cells=1)
        self.assertEqual(backend.calls, 0)
        with self.assertRaises(ValidationError):
            ProfileManager(self.inventory, backend, allow_host_runtime=1)

    def test_deadline_and_cancel_signal_only_host_worker_and_keep_unknown(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), Inventory(":memory:") as inventory:
                backend = RemoteHostFixture()
                event = threading.Event()
                finished = threading.Event()
                def complete(*args, **kwargs):
                    try:
                        event.wait(1)
                        raise UnknownCompletion("cancelled_fixture")
                    finally:
                        finished.set()
                backend.complete = complete
                backend.cancel = event.set
                manager = ProfileManager(inventory, backend, allow_host_runtime=True)
                plan = manager.plan("forge", contrast=False)
                config = inventory.configuration(plan["configuration_ids"][0])
                request = ProbeRequest(config.id, "openai-host", "fictional-local-v1", "forge-v1",
                                       config.native_json, CASES[0].id, 256)
                if cancelled:
                    manager.cancel(plan["plan_id"])
                with self.assertRaises(UnknownCompletion):
                    manager._call(request, CASES[0], 0.02, plan["plan_id"], None, "test-worker")
                self.assertTrue(event.is_set())
                self.assertTrue(finished.wait(1))
                # Wait for the owned worker's final release, not a provider retry.
                from gcb import profiler
                for _ in range(100):
                    with profiler._SLOT_LOCK:
                        pending = bool(profiler._INFLIGHT)
                    if not pending:
                        break
                    time.sleep(0.001)
                self.assertFalse(pending)

    def test_local_backend_keeps_existing_cancellation_behavior(self):
        self.manager.research("forge")
        config = self.inventory.configurations()[0]
        request = ProbeRequest(config.id, "fictional", "fictional-local-v1", "forge-v1",
                               config.native_json, CASES[0].id, 256)
        with patch.object(self.backend, "cancel", create=True) as cancel:
            ProfileManager(self.inventory, self.backend, allow_host_runtime=True)._cancel_host_call(request)
            cancel.assert_not_called()

    def test_keyboard_interrupt_joins_cancel_cleanup_without_claiming_native_completion(self):
        backend = RemoteHostFixture()
        cancel_requested = threading.Event()
        cleanup_finished = threading.Event()
        workers = []
        cancellation_calls = []

        def complete(*args, **kwargs):
            backend.calls += 1
            workers.append(threading.current_thread())
            try:
                cancel_requested.wait(1)
                # The cancellation RPC cleanup finishes after the signal. Its
                # completion does not establish native inference termination.
                time.sleep(0.02)
                raise UnknownCompletion("native_termination_unconfirmed")
            finally:
                cleanup_finished.set()

        def cancel():
            cancellation_calls.append("requested_only")
            cancel_requested.set()

        backend.complete = complete
        backend.cancel = cancel
        manager = ProfileManager(self.inventory, backend, allow_host_runtime=True)
        plan = manager.plan("forge", contrast=False)
        grant = manager.authorize(plan["plan_id"])
        with patch("gcb.profiler.queue.Queue.get", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.start(plan["plan_id"], grant, max_cells=1)
        self.assertTrue(cancellation_calls)
        self.assertEqual(set(cancellation_calls), {"requested_only"})
        self.assertTrue(cleanup_finished.is_set())
        self.assertEqual(len(workers), 1)
        self.assertFalse(workers[0].is_alive())
        evidence = manager.evidence(plan["plan_id"])
        self.assertEqual(len(evidence["attempts"]), 1)
        self.assertEqual(evidence["attempts"][0]["status"], "unknown_completion")
        self.assertEqual(evidence["attempts"][0]["summary_json"], {"error_code": "interrupted"})
        self.assertFalse(any(cell["status"] == "observed" for cell in evidence["cells"]))
        self.assertTrue(all(value is None for value in manager.bio("forge")["configurations"][0]["usage"].values()))
        self.assertEqual(manager.resume(plan["plan_id"], grant)["status"], "partial_unknown")
        self.assertEqual(backend.calls, 1)


if __name__ == "__main__":
    unittest.main()
