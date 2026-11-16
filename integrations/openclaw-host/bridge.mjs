const SCHEMA = "gcb.openclaw-host/v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const FIELDS = new Set(["requestId", "agentId", "model", "reasoning", "messages", "systemPrompt", "maxTokens", "timeoutMs", "expectedHostVersion"]);
const EFFORTS = new Set(["low", "medium", "high", "xhigh", "max", "ultra"]);
const MODEL = /^openai\/[A-Za-z0-9][A-Za-z0-9._:-]*$/;
const CONFIG_FIELDS = new Set(["agentId", "authProfileId", "model", "allowedReasoning", "modelReasoning"]);
const SAFE_ERRORS = new Set(["LLM_COMPLETION_NOT_AUTHORIZED", "LLM_ISOLATED_INPUT_REJECTED", "LLM_ISOLATED_UNSUPPORTED", "LLM_RUNTIME_UNAVAILABLE", "LLM_COMPLETION_ABORTED", "LLM_COMPLETION_TIMEOUT", "LLM_COMPLETION_OUTPUT_REJECTED", "LLM_COMPLETION_FAILED"]);
const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value);
const fail = (code, diagnostic) => { throw Object.assign(new Error(code), { code, ...(diagnostic ? { diagnostic } : {}) }); };

// Fixed labels only. Never return a host exception's message, arbitrary code,
// profile identifier, path or diagnostic object as readiness evidence.
const DIAGNOSTICS = Object.freeze({
  configured_runtime_unresolved: ["authorization_unresolved", "model"],
  unsupported_execution_backend: ["unsupported", "model"],
  missing_authorization: ["authorization_unresolved", "model"],
  credential_profile_mismatch: ["authorization_unresolved", "model"],
  auth_plan_unresolved: ["authorization_unresolved", "model"],
  model_route_unresolved: ["authorization_unresolved", "model"],
  model_route_mismatch: ["authorization_unresolved", "model"],
  subscription_route_mismatch: ["authorization_unresolved", "model"],
  reasoning_configuration_mismatch: ["authorization_unresolved", "model"],
  credential_authority_stale: ["authorization_unresolved", "shared_execution_state"],
  runtime_configuration_changed: ["authorization_unresolved", "shared_execution_state"],
  bridge_configuration_changed: ["authorization_unresolved", "shared_execution_state"],
  bridge_configuration_invalid: ["authorization_unresolved", "shared_execution_state"],
  host_authority_unavailable: ["authorization_unresolved", "shared_execution_state"],
  runtime_verification_failed: ["authorization_unresolved", "shared_execution_state"],
  request_scope_mismatch: ["authorization_unresolved", "shared_execution_state"],
  request_replay_rejected: ["authorization_unresolved", "shared_execution_state"],
  execution_completion_unknown: ["authorization_unresolved", "shared_execution_state"],
  execution_busy: ["temporarily_unavailable", "shared_execution_state"],
  metadata_timeout: ["temporarily_unavailable", "shared_execution_state"],
  metadata_cancelled: ["temporarily_unavailable", "shared_execution_state"],
  runtime_unavailable: ["temporarily_unavailable", "model"],
});
const CODE_DIAGNOSTICS = Object.freeze({
  BRIDGE_CONFIGURATION_REQUIRED: "bridge_configuration_invalid",
  BRIDGE_INPUT_REJECTED: "request_scope_mismatch",
  BRIDGE_HOST_VERSION_MISMATCH: "runtime_configuration_changed",
  BRIDGE_BUSY: "execution_busy",
  BRIDGE_CALLER_ABORTED: "metadata_cancelled",
  BRIDGE_PREFLIGHT_TIMEOUT: "metadata_timeout",
  BRIDGE_DUPLICATE_REQUEST: "request_replay_rejected",
  BRIDGE_SUBSCRIPTION_REQUIRED: "missing_authorization",
  LLM_RUNTIME_UNAVAILABLE: "runtime_unavailable",
  LLM_ISOLATED_UNSUPPORTED: "unsupported_execution_backend",
  LLM_COMPLETION_NOT_AUTHORIZED: "missing_authorization",
});
const INPUT_ERRORS = new Set(["BRIDGE_CONFIGURATION_REQUIRED", "BRIDGE_INPUT_REJECTED", "BRIDGE_HOST_VERSION_MISMATCH",
  "BRIDGE_RECONCILIATION_REQUIRED", "BRIDGE_BUSY", "BRIDGE_CALLER_ABORTED", "BRIDGE_DUPLICATE_REQUEST"]);
