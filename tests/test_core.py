import dataclasses
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gcb import Backend, Inventory, Model, Provider, ResearchFact, SourceCitation
from gcb.demo import seed_demo
from gcb.models import RoutingPolicy, TaskRequirements
from gcb.profiling import ProbeRequest, SyntheticBackend, prepare_probe
from gcb.routing import preview_route
from gcb.validation import ValidationError, canonical, native_settings


SOURCE = SourceCitation("https://example.org/model-spec", "Synthetic test citation", "2026-11-14T00:00:00Z")


def research_model(inventory, model_id, native=None, *, verification="documented", context=4096):
    values = {"context_window":context, "max_output":512, "modalities":["text"], "native_tools":False,
              "reasoning":{"semantics":"effort", "native_configurations":native or [{"effort":"low"}]}}
    for property, value in values.items():
        inventory.add_research(ResearchFact.create(model_id, property, value, verification, (SOURCE,)))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.addCleanup(self.inventory.close)
        self.inventory.add_provider(Provider("p", "Fixture"))
        self.inventory.add_backend(Backend("b", "p", "fixture", "1", "local", "allowed", "ready"))
        self.inventory.add_model(Model("m", "b", "native-model", "1"))

    def test_research_precedes_configuration(self):
        with self.assertRaisesRegex(ValidationError, "research_required"):
            self.inventory.add_configuration("m", {})
        research_model(self.inventory, "m")
        self.assertEqual(self.inventory.add_configuration("m", {"effort":"low"}).native, {"effort":"low"})

    def test_identity_is_immutable_and_references_exist(self):
        self.inventory.add_provider(Provider("p", "Fixture"))
        with self.assertRaises(ValidationError):
            self.inventory.add_provider(Provider("p", "Changed"))
        with self.assertRaises(ValidationError):
            self.inventory.add_model(Model("orphan", "missing", "native"))
        with self.assertRaises(ValidationError):
            self.inventory.add_model(Model("duplicate", "b", "native-model", "1"))

    def test_configuration_json_is_defensive_and_exact(self):
        native = {"think":True}
        research_model(self.inventory, "m", [native])
        config = self.inventory.add_configuration("m", native)
        native["think"] = False
        self.assertEqual(config.native, {"think":True})
        copied = config.native
        copied["think"] = False
        self.assertEqual(config.native, {"think":True})
        with self.assertRaises(ValidationError):
            self.inventory.add_configuration("m", {"think":1})

    def test_backend_specific_values_and_identity(self):
        research_model(self.inventory, "m", [{"effort":"off"}])
        first = self.inventory.add_configuration("m", {"effort":"off"})
        self.inventory.add_backend(Backend("b2", "p", "other-wire", "1", "local", "allowed", "ready"))
        self.inventory.add_model(Model("m2", "b2", "native-model", "1"))
        research_model(self.inventory, "m2", [{"thinking":{"enabled":True,"budget_tokens":128}}])
        second = self.inventory.add_configuration("m2", {"thinking":{"enabled":True,"budget_tokens":128}})
        self.assertNotEqual(first.id, second.id)
        with self.assertRaises(ValidationError):
            self.inventory.add_configuration("m2", {"effort":"off"})

    def test_configuration_digest_binds_immutable_backend_identity(self):
        research_model(self.inventory,"m")
        first=self.inventory.add_configuration("m",{"effort":"low"})
        self.inventory.set_backend_state("b",authorization="denied",availability="unavailable")
        self.assertEqual(self.inventory.add_configuration("m",{"effort":"low"}).id,first.id)
        with Inventory() as other:
            other.add_provider(Provider("p","Fixture"))
            other.add_backend(Backend("b","p","responses","2","remote","allowed","ready",endpoint_ref="deployment-2"))
            other.add_model(Model("m","b","native-model","1"))
            research_model(other,"m")
            self.assertNotEqual(other.add_configuration("m",{"effort":"low"}).id,first.id)

    def test_immutable_conflicting_facts_block_existing_configuration(self):
        research_model(self.inventory, "m")
        config = self.inventory.add_configuration("m", {"effort":"low"})
        old = tuple(fact.id for fact in self.inventory.research("m"))
        self.inventory.add_research(ResearchFact.create("m", "context_window", 2048, "verified", (SOURCE,)))
        self.assertTrue(set(old) <= {fact.id for fact in self.inventory.research("m")})
        self.assertEqual(self.inventory.research_summary("m")["context_window"].status, "conflict")
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy())
        self.assertEqual(result.status, "no_eligible")
        self.assertIn("research_context_window_conflict", result.candidates[0].reasons)
        self.assertEqual(self.inventory.configuration(config.id), config)

    def test_unverified_and_unknown_are_preserved_but_not_eligible(self):
        research_model(self.inventory, "m", verification="unverified")
        self.assertTrue(self.inventory.research("m"))
        self.assertEqual(self.inventory.research_summary("m")["reasoning"].status, "unknown")
        with self.assertRaises(ValidationError):
            self.inventory.add_configuration("m", {"effort":"low"})

    def test_documented_null_is_unknown(self):
        research_model(self.inventory, "m", context=None)
        self.assertEqual(self.inventory.research_summary("m")["context_window"].status, "unknown")
        with self.assertRaises(ValidationError):
            self.inventory.add_configuration("m", {"effort":"low"})
        self.assertEqual(preview_route(self.inventory, TaskRequirements(), RoutingPolicy()).status, "no_eligible")

    def test_malformed_nested_modalities_are_validation_errors(self):
        for value in ([[]], [{}], [True], ["text", "text"]):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ResearchFact.create("m", "modalities", value, "documented", (SOURCE,))

    def test_citations_required_and_secret_urls_rejected(self):
        with self.assertRaises(ValidationError):
            ResearchFact.create("m", "max_output", 1, "verified", ())
        for url in ("https://u:secret@example.org/x", "https://example.org/x?api_key=secret", "file:///private/file"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                SourceCitation(url, "Fixture", "2026-11-14T00:00:00Z")

    def test_native_reserved_keys_and_deep_inputs(self):
        for payload in ({"model":"overridden"}, {"reasoning":{"headers":{"x":"secret"}}},
                        {"case_id":"other"}, {"api_key":"secret"}, {"input":"prompt"}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                native_settings(payload)
        value = {}
        for _ in range(100):
            value = {"x":value}
        with self.assertRaises(ValidationError):
            native_settings(value)

    def test_reserved_native_aliases_preserve_safe_native_spelling(self):
        for key in ("apiKey","api-key","baseUrl","maxOutputTokens","authToken","Max.Completion.Tokens"):
            with self.subTest(key=key),self.assertRaises(ValidationError):
                native_settings({key:"CANARY"})
        self.assertEqual(native_settings({"thinkingBudget":128}),'{"thinkingBudget":128}')

    def test_reopen_preserves_facts_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"inventory.db"
            with Inventory(path) as inventory:
                original = seed_demo(inventory)
            with Inventory(path) as inventory:
                self.assertEqual({config.id for config in original}, {config.id for config in inventory.configurations()})
                self.assertEqual(len(inventory.research("fixture")), 5)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unrelated_and_future_database_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"other.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE unrelated(x)")
            with self.assertRaises(ValidationError):
                Inventory(path)

    def test_foreign_version_one_and_incomplete_owned_schema_rejected_before_write(self):
        from gcb.inventory import APPLICATION_ID
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"foreign.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA user_version=1")
                db.execute("CREATE TABLE providers(id TEXT PRIMARY KEY,name TEXT NOT NULL)")
            with self.assertRaises(ValidationError):
                Inventory(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM providers").fetchone()[0],0)
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            with self.assertRaises(ValidationError):
                Inventory(path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("PRAGMA user_version=99")
            with self.assertRaises(ValidationError):
                Inventory(path)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.addCleanup(self.inventory.close)
        self.configs = seed_demo(self.inventory)

    def test_stable_selection_and_no_quality_claim(self):
        first = preview_route(self.inventory, TaskRequirements(category="coding"), RoutingPolicy())
        for _ in range(3):
            self.assertEqual(preview_route(self.inventory, TaskRequirements(category="coding"), RoutingPolicy()), first)
        self.assertEqual(first.selected_configuration_id, min(config.id for config in self.configs))
        self.assertEqual(first.enforcement, "preview_only")
        self.assertIn("no_quality_inference", first.ranking)

    def test_hard_requirements_have_explicit_reasons(self):
        result = preview_route(self.inventory, TaskRequirements(context_tokens=4096, output_tokens=513,
                                                                modalities=("image",), require_tools=True), RoutingPolicy())
        self.assertEqual(result.status, "no_eligible")
        self.assertTrue({"context_capacity_exceeded", "output_capacity_exceeded", "required_modality_not_researched",
                         "native_tools_not_researched"} <= set(result.candidates[0].reasons))

    def test_empty_allowlist_denies_everything(self):
        for field in ("allowed_providers", "allowed_backends", "allowed_configurations"):
            result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy(**{field:()}))
            self.assertEqual(result.status, "no_eligible")

    def test_revoked_and_unavailable_backend_retained(self):
        self.inventory.set_backend_state("synthetic-local-v1", authorization="denied", availability="unavailable")
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy())
        self.assertEqual(result.status, "no_eligible")
        self.assertTrue({"backend_not_authorized", "backend_not_ready"} <= set(result.candidates[0].reasons))
        self.assertEqual(len(self.inventory.models()), 1)
        self.assertEqual(len(self.inventory.configurations()), 2)

    def test_synthetic_research_cannot_satisfy_verified_policy(self):
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy(require_verified_research=True))
        self.assertEqual(result.status, "no_eligible")
        self.assertIn("research_reasoning_not_verified", result.candidates[0].reasons)

    def test_remote_policy_and_task_constraints_are_independent(self):
        self.inventory.add_provider(Provider("remote", "Fixture remote"))
        self.inventory.add_backend(Backend("remote-b", "remote", "fixture", "1", "remote", "allowed", "ready"))
        self.inventory.add_model(Model("remote-m", "remote-b", "fixture-model", "1"))
        research_model(self.inventory, "remote-m")
        config = self.inventory.add_configuration("remote-m", {"effort":"low"})
        policy = RoutingPolicy(allowed_configurations=(config.id,))
        self.assertEqual(preview_route(self.inventory, TaskRequirements(), policy).status, "no_eligible")
        policy = dataclasses.replace(policy, allow_remote=True)
        self.assertEqual(preview_route(self.inventory, TaskRequirements(), policy).selected_configuration_id, config.id)
        self.assertEqual(preview_route(self.inventory, TaskRequirements(local_only=True), policy).status, "no_eligible")

    def test_gate_precedes_inventory_and_denied_gate_never_falls_through(self):
        self.inventory.set_backend_state("synthetic-local-v1", authorization="denied", availability="unknown")
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy(), operation="math.calculate",
                               arguments={"operator":"add", "operands":[1,2]})
        self.assertEqual(result.status, "gate0")
        self.assertFalse(result.candidates)
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy(), operation="math.calculate",
                               arguments={"operator":"divide", "operands":[1,0]})
        self.assertEqual(result.status, "denied")
        self.assertFalse(result.candidates)

    def test_unknown_operation_declines_and_remains_preview(self):
        result = preview_route(self.inventory, TaskRequirements(), RoutingPolicy(), operation="unknown.operation", arguments={})
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.gate0.reason, "unsupported_operation")

    def test_typed_inputs_reject_bool_numeric_and_text_flags(self):
        for kwargs in ({"context_tokens":True}, {"output_tokens":0}, {"local_only":"false"}, {"modalities":("text", "text")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                TaskRequirements(**kwargs)
        with self.assertRaises(ValidationError):
            RoutingPolicy(allow_remote="yes")
        with self.assertRaises(ValidationError):
            preview_route(self.inventory, {}, RoutingPolicy())


class ProfilerFoundationTests(unittest.TestCase):
    def setUp(self):
        self.inventory = Inventory()
        self.addCleanup(self.inventory.close)
        self.config = seed_demo(self.inventory)[0]

    def test_exact_synthetic_round_trip_without_invented_metrics(self):
        request = prepare_probe(self.inventory, self.config.id, case_id="synthetic.identity")
        result = SyntheticBackend().complete(request)
        self.assertEqual(result.effective_native_json, self.config.native_json)
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.output_tokens)
        self.assertIsNone(result.estimated_cost_usd)
        self.assertIsNone(result.billed_cost_usd)
        self.assertIn("not_model_measurement", result.evidence_kind)

    def test_reserved_native_payload_regression(self):
        for native in ({"model":"unauthorized"}, {"case_id":"unauthorized"}, {"max_output_tokens":1000000},
                       {"reasoning":{"authorization":"secret"}}):
            with self.subTest(native=native), self.assertRaises(ValidationError):
                ProbeRequest(self.config.id, "synthetic", "synthetic-local-v1", "fixture-model", canonical(native),
                             "synthetic.identity", 64)

    def test_probe_request_keeps_identity_and_limits_last(self):
        request = prepare_probe(self.inventory, self.config.id, case_id="synthetic.identity", max_output_tokens=30)
        self.assertEqual(request.payload()["model"], "fixture-model")
        self.assertEqual(request.payload()["max_output_tokens"], 30)
        self.assertEqual(request.payload()["reasoning_effort"], "low")

    def test_ineligible_or_conflicting_research_prevents_preparation(self):
        self.inventory.set_backend_state("synthetic-local-v1", authorization="denied", availability="ready")
        with self.assertRaises(ValidationError):
            prepare_probe(self.inventory, self.config.id, case_id="synthetic.identity")

    def test_synthetic_backend_rejects_foreign_case_or_identity(self):
        request = prepare_probe(self.inventory, self.config.id, case_id="synthetic.identity")
        for replacement in ({"case_id":"user.prompt"}, {"backend_id":"remote"}, {"native_model_id":"real-model"}):
            with self.subTest(replacement=replacement), self.assertRaises(ValidationError):
                SyntheticBackend().complete(dataclasses.replace(request, **replacement))


if __name__ == "__main__":
    unittest.main()
