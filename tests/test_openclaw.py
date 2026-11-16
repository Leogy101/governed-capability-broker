"""Offline host adapter contracts. No installed host/plugin/model is invoked."""
from dataclasses import replace
import json
import subprocess
import shutil
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from gcb import Inventory
from gcb.cases import CASES
from gcb.openclaw import OpenClawBackend, GatewayTransport
from gcb.profile_backend import UnknownCompletion
from gcb.profiler import ProfileManager
from gcb.profiling import ProbeRequest
from gcb.validation import ValidationError, canonical

SNAPSHOT={"snapshot_version":1,"checked_at":"2026-11-15T00:00:00+00:00","agent_id":"fixture-agent",
          "model_ref":"openai/gpt-fixture","openclaw_version":"2026.11.1","codex_version":"0.164.0",
          "context_window":872000,"reasoning_settings":["medium","high"],"selected_reasoning":"medium"}

class FakeHost:
    def __init__(self):
        self.calls=[]
        self.cancelled=False
        self.usage={"inputTokens":30,"cacheReadTokens":70,"cacheWriteTokens":0,"outputTokens":12,"totalTokens":112,"costUsd":0}
        self.output='{"missing_guard":"empty_items"}'
        self.change=lambda result:result
    def cancel(self):self.cancelled=True
    def __call__(self,params,*,deadline):
        self.calls.append(params)
        return self.change({"schema":"gcb.openclaw-host/v1","requestId":params["requestId"],"status":"completed",
            "result":{"text":self.output,"provider":"openai","model":"gpt-fixture","agentId":"fixture-agent",
                      "execution":{"mode":"isolated-agent-runtime","owner":{"kind":"harness","id":"codex"}},
                      "usage":self.usage}})

