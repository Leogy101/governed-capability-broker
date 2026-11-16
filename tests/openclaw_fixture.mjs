// Cross-language contract fixture: no SDK imports, credentials, network or real host.
import { createBridge } from "../integrations/openclaw-host/bridge.mjs";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const api = {
  pluginConfig: { agentId: "fixture-agent", model: "openai/gpt-fixture", allowedReasoning: ["medium"], authProfileId: "fixture-subscription" },
  runtime: { version: "2026.11.1", llm: { complete: async params => ({
    text: '{"missing_guard":"empty_items"}', provider: "openai", model: "gpt-fixture", agentId: params.agentId,
    execution: { mode: params.execution.mode, owner: { kind: "harness", id: "codex" } },
    usage: { inputTokens: 30, cacheReadTokens: 70, cacheWriteTokens: 0, outputTokens: 12, totalTokens: 112 },
    audit: { credentialCanary: "DO_NOT_PERSIST" },
  }) } },
};
console.log(JSON.stringify(await createBridge(api, { verifySubscription: async () => {} }).complete(request)));
