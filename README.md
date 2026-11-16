<p align="center"><img src="assets/gcb-logo.svg" alt="Governed Capability Broker" width="680"></p>

# Governed Capability Broker

**Research models. Profile capabilities. Explain routing decisions.**

GCB is a lightweight Python toolkit for deterministic operations, model capability research, built-in benchmarks and policy-aware routing previews. It works independently, stores observations locally in SQLite and has **no Python runtime dependencies**.

**Experimental alpha `0.3.2a1`.** The Apache-2.0 [public source](https://github.com/Leogy101/governed-capability-broker) is a curated snapshot; private development history is excluded. Release artifacts must pass the [release verification gates](docs/public/RELEASE_PLAN.md).

Task suitability comes from observed profiles, **not universal model certification**. Weak results remain visible. Unknown usage stays unknown. Native OpenClaw and Hermes routing paths remain **preview-only**: a recommendation does not dispatch or reassign an agent.

## What works

| Capability | Alpha behavior |
|---|---|
| Deterministic Gate 0 | Explicit bounded arithmetic, record selection and permission-checked inventory inspection; trusted Python handlers can extend it |
| Research before benchmarking | Cache exact model/backend identity, native reasoning controls, defaults and cited metadata before planning |
| Local profiling | Explicit installed Ollama tag, loopback-only API; tested GGUF and Ollama-managed MLX/safetensors deployments |
| GPT profiling | Optional OpenClaw/Codex bridge using host-owned subscription authentication, scoped administrator operations and no agent-facing tools |
| Integrated benchmarks | Six baseline fixtures; two contrasts for additional native settings; native fixture tools only where supported |
| Capability bios | Descriptive strengths, weaknesses, coverage, usage, latency and uncertainty per configuration |
| Durable execution | Persist attempts before dispatch and save each observation; resume untouched work without replaying unknown completion |
| Routing previews | Explain policy eligibility and comparable evidence; no native live-routing enforcement claim |

See the [architecture](docs/public/ARCHITECTURE.md), [compatibility matrix](docs/public/COMPATIBILITY.md) and [limitations](docs/public/LIMITATIONS.md).

## Five-minute offline quickstart

Requires Python 3.11+; the offline workflow is verified on macOS arm64 and Ubuntu x64. No model runtime, credentials or network service is needed for the demo. From the reviewed source checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
gcb doctor
gcb --db demo.sqlite3 profile demo
gcb --db demo.sqlite3 bio forge
gcb --db demo.sqlite3 route --task '{"category":"code_review"}'
gcb --db demo.sqlite3 route --task '{"category":"research"}'
gcb gate --operation math.calculate --input '{"operator":"multiply","operands":[6,7]}'
```

Fictional Forge and Scribe have deliberately different strengths. The route commands explain those observations without calling a real model. Gate 0 returns the exact fraction `42/1`. Repeating the demo reuses completed cells. [Example bio](examples/public/synthetic-bio.json).

Installation may download ordinary Python build tooling; GCB has no runtime dependency or background service. Demo timing and scores are synthetic, not provider measurements.

## Onboard an existing model

1. **Ollama:** choose an installed tag, research its metadata, inspect the bounded plan, then explicitly authorize profiling. GCB does not download or convert models. Follow the [CLI walkthrough](docs/public/ONBOARDING.md).
2. **OpenClaw/Codex:** an operator installs the optional bridge and supplies a reviewed, nonsecret catalog snapshot. Subscription authentication stays with the host. Follow the [bridge setup](integrations/openclaw-host/README.md). GCB does not automatically discover account entitlements or replace them with API billing.
3. Inspect `bio`, `evidence` and `route` output. Profiler bounds are distinct from provider billing/cancellation guarantees. Stop and reconcile uncertain completion; never force replay.

Use a quiet environment appropriate for the model. Private campaign scheduling/resource monitors are not part of the public CLI. GCB does not promise to protect other applications from memory contention.

## Measured evidence

The GPT campaign covered **seven models, 40 requested reasoning configurations and 108 observations**: six baseline cases per model plus two contrasts per additional setting. All matched the small frozen criteria. Host-reported usage was **495,107 input + 2,962 output = 498,069 tokens**, including 185,856 cached input tokens already counted in input. Two smoke observations were reused; the 106-call continuation took **6m16s active / 25m45s elapsed**, including checks and waits.

The fixtures were limited in difficulty. Equal scores do not establish equal overall capability. Higher effort produced no correctness improvement on the two contrasts; effective reasoning was not independently echoed. Underlying requests, reasoning tokens and actual subscription charges remain unknown. No general superiority, guaranteed savings or API-cost claim follows.

Local Ollama experiments also produced real bios, including failures, resource limits and partial coverage. Read the [methodology and model tables](docs/public/BENCHMARKS.md), [GPT bio](examples/public/gpt-medium-bio.json) and [partial local bio](examples/public/local-partial-bio.json). Public examples contain curated technical fields, not private account or Gateway diagnostics.

```sh
python examples/public/recompute.py
```

That command checks public evidence arithmetic offline. Repeating live profiling requires the operator's own runtime, installed models, entitlement and usage authorization. Latency and stochastic answers will vary.

## Integrations and security

- Python and JSON CLI work independently of a harness.
- The optional [MCP interface](docs/public/INTERFACES.md) is read-only by default; local profiling needs explicit startup delegation.
- The OpenClaw bridge exposes only three administrator operations. Subscription usage is not API billing.
- Read [security and privacy](SECURITY.md) for data boundaries, trusted extensions and interruption limits.
- No mandatory cloud service, external benchmark suite, vector database or KRE installation.

## Works Well With

**Knowledge Retrieval Engine (KRE)** is an independent project for local evidence retrieval, exact citations and bounded context. GCB can consume the versioned contract without importing or installing KRE. KRE remains private; no public URL is asserted. See the [interoperability contract](contracts/context/0.1/README.md).

## Contributing and verification

Licensed under [Apache-2.0](LICENSE). Copyright 2026 Brandon Gordon and GCB contributors. See [NOTICE](NOTICE), [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md), [release notes](docs/public/RELEASE_NOTES.md), [licensing details](docs/public/LICENSING.md) and [third-party attribution](THIRD_PARTY_NOTICES.md).

GitHub Actions runs offline tests and package installation; CodeQL checks the source separately. See the [verification record](docs/public/VERIFICATION.md) for actual completed results and platform scope. No live model calls belong in CI.
