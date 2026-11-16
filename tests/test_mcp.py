import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from gcb._stdio import serve
from gcb.inventory import Inventory
from gcb.mcp import BrokerTools
from gcb.profile_backend import FictionalBackend
from gcb.profiler import ProfileManager


def request(method, params=None, ident=1):
    return {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}


def exchange(handler, messages):
    incoming = io.BytesIO(b''.join(json.dumps(item).encode() + b'\n' for item in messages))
    outgoing = io.BytesIO()
    serve('gcb', 'test', handler.definitions(), handler.call, incoming=incoming, outgoing=outgoing)
    return [json.loads(line) for line in outgoing.getvalue().splitlines()]


INIT = request('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.inv = Inventory()
        self.addCleanup(self.inv.close)
        self.backend = FictionalBackend()
        self.handler = BrokerTools(self.inv, models=['forge'], backend_factory=lambda model: self.backend,
                                   allow_profiling=True)

    def test_protocol_initialize_list_call_and_notifications(self):
        outputs = exchange(self.handler, [INIT, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                           request('tools/list', ident=2), request('tools/call', {'name': 'gcb_models'}, ident=3)])
        self.assertEqual(len(outputs), 3)
        self.assertEqual(outputs[0]['result']['protocolVersion'], '2025-11-25')
        self.assertIn('gcb_start', {tool['name'] for tool in outputs[1]['result']['tools']})
        self.assertFalse(outputs[2]['result']['isError'])
        self.assertEqual(self.backend.calls, 0)

    def test_uninitialized_and_unknown_tools_do_not_execute(self):
        outputs = exchange(self.handler, [request('tools/call', {'name': 'gcb_start', 'arguments': {'plan_id': 'x'}}),
                           INIT, request('tools/call', {'name': 'shell', 'arguments': {'command': 'echo unsafe'}})])
        self.assertEqual(outputs[0]['error']['code'], -32002)
        self.assertEqual(outputs[-1]['error']['code'], -32602)
        self.assertEqual(self.backend.calls, 0)

    def test_invalid_name_types_do_not_crash_protocol(self):
        outputs = exchange(self.handler, [INIT, request('tools/call', {'name': []}),
                           request('tools/call', {'name': {}}), request('ping')])
        self.assertEqual(outputs[1]['error']['code'], -32602)
        self.assertEqual(outputs[2]['error']['code'], -32602)
        self.assertEqual(outputs[3]['result'], {})

    def test_read_only_binding_hides_and_denies_mutating_tools(self):
        handler = BrokerTools(self.inv, models=['forge'])
        self.assertNotIn('gcb_plan', {tool['name'] for tool in handler.definitions()})
        for name, args in [('gcb_plan', {'model': 'forge'}), ('gcb_start', {'plan_id': 'x'})]:
            with self.assertRaises((ValueError, PermissionError)):
                handler.call(name, args)
        self.assertEqual(self.backend.calls, 0)

    def test_host_profiling_delegation_requires_actual_boolean(self):
        for flag in ('false', 1, None):
            with self.assertRaises(ValueError):
                BrokerTools(self.inv, models=['forge'], backend_factory=lambda model: self.backend, allow_profiling=flag)
        with self.assertRaises(ValueError):
            BrokerTools(self.inv, models='forge')

    def test_host_allowlist_and_endpoints_cannot_be_overridden(self):
        for args in ({'model': 'scribe'}, {'model': 'forge', 'endpoint': 'https://paid.example'},
                     {'model': 'forge', 'db': '/private'}, {'model': 'forge', 'grant': 'forged'}):
            with self.assertRaises((ValueError, PermissionError)):
                self.handler.call('gcb_plan', args)
        self.assertEqual(self.backend.calls, 0)

    def test_one_cell_progress_reopen_status_bio_and_dedup(self):
        plan = self.handler.call('gcb_plan', {'model': 'forge'})
        self.assertEqual(self.backend.calls, 0)
        first = self.handler.call('gcb_start', {'plan_id': plan['plan_id']})
        self.assertEqual(first['cells']['observed'], 1)
        self.assertEqual(first['status'], 'paused')
        self.assertEqual(self.handler.call('gcb_status', {'plan_id': plan['plan_id']})['attempts'], 1)
        self.assertTrue(self.handler.call('gcb_bio', {'model': 'forge'})['configurations'])
        for _ in range(8):
            self.handler.call('gcb_start', {'plan_id': plan['plan_id']})
        used = self.backend.calls
        self.assertEqual(self.handler.call('gcb_start', {'plan_id': plan['plan_id']})['status'], 'complete')
        self.assertEqual(self.backend.calls, used)

    def test_preexisting_broader_plan_not_implicitly_authorized(self):
        manager = ProfileManager(self.inv, self.backend)
        plan = manager.plan('forge', call_timeout_seconds=60, max_requests=64)
        with self.assertRaises(PermissionError):
            self.handler.call('gcb_start', {'plan_id': plan['plan_id']})
        self.assertEqual(self.backend.calls, 0)

    def test_cancel_reopen_mcp_resume_preserves_receipt_and_cumulative_request_cap(self):
        # MCP start mints a new grant each time; it must not reset a host-prepared plan's budget.
        for request_cap in (9, 1):
            with self.subTest(request_cap=request_cap), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'pilot.db'
                backend = FictionalBackend()
                with Inventory(path) as inventory:
                    manager = ProfileManager(inventory, backend)
                    plan = manager.plan('forge', contrast=False, max_requests=request_cap,
                                        call_timeout_seconds=45, total_timeout_seconds=300)
                    self.assertEqual(len(plan['cells']), 8)
                    handler = BrokerTools(inventory, models=['forge'], allow_profiling=True,
                                          backend_factory=lambda model: backend)
                    arguments = {'plan_id': plan['plan_id']}
                    first = handler.call('gcb_start', arguments)
                    self.assertEqual(first['cells']['observed'], 1)
                    receipt = handler.call('gcb_evidence', arguments)['attempts'][0]
                    self.assertEqual(handler.call('gcb_cancel', arguments)['status'], 'cancelled')
                    self.assertEqual(backend.calls, 1)
                with Inventory(path) as reopened:
                    handler = BrokerTools(reopened, models=['forge'], allow_profiling=True,
                                          backend_factory=lambda model: backend)
                    self.assertEqual(handler.call('gcb_status', arguments)['status'], 'cancelled')
                    for _ in range(8):
                        result = handler.call('gcb_start', arguments)
                    self.assertEqual(result['status'], 'complete' if request_cap == 9 else 'budget_exhausted')
                    self.assertEqual(result['attempts'], request_cap)
                    self.assertEqual(backend.calls, request_cap)
                    evidence = handler.call('gcb_evidence', arguments)
                    self.assertEqual([row for row in evidence['attempts'] if row['id'] == receipt['id']], [receipt])
                    self.assertEqual(sum(row['cell_id'] == receipt['cell_id'] for row in evidence['attempts']), 1)
                    self.assertGreater(reopened.db.execute('SELECT count(*) FROM profile_grants').fetchone()[0], 1)

    def test_recommendation_scope_is_fixed_and_preview_only(self):
        manager = ProfileManager(self.inv, self.backend)
        manager.research('forge')
        manager.research('scribe')
        preview = self.handler.call('gcb_recommend', {'category': 'coding'})
        self.assertEqual(preview['enforcement'], 'preview_only')
        self.assertEqual(preview['selected_model'], 'forge-fixture-1')
        self.assertTrue(preview['candidates'])
        self.assertEqual({candidate['model_id'] for candidate in preview['candidates']}, {'forge'})
        self.assertNotIn('scribe', json.dumps(preview))
        for config in self.inv.configurations():
            if config.model_id != 'forge':
                self.assertNotIn(config.id, json.dumps(preview))
        for args in ({'category': 'coding', 'allow_remote': True}, {'category': 'coding', 'local_only': False},
                     {'category': 'coding', 'allowed_configurations': None}):
            with self.assertRaises(ValueError):
                self.handler.call('gcb_recommend', args)
        self.assertEqual(self.backend.calls, 0)

    def test_raw_failure_arguments_never_echo_to_protocol(self):
        marker = 'PRIVATE_MCP_CANARY'
        outputs = exchange(self.handler, [INIT, request('tools/call', {'name': 'gcb_bio', 'arguments': {'model': marker}})])
        self.assertTrue(outputs[-1]['result']['isError'])
        self.assertNotIn(marker, json.dumps(outputs))

    def test_weak_observed_category_is_not_presented_as_positive_recommendation(self):
        manager = ProfileManager(self.inv, self.backend)
        plan = manager.plan('forge', contrast=False)
        manager.start(plan['plan_id'], manager.authorize(plan['plan_id']), max_cells=4)
        preview = self.handler.call('gcb_recommend', {'category': 'planning'})
        self.assertEqual(preview['status'], 'selected')
        self.assertEqual(preview['task_fit'], 'not_demonstrated')
        self.assertIn('no positive observed evidence', preview['warnings'][1])

    def test_duplicate_json_keys_and_oversized_input_are_bounded(self):
        outgoing = io.BytesIO()
        incoming = io.BytesIO(b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n' + b'x' * 65537)
        code = serve('gcb', 'test', self.handler.definitions(), self.handler.call, incoming=incoming, outgoing=outgoing)
        self.assertEqual(code, 2)
        results = [json.loads(line) for line in outgoing.getvalue().splitlines()]
        self.assertEqual([r['error']['code'] for r in results], [-32700, -32600])

    def test_installed_compatible_stdio_process_without_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'gcb.sqlite3'
            with Inventory(path):
                pass
            inputs = '\n'.join(json.dumps(item) for item in (INIT, request('tools/list'))) + '\n'
            result = subprocess.run([sys.executable, '-m', 'gcb.mcp', '--db', str(path), '--model', 'qwen3.5:9b'],
                                    input=inputs, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(outputs), 2)
            names = {item['name'] for item in outputs[1]['result']['tools']}
            self.assertIn('gcb_bio', names)
            self.assertNotIn('gcb_start', names)


if __name__ == '__main__':
    unittest.main()
