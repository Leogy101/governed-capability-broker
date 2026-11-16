import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import plugin from "./index.mjs";
import { createBridge, verifySubscription } from "./bridge.mjs";

const config = () => ({ agentId: "main", model: "openai/gpt-example", allowedReasoning: ["medium"], authProfileId: "openai:synthetic-subscription" });
const request = (extra = {}) => ({ requestId: randomUUID(), agentId: "main", model: "openai/gpt-example", reasoning: "medium", expectedHostVersion: "2026.11.1",
  messages: [{ role: "user", content: "Return the supplied facts." }], systemPrompt: "Return JSON.", maxTokens: 512, timeoutMs: 60000, ...extra });
const result = (extra = {}) => ({ text: '{"facts":["synthetic"]}', provider: "openai", model: "gpt-example", agentId: "main",
  execution: { mode: "isolated-agent-runtime", owner: { kind: "harness", id: "codex" } },
  usage: { inputTokens: 50, outputTokens: 10, cacheReadTokens: 20, cacheWriteTokens: 0, totalTokens: 80 }, ...extra });
const fake = (complete = async () => result()) => ({ config: {}, pluginConfig: config(), runtime: { version: "2026.11.1", llm: { complete } } });
const make = api => createBridge(api, { verifySubscription: async () => {} });

test("maps the installed host contract and returns only curated attribution", async () => {
  let sent;
  const api = fake(async payload => { sent = payload; return result({ audit: { sessionKey: "private" }, secret: "do not export" }); });
  const input = request();
  const output = await make(api).complete(input);
  assert.equal(output.status, "completed");
  assert.equal(sent.model, input.model);
  assert.equal(sent.agentId, "main");
  assert.equal(sent.reasoning, "medium");
  assert.equal(sent.execution.mode, "isolated-agent-runtime");
  assert.equal(sent.execution.authProfileId, config().authProfileId);
  assert.ok(sent.execution.timeoutMs <= 60000 && sent.execution.timeoutMs > 0);
  assert.equal(output.measurement.nativeRequestCount, null);
  assert.equal(output.measurement.effectiveReasoning, null);
  assert.equal(output.measurement.outputLimitEnforced, false);
  assert.equal(output.measurement.billingProvenance, "unknown");
  assert.equal(output.measurement.hostVersion, "2026.11.1");
  assert.deepEqual(output.result.usage, result().usage);
  assert.equal(JSON.stringify(output).includes("private"), false);
  assert.equal(JSON.stringify(output).includes("secret"), false);
  assert.equal("tools" in sent, false);
  assert.equal("responseFormat" in sent, false);
});

test("missing usage stays missing and cost provenance is not invented", async () => {
  const out = await make(fake(async () => result({ usage: { costUsd: 0.02 } }))).complete(request());
  assert.deepEqual(out.result.usage, { costUsd: 0.02 });
  assert.equal(out.measurement.billingProvenance, "unknown");
});

for (const [label, changed] of Object.entries({
  model: { model: "openai/not-allowed" }, agent: { agentId: "other" }, effort: { reasoning: "high" },
  observedRoutes: { observedRoutes: [{ api: "injected", baseUrl: "https://unapproved.invalid" }] },
  tools: { tools: [] }, auth: { authProfileId: "other" }, direct: { execution: { mode: "direct-provider" } },
  manyMessages: { messages: [{ role: "user", content: "a" }, { role: "user", content: "b" }] },
  assistantRole: { messages: [{ role: "assistant", content: "a" }] }, duration: { timeoutMs: 60001 },
  output: { maxTokens: 513 }, promptSize: { systemPrompt: "x".repeat(32769) },
})) test(`rejects ${label} before host dispatch`, async () => {
  let calls = 0;
  const out = await make(fake(async () => { calls++; return result(); })).complete(request(changed));
  assert.equal(out.status, "rejected");
  assert.equal(out.measurement.hostInvocations, 0);
  assert.equal(calls, 0);
});

test("completed IDs are never automatically replayed", async () => {
  let calls = 0;
  const bridge = make(fake(async () => { calls++; return result(); }));
  const input = request();
  assert.equal((await bridge.complete(input)).status, "completed");
  assert.equal((await bridge.complete(input)).code, "BRIDGE_DUPLICATE_REQUEST");
  assert.equal(calls, 1);
});

