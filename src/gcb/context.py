"""Inspect the carried context/0.1 envelope without importing KRE.

This is a local consumer boundary, not authentication or a permission grant.
The trusted host must ask the source to revalidate scope/revision immediately
before any future dispatch. Retrieved text is never interpreted as policy.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
import re


class ContextRejected(ValueError):
    """Malformed, incompatible, or restricted evidence; no source text echoed."""


@dataclass(frozen=True)
class ContextConstraints:
    request_id: str
    context_revision: str
    scope_fingerprint: str
    local_only: bool
    context_bytes: int
    estimated_tokens: int
    item_count: int
    citation_ids: tuple[str, ...]

    def constrain_task(self, task):
        """Add this envelope once to existing input; only tighten restrictions."""
        return replace(task, local_only=task.local_only or self.local_only,
                       context_tokens=task.context_tokens + self.estimated_tokens)


def _require(condition, code):
    if not condition:
        raise ContextRejected(code)


def _object(value, required, optional=()):
    _require(type(value) is dict, 'INVALID_OBJECT')
    _require(set(required) <= value.keys() <= set(required) | set(optional), 'INVALID_FIELDS')


def _string(value, maximum=128, minimum=1):
    _require(type(value) is str and minimum <= len(value) <= maximum, 'INVALID_STRING')


def _integer(value, minimum=0, maximum=2**63-1):
    _require(type(value) is int and minimum <= value <= maximum, 'INVALID_INTEGER')


def _timestamp(value):
    _string(value, 64)
    _require(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)', value), 'INVALID_TIMESTAMP')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ContextRejected('INVALID_TIMESTAMP') from None
    _require(parsed.utcoffset() == timedelta(0), 'INVALID_TIMESTAMP')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'DUPLICATE_RENDERED_FIELD')
        result[key] = value
    return result


def inspect_context(request, response, *, expected_scope_fingerprint=None):
    """Validate the G0/K0 subset and return restrictions, never authorization.

    Actual UTF-8 length, excerpt hashes, rendering, correlation and declared
    budgets are checked. Token estimates do not replace final prompt counting.
    Input must already be decoded JSON from a trusted local retrieval handle.
    """
    try:
        encoded = json.dumps([request, response], ensure_ascii=False, allow_nan=False).encode('utf8')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ContextRejected('INVALID_JSON') from None
    _require(len(encoded) <= 1048576, 'ENVELOPE_TOO_LARGE')
    _object(request, ('contract_version', 'request_id', 'query', 'access_scope_ref',
                      'destination', 'budget', 'purpose'), ('freshness_requirement', 'source_ids'))
    _require(request['contract_version'] == '0.1', 'UNSUPPORTED_VERSION')
    for key in ('request_id', 'access_scope_ref'):
        _string(request[key])
    _string(request['query'], 8192)
    _require(request['destination'] in ('local_only', 'approved_remote'), 'INVALID_DESTINATION')
    _require(request['purpose'] in ('current_answer', 'historical_evidence'), 'INVALID_PURPOSE')
    _require(request.get('freshness_requirement', 'allow_unknown') in ('allow_unknown', 'fresh_only'), 'INVALID_FRESHNESS')
    if 'source_ids' in request:
        _require(type(request['source_ids']) is list and 1 <= len(request['source_ids']) <= 64, 'INVALID_SOURCES')
        for source in request['source_ids']:
            _string(source)
    budget = request['budget']
    _object(budget, ('max_tokens', 'max_items', 'max_bytes'))
    for key, maximum in [('max_tokens', 32768), ('max_items', 32), ('max_bytes', 262144)]:
        _integer(budget[key], 1, maximum)
    _object(response, ('contract_version', 'request_id', 'status', 'context_revision',
                      'generated_at', 'scope_fingerprint', 'items', 'context_text',
                      'token_usage', 'coverage', 'omissions', 'warnings', 'error'))
    _require(response['contract_version'] == '0.1', 'UNSUPPORTED_VERSION')
    _require(response['request_id'] == request['request_id'], 'REQUEST_MISMATCH')
    for key in ('context_revision', 'scope_fingerprint'):
        _string(response[key])
    if expected_scope_fingerprint is not None:
        _require(response['scope_fingerprint'] == expected_scope_fingerprint, 'SCOPE_MISMATCH')
    _timestamp(response['generated_at'])
    _require(response['status'] in ('ok', 'partial'), 'RETRIEVAL_NOT_USABLE')
    _require(response['error'] is None, 'RETRIEVAL_NOT_USABLE')
    _require(response['coverage'] in ('sufficient', 'partial', 'unknown'), 'INVALID_COVERAGE')
    warnings = response['warnings']
    _require(type(warnings) is list and len(warnings) <= 32, 'INVALID_WARNINGS')
    for warning in warnings:
        _string(warning, minimum=0)
    omissions = response['omissions']
    _require(type(omissions) is dict and len(omissions) <= 8, 'INVALID_OMISSIONS')
    for count in omissions.values():
        _integer(count)
    items = response['items']
    _require(type(items) is list and len(items) <= budget['max_items'], 'ITEM_BUDGET_EXCEEDED')
    _string(response['context_text'], 262144, 0)
    size = len(response['context_text'].encode('utf8'))
    _require(size <= budget['max_bytes'], 'BYTE_BUDGET_EXCEEDED')
    try:
        rendered = json.loads(response['context_text'], object_pairs_hook=_unique_object) if response['context_text'] else []
    except (ValueError, RecursionError):
        raise ContextRejected('INVALID_RENDERING') from None
    _require(json.dumps(rendered, sort_keys=True) == json.dumps(items, sort_keys=True)
             and (bool(items) or not response['context_text']), 'RENDERING_MISMATCH')
    usage = response['token_usage']
    _object(usage, ('estimated_tokens', 'counter_kind', 'counter_id', 'counter_revision',
                    'context_bytes', 'includes_citations'))
    _integer(usage['estimated_tokens'], 0, budget['max_tokens'])
    _integer(usage['context_bytes'], 0, 262144)
    _require(usage['context_bytes'] == size and usage['includes_citations'] is True, 'ACCOUNTING_MISMATCH')
    _require(usage['counter_kind'] in ('exact', 'conservative_bytes'), 'INVALID_COUNTER')
    for key in ('counter_id', 'counter_revision'):
        if usage[key] is not None:
            _string(usage[key], minimum=0)
    if usage['counter_kind'] == 'conservative_bytes':
        _require(usage['estimated_tokens'] == size, 'ACCOUNTING_MISMATCH')
    citations = set()
    identities = set()
    local_only = request['destination'] == 'local_only'
    for item in items:
        _object(item, ('citation_id', 'source_id', 'revision_id', 'chunk_id', 'text',
                       'content_sha256', 'locator', 'recorded_at', 'authority',
                       'origin', 'lifecycle', 'freshness', 'egress'))
        for key in ('citation_id', 'source_id', 'revision_id', 'chunk_id'):
            _string(item[key])
        _require(item['citation_id'] not in citations, 'DUPLICATE_CITATION')
        identity = (item['source_id'], item['revision_id'], item['chunk_id'])
        _require(identity not in identities, 'DUPLICATE_EVIDENCE')
        citations.add(item['citation_id']); identities.add(identity)
        _string(item['text'], 262144, 0)
        _require(item['content_sha256'] == hashlib.sha256(item['text'].encode('utf8')).hexdigest(), 'HASH_MISMATCH')
        _timestamp(item['recorded_at'])
        _object(item['locator'], ('label', 'line_start', 'line_end'))
        _string(item['locator']['label'], 2048, 0)
        _integer(item['locator']['line_start'], 1)
        _integer(item['locator']['line_end'], item['locator']['line_start'])
        _require(item['authority'] in ('authoritative', 'unverified'), 'INVALID_AUTHORITY')
        _require(item['origin'] in ('source_evidence', 'user_assertion', 'model_inference'), 'INVALID_ORIGIN')
        _require(item['lifecycle'] in ('active', 'superseded'), 'INVALID_LIFECYCLE')
        _require(request['purpose'] != 'current_answer' or item['lifecycle'] == 'active', 'INACTIVE_EVIDENCE')
        _require(item['freshness'] in ('fresh', 'stale', 'unknown'), 'INVALID_FRESHNESS')
        _require(request['purpose'] != 'current_answer' or item['freshness'] != 'stale', 'STALE_EVIDENCE')
        _require(request.get('freshness_requirement') != 'fresh_only' or item['freshness'] == 'fresh', 'STALE_EVIDENCE')
        _require('source_ids' not in request or item['source_id'] in request['source_ids'], 'SOURCE_MISMATCH')
        _object(item['egress'], ('restriction',))
        _require(item['egress']['restriction'] in ('local_only', 'approved_remote'), 'INVALID_EGRESS')
        local_only |= item['egress']['restriction'] == 'local_only'
        _require(request['destination'] != 'approved_remote' or item['egress']['restriction'] == 'approved_remote', 'EGRESS_DENIED')
    return ContextConstraints(request['request_id'], response['context_revision'],
                              response['scope_fingerprint'], local_only, size,
                              usage['estimated_tokens'], len(items), tuple(sorted(citations)))
