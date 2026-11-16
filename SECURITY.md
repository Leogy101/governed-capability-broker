# Security and privacy

This experimental alpha performs explicit deterministic operations and bounded profiling. Native harness routing remains preview-only. It is not a security sandbox, universal enforcement layer or guaranteed spending limiter.

## Data boundaries

- Offline demos/tests use synthetic fixtures and require no credentials or real inference.
- Ollama receives bundled public input/schema and fixture-tool feedback at an explicit loopback HTTP endpoint. Redirects, proxies and cloud-backed metadata are rejected. Do not expose an untrusted service there.
- The optional bridge sends synthetic tasks through an operator-approved OpenClaw/Codex subscription route. Authentication stays with the host. GCB does not copy OAuth tokens, create API keys or change shared logins.
- Local SQLite stores research/identity, observations, usage and provenance. Standard evidence excludes arbitrary model text, hidden thinking and raw exception bodies. Caller-supplied IDs/research citations can themselves be sensitive; do not put secrets there.
- Public examples contain curated technical fields. Account diagnostics, private infrastructure settings and development transcripts are excluded. Provider retention/privacy rules still apply to real remote execution.

## Protections and limits

Research precedes immutable plans; trusted grants precede dispatch. Attempts are reserved durably. Completed/uncertain cells are not automatically repeated. Identity/policy drift and missing required metadata fail closed. Technical failures remain distinct from weak performance.

Timeouts bound local waiting, not provider termination, hidden request counts, output enforcement or billing. A cancellation request does not establish remote completion. Do not reset a ledger, reload a plugin or switch models to bypass uncertainty. Reconcile before new authorization.

Gate 0 handlers and injected backends are trusted Python extensions, not sandboxed code. Generated benchmark code is never executed. Native fixtures call only a fixed pure lookup. Isolated Codex has no native tool surface.

The ordinary package does not monitor another application's schedules or memory. Operators must choose suitable hardware and safe windows. Private campaign monitoring is not shipped.

## Disclosure

No public security-reporting channel exists yet. Keep vulnerabilities private and contact the maintainer through the channel that supplied this candidate. The approved target for future publication is `Leogy101/governed-capability-broker`. The publication procedure must enable and verify GitHub private vulnerability reporting before pushing source. Once published with that feature enabled, use the repository's **Security → Report a vulnerability** action. Do not post credentials or private transcripts in public issues. No unverified reporting email address is supplied.

## Publication boundary

Existing development history contains private operational material and must not be pushed or mirrored publicly. The approved public snapshot is an explicit allowlist export with a fresh public root history; it excludes historical verification archives, private scripts and account snapshots. The exporter does not copy private `.git` metadata. Hashes preserve provenance while internal evidence stays private; the original history is not rewritten.

Scans are checks, not proof that every secret format is detectable. Review the exact source archive, wheel, sdist, documentation and commit metadata before publishing. Never attach private verification logs as public CI artifacts.