test("only one invocation is admitted, cancel is not completion, and further work is blocked", async () => {
  const bridge = make(fake(() => new Promise(() => {})));
  const input = request();
  const work = bridge.complete(input);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await bridge.complete(request())).code, "BRIDGE_BUSY");
  assert.equal(bridge.cancel({ requestId: randomUUID() }).code, "BRIDGE_NO_ACTIVE_REQUEST");
  assert.equal(bridge.cancel({ requestId: input.requestId }).status, "cancellation_requested");
  assert.equal((await work).status, "unknown_completion");
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

test("deadline aborts even a nonsettling fake host and latches uncertainty", async () => {
  let signal;
  const bridge = make(fake(payload => { signal = payload.signal; return new Promise(() => {}); }));
  const out = await bridge.complete(request({ timeoutMs: 5 }));
  assert.equal(signal.aborted, true);
  assert.equal(out.status, "unknown_completion");
  assert.equal(out.measurement.hostInvocations, 1);
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

test("caller abort before admission does not dispatch", async () => {
  const controller = new AbortController(); controller.abort();
  const out = await make(fake(() => assert.fail("dispatch"))).complete(request(), controller.signal);
  assert.equal(out.code, "BRIDGE_CALLER_ABORTED");
  assert.equal(out.measurement.hostInvocations, 0);
});

for (const [name, response] of Object.entries({
  owner: result({ execution: { mode: "isolated-agent-runtime", owner: { kind: "harness", id: "openclaw" } } }),
  model: result({ model: "different" }), agent: result({ agentId: "different" }),
  usage: result({ usage: { inputTokens: -1 } }),
})) test(`${name} mismatch preserves uncertainty and stops future dispatch`, async () => {
  const bridge = make(fake(async () => response));
  assert.equal((await bridge.complete(request())).status, "unknown_completion");
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

test("provider failures cannot leak credentials or be retried", async () => {
  let calls = 0;
  const bridge = make(fake(async () => { calls++; throw new Error("credential=secret account=private"); }));
  const output = await bridge.complete(request());
  assert.equal(output.code, "LLM_COMPLETION_FAILED");
  assert.equal(output.status, "unknown_completion");
  assert.equal(JSON.stringify(output).includes("secret"), false);
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
  assert.equal(calls, 1);
});

function authApi(profile = { type: "oauth", provider: "openai", access: "not-exported" }) {
  const current = {};
  return { config: { stale: true }, runtime: {
    config: { current: () => current },
    agent: { resolveAgentDir: () => "/synthetic/agent", resolveAgentWorkspaceDir: () => "/synthetic/workspace" },
    modelConfig: { resolveModelRuntimePolicy: () => ({ policy: { id: "codex" } }) },
    modelAuth: { ensureAuthProfileStoreAsync: async (_dir, opts) => {
      assert.equal(opts.readOnly, true); assert.equal(opts.allowKeychainPrompt, false);
      return { profiles: { [config().authProfileId]: profile } };
    } },
  } };
}
function prepared(params) {
  assert.equal(params.allowAuthProfileFallback, false);
  assert.equal(params.sessionAuthProfileSource, "user");
  return { attempts: [{ kind: "profile", profileId: config().authProfileId, plan: {
    forwardedAuthProfileId: config().authProfileId, harnessAuthProvider: "openai", modelRoute: { provider: "openai", modelId: params.modelId, authRequirement: "subscription", runtimePolicy: { compatibleIds: ["codex"] } },
  } }] };
}
test("subscription verification uses read-only host metadata and exact route/profile", async () => {
  const api = authApi();
  assert.equal(await verifySubscription(api, config(), prepared), api.runtime.config.current());
});
test("API keys cannot enter subscription-only dispatch", async () => {
  await assert.rejects(verifySubscription(authApi({ type: "api_key", provider: "openai" }), config(), () => assert.fail("planning")), { code: "BRIDGE_SUBSCRIPTION_REQUIRED" });
});
test("indeterminate, fallback and API-key prepared routes are rejected", async () => {
  for (const alter of [value => { value.attempts = []; }, value => { value.attempts.push(value.attempts[0]); },
    value => { value.attempts[0].plan.modelRoute.authRequirement = "api-key"; },
    value => { value.attempts[0].plan.forwardedAuthProfileId = "other"; }]) {
    await assert.rejects(verifySubscription(authApi(), config(), params => { const value = prepared(params); alter(value); return value; }), { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED" });
  }
});
test("registration exposes three admin RPCs and no tools or session mutations", () => {
  const methods = [];
  plugin.register({ ...fake(), registerGatewayMethod: (name, handler, policy) => methods.push({ name, handler, policy }) });
  assert.deepEqual(methods.map(entry => entry.name), ["gcb.profile.preflight", "gcb.profile.complete", "gcb.profile.cancel"]);
  assert.ok(methods.every(entry => entry.policy.scope === "operator.admin"));
});

test("host output size is bounded without pretending the provider enforced maxTokens", async () => {
  const out = await make(fake(async () => result({ text: "x".repeat(32769) }))).complete(request());
  assert.equal(out.code, "BRIDGE_OUTPUT_OVERSIZE");
  assert.equal(out.status, "unknown_completion");
});
test("authorization setup time is included in the deadline and cannot later dispatch", async () => {
  let release;
  let calls = 0;
  const bridge = createBridge(fake(async () => { calls++; return result(); }), {
    verifySubscription: () => new Promise(resolve => { release = resolve; }),
  });
  const output = await bridge.complete(request({ timeoutMs: 5 }));
  assert.equal(output.status, "rejected");
  assert.equal(output.code, "LLM_COMPLETION_TIMEOUT");
  assert.equal(output.measurement.hostInvocations, 0);
  release();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls, 0);
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
});
test("non-Codex runtime is rejected before credentials or inference", async () => {
  const api = authApi();
  api.runtime.modelConfig.resolveModelRuntimePolicy = () => ({ policy: { id: "openclaw" } });
  api.runtime.modelAuth.ensureAuthProfileStoreAsync = () => assert.fail("credential metadata lookup");
  await assert.rejects(verifySubscription(api, config(), prepared), { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED" });
});
test("runtime config drift during metadata preparation is rejected", async () => {
  const api = authApi();
  await assert.rejects(verifySubscription(api, config(), params => {
    const output = prepared(params);
    api.runtime.config.current = () => ({ changed: true });
    return output;
  }), { code: "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED" });
});
test("an installed host-version change rejects stale research before dispatch", async () => {
  const bridge = make(fake(() => assert.fail("dispatch")));
  const output = await bridge.complete(request({ expectedHostVersion: "different-version" }));
  assert.equal(output.code, "BRIDGE_HOST_VERSION_MISMATCH");
  assert.equal(output.measurement.hostInvocations, 0);
});

const preflightRequest = () => ({ expectedHostVersion: "2026.11.1" });
function preflightApi(profile) {
  const api = fake(() => assert.fail("preflight must not infer"));
  Object.assign(api.runtime, authApi(profile).runtime);
  return api;
}
const verifiedBridge = api => createBridge(api, { verifySubscription: (host, cfg) => verifySubscription(host, cfg, prepared) });

test("preflight verifies a subscription route without inference or credential/account claims", async () => {
  const api = preflightApi();
  Object.assign(api.runtime.config.current(), { plugins: { entries: { codex: { config: { appServer: { homeScope: "user" } } } } }, privateToken: "do-not-export" });
  const output = await verifiedBridge(api).preflight(preflightRequest());
  assert.equal(output.status, "subscription_route_verified");
  assert.equal(output.measurement.hostInvocations, 0);
  assert.deepEqual(output.result, {
    agentId: "main", model: "openai/gpt-example", allowedReasoning: ["medium"], hostVersion: "2026.11.1",
    authRequirement: "subscription", metadataOnly: true, nativeAccountIdentityVerified: false,
    effectiveReasoning: null, configuredCodexHomeScope: "user",
    profilePinRole: "subscription_route_selector_not_native_account_identity",
  });
  const serialized = JSON.stringify(output);
  for (const secret of [config().authProfileId, "not-exported", "do-not-export", "authProfileId", "privateToken"]) assert.equal(serialized.includes(secret), false);
});

test("preflight does not invent an unconfigured native home scope", async () => {
  const output = await verifiedBridge(preflightApi()).preflight(preflightRequest());
  assert.equal(output.status, "subscription_route_verified");
  assert.equal("configuredCodexHomeScope" in output.result, false);
  assert.equal("profilePinRole" in output.result, false);
});

test("preflight rejects API-key metadata through the unchanged subscription verifier", async () => {
  const output = await verifiedBridge(preflightApi({ type: "api_key", provider: "openai", key: "do-not-export" })).preflight(preflightRequest());
  assert.equal(output.code, "BRIDGE_SUBSCRIPTION_REQUIRED");
  assert.equal(output.measurement.hostInvocations, 0);
  assert.equal(JSON.stringify(output).includes("do-not-export"), false);
});

for (const [name, changed, code] of [
  ["host version", { expectedHostVersion: "other" }, "BRIDGE_HOST_VERSION_MISMATCH"],
  ["model override", { ...preflightRequest(), model: "openai/not-allowed" }, "BRIDGE_INPUT_REJECTED"],
  ["deadline override", { ...preflightRequest(), timeoutMs: 60000 }, "BRIDGE_INPUT_REJECTED"],
  ["missing version", {}, "BRIDGE_INPUT_REJECTED"],
]) test(`preflight rejects ${name} before metadata preparation`, async () => {
  const bridge = createBridge(fake(() => assert.fail("inference")), { verifySubscription: () => assert.fail("metadata read") });
  const output = await bridge.preflight(changed);
  assert.equal(output.status, "rejected");
  assert.equal(output.code, code);
  assert.equal(output.measurement.hostInvocations, 0);
});

test("preflight rejects missing configured pins", async () => {
  const api = fake(); delete api.pluginConfig.authProfileId;
  const bridge = createBridge(api, { verifySubscription: () => assert.fail("metadata read") });
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_CONFIGURATION_REQUIRED");
});

test("preflight has a bounded reply, serializes pending reads and never dispatches on late completion", async () => {
  const api = preflightApi();
  let release;
  const bridge = createBridge(api, { preflightTimeoutMs: 5, verifySubscription: () => new Promise(resolve => { release = resolve; }) });
  const output = await bridge.preflight(preflightRequest());
  assert.equal(output.status, "rejected");
  assert.equal(output.code, "BRIDGE_PREFLIGHT_TIMEOUT");
  assert.equal(output.measurement.hostInvocations, 0);
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_BUSY");
  assert.equal((await bridge.complete(request())).code, "BRIDGE_BUSY");
  release(api.runtime.config.current());
  await new Promise(resolve => setImmediate(resolve));
  // No action follows the late result. A new preflight still requires a new request.
  const next = bridge.preflight(preflightRequest());
  await new Promise(resolve => setImmediate(resolve));
  release(api.runtime.config.current());
  assert.equal((await next).status, "subscription_route_verified");
});

test("preflight honors caller abort before admission and during a metadata read", async () => {
  const before = new AbortController(); before.abort();
  const untouched = createBridge(fake(), { verifySubscription: () => assert.fail("metadata read") });
  assert.equal((await untouched.preflight(preflightRequest(), before.signal)).code, "BRIDGE_CALLER_ABORTED");
  const api = preflightApi();
  let release;
  const controller = new AbortController();
  const bridge = createBridge(api, { verifySubscription: () => new Promise(resolve => { release = resolve; }) });
  const pending = bridge.preflight(preflightRequest(), controller.signal);
  await new Promise(resolve => setImmediate(resolve));
  controller.abort(new Error("private caller reason"));
  const output = await pending;
  assert.equal(output.code, "BRIDGE_CALLER_ABORTED");
  assert.equal(JSON.stringify(output).includes("private caller reason"), false);
  assert.equal((await bridge.complete(request())).code, "BRIDGE_BUSY");
  release(api.runtime.config.current());
  await new Promise(resolve => setImmediate(resolve));
});

test("preflight rejects config drift and does not leak verifier failures", async () => {
  const api = preflightApi();
  const original = api.runtime.config.current();
  const bridge = createBridge(api, { verifySubscription: async () => {
    api.runtime.config.current = () => ({ changed: true });
    return original;
  } });
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED");
  const failing = createBridge(preflightApi(), { verifySubscription: async () => { throw new Error("credential=private"); } });
  const output = await failing.preflight(preflightRequest());
  assert.equal(output.code, "BRIDGE_SUBSCRIPTION_UNVERIFIED");
  assert.equal(JSON.stringify(output).includes("private"), false);
});

test("preflight rejects host version drift during metadata preparation", async () => {
  const api = preflightApi();
  const bridge = createBridge(api, { verifySubscription: async () => {
    api.runtime.version = "new-version";
    return api.runtime.config.current();
  } });
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_HOST_VERSION_MISMATCH");
});

test("preflight cannot run during inference or clear an unknown-completion latch", async () => {
  const bridge = make(fake(() => new Promise(() => {})));
  const input = request();
  const inference = bridge.complete(input);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_BUSY");
  bridge.cancel({ requestId: input.requestId });
  assert.equal((await inference).status, "unknown_completion");
  assert.equal((await bridge.preflight(preflightRequest())).code, "BRIDGE_RECONCILIATION_REQUIRED");
  assert.equal((await bridge.complete(request())).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

const campaignEfforts = ["low", "medium", "high", "xhigh", "max", "ultra"];
const campaignModels = ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna"];
function campaignConfig() {
  return { agentId: "main", authProfileId: config().authProfileId,
    modelReasoning: Object.fromEntries(campaignModels.map(model => ["openai/" + model,
      campaignEfforts.filter(effort => !model.endsWith("luna") || effort !== "ultra")])) };
}

for (const [model, efforts] of Object.entries(campaignConfig().modelReasoning)) {
  for (const reasoning of efforts) test(`explicit campaign binding ${model} / ${reasoning}`, async () => {
    let calls = 0;
    const api = fake(async payload => {
      calls++;
      assert.equal(payload.model, model);
      assert.equal(payload.reasoning, reasoning);
      assert.equal(payload.agentId, "main");
      assert.equal(payload.execution.authProfileId, config().authProfileId);
      return result({ model: model.slice("openai/".length) });
    });
    Object.assign(api.runtime, authApi().runtime);
    api.pluginConfig = campaignConfig();
    const bridge = createBridge(api, { verifySubscription: (host, selected) => {
      assert.equal(selected.model, model);
      assert.deepEqual(selected.allowedReasoning, efforts);
      return verifySubscription(host, selected, params => {
        assert.equal(params.modelId, model.slice("openai/".length));
        assert.equal(params.sessionAuthProfileId, config().authProfileId);
        return prepared(params);
      });
    } });
    const output = await bridge.complete(request({ model, reasoning }));
    assert.equal(output.status, "completed");
    assert.equal(output.result.model, model.slice("openai/".length));
    assert.equal(output.measurement.hostInvocations, 1);
    assert.equal(calls, 1);
  });
}

for (const [label, alter] of [
  ["mixed model", c => { c.model = "openai/gpt-6.1-sol"; }],
  ["mixed effort list", c => { c.allowedReasoning = ["medium"]; }],
  ["unknown field", c => { c.allowAnyModel = true; }],
  ["empty map", c => { c.modelReasoning = {}; }],
  ["non-object map", c => { c.modelReasoning = []; }],
  ["empty effort list", c => { c.modelReasoning["openai/gpt-6.1-sol"] = []; }],
  ["duplicate efforts", c => { c.modelReasoning["openai/gpt-6.1-sol"] = ["medium", "medium"]; }],
  ["unknown effort", c => { c.modelReasoning["openai/gpt-6.1-sol"] = ["invented"]; }],
  ["wildcard model", c => { c.modelReasoning["openai/*"] = ["medium"]; }],
  ["missing subscription pin", c => { delete c.authProfileId; }],
]) test(`campaign policy rejects ${label} before authorization`, async () => {
  const api = fake(() => assert.fail("inference")); api.pluginConfig = campaignConfig(); alter(api.pluginConfig);
  const bridge = createBridge(api, { verifySubscription: () => assert.fail("authorization") });
  const input = request({ model: "openai/gpt-6.1-sol" });
  assert.equal((await bridge.complete(input)).code, "BRIDGE_CONFIGURATION_REQUIRED");
  assert.equal((await bridge.preflight({ ...preflightRequest(), model: input.model })).code, "BRIDGE_CONFIGURATION_REQUIRED");
});

for (const model of ["openai/gpt-6-luna", "openai/gpt-5.6-luna", "openai/not-approved"]) {
  test(`campaign rejects unauthorized model/effort pair ${model} / ultra`, async () => {
    const api = fake(() => assert.fail("inference")); api.pluginConfig = campaignConfig();
    const bridge = createBridge(api, { verifySubscription: () => assert.fail("authorization") });
    const out = await bridge.complete(request({ model, reasoning: "ultra" }));
    assert.equal(out.code, "BRIDGE_INPUT_REJECTED");
    assert.equal(out.measurement.hostInvocations, 0);
  });
}

test("map preflight requires an exact selector for multiple models", async () => {
  const api = preflightApi(); api.pluginConfig = campaignConfig();
  const bridge = verifiedBridge(api);
  for (const model of [undefined, null, "openai/not-approved", "openai/*"]) {
    const output = await bridge.preflight({ ...preflightRequest(), ...model === undefined ? {} : { model } });
    assert.equal(output.code, "BRIDGE_INPUT_REJECTED");
  }
  const output = await bridge.preflight({ ...preflightRequest(), model: "openai/gpt-6-luna" });
  assert.equal(output.status, "subscription_route_verified");
  assert.equal(output.result.model, "openai/gpt-6-luna");
  assert.deepEqual(output.result.allowedReasoning, ["low", "medium", "high", "xhigh", "max"]);
  assert.equal(JSON.stringify(output).includes(config().authProfileId), false);
});

test("single-model map permits omitted preflight selector but rejects null", async () => {
  const api = preflightApi(); api.pluginConfig = { agentId: "main", authProfileId: config().authProfileId,
    modelReasoning: { "openai/gpt-6.1-sol": ["medium"] } };
  const bridge = verifiedBridge(api);
  assert.equal((await bridge.preflight(preflightRequest())).result.model, "openai/gpt-6.1-sol");
  assert.equal((await bridge.preflight({ ...preflightRequest(), model: null })).code, "BRIDGE_INPUT_REJECTED");
});

for (const operation of ["preflight", "complete"]) test(`${operation} rejects mutable campaign policy drift during authorization`, async () => {
  const api = preflightApi(); api.pluginConfig = campaignConfig();
  const bridge = createBridge(api, { verifySubscription: async (_api, selected) => {
    api.pluginConfig.modelReasoning["openai/gpt-6.1-sol"].pop();
    assert.ok(selected.allowedReasoning.includes("ultra"), "selected policy was independently cloned");
    return api.runtime.config.current();
  } });
  const output = operation === "preflight"
    ? await bridge.preflight({ ...preflightRequest(), model: "openai/gpt-6.1-sol" })
    : await bridge.complete(request({ model: "openai/gpt-6.1-sol" }));
  assert.equal(output.code, "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED");
  assert.equal(output.measurement.hostInvocations, 0);
});

test("campaign subscription pin drift cannot dispatch", async () => {
  const api = fake(() => assert.fail("inference")); api.pluginConfig = campaignConfig();
  const bridge = createBridge(api, { verifySubscription: async (_api, selected) => {
    api.pluginConfig.authProfileId = "different";
    assert.equal(selected.authProfileId, config().authProfileId);
  } });
  assert.equal((await bridge.complete(request({ model: "openai/gpt-6.1-sol" }))).code, "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED");
});

test("a returned different approved model is still an attribution mismatch", async () => {
  const api = fake(async () => result({ model: "gpt-6.1-terra" })); api.pluginConfig = campaignConfig();
  const bridge = make(api);
  assert.equal((await bridge.complete(request({ model: "openai/gpt-6.1-sol" }))).code, "BRIDGE_ATTRIBUTION_MISMATCH");
  assert.equal((await bridge.complete(request({ model: "openai/gpt-6.1-terra" }))).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

test("busy admission, cancellation and uncertainty remain global across campaign models", async () => {
  const api = fake(() => new Promise(() => {})); api.pluginConfig = campaignConfig();
  const bridge = make(api); const first = request({ model: "openai/gpt-6.1-sol" });
  const pending = bridge.complete(first); await new Promise(resolve => setImmediate(resolve));
  const second = request({ model: "openai/gpt-6.1-terra" });
  assert.equal((await bridge.complete(second)).code, "BRIDGE_BUSY");
  bridge.cancel({ requestId: first.requestId });
  assert.equal((await pending).status, "unknown_completion");
  assert.equal((await bridge.complete(second)).code, "BRIDGE_RECONCILIATION_REQUIRED");
  assert.equal((await bridge.preflight({ ...preflightRequest(), model: second.model })).code, "BRIDGE_RECONCILIATION_REQUIRED");
});

test("completed request IDs remain single-use across models", async () => {
  let calls = 0;
  const api = fake(async payload => { calls++; return result({ model: payload.model.slice("openai/".length) }); });
  api.pluginConfig = campaignConfig(); const bridge = make(api);const input = request({ model: "openai/gpt-6.1-sol" });
  assert.equal((await bridge.complete(input)).status, "completed");
  assert.equal((await bridge.complete({ ...input, model: "openai/gpt-6.1-terra" })).code, "BRIDGE_DUPLICATE_REQUEST");
  assert.equal(calls, 1);
});
