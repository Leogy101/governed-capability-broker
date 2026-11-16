# Optional OpenClaw/Codex profiling bridge

This optional plugin connects GCB's existing profiler to host-owned, subscription-backed Codex isolated completions. It is separate from the Python wheel. The Python package receives no credentials. It does not create an API key, install a second Gateway, change an agent's model assignment, or provide a direct OpenAI billing transport.

The verified execution combination is **OpenClaw 2026.11.1 and Codex 0.169.0-alpha.11.1 on macOS arm64**. Other versions and execution backends are unverified. The [compatibility guide](../../docs/public/COMPATIBILITY.md) describes the boundary. This bridge profiles models; native OpenClaw/Hermes governed routing remains **preview-only**.

## Scope and prerequisites

An existing healthy OpenClaw installation must already have a supported Codex runtime and host-managed subscription authentication. The operator must confirm the exact account-accessible catalog, native reasoning choices, model policy and available execution capacity through that installation's supported interfaces. A public model announcement or an offline catalog entry is insufficient.

The bridge registers exactly three authenticated `operator.admin` Gateway operations:

| Operation | Purpose |
|---|---|
| `gcb.profile.preflight` | Metadata-only subscription-route verification; no inference |
| `gcb.profile.complete` | One explicitly scoped isolated host completion |
| `gcb.profile.cancel` | Request cancellation of the matching owned request |

It registers **no agent-facing tools**. Authentication remains with OpenClaw/Codex. The existing profile identifier selects a route; it is not a credential to copy or a guarantee of native account identity. When the host uses Codex `homeScope:"user"`, the native account remains the existing user-home login. The bridge rejects API-key routes, fallback ambiguity and unsupported runtime ownership.

## Prepare a narrow configuration

Two portable templates are supplied:

- [Host snapshot](../../examples/public/host-snapshot.example.json): a fictional model and example metadata accepted by the Python snapshot format.
- [Disabled bridge configuration](../../examples/public/bridge-config.example.json): the same fictional model, agent and reasoning selector, with a placeholder profile identifier.

**These templates are not verified catalog evidence or execution authorization.** Do not activate them unchanged. Make private copies outside the repository and replace every example value using current supported host metadata. The snapshot's context value is illustrative, not a claim about any GPT model. `checked_at` must describe the actual metadata check; selected reasoning must belong to that exact model's native list. Record the installed versions. GCB does not automatically discover or refresh this snapshot.

Pin one existing agent, one or more exact models, their permitted native reasoning values, and one existing healthy subscription route. Both host model allowlists must equal the approved model set. No wildcard is accepted. The example contains only this plugin's entry; preserve other settings and append its ID to `plugins.allow` only when an existing plugin allowlist requires it. Do not replace an existing allowlist with a one-item example.

For multiple models, replace both `model` and `allowedReasoning` with a `modelReasoning` map. Never mix the forms. For example, `{"openai/gpt-example":["medium","high"]}` means exactly those two operator-verified settings; it does not assert that a real model supports them. Keep host `allowedModels` and `allowedCompletionModels` equal to the map's keys.

## Install and activate through supported interfaces

This is an operator procedure, not an automatic installer. Review the exact local configuration diff and restore values first. Use a quiet point, with no unrelated pending configuration, recovery or reload work. Activation can briefly fence or drain admissions. Do not restart the Gateway or broaden permissions merely to make this example work.

1. Check readiness, installed versions, saved/applied configuration agreement, agent activity and plugin state through the existing host. Keep a private rollback record of the original allowlist, plugin entry and reload setting. Do not export credentials into this repository.
2. From the repository root, install the reviewed local package **disabled**:

   ```sh
   openclaw plugins install ./integrations/openclaw-host --no-enable
   ```

   Use normal source confirmation. Do not automatically override an existing installation or unexpected policy warning. A managed copy does not require a manually added load path.
3. Validate the operator-specific disabled patch against the installed host schema:

   ```sh
   openclaw config patch --file /path/to/private/bridge-config.json --dry-run --json
   ```

   If passive automatic reload is enabled, the verified 2026.11.1 procedure temporarily holds `gateway.reload.mode` at `off`, applies only the disabled plugin configuration and necessary allowlist addition, and uses explicit plugin lifecycle activation. That temporary setting is a separate operational change to review. Restore its **exact original value**, including absence, immediately after staging and on failure. `hot` is not a supported replacement for `off`. Do not disable schedules or change assignments.

   Apply the reviewed patch with `openclaw config patch --file /path/to/private/bridge-config.json`. Do not apply unrelated pending changes. Private hold/restore patches are installation-specific; GCB deliberately does not automatically rewrite shared configuration.
4. Enable only this plugin:

   ```sh
   openclaw gateway call plugins.setEnabled --json \
     --params '{"pluginId":"gcb-openclaw-host","enabled":true}' --timeout 90000
   ```

   Require a successful, settled receipt with `restartRequired:false`, unchanged unrelated settings and healthy readiness. Stop on an uncertain outcome or restart requirement; do not repeat uncertain activation. Restore the original reload setting after staging settles, including failure rollback.