function readiness(error, code, uncertain = false) {
  const diagnostic = error?.diagnostic;
  const reason = uncertain || code === "BRIDGE_RECONCILIATION_REQUIRED" ? "execution_completion_unknown"
    : typeof diagnostic === "string" && Object.hasOwn(DIAGNOSTICS, diagnostic) ? diagnostic
    : Object.hasOwn(CODE_DIAGNOSTICS, code) ? CODE_DIAGNOSTICS[code] : "runtime_verification_failed";
  const [state, scope] = DIAGNOSTICS[reason];
  return { state, reason, scope, accountEntitlement: "unknown" };
}

function validateConfig(config) {
  if (!isObject(config) || Object.keys(config).some(key => !CONFIG_FIELDS.has(key))
      || typeof config.agentId !== "string" || !config.agentId.trim()
      || typeof config.authProfileId !== "string" || !config.authProfileId.trim()) fail("BRIDGE_CONFIGURATION_REQUIRED");
  const efforts = values => {
    if (!Array.isArray(values) || !values.length || new Set(values).size !== values.length
        || values.some(value => !EFFORTS.has(value))) fail("BRIDGE_CONFIGURATION_REQUIRED");
    return [...values];
  };
  const common = { agentId: config.agentId, authProfileId: config.authProfileId };
  if (Object.hasOwn(config, "modelReasoning")) {
    if (Object.hasOwn(config, "model") || Object.hasOwn(config, "allowedReasoning")
        || !isObject(config.modelReasoning) || !Object.keys(config.modelReasoning).length) fail("BRIDGE_CONFIGURATION_REQUIRED");
    const modelReasoning = {};
    for (const model of Object.keys(config.modelReasoning).sort()) {
      if (!MODEL.test(model)) fail("BRIDGE_CONFIGURATION_REQUIRED");
      modelReasoning[model] = efforts(config.modelReasoning[model]);
    }
    return { ...common, modelReasoning };
  }
  if (typeof config.model !== "string" || !MODEL.test(config.model)) fail("BRIDGE_CONFIGURATION_REQUIRED");
  return { ...common, model: config.model, allowedReasoning: efforts(config.allowedReasoning) };
}

function selectModel(policy, model, preflight = false) {
  if (!policy.modelReasoning) {
    if (preflight && model !== undefined) fail("BRIDGE_INPUT_REJECTED");
    return policy;
  }
  const models = Object.keys(policy.modelReasoning);
  const selected = model === undefined && preflight && models.length === 1 ? models[0] : model;
  if (typeof selected !== "string" || !Object.hasOwn(policy.modelReasoning, selected)) fail("BRIDGE_INPUT_REJECTED");
  return { agentId: policy.agentId, authProfileId: policy.authProfileId,
    model: selected, allowedReasoning: [...policy.modelReasoning[selected]] };
}

function assertPolicyUnchanged(api, policy) {
  let current;
  try { current = validateConfig(api.pluginConfig); }
  catch { fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "bridge_configuration_changed"); }
  if (JSON.stringify(current) !== JSON.stringify(policy)) fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "bridge_configuration_changed");
}

