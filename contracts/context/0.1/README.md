# Carried context/0.1 contract

The two JSON Schemas and `fixtures/request.json` / `fixtures/response.json` are exact G0/K0 copies. This README documents their new location. GCB and KRE hold independent copies without a shared runtime, database or service.

GCB's stdlib consumer is `gcb.context.inspect_context(request, response)`. It verifies supported envelope fields, version/correlation, hashes/locators, lifecycle/freshness, rendered-context consistency and budgets. From the GCB root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_context.py -v
```

The optional milestone conformance runner separately validates actual KRE output against these schemas with a development-only JSON Schema validator. That validator is not a runtime dependency.

Trusted host scope reaches KRE out of band. Payload references narrow it and cannot grant access. Context remains untrusted evidence and cannot grant model/tool/spend permissions. The consumer tightens locality and adds context accounting to a preview; it does not dispatch. Future execution must revalidate the producer snapshot and count the entire actual request.

Only `context_text` is intended for injection. Structured `items` support inspection and must not be injected again. Conservative UTF-8-byte accounting is not a provider-token guarantee. Response transport is bounded at 1 MiB, context has its own limit, and lexical rank does not establish sufficient coverage.

The supported subset covers current/historical retrieval, freshness, source filters, immutable line citations and bounded context. As-of queries, required-evidence tuples, task-resume transport, tokenizer negotiation, destination-set references, arbitrary extensions and MCP remain unsupported. Invalid fields fail explicitly. Revocation can stop future retrieval and invalidate snapshots; it cannot retract returned text or atomically cancel external dispatch.