5. Run metadata-only verification for the configured single-model entry:

   ```sh
   openclaw gateway call gcb.profile.preflight --json \
     --params '{"expectedHostVersion":"2026.11.1"}'
   ```

   Require `subscription_route_verified`, the expected agent/model and reasoning values. The legacy `model`/`allowedReasoning` form requires omitting the preflight `model` field. In `modelReasoning` map mode, include the exact selector, for example `{"expectedHostVersion":"2026.11.1","model":"openai/gpt-example"}`; a one-model map may omit it. This verifies a subscription route, **not current capacity, effective reasoning or successful inference**. Check ordinary execution availability separately through supported account interfaces. Do not automatically redeem resets, purchase capacity or switch accounts.

The host resolves the actual route. If a static route lacks physical model facts, the bridge can use the current agent-scoped `getPreparedModelCatalogSnapshot` and host auth planner. It never guesses endpoints or receives credentials from GCB. A missing/degraded catalog requires supported catalog refresh or an unavailable status, not weaker authorization.

## Profile through the installed GCB CLI

Research and plan are local preparation from the operator-reviewed snapshot; they do not call a model. This example creates a one-case plan. `PLAN_ID` and `GRANT_ID` mean returned values, not literal arguments.

```sh
gcb --db gpt-profile.db init
gcb --db gpt-profile.db profile research example-gpt \
  --backend openclaw --host-snapshot /path/to/private/host-snapshot.json
gcb --db gpt-profile.db profile plan example-gpt --backend openclaw \
  --host-snapshot /path/to/private/host-snapshot.json \
  --no-contrast --max-requests 1 --call-timeout 60 --total-timeout 60
```

Only after the operator approves that exact usage scope and fresh authorization/capacity checks pass:

```sh
gcb --db gpt-profile.db profile start PLAN_ID --backend openclaw \
  --host-snapshot /path/to/private/host-snapshot.json \
  --approve-host-runtime --authorize --max-cells 1
gcb --db gpt-profile.db profile status PLAN_ID
gcb --db gpt-profile.db bio example-gpt
gcb --db gpt-profile.db evidence PLAN_ID
```

Both `--approve-host-runtime` and a valid bounded grant are required. Larger baseline/contrast plans must be explicitly scoped first. Untouched pending work can use `profile resume PLAN_ID` with the same backend arguments and returned `--grant GRANT_ID`; counts and elapsed time never reset. Completed cells are reused. There is no automatic model campaign, hidden GCB retry or answer repair.

`gcb --db gpt-profile.db profile cancel PLAN_ID` marks the durable plan cancelled. The executing profiler polls it and requests host cancellation. Ctrl-C also signals its owned transport. Neither proves provider termination. An uncertain attempt remains unknown and cannot be silently replayed. The bridge serializes invocations and latches uncertainty after any post-dispatch failure, timeout or cancellation; changing models or reloading the plugin is not reconciliation.

Preflight outcomes separate model-scoped `ready`, `temporarily_unavailable`, `unsupported` and `authorization_unresolved` states. An operator can skip a model rejected **before dispatch** while independently checking another. Shared execution uncertainty stops the batch. Low scores do not make a model technically unavailable.

## Disable and remove

After requests settle and owned workers stop, disable only the bridge:

```sh
openclaw gateway call plugins.setEnabled --json \
  --params '{"pluginId":"gcb-openclaw-host","enabled":false}' --timeout 90000
openclaw plugins uninstall gcb-openclaw-host
```

Use supported uninstall preview/confirmation. Remove only this plugin's configuration, LLM policy, installation record and allowlist addition as appropriate; restore the original reload setting. Preserve concurrent unrelated edits, shared authentication, GCB observations and agents. The host may retain an explicit disabled marker. Confirm readiness, saved/applied agreement, endpoint removal and unchanged permissions/assignments. Deferred retired-instance diagnostics do not themselves prove active failure, nor does readiness prove physical disposal of every retired resource. Unresolved active work needs operator review before removal; never force a restart or delete shared authentication to clear it.

## Measurement and security limits

- One GCB invocation counts one `api.runtime.llm.complete` call, not hidden provider requests or retries.
- The verified host reports uncached input, separate cache-read/write input, output and a total. GCB normalizes prompt input once and reports cache as a subset. Missing categories stay unknown.
- `costUsd` does not reliably distinguish subscription charges from estimates. It remains unclassified; zero is not proof of free execution.
- Requested reasoning and returned model attribution are recorded. Effective effort, reasoning-token counts and immutable GPT weight revisions are not independently observable.
- The native interface does not enforce the 512-token hint. The 60-second deadline bounds software waiting, not guaranteed provider termination, output size or billing.
- Isolated completions have no external tool surface. Text describing a tool call is not verified tool execution.
- Only synthetic fixture input, schemas and benchmark instructions are sent by the profiler. GCB does not attach an existing chat or project corpus. Host-added context is outside this adapter's complete prompt visibility.
- Model assignment changes, live governed routing and automatic credential/catalog management are outside this bridge.

## Offline checks

From the repository root:

```sh
node --test integrations/openclaw-host/*.test.mjs
```

Tests use fake APIs and synthetic records, covering pins, permitted model/effort combinations, catalog routes, subscription-only authorization, privacy, missing usage, cancellation, timeouts and uncertainty/no-replay. Python adapter tests exercise the same wire contract. They perform no live inference and do not prove account entitlement or capacity. The [interfaces guide](../../docs/public/INTERFACES.md) covers CLI, Python, MCP and the independent KRE context contract.