class HostAdapterTests(unittest.TestCase):
    def setUp(self):
        self.host=FakeHost()
        self.backend=OpenClawBackend(SNAPSHOT,transport=self.host)
        self.inventory=Inventory(":memory:");self.addCleanup(self.inventory.close)
        self.manager=ProfileManager(self.inventory,self.backend,allow_host_runtime=True)
    def request(self,case=CASES[0]):
        metadata=self.manager.research("fixture-gpt")
        config=self.inventory.configuration(metadata['configuration_ids'][0])
        return ProbeRequest(config.id,'openai-host',metadata['backend']['id'],SNAPSHOT['model_ref'],config.native_json,case.id,512)
    def complete(self):return self.backend.complete(self.request(),CASES[0],deadline=time.monotonic()+1)
    def test_research_cached_without_host_inference_or_secret_fields(self):
        a=self.manager.research("fixture-gpt");b=self.manager.research("fixture-gpt")
        self.assertTrue(b['cached']);self.assertFalse(self.host.calls)
        self.assertFalse(a['model']['native_tools']);self.assertEqual(a['backend']['locality'],'remote')
        self.assertEqual(a['model']['revision'],'unknown')
        self.assertIn('advisory',a['model']['execution_limits']['max_output_scope'])
    def test_native_identity_and_public_case_no_gold_or_tools(self):
        r=self.complete();p=self.host.calls[0]
        self.assertEqual(p['model'],SNAPSHOT['model_ref']);self.assertEqual(p['reasoning'],'medium')
        self.assertEqual(p['agentId'],'fixture-agent');self.assertLessEqual(p['timeoutMs'],1000)
        self.assertNotIn('tools',p);self.assertNotIn('expected',p['messages'][0]['content'])
        self.assertEqual(r.native_confirmation,'request_only_not_echoed')
        self.assertEqual(r.input_tokens,100);self.assertEqual(r.output_tokens,12)
        self.assertIsNone(r.billed_cost_usd);self.assertIsNone(r.estimated_cost_usd)
        self.assertEqual(json.loads(r.host_usage_json)['reported_total_tokens'],112)
    def test_missing_cache_usage_does_not_become_zero(self):
        self.host.usage={'inputTokens':30,'outputTokens':12}
        r=self.complete();self.assertIsNone(r.input_tokens)
        self.assertIsNone(json.loads(r.host_usage_json)['cache_read_tokens'])
    def test_all_missing_usage_is_unknown(self):
        self.host.usage={};r=self.complete()
        self.assertEqual(json.loads(r.host_usage_json)['coverage'],'unknown')
        self.assertIsNone(r.output_tokens)
    def test_fake_bio_low_performance_not_removed(self):
        self.host.output='{"wrong":true}'
        plan=self.manager.plan('fixture-gpt',contrast=False,max_requests=1)
        grant=self.manager.authorize(plan['plan_id'])
        result=self.manager.start(plan['plan_id'],grant,max_cells=1)
        self.assertEqual(result['attempts'],1)
        bio=self.manager.bio('fixture-gpt')
        self.assertEqual(len(self.inventory.models()),1)
        self.assertEqual(bio['configurations'][0]['categories']['coding']['correct'],0)
        self.assertEqual(result['cells']['not_applicable'],2)
    def test_one_call_smoke_is_reused_by_later_full_host_plan(self):
        smoke=self.manager.plan('fixture-gpt',contrast=False,max_requests=1)
        self.manager.start(smoke['plan_id'],self.manager.authorize(smoke['plan_id']),max_cells=1)
        full=self.manager.plan('fixture-gpt',contrast=True,max_requests=7)
        self.assertEqual(smoke['cells'][0]['cell_id'],full['cells'][0]['cell_id'])
        self.manager.start(full['plan_id'],self.manager.authorize(full['plan_id']),max_cells=1)
        self.assertEqual(len(self.host.calls),2)
        self.assertNotEqual(self.host.calls[0]['messages'],self.host.calls[1]['messages'])
        count=self.inventory.db.execute('SELECT count(*) FROM profile_attempts WHERE cell_id=?',
                                       (smoke['cells'][0]['cell_id'],)).fetchone()[0]
        self.assertEqual(count,1)
    def test_unapproved_remote_execution_is_rejected(self):
        plan=self.manager.plan('fixture-gpt',contrast=False,max_requests=1)
        default=ProfileManager(self.inventory,self.backend)
        with self.assertRaises(ValidationError):default.start(plan['plan_id'],default.authorize(plan['plan_id']))
        self.assertFalse(self.host.calls)
    def test_identity_runtime_or_agent_mismatch_is_uncertain_and_latches(self):
        for field,value in [('model','gpt-other'),('agentId','other'),('execution',{'mode':'direct-provider'})]:
            with self.subTest(field=field):
                backend=OpenClawBackend(SNAPSHOT,transport=self.host)
                request=self.request();backend.bind_metadata(self.backend._metadata)
                self.host.change=lambda r:{**r,'result':{**r['result'],field:value}}
                with self.assertRaises(UnknownCompletion):backend.complete(request,CASES[0],deadline=time.monotonic()+1)
                self.assertTrue(self.host.cancelled)
                before=len(self.host.calls)
                with self.assertRaises(UnknownCompletion):backend.complete(request,CASES[0],deadline=time.monotonic()+1)
                self.assertEqual(len(self.host.calls),before)
    def test_timeout_provider_error_and_rejected_envelopes_never_retried(self):
        for status in ['unknown_completion','rejected','other']:
            with self.subTest(status=status):
                host=FakeHost();host.change=lambda r:{**r,'status':status}
                backend=OpenClawBackend(SNAPSHOT,transport=host)
                backend.metadata('fixture-gpt');request=self.request()
                with self.assertRaises(UnknownCompletion):backend.complete(request,CASES[0],deadline=time.monotonic()+1)
                self.assertEqual(len(host.calls),1)
    def test_unsupported_tool_and_reasoning_fail_before_host(self):
        request=self.request()
        for req,case in [(replace(request,native_json=canonical({'reasoning_effort':'ultra'})),CASES[0]),
                         (replace(request,case_id=CASES[-1].id),CASES[-1])]:
            with self.assertRaises(ValidationError):self.backend.complete(req,case,deadline=time.monotonic()+1)
        self.assertFalse(self.host.calls)
    def test_snapshot_drift_rejected(self):
        self.request();body=json.loads(canonical(self.backend._metadata));body['model']['revision']='invented'
        with self.assertRaises(ValidationError):self.backend.bind_metadata(body)
    def test_invalid_usage_not_retained_as_measurement(self):
        self.host.usage['inputTokens']=True
        with self.assertRaises(UnknownCompletion):self.complete()
    def test_cancel_before_dispatch_makes_no_call(self):
        request=self.request();self.backend.cancel()
        with self.assertRaises(UnknownCompletion):self.backend.complete(request,CASES[0],deadline=time.monotonic()+1)
        self.assertFalse(self.host.calls)
    def test_transport_cancel_pre_dispatch_no_process(self):
        t=GatewayTransport();t.cancel()
        with patch('gcb.openclaw.subprocess.Popen') as popen:
            with self.assertRaises(UnknownCompletion):t({},deadline=time.monotonic()+1)
            popen.assert_not_called()
    def test_cli_failure_cancels_once_without_retry_or_stderr_leak(self):
        with patch('gcb.openclaw.subprocess.Popen') as popen,patch('gcb.openclaw.subprocess.run') as run:
            process=popen.return_value;process.communicate.return_value=(b'',b'SECRET_CANARY');process.returncode=1;process.poll.return_value=1
            t=GatewayTransport()
            with self.assertRaisesRegex(UnknownCompletion,'host_rpc_uncertain') as exc:
                t({'requestId':'123','timeoutMs':100},deadline=time.monotonic()+1)
            self.assertNotIn('SECRET',str(exc.exception));self.assertEqual(popen.call_count,1);self.assertEqual(run.call_count,1)

    @unittest.skipUnless(shutil.which("node"), "Node required only for optional host bridge integration")
    def test_python_profiler_to_real_js_bridge_contract_offline(self):
        def transport(params, *, deadline):
            result=subprocess.run([shutil.which("node"),str(Path(__file__).with_name("openclaw_fixture.mjs"))],
                                  input=canonical(params),text=True,capture_output=True,check=True,timeout=2)
            self.assertNotIn("DO_NOT_PERSIST",result.stdout)
            return json.loads(result.stdout)
        backend=OpenClawBackend(SNAPSHOT,transport=transport)
        manager=ProfileManager(self.inventory,backend,allow_host_runtime=True)
        plan=manager.plan('cross-language',contrast=False,max_requests=1,call_timeout_seconds=3)
        status=manager.start(plan['plan_id'],manager.authorize(plan['plan_id']),max_cells=1)
        self.assertEqual(status['attempts'],1)
        config=manager.bio('cross-language')['configurations'][0]
        self.assertEqual(config['categories']['coding']['correct'],1)
        self.assertEqual(config['usage']['input_tokens'],100)
        self.assertEqual(config['host_usage']['reported_total_tokens'],112)
        self.assertIsNone(config['host_usage']['provider_request_count'])
