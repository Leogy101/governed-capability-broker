"""Optional stdio tools for a fixed local model allowlist; recommendations are previews."""
import argparse
from dataclasses import asdict
from pathlib import Path
import sqlite3
import sys

from . import __version__
from ._stdio import serve, shape, tool
from .inventory import Inventory
from .models import RoutingPolicy, TaskRequirements
from .profiler import ProfileManager
from .routing import preview_route
from .validation import identifier


class BrokerTools:
    def __init__(self, inventory, *, models, backend_factory=None, allow_profiling=False):
        if not isinstance(models, (tuple, list)) or not models or len(models) > 32 or len(set(models)) != len(models):
            raise ValueError('bounded_model_allowlist_required')
        if type(allow_profiling) is not bool or (backend_factory is not None and not callable(backend_factory)):
            raise ValueError('typed_host_delegation_required')
        self.models = tuple(identifier(model) for model in models)
        self.inventory, self.backend_factory, self.allow_profiling = inventory, backend_factory, allow_profiling
        if allow_profiling and backend_factory is None:
            raise ValueError('fixed_host_backend_required')

    def definitions(self):
        model = {'type': 'string', 'enum': list(self.models)}
        plan = {'type': 'string', 'maxLength': 128}
        tools = [tool('gcb_models', 'List the operator-allowed model IDs and whether a bio exists.', {}),
                 tool('gcb_status', 'Read durable profiling progress for an allowed model.', {'plan_id': plan}, ('plan_id',)),
                 tool('gcb_bio', 'Read a descriptive model bio. Tiny samples are not qualification or broad competence.',
                      {'model': model}, ('model',)),
                 tool('gcb_evidence', 'Inspect bounded benchmark observations and diagnostics; arbitrary output/thinking is omitted.',
                      {'plan_id': plan}, ('plan_id',)),
                 tool('gcb_recommend', 'Preview local allowed configurations using current comparable observations. '
                      'Does not dispatch inference, change agent models or enforce native routing.',
                      {'category': {'type': 'string'}, 'context_tokens': {'type': 'integer', 'minimum': 0},
                       'output_tokens': {'type': 'integer', 'minimum': 1}, 'require_tools': {'type': 'boolean'}}, ('category',))]
        if self.allow_profiling:
            tools += [tool('gcb_research', 'Research and cache exact local model metadata before any benchmark. No inference.',
                           {'model': model}, ('model',), readonly=False),
                      tool('gcb_plan', 'Plan the small baseline and optional short contrasts after model research. '
                           'Use a researched baseline_configuration_id to select an explicit nondefault setting.',
                           {'model': model, 'contrast': {'type': 'boolean'}, 'baseline_configuration_id': plan},
                           ('model',), readonly=False),
                      tool('gcb_start', 'Run or resume one pending benchmark cell within the preauthorized local plan. '
                           'Inspect status/bio and call again for remaining cells. Completed cells are not repeated; '
                           'unknown completion requires review, not automatic retry.',
                           {'plan_id': plan}, ('plan_id',), readonly=False),
                      tool('gcb_cancel', 'Cancel a pending profile between bounded tool calls; completed evidence remains.',
                           {'plan_id': plan}, ('plan_id',), readonly=False)]
        return tools

    def _allowed(self, model):
        if model not in self.models:
            raise PermissionError('model_not_in_operator_allowlist')
        return model

    def _manager(self, model):
        self._allowed(model)
        backend = self.backend_factory(model) if self.backend_factory else None
        return ProfileManager(self.inventory, backend)

    def _plan_manager(self, plan_id):
        identifier(plan_id)
        row = self.inventory.db.execute('SELECT model_id FROM profile_plans WHERE id=?', (plan_id,)).fetchone()
        if row is None:
            raise ValueError('plan_not_found')
        return self._manager(row['model_id'])

    def call(self, name, args):
        if name == 'gcb_models':
            shape(args)
            present = {model.id for model in self.inventory.models()}
            return {'models': [{'model_id': model, 'researched': model in present} for model in self.models],
                    'profiling_enabled': self.allow_profiling, 'native_routing': 'preview_only'}
        if name == 'gcb_bio':
            shape(args, ('model',))
            return self._manager(args['model']).bio(args['model'])
        if name in {'gcb_status', 'gcb_evidence', 'gcb_start', 'gcb_cancel'}:
            shape(args, ('plan_id',))
            if name in {'gcb_start', 'gcb_cancel'} and not self.allow_profiling:
                raise PermissionError('profiling_not_enabled')
            manager = self._plan_manager(args['plan_id'])
            if name == 'gcb_start':
                _, body = manager._plan(args['plan_id'])
                if (body['max_requests'] > 16 or body['call_timeout_seconds'] > 45
                        or body['total_timeout_seconds'] > 300 or body['concurrency'] != 1):
                    raise PermissionError('plan_exceeds_mcp_delegation')
                # This server's fixed startup binding is the trusted delegation. Tool data cannot enlarge it.
                return manager.resume(args['plan_id'], manager.authorize(args['plan_id']), max_cells=1)
            return getattr(manager, {'gcb_status': 'status', 'gcb_evidence': 'evidence', 'gcb_cancel': 'cancel'}[name])(args['plan_id'])
        if name in {'gcb_research', 'gcb_plan'} and self.allow_profiling:
            shape(args, ('model',), () if name == 'gcb_research' else ('contrast', 'baseline_configuration_id'))
            manager = self._manager(args['model'])
            if name == 'gcb_research':
                return manager.research(args['model'])
            return manager.plan(args['model'], contrast=args.get('contrast', False),
                                baseline_configuration_id=args.get('baseline_configuration_id'),
                                call_timeout_seconds=45, total_timeout_seconds=300, max_requests=16)
        if name == 'gcb_recommend':
            shape(args, ('category',), ('context_tokens', 'output_tokens', 'require_tools'))
            task = TaskRequirements(**args, local_only=True)
            configs = tuple(config.id for config in self.inventory.configurations() if config.model_id in self.models)
            preview = preview_route(self.inventory, task, RoutingPolicy(allowed_configurations=configs))
            result = asdict(preview)
            # Core previews explain exclusions across the trusted inventory. The
            # adapter must not disclose identities outside its host allowlist.
            result['candidates'] = [candidate for candidate in result['candidates']
                                    if candidate['model_id'] in self.models]
            if preview.selected_configuration_id:
                config = self.inventory.configuration(preview.selected_configuration_id)
                result['selected_model'] = self.inventory.model(config.model_id).native_id
                result['selected_native'] = config.native
                candidate = next(item for item in preview.candidates if item.configuration_id == config.id)
                result['task_fit'] = ('limited_positive_observations' if candidate.comparable_correct else 'not_demonstrated')
                result['warnings'] = ['Tiny task samples do not establish broad competence.']
                if not candidate.comparable_correct:
                    result['warnings'].append('Eligibility selection has no positive observed evidence for this task; inspect the bio.')
            return result
        raise ValueError('tool_not_enabled')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--model', action='append', required=True, help='Exact allowed local native model tag; repeat as needed')
    parser.add_argument('--endpoint', default='http://127.0.0.1:11434')
    parser.add_argument('--context-tokens', type=int, default=4096)
    parser.add_argument('--output-cap', type=int, default=512)
    parser.add_argument('--allow-profiling', action='store_true', help='Delegate bounded local profiling; no paid/remote provider support')
    args = parser.parse_args(argv)
    if not args.db.is_file():
        parser.error('Initialize an owned GCB database before enabling the server')
    try:
        from .ollama import OllamaBackend
        def backend(model):
            return OllamaBackend(args.endpoint, model, context_tokens=args.context_tokens, output_cap=args.output_cap)
        # Validate fixed startup settings without reading metadata or invoking a model.
        for model in args.model:
            backend(model)
        with Inventory(args.db) as inventory:
            handler = BrokerTools(inventory, models=args.model, backend_factory=backend,
                                  allow_profiling=args.allow_profiling)
            return serve('gcb', __version__, handler.definitions(), handler.call)
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        print('gcb: invalid or unavailable operator binding', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