function validateRequest(request, config) {
  if (isObject(request) && request.model === config.model && !config.allowedReasoning.includes(request.reasoning)) {
    fail("BRIDGE_INPUT_REJECTED", "reasoning_configuration_mismatch");
  }
  if (!isObject(request) || Object.keys(request).some(key => !FIELDS.has(key))
      || !UUID.test(request.requestId ?? "") || request.agentId !== config.agentId
      || typeof request.expectedHostVersion !== "string" || !request.expectedHostVersion.trim()
      || request.model !== config.model || !config.allowedReasoning.includes(request.reasoning)
      || !Array.isArray(request.messages) || request.messages.length !== 1
      || !isObject(request.messages[0]) || request.messages[0].role !== "user"
      || Object.keys(request.messages[0]).some(key => key !== "role" && key !== "content")
      || typeof request.messages[0].content !== "string" || !request.messages[0].content.trim()
      || typeof request.systemPrompt !== "string"
      || !Number.isInteger(request.maxTokens) || request.maxTokens < 1 || request.maxTokens > 512
      || !Number.isInteger(request.timeoutMs) || request.timeoutMs < 1 || request.timeoutMs > 60000
      || Buffer.byteLength(request.systemPrompt + request.messages[0].content, "utf8") > 32768) fail("BRIDGE_INPUT_REJECTED");
}

/** Read only host-owned metadata; neither credentials nor private route facts leave the bridge. */
export async function verifySubscription(api, config, prepareAuth, readCatalogSnapshot) {
  const cfg = api.runtime.config.current();
  const modelId = config.model.slice("openai/".length);
  const policy = api.runtime.modelConfig.resolveModelRuntimePolicy({
    config: cfg, provider: "openai", modelId, agentId: config.agentId,
  });
  // This public helper resolves authored policy, not implicit runtime defaults.
  // Preserve the explicit Codex requirement; absent policy is unresolved, not unsupported.
  if (policy?.policy?.id !== "codex") fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED",
    policy?.policy?.id ? "unsupported_execution_backend" : "configured_runtime_unresolved");
  const agentDir = api.runtime.agent.resolveAgentDir(cfg, config.agentId);
  const workspaceDir = api.runtime.agent.resolveAgentWorkspaceDir(cfg, config.agentId);
  const store = await api.runtime.modelAuth.ensureAuthProfileStoreAsync(agentDir, {
    config: cfg, readOnly: true, allowKeychainPrompt: false,
  });
  const profile = store?.profiles?.[config.authProfileId];
  if (!profile) fail("BRIDGE_SUBSCRIPTION_REQUIRED", "missing_authorization");
  if (profile.provider !== "openai" || !["oauth", "token"].includes(profile.type))
    fail("BRIDGE_SUBSCRIPTION_REQUIRED", "credential_profile_mismatch");
  const prepare = prepareAuth ?? (await import("openclaw/plugin-sdk/agent-harness-runtime")).prepareAgentRuntimeAuth;
  const params = {
    config: cfg, provider: "openai", modelId, agentId: config.agentId, agentDir, workspaceDir,
    authProfileStore: store, sessionAuthProfileId: config.authProfileId,
    sessionAuthProfileSource: "user", allowAuthProfileFallback: false,
    harnessId: "codex", harnessRuntime: "codex", harnessAuthBootstrap: "harness",
  };
  const exactAttempt = prepared => {
    const attempts = prepared?.attempts;
    if (!Array.isArray(attempts) || attempts.length !== 1 || attempts[0]?.kind !== "profile")
      fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "auth_plan_unresolved");
    const attempt = attempts[0];
    if (attempt.profileId !== config.authProfileId
        || attempt.plan?.forwardedAuthProfileId !== config.authProfileId
        || attempt.plan?.harnessAuthProvider !== "openai")
      fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "credential_profile_mismatch");
    return attempt;
  };
  let attempt = exactAttempt(prepare(params));
  if (!attempt.plan.modelRoute) {
    // Native catalog-only models need the same observed transport facts that the
    // host passes to its auth planner. Read the current scoped owner, never a
    // caller-supplied URL, cached GCB research record or fabricated route.
    const read = readCatalogSnapshot ?? (await import("openclaw/plugin-sdk/agent-runtime")).getPreparedModelCatalogSnapshot;
    const snapshot = read({ config: cfg, agentId: config.agentId, agentDir, workspaceDir, readOnly: true });
    if (!isObject(snapshot) || snapshot.authoritative === false
        || !Array.isArray(snapshot.routeVariants) || !Array.isArray(snapshot.entries))
      fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "model_route_unresolved");
    const rows = snapshot.routeVariants.length ? snapshot.routeVariants : snapshot.entries;
    const observedRoutes = rows.filter(row => row?.provider === "openai" && row.id === modelId)
      .filter(row => typeof row.api === "string" && row.api.trim())
      .map(({ api, baseUrl }) => ({ api, baseUrl }));
    if (!observedRoutes.length) fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "model_route_unresolved");
    attempt = exactAttempt(prepare({ ...params, observedRoutes }));
  }
  const route = attempt.plan.modelRoute;
  if (!route) fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "model_route_unresolved");
  if (route.provider !== "openai" || route.modelId !== modelId)
    fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "model_route_mismatch");
  if (route.authRequirement !== "subscription")
    fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "subscription_route_mismatch");
  if (!Array.isArray(route.runtimePolicy?.compatibleIds) || !route.runtimePolicy.compatibleIds.includes("codex"))
    fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "unsupported_execution_backend");
  if (api.runtime.config.current() !== cfg)
    fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "runtime_configuration_changed");
  return cfg;
}

