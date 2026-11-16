"""Explicit untouched-only continuation; no installed models or live transports."""
from pathlib import Path
import tempfile
import unittest

from gcb import Inventory
from gcb.profile_backend import FictionalBackend, RateLimited, UnknownCompletion
from gcb.profiler import ProfileManager
from gcb.validation import ValidationError, canonical


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.inventory=Inventory(Path(self.directory.name)/"profiles.db")
        self.addCleanup(self.inventory.close)
        self.backend=FictionalBackend()
        self.manager=ProfileManager(self.inventory,self.backend)
        self.parent=self.manager.plan("forge",max_requests=11,total_timeout_seconds=300)
        self.grant=self.manager.authorize(self.parent["plan_id"])
        self.original=self.backend.complete
        self.uncertain_calls=0
        def uncertain(*args,**kwargs):
            self.uncertain_calls+=1
            raise UnknownCompletion("fixture_unknown")
        self.backend.complete=uncertain
        self.manager.start(self.parent["plan_id"],self.grant,max_cells=1)
        self.backend.complete=self.original
        self.unknown=self.parent["cells"][0]["cell_id"]

    def create(self,**changes):
        args=dict(excluded_unknown_cell_ids=[self.unknown],max_requests=10,
                  total_timeout_seconds=287.99,prior_active_seconds=12.01,
                  termination_evidence_ref="fixture:confirmed_terminal_log")
        args.update(changes)
        return self.manager.plan_untouched_continuation(self.parent["plan_id"],**args)

    def raw_parent(self):
        return self.manager._continuation_parent_snapshot(self.parent["plan_id"])

    def test_child_runs_only_untouched_cells_and_preserves_history(self):
        before=self.raw_parent()
        child=self.create()
        self.assertEqual(len(child["cells"]),9)
        self.assertNotIn(self.unknown,[c["cell_id"] for c in child["cells"]])
        self.assertEqual(child["continuation"]["prior_attempts"],1)
        self.assertEqual(child["continuation"]["prior_active_seconds"],12.01)
        grant=self.manager.authorize(child["plan_id"])
        # Batching, fresh process/inventory binding and stage-two recovery stay supported.
        self.assertEqual(self.manager.start(child["plan_id"],grant,max_cells=1)["attempts"],1)
        with Inventory(self.inventory.database) as reopened:
            manager=ProfileManager(reopened,self.backend)
            result=manager.resume(child["plan_id"],manager.grant(grant.id))
        self.assertEqual(result["status"],"complete")
        self.assertEqual(result["attempts"],10)
        self.assertEqual(self.backend.calls,10)
        self.assertEqual(self.raw_parent(),before)
        self.assertEqual(self.manager.status(self.parent["plan_id"])["status"],"partial_unknown")
        bio=self.manager.bio("forge")
        self.assertEqual(sum(c["categories"].get("coding",{}).get("unknown_completion",0) for c in bio["configurations"]),1)
        self.assertEqual(sum(c["attempt_count"] for c in bio["configurations"]),11)
        self.manager.resume(child["plan_id"],grant)
        self.assertEqual(self.backend.calls,10)

    def test_general_parent_start_and_resume_guard_remains(self):
        self.create()
        self.assertEqual(self.manager.start(self.parent["plan_id"],self.grant)["status"],"partial_unknown")
        self.assertEqual(self.manager.resume(self.parent["plan_id"],self.grant)["status"],"partial_unknown")
        self.assertEqual(self.backend.calls,0)
        self.assertEqual(self.uncertain_calls,1)

    def test_requires_exact_exclusion_manifest_and_termination_reference(self):
        for kwargs in ({"excluded_unknown_cell_ids":[]},{"excluded_unknown_cell_ids":["wrong"]},
                       {"excluded_unknown_cell_ids":[self.unknown,self.unknown]},
                       {"termination_evidence_ref":""}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValidationError):
                self.create(**kwargs)
        self.assertEqual(self.inventory.db.execute("SELECT count(*) FROM profile_plans").fetchone()[0],1)

    def test_cumulative_request_and_conservative_time_cannot_expand(self):
        for kwargs in ({"max_requests":11},{"total_timeout_seconds":288},
                       {"prior_active_seconds":0},{"prior_active_seconds":301}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValidationError):
                self.create(**kwargs)
        child=self.create(max_requests=1)
        grant=self.manager.authorize(child["plan_id"])
        self.assertEqual(self.manager.start(child["plan_id"],grant)["status"],"budget_exhausted")
        self.assertEqual(self.backend.calls,1)
        self.assertEqual(self.inventory.db.execute("SELECT count(*) FROM profile_attempts").fetchone()[0],2)

    def test_only_one_child_and_no_nested_continuation(self):
        child=self.create()
        self.assertEqual(self.create()["plan_id"],child["plan_id"])
        with self.assertRaisesRegex(ValidationError,"continuation_already_exists"):
            self.create(max_requests=9)
        with self.assertRaisesRegex(ValidationError,"exact_stopped_parent_required"):
            self.manager.plan_untouched_continuation(child["plan_id"],excluded_unknown_cell_ids=[self.unknown],
                max_requests=1,total_timeout_seconds=1,prior_active_seconds=0,termination_evidence_ref="fixture:stop")

    def test_changed_parent_record_fails_before_new_attempt(self):
        child=self.create();grant=self.manager.authorize(child["plan_id"])
        with self.inventory.db:
            self.inventory.db.execute("UPDATE profile_plans SET elapsed_seconds='12.02' WHERE id=?",(self.parent["plan_id"],))
        with self.assertRaisesRegex(ValidationError,"continuation_parent_changed"):
            self.manager.start(child["plan_id"],grant)
        self.assertEqual(self.backend.calls,0)

    def test_new_unknown_in_child_is_not_replayed(self):
        child=self.create();grant=self.manager.authorize(child["plan_id"])
        def uncertain(*args,**kwargs):
            raise UnknownCompletion("second_unknown")
        self.backend.complete=uncertain
        self.assertEqual(self.manager.start(child["plan_id"],grant)["status"],"partial_unknown")
        attempts=self.inventory.db.execute("SELECT count(*) FROM profile_attempts").fetchone()[0]
        with self.assertRaisesRegex(ValidationError,"continuation_parent_changed"):
            self.manager.resume(child["plan_id"],grant)
        self.assertEqual(self.inventory.db.execute("SELECT count(*) FROM profile_attempts").fetchone()[0],attempts)

    def test_rate_limited_pending_cell_cannot_be_retried(self):
        child=self.create();grant=self.manager.authorize(child["plan_id"])
        def limited(*args,**kwargs):
            raise RateLimited(0)
        self.backend.complete=limited
        self.assertEqual(self.manager.start(child["plan_id"],grant)["status"],"rate_limited")
        before=self.manager.evidence(child["plan_id"])["cells"][0]
        self.backend.complete=self.original
        self.assertEqual(self.manager.resume(child["plan_id"],grant)["status"],"blocked")
        self.assertEqual(self.backend.calls,0)
        self.assertEqual(self.manager.evidence(child["plan_id"])["cells"][0],before)

    def test_completed_and_attempted_pending_cells_not_selected(self):
        # Other existing plans can share cells; continuation never revives one.
        cell=self.parent["cells"][1]["cell_id"]
        with self.inventory.db:
            self.inventory.db.execute("INSERT INTO profile_attempts VALUES (?,?,?,?,?,?,?)",
                ("prior-rate-limited",self.parent["plan_id"],cell,"rate_limited","1","2","{}"))
        child=self.create(max_requests=9)
        self.assertNotIn(cell,[c["cell_id"] for c in child["cells"]])

    def test_plan_creation_does_not_grant_or_extend_deadline(self):
        original_grants=[tuple(row) for row in self.inventory.db.execute("SELECT * FROM profile_grants")]
        child=self.create()
        self.assertEqual([tuple(row) for row in self.inventory.db.execute("SELECT * FROM profile_grants")],original_grants)
        with self.assertRaisesRegex(ValidationError,"trusted_matching_profile_grant_required"):
            self.manager.start(child["plan_id"],self.grant)
        self.assertEqual(self.backend.calls,0)

    def test_racing_terminal_cell_is_not_reclassified_or_overwritten(self):
        child=self.create();grant=self.manager.authorize(child["plan_id"])
        original=self.manager._reserve
        saved=[]
        def race(plan_id,cell_id,body,grant,**kwargs):
            with self.inventory.db:
                self.inventory.db.execute("UPDATE profile_cells SET status='unknown_completion',observation_json=? WHERE id=?",
                                          ('{"preserved":"uncertain_external_attempt"}',cell_id))
            saved.append(dict(self.inventory.db.execute("SELECT * FROM profile_cells WHERE id=?",(cell_id,)).fetchone()))
            return original(plan_id,cell_id,body,grant,**kwargs)
        self.manager._reserve=race
        status=self.manager.start(child["plan_id"],grant,max_cells=1)
        self.assertEqual(status["status"],"blocked")
        self.assertEqual(status["attempts"],0)
        self.assertEqual(self.backend.calls,0)
        current=dict(self.inventory.db.execute("SELECT * FROM profile_cells WHERE id=?",(saved[0]["id"],)).fetchone())
        self.assertEqual(current,saved[0])

    def test_child_time_budget_is_not_reset_by_resume(self):
        child=self.create();grant=self.manager.authorize(child["plan_id"])
        with self.inventory.db:
            self.inventory.db.execute("UPDATE profile_plans SET elapsed_seconds=? WHERE id=?",
                                      (str(child["total_timeout_seconds"]),child["plan_id"]))
        self.assertEqual(self.manager.resume(child["plan_id"],grant)["status"],"budget_exhausted")
        self.assertEqual(self.backend.calls,0)
        preserved={key:value for key,value in child.items() if key not in {"plan_id","status","cells","max_requests","total_timeout_seconds","continuation"}}
        original={key:value for key,value in self.parent.items() if key not in {"plan_id","status","cells","max_requests","total_timeout_seconds"}}
        self.assertEqual(canonical(preserved),canonical(original))


if __name__=="__main__":
    unittest.main()
