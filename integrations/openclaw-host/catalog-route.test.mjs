import test from "node:test";
import assert from "node:assert/strict";
import { verifySubscription, createBridge } from "./bridge.mjs";

const model = "openai/gpt-6.1-terra";
const config = { agentId: "main", model, allowedReasoning: ["medium"], authProfileId: "synthetic-private-profile" };
const nativeId = model.slice(7);
const variant = { provider: "openai", id: nativeId, api: "synthetic-subscription-api", baseUrl: "https://private-route.invalid" };
const snapshot = () => ({ entries: [{ provider: "openai", id: nativeId }], routeVariants: [{ ...variant }], authoritative: true });
function host() {
  const cfg = {};
  return { pluginConfig: { ...config }, runtime: {
    version: "2026.11.1", config: { current: () => cfg },
    modelConfig: { resolveModelRuntimePolicy: () => ({ policy: { id: "codex" } }) },
    agent: { resolveAgentDir: () => "/synthetic/private-agent", resolveAgentWorkspaceDir: () => "/synthetic/private-workspace" },
    modelAuth: { ensureAuthProfileStoreAsync: async (_dir, options) => {
      assert.equal(options.readOnly, true); assert.equal(options.allowKeychainPrompt, false);
      return { profiles: { [config.authProfileId]: { provider: "openai", type: "oauth", access: "SECRET_CANARY" } } };
    } },
    llm: { complete: () => assert.fail("metadata must never invoke inference") },
  } };
}
function planner(captured, overrides = {}) {
  return params => {
    captured.push(params);
    assert.equal(params.allowAuthProfileFallback, false);
    assert.equal(params.sessionAuthProfileId, config.authProfileId);
    const plan = { forwardedAuthProfileId: config.authProfileId, harnessAuthProvider: "openai" };
    if (params.observedRoutes) plan.modelRoute = { provider: "openai", modelId: nativeId,
      authRequirement: "subscription", runtimePolicy: { compatibleIds: ["codex"] }, ...overrides };
    return { attempts: [{ kind: "profile", profileId: config.authProfileId, plan }] };
  };
}

test("native catalog-only route is verified through the same pinned planner", async () => {
  const api = host(), captured = [];
  const cfg = await verifySubscription(api, config, planner(captured), args => {
    assert.equal(args.config, api.runtime.config.current());
    assert.equal(args.agentId, config.agentId);
    assert.equal(args.agentDir, "/synthetic/private-agent");
    assert.equal(args.workspaceDir, "/synthetic/private-workspace");
    assert.equal(args.readOnly, true);
    return snapshot();
  });
  assert.equal(cfg, api.runtime.config.current());
  assert.equal(captured.length, 2);
  assert.equal(captured[0].observedRoutes, undefined);
  assert.deepEqual(captured[1].observedRoutes, [{ api: variant.api, baseUrl: variant.baseUrl }]);
  assert.equal(captured[0].authProfileStore, captured[1].authProfileStore);
});

test("concrete Sol-style route needs no fallback read", async () => {
  const api = host();
  await verifySubscription(api, config, params => planner([])({ ...params, observedRoutes: [] }),
    () => assert.fail("unnecessary catalog lookup"));
});

for (const [name, change] of Object.entries({
  missing: () => undefined,
  degraded: value => ({ ...value, authoritative: false }),
  malformed: () => ({ entries: [] }),
  identityOnly: () => ({ entries: [{ provider: "openai", id: nativeId, nativeRuntime: "codex" }], routeVariants: [] }),
  otherModel: value => ({ ...value, routeVariants: [{ ...variant, id: "gpt-6.1-sol" }] }),
  otherProvider: value => ({ ...value, routeVariants: [{ ...variant, provider: "other" }] }),
  noTransport: value => ({ ...value, routeVariants: [{ provider: "openai", id: nativeId }] }),
  // Do not substitute logical entries for an existing physical variant set.
  wrongPhysicalDonor: value => ({ entries: [{ ...variant }], routeVariants: [{ ...variant, id: "wrong" }] }),
})) test(`missing or unsuitable catalog route remains blocked: ${name}`, async () => {
  const captured = [];
  await assert.rejects(verifySubscription(host(), config, planner(captured), () => change(snapshot())),
    { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", diagnostic: "model_route_unresolved" });
  assert.equal(captured.length, 1);
});

test("empty routeVariants permits physical facts in exact entries", async () => {
  const captured = [];
  await verifySubscription(host(), config, planner(captured), () => ({ entries: [{ ...variant }], routeVariants: [] }));
  assert.equal(captured.length, 2);
});

test("all exact model variants go to the host planner without invented billing selection", async () => {
  const captured = [];
  await verifySubscription(host(), config, planner(captured), () => ({ entries: [], routeVariants: [
    { ...variant, api: "synthetic-api-key-route" }, variant, { ...variant, id: "other" },
  ] }));
  assert.equal(captured[1].observedRoutes.length, 2);
  assert.deepEqual(captured[1].observedRoutes.map(row => row.api), ["synthetic-api-key-route", variant.api]);
});

for (const [name, overrides, diagnostic] of [
  ["API billing", { authRequirement: "api-key" }, "subscription_route_mismatch"],
  ["wrong model", { modelId: "gpt-6.1-sol" }, "model_route_mismatch"],
  ["wrong provider", { provider: "other" }, "model_route_mismatch"],
  ["non-Codex transport", { runtimePolicy: { compatibleIds: ["openclaw"] } }, "unsupported_execution_backend"],
]) test(`catalog input never bypasses final route checks: ${name}`, async () => {
  await assert.rejects(verifySubscription(host(), config, planner([], overrides), snapshot),
    { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", diagnostic });
});

test("ambiguous attempts or altered profile pins do not trigger catalog fallback", async () => {
  for (const prepared of [{ attempts: [] }, { attempts: [{ kind: "implicit", plan: {} }] },
    { attempts: [{ kind: "profile", profileId: "other", plan: {} }] }]) {
    await assert.rejects(verifySubscription(host(), config, () => prepared, () => assert.fail("fallback")),
      { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED" });
  }
});

test("unresolved after catalog input is still rejected", async () => {
  await assert.rejects(verifySubscription(host(), config, params => planner([])({ ...params, observedRoutes: undefined }), snapshot),
    { diagnostic: "model_route_unresolved" });
});

test("configuration replacement during catalog read cannot dispatch", async () => {
  const api = host();
  await assert.rejects(verifySubscription(api, config, planner([]), () => {
    api.runtime.config.current = () => ({ replaced: true }); return snapshot();
  }), { diagnostic: "runtime_configuration_changed" });
});

test("successful catalog preflight exports neither transport nor credentials", async () => {
  const api = host();
  const bridge = createBridge(api, { verifySubscription: (api, cfg) => verifySubscription(api, cfg, planner([]), snapshot) });
  const out = await bridge.preflight({ expectedHostVersion: api.runtime.version });
  assert.equal(out.status, "subscription_route_verified");
  assert.equal(out.measurement.hostInvocations, 0);
  for (const secret of ["SECRET_CANARY", config.authProfileId, variant.baseUrl, "/synthetic/private"])
    assert.equal(JSON.stringify(out).includes(secret), false);
});