function curateResult(value, config) {
  const model = config.model.slice("openai/".length);
  if (!isObject(value) || value.provider !== "openai" || value.model !== model
      || value.agentId !== config.agentId || value.execution?.mode !== "isolated-agent-runtime"
      || value.execution?.owner?.kind !== "harness" || value.execution?.owner?.id !== "codex"
      || typeof value.text !== "string" || !value.text.trim()) fail("BRIDGE_ATTRIBUTION_MISMATCH");
  if (Buffer.byteLength(value.text, "utf8") > 32768) fail("BRIDGE_OUTPUT_OVERSIZE");
  const usage = {};
  for (const key of ["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens", "totalTokens", "costUsd"]) {
    const raw = value.usage?.[key];
    if (raw === undefined) continue;
    if (typeof raw !== "number" || !Number.isFinite(raw) || raw < 0 || (key !== "costUsd" && !Number.isSafeInteger(raw))) fail("BRIDGE_USAGE_INVALID");
    usage[key] = raw;
  }
  return { text: value.text, provider: value.provider, model: value.model, agentId: value.agentId,
    execution: { mode: "isolated-agent-runtime", owner: { kind: "harness", id: "codex" } }, usage };
}

export function createBridge(api, options = {}) {
  const verify = options.verifySubscription ?? verifySubscription;
  const now = options.now ?? (() => performance.now());
  // The test seam may shorten the metadata deadline, never extend it.
  const preflightTimeoutMs = Math.min(10000, options.preflightTimeoutMs ?? 10000);
  if (!Number.isInteger(preflightTimeoutMs) || preflightTimeoutMs < 1) throw new TypeError("Invalid preflight deadline");
  const seen = new Set();
  let active;
  let metadataOwner;
  let blocked = false;
  const envelope = (requestId, status, code, hostInvocations = 0) => ({
    schema: SCHEMA, requestId: UUID.test(requestId ?? "") ? requestId : null, status,
    ...(code ? { code } : {}), measurement: { hostInvocations, nativeRequestCount: null },
  });
  return {
    async preflight(request, callerSignal) {
      let config;
      let policy;
      try {
        policy = validateConfig(api.pluginConfig);
        if (!isObject(request) || Object.keys(request).some(key => key !== "expectedHostVersion" && key !== "model")
            || typeof request.expectedHostVersion !== "string" || !request.expectedHostVersion.trim()) fail("BRIDGE_INPUT_REJECTED");
        config = selectModel(policy, request.model, true);
        if (request.expectedHostVersion !== api.runtime.version) fail("BRIDGE_HOST_VERSION_MISMATCH");
        if (blocked) fail("BRIDGE_RECONCILIATION_REQUIRED");
        if (active || metadataOwner) fail("BRIDGE_BUSY");
        if (callerSignal?.aborted) fail("BRIDGE_CALLER_ABORTED");
      } catch (error) {
        const code = INPUT_ERRORS.has(error?.code) ? error.code : "BRIDGE_INPUT_REJECTED";
        return { ...envelope(null, "rejected", code), readiness: readiness(error, code) };
      }
      const owner = {};
      metadataOwner = owner;
      let timer;
      let onAbort;
      const interrupted = new Promise((_, reject) => {
        onAbort = () => reject(Object.assign(new Error("BRIDGE_CALLER_ABORTED"), { code: "BRIDGE_CALLER_ABORTED" }));
        callerSignal?.addEventListener("abort", onAbort, { once: true });
        timer = setTimeout(() => reject(Object.assign(new Error("BRIDGE_PREFLIGHT_TIMEOUT"), { code: "BRIDGE_PREFLIGHT_TIMEOUT" })), preflightTimeoutMs);
      });
      const verification = Promise.resolve().then(() => verify(api, config));
      // A bounded reply cannot cancel an unabortable read. Keep admission busy until
      // that read settles; a late read never dispatches or clears uncertainty.
      const releaseMetadata = () => { if (metadataOwner === owner) metadataOwner = undefined; };
      verification.then(releaseMetadata, releaseMetadata);
      try {
        const cfg = await Promise.race([verification, interrupted]);
        if (callerSignal?.aborted) fail("BRIDGE_CALLER_ABORTED");
        if (!isObject(cfg) || api.runtime.config.current() !== cfg) fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "runtime_configuration_changed");
        assertPolicyUnchanged(api, policy);
        if (request.expectedHostVersion !== api.runtime.version) fail("BRIDGE_HOST_VERSION_MISMATCH");
        const homeScope = cfg.plugins?.entries?.codex?.config?.appServer?.homeScope;
        return { ...envelope(null, "subscription_route_verified"),
          readiness: { state: "ready", reason: "subscription_route_verified", scope: "model", accountEntitlement: "unknown" }, result: {
          agentId: config.agentId, model: config.model, allowedReasoning: [...config.allowedReasoning],
          hostVersion: api.runtime.version, authRequirement: "subscription", metadataOnly: true,
          nativeAccountIdentityVerified: false, effectiveReasoning: null,
          ...["user", "agent"].includes(homeScope) ? { configuredCodexHomeScope: homeScope } : {},
          ...homeScope === "user" ? { profilePinRole: "subscription_route_selector_not_native_account_identity" } : {},
        } };
      } catch (error) {
        const code = ["BRIDGE_SUBSCRIPTION_REQUIRED", "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "BRIDGE_CALLER_ABORTED", "BRIDGE_PREFLIGHT_TIMEOUT", "BRIDGE_HOST_VERSION_MISMATCH"].includes(error?.code)
          ? error.code : "BRIDGE_SUBSCRIPTION_UNVERIFIED";
        return { ...envelope(null, "rejected", code), readiness: readiness(error, code) };
      } finally {
        clearTimeout(timer);
        callerSignal?.removeEventListener("abort", onAbort);
      }
    },
    async complete(request, callerSignal) {
      const requestId = request?.requestId;
      let config;
      let policy;
      try {
        policy = validateConfig(api.pluginConfig);
        config = selectModel(policy, request?.model);
        validateRequest(request, config);
        if (request.expectedHostVersion !== api.runtime.version) fail("BRIDGE_HOST_VERSION_MISMATCH");
        if (blocked) fail("BRIDGE_RECONCILIATION_REQUIRED");
        if (seen.has(requestId)) fail("BRIDGE_DUPLICATE_REQUEST");
        if (active || metadataOwner) fail("BRIDGE_BUSY");
        if (callerSignal?.aborted) fail("BRIDGE_CALLER_ABORTED");
      } catch (error) {
        const code = INPUT_ERRORS.has(error?.code) ? error.code : "BRIDGE_INPUT_REJECTED";
        return { ...envelope(requestId, "rejected", code), readiness: readiness(error, code) };
      }
      const controller = new AbortController();
      active = { requestId, controller, dispatched: false };
      const abortFromCaller = () => controller.abort();
      callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
      let dispatched = false;
      let timer;
      let onAbort;
      const started = now();
      const aborted = new Promise((_, reject) => {
        onAbort = () => {
          blocked = true;
          reject(controller.signal.reason instanceof Error ? controller.signal.reason
            : Object.assign(new Error("LLM_COMPLETION_ABORTED"), { code: "LLM_COMPLETION_ABORTED" }));
        };
        controller.signal.addEventListener("abort", onAbort, { once: true });
      });
      timer = setTimeout(() => controller.abort(Object.assign(new Error("LLM_COMPLETION_TIMEOUT"), { code: "LLM_COMPLETION_TIMEOUT" })), request.timeoutMs);
      try {
        const verifiedConfig = await Promise.race([verify(api, config), aborted]);
        if (controller.signal.aborted) fail("BRIDGE_CALLER_ABORTED");
        if (verifiedConfig && api.runtime.config.current() !== verifiedConfig) fail("BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED", "runtime_configuration_changed");
        assertPolicyUnchanged(api, policy);
        if (request.expectedHostVersion !== api.runtime.version) fail("BRIDGE_HOST_VERSION_MISMATCH");
        const remainingMs = Math.floor(request.timeoutMs - (now() - started));
        if (remainingMs < 1) fail("LLM_COMPLETION_TIMEOUT");
        // Python persists its attempt before this RPC. IDs are also single-use in this plugin instance.
        seen.add(requestId);
        dispatched = true;
        active.dispatched = true;
        const operation = api.runtime.llm.complete({
          agentId: config.agentId, model: config.model, reasoning: request.reasoning,
          messages: request.messages.map(message => ({ ...message })), systemPrompt: request.systemPrompt,
          maxTokens: request.maxTokens, purpose: "gcb.profile",
          signal: controller.signal,
          execution: { mode: "isolated-agent-runtime", authProfileId: config.authProfileId, timeoutMs: remainingMs },
        });
        const response = await Promise.race([operation, aborted]);
        if (controller.signal.aborted) fail("LLM_COMPLETION_ABORTED");
        const result = curateResult(response, config);
        return { ...envelope(requestId, "completed", undefined, 1), result,
          measurement: { hostInvocations: 1, nativeRequestCount: null, effectiveReasoning: null,
            elapsedMs: Math.max(0, now() - started), outputLimitEnforced: false, billingProvenance: "unknown",
            hostVersion: api.runtime.version } };
      } catch (error) {
        if (dispatched) blocked = true;
        const safeCode = SAFE_ERRORS.has(error?.code) || ["BRIDGE_ATTRIBUTION_MISMATCH", "BRIDGE_OUTPUT_OVERSIZE", "BRIDGE_USAGE_INVALID", "BRIDGE_CALLER_ABORTED", "BRIDGE_SUBSCRIPTION_REQUIRED", "BRIDGE_SUBSCRIPTION_ROUTE_UNVERIFIED"].includes(error?.code)
          ? error.code : dispatched ? "LLM_COMPLETION_FAILED" : "BRIDGE_SUBSCRIPTION_UNVERIFIED";
        return { ...envelope(requestId, dispatched ? "unknown_completion" : "rejected", safeCode, dispatched ? 1 : 0),
          readiness: readiness(error, safeCode, dispatched || blocked) };
      } finally {
        clearTimeout(timer);
        controller.signal.removeEventListener("abort", onAbort);
        callerSignal?.removeEventListener("abort", abortFromCaller);
        active = undefined;
      }
    },
    cancel(request) {
      if (!isObject(request) || Object.keys(request).some(key => key !== "requestId") || !UUID.test(request.requestId ?? "")) return envelope(null, "rejected", "BRIDGE_INPUT_REJECTED");
      if (active?.requestId !== request.requestId) return envelope(request.requestId, "rejected", "BRIDGE_NO_ACTIVE_REQUEST");
      blocked = true;
      active.controller.abort();
      return envelope(request.requestId, "cancellation_requested", undefined, active.dispatched ? 1 : 0);
    },
  };
}
