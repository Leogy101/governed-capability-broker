"""Synthetic stored receipts test ranking logic; no real performance is measured."""
import json
import unittest

from gcb import Inventory, RoutingPolicy, TaskRequirements, preview_route
from gcb.profiler import ProfileManager
from gcb.validation import canonical


class RouteLatencyTests(unittest.TestCase):
    def setUp(self):
        self.inventory=Inventory(":memory:")
        self.addCleanup(self.inventory.close)
        manager=ProfileManager(self.inventory)
        plan=manager.plan("scribe")
        manager.start(plan["plan_id"],manager.authorize(plan["plan_id"]))
        self.configs=sorted(c.id for c in self.inventory.configurations())
        for index,config in enumerate(self.configs):
            self.receipt(config,latency=100 if index==0 else 1)

    def receipt(self, config, *, latency=1, correct=True, evidence_kind="local_model", missing=False):
        db=self.inventory.db
        cell=db.execute("SELECT * FROM profile_cells WHERE configuration_id=? AND case_id='structured.invoice'",(config,)).fetchone()
        observation=json.loads(cell["observation_json"])
        observation.update(correct=correct,evidence_kind=evidence_kind)
        with db:
            db.execute("UPDATE profile_cells SET observation_json=? WHERE id=?",(canonical(observation),cell["id"]))
            for attempt in db.execute("SELECT * FROM profile_attempts WHERE cell_id=?",(cell["id"],)).fetchall():
                summary=json.loads(attempt["summary_json"])
                summary.update(latency_ms=latency,evidence_kind=evidence_kind)
                if missing:
                    summary.pop("latency_ms")
                db.execute("UPDATE profile_attempts SET summary_json=? WHERE id=?",(canonical(summary),attempt["id"]))

    def preview(self):
        return preview_route(self.inventory,TaskRequirements(category="structured_data"),RoutingPolicy())

    def test_complete_local_common_case_latency_breaks_equal_quality_tie(self):
        result=self.preview()
        self.assertEqual(result.selected_configuration_id,self.configs[1])
        self.assertIn("complete_local_case_latency",result.ranking)
        self.assertEqual([c.comparable_correct for c in result.candidates],[1,1])
        self.assertEqual([c.comparable_latency_ms for c in result.candidates],[100,1])
        self.assertEqual([c.latency_case_count for c in result.candidates],[1,1])
        self.assertEqual(result.enforcement,"preview_only")
        self.assertEqual(self.preview(),result)

    def test_quality_fraction_precedes_local_latency(self):
        self.receipt(self.configs[1],latency=1,correct=False)
        result=self.preview()
        self.assertEqual(result.selected_configuration_id,self.configs[0])
        self.assertEqual([c.comparable_correct for c in result.candidates],[1,0])
        self.assertTrue(all(c.comparable_latency_ms is None for c in result.candidates))

    def test_nonlocal_missing_invalid_or_wrong_identity_timing_cannot_reorder(self):
        for kind,missing,latency in (("synthetic_fixture",False,1),("transport_fixture",False,1),
                                     ("local_model",True,1),("local_model",False,-1),("local_model",False,True)):
            with self.subTest(kind=kind,missing=missing,latency=latency):
                self.receipt(self.configs[1],latency=latency,evidence_kind=kind,missing=missing)
                result=self.preview()
                self.assertEqual(result.selected_configuration_id,self.configs[0])
                self.assertTrue(all(c.comparable_latency_ms is None for c in result.candidates))
        self.receipt(self.configs[1],latency=1)
        row=self.inventory.db.execute("SELECT id,summary_json FROM profile_attempts WHERE cell_id IN "
             "(SELECT id FROM profile_cells WHERE configuration_id=? AND case_id='structured.invoice')",(self.configs[1],)).fetchone()
        summary=json.loads(row["summary_json"]); summary["served_model"]="other-model"
        with self.inventory.db:
            self.inventory.db.execute("UPDATE profile_attempts SET summary_json=? WHERE id=?",(canonical(summary),row["id"]))
        self.assertEqual(self.preview().selected_configuration_id,self.configs[0])
