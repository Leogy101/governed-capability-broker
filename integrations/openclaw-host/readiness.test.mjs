import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createBridge } from "./bridge.mjs";

const SOL = "openai/gpt-6.1-sol";
const TERRA = "openai/gpt-6.1-terra";
const LUNA = "openai/gpt-6-luna";
const VERSION = "2026.11.1";
const SECRET = "SECRET_CANARY_account@example.invalid_/private/credential";
const preflight = model => ({ expectedHostVersion: VERSION, model });
const request = (model, reasoning = "medium") => ({ requestId: randomUUID(), expectedHostVersion: VERSION,
  model, reasoning, agentId: "main", messages: [{ role: "user", content: "Synthetic fixture only." }],
  systemPrompt: "Return JSON.", maxTokens: 512, timeoutMs: 60000 });
const error = diagnostic => Object.assign(new Error(SECRET), { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", diagnostic });
function setup(verify, complete) {
  const current = {};
  const calls = [];
  const api = {
    pluginConfig: { agentId: "main", authProfileId: SECRET,
      modelReasoning: { [SOL]: ["medium", "high"], [TERRA]: ["medium", "high"], [LUNA]: ["medium"] } },
    runtime: { version: VERSION, config: { current: () => current }, llm: {
      complete: async payload => {
        calls.push(payload);
        if (complete) return complete(payload);
        return { provider: "openai", model: payload.model.slice(7), agentId: "main", text: '{"ok":true}',
          execution: { mode: "isolated-agent-runtime", owner: { kind: "harness", id: "codex" } } };
      },
    } },
  };
  const bridge = createBridge(api, { verifySubscription: async (_api, selected) => {
    if (verify) await verify(selected, api);
    return current;
  } });
  return { bridge, api, calls };
}

test("ready means selected route readiness, never account entitlement or effective effort", async () => {
  const { bridge, calls } = setup();
  const out = await bridge.preflight(preflight(SOL));
  assert.equal(out.status, "subscription_route_verified");
  assert.deepEqual(out.readiness, { state: "ready", reason: "subscription_route_verified", scope: "model", accountEntitlement: "unknown" });
  assert.equal(out.result.model, SOL);
  assert.equal(out.result.effectiveReasoning, null);
  assert.equal(out.measurement.hostInvocations, 0);
  assert.equal(calls.length, 0);
});

for (const [reason, state, scope] of [
  ["configured_runtime_unresolved", "authorization_unresolved", "model"],
  ["unsupported_execution_backend", "unsupported", "model"],
  ["missing_authorization", "authorization_unresolved", "model"],
  ["credential_profile_mismatch", "authorization_unresolved", "model"],
  ["auth_plan_unresolved", "authorization_unresolved", "model"],
  ["model_route_unresolved", "authorization_unresolved", "model"],
  ["model_route_mismatch", "authorization_unresolved", "model"],
  ["subscription_route_mismatch", "authorization_unresolved", "model"],
  ["credential_authority_stale", "authorization_unresolved", "shared_execution_state"],
  ["runtime_configuration_changed", "authorization_unresolved", "shared_execution_state"],
  ["bridge_configuration_changed", "authorization_unresolved", "shared_execution_state"],
  ["host_authority_unavailable", "authorization_unresolved", "shared_execution_state"],
]) test(`preflight exposes only the fixed diagnostic ${reason}`, async () => {
  const { bridge, calls } = setup(() => { throw error(reason); });
  const out = await bridge.preflight(preflight(TERRA));
  assert.equal(out.status, "rejected");
  assert.equal(out.code, "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED");
  assert.deepEqual(out.readiness, { state, reason, scope, accountEntitlement: "unknown" });
  assert.equal(out.measurement.hostInvocations, 0);
  assert.equal(calls.length, 0);
  assert.equal(JSON.stringify(out).includes(SECRET), false);
});

for (const malformed of [undefined, null, "ready", "subscription_route_verified", SECRET, { state: "ready", secret: SECRET }, [SECRET]]) {
  test(`untrusted diagnostic ${typeof malformed} cannot claim ready or disclose details`, async () => {
    const { bridge } = setup(() => { throw error(malformed); });
    const out = await bridge.preflight(preflight(TERRA));
    assert.deepEqual(out.readiness, { state: "authorization_unresolved", reason: "runtime_verification_failed",
      scope: "shared_execution_state", accountEntitlement: "unknown" });
    assert.equal(out.status, "rejected");
    assert.equal(JSON.stringify(out).includes(SECRET), false);
  });
}

test("unknown host errors and null throws return safe generic readiness", async () => {
  for (const failure of [Object.assign(new Error(SECRET), { code: SECRET }), null]) {
    const { bridge } = setup(() => { throw failure; });
    const out = await bridge.preflight(preflight(TERRA));
    assert.equal(out.code, "BRIDGE_SUBSCRIPTION_UNVERIFIED");
    assert.equal(out.readiness.state, "authorization_unresolved");
    assert.equal(out.readiness.scope, "shared_execution_state");
    assert.equal(JSON.stringify(out).includes(SECRET), false);
  }
});

test("one model's failed metadata preflight does not invalidate independent authorized work", async () => {
  const { bridge, calls, api } = setup(selected => {
    if (selected.model === TERRA) throw error("model_route_mismatch");
  });
  const before = JSON.stringify(api.pluginConfig);
  const denied = await bridge.preflight(preflight(TERRA));
  assert.equal(denied.readiness.state, "authorization_unresolved");
  assert.equal(denied.readiness.scope, "model");
  assert.equal(denied.measurement.hostInvocations, 0);
  assert.equal((await bridge.preflight(preflight(SOL))).readiness.state, "ready");
  assert.equal((await bridge.complete(request(SOL))).status, "completed");
  const stillDenied = await bridge.complete(request(TERRA));
  assert.equal(stillDenied.status, "rejected");
  assert.equal(stillDenied.measurement.hostInvocations, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].model, SOL);
  assert.equal(JSON.stringify(api.pluginConfig), before, "unavailable model is retained in the configured catalog");
});

test("post-dispatch uncertainty cannot be skipped by switching to an authorized model", async () => {
  const { bridge, calls } = setup(undefined, () => { throw error("model_route_mismatch"); });
  const unknown = await bridge.complete(request(TERRA));
  assert.equal(unknown.status, "unknown_completion");
  assert.equal(unknown.measurement.hostInvocations, 1);
  assert.deepEqual(unknown.readiness, { state: "authorization_unresolved", reason: "execution_completion_unknown",
    scope: "shared_execution_state", accountEntitlement: "unknown" });
  const next = await bridge.preflight(preflight(SOL));
  assert.equal(next.code, "BRIDGE_RECONCILIATION_REQUIRED");
  assert.equal(next.readiness.scope, "shared_execution_state");
  assert.equal((await bridge.complete(request(SOL))).code, "BRIDGE_RECONCILIATION_REQUIRED");
  assert.equal(calls.length, 1);
});

test("forbidden model and reasoning settings remain blocked before authorization", async () => {
  const { bridge, calls } = setup(() => assert.fail("unauthorized request reached authentication"));
  assert.equal((await bridge.preflight(preflight("openai/not-approved"))).code, "BRIDGE_INPUT_REJECTED");
  const effort = await bridge.complete(request(LUNA, "ultra"));
  assert.equal(effort.code, "BRIDGE_INPUT_REJECTED");
  assert.equal(effort.readiness.reason, "reasoning_configuration_mismatch");
  assert.equal(effort.measurement.hostInvocations, 0);
  assert.equal(calls.length, 0);
});

test("busy metadata is temporary shared state and cannot dispatch late or independently", async () => {
  let release;
  const { bridge, calls } = setup(() => new Promise(resolve => { release = resolve; }));
  const pending = bridge.preflight(preflight(TERRA));
  await new Promise(resolve => setImmediate(resolve));
  const busy = await bridge.preflight(preflight(SOL));
  assert.equal(busy.readiness.state, "temporarily_unavailable");
  assert.equal(busy.readiness.scope, "shared_execution_state");
  assert.equal((await bridge.complete(request(SOL))).code, "BRIDGE_BUSY");
  release();
  assert.equal((await pending).readiness.state, "ready");
  assert.equal(calls.length, 0);
});
