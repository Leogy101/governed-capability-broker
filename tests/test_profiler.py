from contextlib import closing
import contextlib
from dataclasses import replace
import json
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import gcb
from gcb import Inventory, RoutingPolicy, TaskRequirements, preview_route
from gcb.cases import CASES
from gcb.cli import main
from gcb.profile_backend import FictionalBackend, NativeToolCall, ProfileResponse, RateLimited, UnknownCompletion
from gcb.profiler import ProfileManager, run_profile_demo
from gcb.validation import ValidationError, canonical


class ProfilerTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/"profile.db"
        self.inventory=Inventory(self.path)
        self.addCleanup(self.inventory.close)
        self.backend=FictionalBackend()
        self.manager=ProfileManager(self.inventory,self.backend)

    def plan(self,model="forge",**kwargs):
        plan=self.manager.plan(model,**kwargs)
        return plan,self.manager.authorize(plan["plan_id"])

    def test_nonposix_worker_fails_closed_without_process_signal(self):
        from gcb.profiler import _owner_alive
        plan,grant=self.plan()
        with patch("gcb.profiler.os.name","nt"), patch("gcb.profiler.os.kill") as kill:
            self.assertTrue(_owner_alive("12345:owner"))
            with self.assertRaisesRegex(ValidationError,"profiling_requires_posix_worker_ownership"):
                self.manager.start(plan["plan_id"],grant)
            kill.assert_not_called()
        self.assertEqual(self.backend.calls,0)
        self.assertEqual(self.manager.status(plan["plan_id"])["attempts"],0)

    def test_research_cached_before_calls_with_exact_defaults(self):
        first=self.manager.research("forge")
        second=self.manager.research("forge")
        self.assertEqual(self.backend.research_calls,1)
        self.assertEqual(self.backend.calls,0)
        self.assertTrue(second["cached"])
        self.assertEqual(first["model"]["default_native"],{"reasoning_effort":"low"})
        scribe=self.manager.research("scribe")
        self.assertEqual(scribe["model"]["reasoning"]["semantics"],"token_budget")
        self.assertEqual(scribe["model"]["default_native"],{"thinkingBudget":128})

    def test_research_default_outside_native_values_rejected_before_dispatch(self):
        original=self.backend.metadata
        def bad(model):
            result=original(model)
            result["model"]["default_native"]={"reasoning_effort":"invented"}
            return result
        self.backend.metadata=bad
        with self.assertRaises(ValidationError):
            self.manager.plan("forge")
        self.assertEqual(self.backend.calls,0)

    def test_small_baseline_and_short_contrast(self):
        plan,_=self.plan()
        self.assertEqual(len(plan["cells"]),10)
        self.assertEqual(sum(cell["configuration_id"]==plan["configuration_ids"][0] for cell in plan["cells"]),8)
        self.assertEqual(sum(cell["configuration_id"]==plan["configuration_ids"][1] for cell in plan["cells"]),2)
        self.assertEqual(len([case for case in CASES if not case.requires]),6)

    def test_complete_two_models_and_different_observation_based_previews(self):
        result=run_profile_demo(self.inventory)
        self.assertEqual([row["status"] for row in result["profiles"]],["complete","complete"])
        self.assertEqual([row["attempts"] for row in result["profiles"]],[11,8])
        for category,model in (("code_review","forge"),("research","scribe")):
            decision=result["routing_previews"][category]
            selected=self.inventory.configuration(decision["selected_configuration_id"])
            self.assertEqual(selected.model_id,model)
            self.assertEqual(decision["enforcement"],"preview_only")

    def test_completed_cells_dedup_across_start_resume_and_new_plan(self):
        plan,grant=self.plan()
        self.manager.start(plan["plan_id"],grant)
        count=self.backend.calls
        self.manager.start(plan["plan_id"],grant)
        self.manager.resume(plan["plan_id"],grant)
        new,new_grant=self.plan(max_requests=20)
        self.manager.start(new["plan_id"],new_grant)
        self.assertEqual(self.backend.calls,count)
        self.assertEqual(self.manager.status(new["plan_id"])["attempts"],0)

    def test_partial_bio_survives_reopen_and_resume(self):
        plan,grant=self.plan()
        self.assertEqual(self.manager.start(plan["plan_id"],grant,max_cells=1)["status"],"paused")
        partial=self.manager.bio("forge")
        self.assertEqual(sum(c["attempt_count"] for c in partial["configurations"]),1)
        baseline=next(config for config in partial["configurations"] if config["native"]=={"reasoning_effort":"low"})
        self.assertNotIn("coding",baseline["untested_categories"])
        self.assertIn("planning",baseline["untested_categories"])
        with Inventory(self.path) as reopened:
            manager=ProfileManager(reopened,self.backend)
            result=manager.resume(plan["plan_id"],manager.grant(grant.id))
            self.assertEqual(result["status"],"complete")
        self.assertEqual(self.backend.calls,11)

    def test_cancel_between_cells_requires_explicit_resume(self):
        plan,grant=self.plan()
        self.manager.start(plan["plan_id"],grant,max_cells=1)
        self.manager.cancel(plan["plan_id"])
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"cancelled")
        self.assertEqual(self.backend.calls,1)
        self.assertEqual(self.manager.resume(plan["plan_id"],grant)["status"],"complete")

    def test_unknown_completion_never_automatically_replays(self):
        plan,grant=self.plan()
        calls=[]
        def uncertain(*args,**kwargs):
            calls.append(1)
            raise UnknownCompletion("secret exception text")
        self.backend.complete=uncertain
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"partial_unknown")
        self.assertEqual(self.manager.resume(plan["plan_id"],grant)["status"],"partial_unknown")
        self.assertEqual(len(calls),1)

    def test_real_hung_callback_returns_bounded_and_retains_inflight_slot(self):
        plan,grant=self.plan(call_timeout_seconds=.02)
        release=threading.Event()
        done=threading.Event()
        original=self.backend.complete
        def hung(*args,**kwargs):
            try:
                release.wait(2)
                return original(*args,**kwargs)
            finally:
                done.set()
        self.backend.complete=hung
        started=time.monotonic()
        try:
            self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"partial_unknown")
            self.assertLess(time.monotonic()-started,.5)
            with self.assertRaisesRegex(ValidationError,"still_running"):
                self.manager.resume(plan["plan_id"],grant)
            self.assertEqual(self.manager.status(plan["plan_id"])["attempts"],1)
        finally:
            release.set()
            self.assertTrue(done.wait(2))
        # Let the callback's final slot cleanup finish, without starting a new attempt.
        for _ in range(100):
            from gcb.profiler import _INFLIGHT
            if not any(thread.is_alive() for thread in tuple(_INFLIGHT)):
                break
            time.sleep(.001)

    def test_total_deadline_is_bounded_and_does_not_reset_on_resume(self):
        plan,grant=self.plan(call_timeout_seconds=1,total_timeout_seconds=.03)
        original=self.backend.complete
        def slow(*args,**kwargs):
            time.sleep(.02)
            return original(*args,**kwargs)
        self.backend.complete=slow
        started=time.monotonic()
        result=self.manager.start(plan["plan_id"],grant)
        self.assertLess(time.monotonic()-started,.5)
        self.assertIn(result["status"],{"partial_unknown","budget_exhausted"})
        attempts=result["attempts"]
        time.sleep(.03)  # allow the deliberately slow daemon's cooperative deadline to finish
        self.manager.resume(plan["plan_id"],grant)
        self.assertEqual(self.manager.status(plan["plan_id"])["attempts"],attempts)

    def test_hung_callback_retains_durable_worker_against_another_process(self):
        plan,grant=self.plan(call_timeout_seconds=.02)
        other,other_grant=self.plan("scribe")
        release=threading.Event()
        done=threading.Event()
        original=self.backend.complete
        def hung(*args,**kwargs):
            try:
                release.wait(3)
                return original(*args,**kwargs)
            finally:
                done.set()
        self.backend.complete=hung
        script="""import sys,json
sys.path.insert(0,sys.argv[1])
from gcb import Inventory
from gcb.profiler import ProfileManager
from gcb.validation import ValidationError
with Inventory(sys.argv[2]) as inventory:
    manager=ProfileManager(inventory)
    try: print(json.dumps(manager.start(sys.argv[3],manager.grant(sys.argv[4]))))
    except ValidationError as error: print(json.dumps({'blocked':str(error)}))
"""
        arguments=[sys.executable,"-I","-c",script,str(Path(gcb.__file__).resolve().parents[1]),str(self.path),other["plan_id"],other_grant.id]
        try:
            self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"partial_unknown")
            self.assertEqual(self.inventory.db.execute("SELECT expires_at FROM profile_worker").fetchone()[0],"retained")
            result=subprocess.run(arguments,capture_output=True,text=True,timeout=3)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{"blocked":"profile_worker_busy"})
            self.assertEqual(self.manager.status(other["plan_id"])["attempts"],0)
        finally:
            release.set()
            self.assertTrue(done.wait(3))
        for _ in range(100):
            if not self.inventory.db.execute("SELECT owner FROM profile_worker").fetchone()[0]:
                break
            time.sleep(.005)
        result=subprocess.run(arguments,capture_output=True,text=True,timeout=3)
        self.assertEqual(json.loads(result.stdout)["status"],"complete",result.stderr)
        self.assertEqual(self.manager.status(plan["plan_id"])["attempts"],1)

    def test_timed_out_callback_cleanup_never_recreates_deleted_database(self):
        from gcb.profiler import _INFLIGHT
        plan,grant=self.plan(call_timeout_seconds=.02)
        release=threading.Event()
        original=self.backend.complete
        def hung(*args,**kwargs):
            release.wait(3)
            return original(*args,**kwargs)
        self.backend.complete=hung
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"partial_unknown")
        threads=tuple(_INFLIGHT)
        self.assertEqual(len(threads),1)
        self.inventory.close()
        self.path.unlink()
        release.set()
        threads[0].join(timeout=3)
        self.assertFalse(threads[0].is_alive())
        self.assertFalse(self.path.exists())

    def test_rate_limit_pauses_without_sleeping_then_retries_within_grant(self):
        now=[time.time()]
        self.manager.clock=lambda:now[0]
        plan,grant=self.plan()
        original=self.backend.complete
        seen=[]
        def limited(*args,**kwargs):
            seen.append(1)
            if len(seen)==1:
                raise RateLimited(10)
            return original(*args,**kwargs)
        self.backend.complete=limited
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"rate_limited")
        self.manager.resume(plan["plan_id"],grant)
        self.assertEqual(len(seen),1)
        now[0]+=11
        self.assertEqual(self.manager.resume(plan["plan_id"],grant)["status"],"complete")
        self.assertEqual(len(seen),12)

    def test_request_bound_and_expired_or_forged_grants(self):
        plan,grant=self.plan(max_requests=1)
        with self.assertRaises(ValidationError):
            self.manager.start(plan["plan_id"],{"approved":True})
        with self.assertRaises(ValidationError):
            self.manager.start(plan["plan_id"],replace(grant,max_requests=99))
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"budget_exhausted")
        self.assertEqual(self.backend.calls,1)
        self.manager.clock=lambda:grant.expires_at+1
        with self.assertRaisesRegex(ValidationError,"expired"):
            self.manager.resume(plan["plan_id"],grant)

    def test_authorization_rechecked_before_every_attempt(self):
        plan,grant=self.plan()
        original=self.backend.complete
        def revoke(*args,**kwargs):
            result=original(*args,**kwargs)
            with Inventory(self.path) as inventory:
                inventory.set_backend_state("fictional-local-v1",authorization="denied",availability="ready")
            return result
        self.backend.complete=revoke
        self.assertEqual(self.manager.start(plan["plan_id"],grant)["status"],"blocked")
        self.assertEqual(self.backend.calls,1)

    def test_poor_answers_still_get_bio_and_no_grade(self):
        plan,grant=self.plan("scribe",contrast=False)
        original=self.backend.complete
        def poor(*args,**kwargs):
            return replace(original(*args,**kwargs),output='{"wrong":true}')
        self.backend.complete=poor
        self.manager.start(plan["plan_id"],grant)
        bio=self.manager.bio("scribe")
        baseline=next(c for c in bio["configurations"] if c["native"]=={"thinkingBudget":128})
        self.assertEqual(sum(v["observed"] for v in baseline["categories"].values()),6)
        self.assertEqual(sum(v["correct"] for v in baseline["categories"].values()),0)
        self.assertTrue(baseline["discoverable"])
        self.assertTrue(baseline["weaknesses"])
        self.assertEqual(len(self.inventory.models()),1)

    def test_technical_error_and_identity_mismatch_are_not_quality_failures(self):
        plan,grant=self.plan(contrast=False)
        original=self.backend.complete
        def mismatch(*args,**kwargs):
            return replace(original(*args,**kwargs),served_model="other-model")
        self.backend.complete=mismatch
        self.manager.start(plan["plan_id"],grant)
        cells=self.manager.evidence(plan["plan_id"])["cells"]
        self.assertTrue(all(cell["status"]=="technical_error" for cell in cells))
        self.assertTrue(all(cell["observation"]["correct"] is None for cell in cells))

    def test_native_tools_are_not_text_imitation(self):
        plan,grant=self.plan(contrast=False)
        original=self.backend.complete
        def imitation(request,case,**kwargs):
            result=original(request,case,**kwargs)
            if case.requires:
                return replace(result,output=case.expected_json,native_calls=(),finish_reason="stop")
            return result
        self.backend.complete=imitation
        self.manager.start(plan["plan_id"],grant)
        cells=self.manager.evidence(plan["plan_id"])["cells"]
        self.assertTrue(all(cell["observation"]["correct"] is False for cell in cells if cell["case_id"] in {"tools.native","recovery.native"}))

    def test_text_case_pending_or_unexpected_native_call_is_not_correct(self):
        plan,grant=self.plan(contrast=False)
        original=self.backend.complete
        def pending(request,case,**kwargs):
            return replace(original(request,case,**kwargs),output=case.expected_json,finish_reason="tool_calls",
                           native_calls=(NativeToolCall("unexpected_action","{}"),))
        self.backend.complete=pending
        self.manager.start(plan["plan_id"],grant,max_cells=1)
        cell=self.manager.evidence(plan["plan_id"])["cells"][0]
        self.assertEqual(cell["status"],"observed")
        self.assertFalse(cell["observation"]["correct"])
        self.assertEqual(cell["observation"]["evaluation"]["outcome"],"unexpected_native_call_or_nonfinal_response")

    def test_native_recovery_has_two_reserved_attempts_and_tool_evidence(self):
        plan,grant=self.plan(contrast=False)
        self.manager.start(plan["plan_id"],grant)
        evidence=self.manager.evidence(plan["plan_id"])
        recovery=next(cell for cell in evidence["cells"] if cell["case_id"]=="recovery.native")
        self.assertEqual(len(recovery["observation"]["attempt_ids"]),2)
        self.assertTrue(recovery["observation"]["correct"])
        attempts=[a for a in evidence["attempts"] if a["cell_id"]==recovery["cell_id"]]
        self.assertTrue(all(a["summary_json"]["native_call_count"]==1 for a in attempts))

    def test_missing_usage_and_cost_are_unknown(self):
        plan,grant=self.plan("scribe")
        self.manager.start(plan["plan_id"],grant)
        for config in self.manager.bio("scribe")["configurations"]:
            self.assertTrue(all(value is None for value in config["usage"].values()))

    def test_routing_rechecks_updated_state_and_research_after_observations(self):
        from gcb import ResearchFact,SourceCitation
        run_profile_demo(self.inventory)
        task=TaskRequirements(category="research")
        self.assertEqual(self.inventory.configuration(preview_route(self.inventory,task,RoutingPolicy()).selected_configuration_id).model_id,"scribe")
        self.inventory.add_research(ResearchFact.create("scribe","context_window",2048,"documented",
                                   (SourceCitation("https://example.org/changed","Changed fixture","2026-11-14T00:00:00Z"),)))
        result=preview_route(self.inventory,task,RoutingPolicy())
        self.assertNotEqual(self.inventory.configuration(result.selected_configuration_id).model_id,"scribe")
        self.inventory.set_backend_state("fictional-local-v1",authorization="denied",availability="ready")
        self.assertEqual(preview_route(self.inventory,task,RoutingPolicy()).status,"no_eligible")

    def test_bio_excludes_prior_research_snapshot_and_reports_historical_count(self):
        from gcb import ResearchFact,SourceCitation
        plan,grant=self.plan("scribe",contrast=False)
        self.manager.start(plan["plan_id"],grant)
        self.inventory.add_research(ResearchFact.create("scribe","context_window",4096,"documented",
                                   (SourceCitation("https://example.org/second-source","Second fixture citation","2026-11-14T00:00:00Z"),)))
        bio=self.manager.bio("scribe")
        self.assertEqual(sum(config["historical_excluded_cells"] for config in bio["configurations"]),8)
        self.assertEqual(sum(config["attempt_count"] for config in bio["configurations"]),0)
        self.assertTrue(all(not config["categories"] for config in bio["configurations"]))
        self.assertEqual(len(self.manager.evidence(plan["plan_id"])["attempts"]),6)

    def test_obsolete_scorer_suite_not_consumed_by_current_bio_or_route(self):
        plan,grant=self.plan(contrast=False)
        self.manager.start(plan["plan_id"],grant)
        row=self.inventory.db.execute("SELECT * FROM profile_cells WHERE case_id='coding.guard'").fetchone()
        observation=json.loads(row["observation_json"])
        observation["suite_hash"]="obsolete"
        with self.inventory.db:
            self.inventory.db.execute("UPDATE profile_cells SET observation_json=? WHERE id=?",(canonical(observation),row["id"]))
        decision=preview_route(self.inventory,TaskRequirements(category="coding"),RoutingPolicy())
        self.assertTrue(all(candidate.comparable_observed==0 for candidate in decision.candidates))
        self.assertEqual(sum(config["historical_excluded_cells"] for config in self.manager.bio("forge")["configurations"]),1)

    def test_evidence_explains_misses_without_arbitrary_output_retention(self):
        import hashlib
        plan,grant=self.plan("scribe",contrast=False)
        self.manager.start(plan["plan_id"],grant)
        evidence=self.manager.evidence(plan["plan_id"])
        coding=next(cell for cell in evidence["cells"] if cell["case_id"]=="coding.guard")
        evaluation=coding["observation"]["evaluation"]
        self.assertEqual(evaluation["outcome"],"structured_mismatch")
        self.assertEqual(evaluation["criterion"],{"missing_guard":"empty_items"})
        self.assertEqual(evaluation["allowlisted_fixture_output"],{"fixture_wrong_answer":True})
        attempt=next(a for a in evidence["attempts"] if a["cell_id"]==coding["cell_id"])
        self.assertEqual(attempt["summary_json"]["output_sha256"],hashlib.sha256(b'{"fixture_wrong_answer":true}').hexdigest())

    def test_cli_persistent_plan_partial_resume_bio_evidence_and_old_demo(self):
        def command(*args):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):
                status=main(["--db",str(self.path),*args])
            self.assertEqual(status,0)
            return json.loads(output.getvalue())
        plan=command("profile","plan","forge")
        partial=command("profile","start",plan["plan_id"],"--authorize","--max-cells","1")
        self.assertEqual(partial["status"],"paused")
        completed=command("profile","resume",plan["plan_id"],"--grant",partial["grant_id"])
        self.assertEqual(completed["status"],"complete")
        self.assertEqual(command("bio","forge")["model"]["id"],"forge")
        self.assertEqual(len(command("evidence",plan["plan_id"])["attempts"]),11)
        self.assertEqual(command("demo")["mode"],"offline_synthetic")

    def test_untrusted_output_and_error_bodies_are_not_persisted_or_executed(self):
        plan,grant=self.plan("scribe",contrast=False)
        secret="PRIVATE_OUTPUT_CANARY_G2"
        marker=Path(self.directory.name)/"never-created"
        original=self.backend.complete
        def malicious(*args,**kwargs):
            return replace(original(*args,**kwargs),output=f"{secret}: __import__('pathlib').Path({str(marker)!r}).touch()")
        self.backend.complete=malicious
        with patch.dict(os.environ,{"OPENAI_API_KEY":"CREDENTIAL_CANARY_G2"}),patch("subprocess.Popen",side_effect=AssertionError("no execution")):
            self.manager.start(plan["plan_id"],grant)
        dump="\n".join(self.inventory.db.iterdump())
        self.assertNotIn(secret,dump)
        self.assertNotIn("CREDENTIAL_CANARY_G2",dump)
        self.assertFalse(marker.exists())

    def test_truncated_outputs_are_resource_limits_not_wrong_answers(self):
        plan,grant=self.plan("scribe",contrast=False)
        original=self.backend.complete
        self.backend.complete=lambda *args,**kwargs:replace(original(*args,**kwargs),finish_reason="length")
        result=self.manager.start(plan["plan_id"],grant)
        self.assertEqual(result["cells"],{"not_applicable":2,"resource_limited":6})

    def test_real_process_interruption_preserves_reservation_and_never_replays(self):
        plan,grant=self.plan()
        package_root=str(Path(gcb.__file__).resolve().parents[1])
        script="""import sys,os
sys.path.insert(0,sys.argv[1])
from gcb import Inventory
from gcb.profiler import ProfileManager
from gcb.profile_backend import FictionalBackend
class Crash(FictionalBackend):
    def complete(self,*args,**kwargs): os._exit(27)
with Inventory(sys.argv[2]) as inventory:
    manager=ProfileManager(inventory,Crash())
    manager.start(sys.argv[3],manager.grant(sys.argv[4]))
"""
        result=subprocess.run([sys.executable,"-I","-c",script,package_root,str(self.path),plan["plan_id"],grant.id],capture_output=True,timeout=5)
        self.assertEqual(result.returncode,27,result.stderr)
        self.assertEqual(self.manager.status(plan["plan_id"])["cells"].get("running"),1)
        self.manager.clock=lambda:time.time()+1000
        result=self.manager.resume(plan["plan_id"],grant)
        self.assertEqual(result["status"],"partial_unknown")
        self.assertEqual(result["attempts"],1)
        self.assertEqual(self.backend.calls,0)


class MigrationTests(unittest.TestCase):
    def test_actual_g1_database_migrates_without_replacing_records(self):
        fixture=Path(__file__).parent/"fixtures"/"g1-inventory.sql"
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"g1.db"
            with closing(sqlite3.connect(path)) as database:
                database.executescript(fixture.read_text())
                self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0],1)
                original={table:database.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                          for table in ("providers","backends","models","research","configurations")}
            with Inventory(path) as inventory:
                self.assertEqual(inventory.db.execute("PRAGMA user_version").fetchone()[0],2)
                for table,rows in original.items():
                    self.assertEqual([tuple(row) for row in inventory.db.execute(f"SELECT * FROM {table} ORDER BY id")],rows)
                manager=ProfileManager(inventory)
                plan=manager.plan("forge")
                self.assertEqual(manager.start(plan["plan_id"],manager.authorize(plan["plan_id"]))["status"],"complete")


if __name__=="__main__":
    unittest.main()
