# Governed Capability Broker 0.3.2a1

**Experimental alpha. Release version/tag: `0.3.2a1` / `v0.3.2a1`.**

Apache-2.0 is adopted, with copyright attribution to 2026 Brandon Gordon and GCB contributors. The [public source](https://github.com/Leogy101/governed-capability-broker) uses a clean snapshot; private operational history is excluded.

GCB researches model settings, executes a small integrated profile, preserves descriptive observations and explains policy-aware routing previews. Deterministic Gate 0 handles explicit supported operations without an LLM. The independent Python/SQLite package has no runtime dependencies and no KRE requirement.

This candidate includes the existing local Ollama adapter and optional host-owned OpenClaw/Codex subscription bridge. Tested GPT evidence covers seven models, 40 requested settings and 108 correct small-fixture observations. Local evidence includes failures and partial coverage. See [methodology](BENCHMARKS.md); these results do not establish broad model equivalence or general savings.

Verified support is deliberately narrow: live profiling on macOS arm64 with selected Ollama deployments and OpenClaw 2026.11.1/Codex 0.169.0-alpha.11.1; offline CI on macOS arm64 and Ubuntu x64 across Python 3.11–3.14. The [compatibility matrix](COMPATIBILITY.md) separates these paths from preview-only governance and unverified live platforms. Effective reasoning, provider request accounting and subscription billing can remain unknown. Native Codex tool execution is unmeasured.

Start with the [offline demo](../../README.md#five-minute-offline-quickstart), then the [model onboarding guide](ONBOARDING.md). Do not interpret timeouts as billing guarantees or replay uncertain attempts. No automatic live routing or new service is installed.

Release artifacts must match the final verified source, provenance and checksums. [Private vulnerability reporting](https://github.com/Leogy101/governed-capability-broker/security/advisories/new) is enabled. The [verification record](VERIFICATION.md) reports actual CI results and their scope; offline tests do not establish live-provider behavior on every platform. This release does not include publication to PyPI or another package registry.
