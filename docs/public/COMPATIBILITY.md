# Verified compatibility

GCB is an independent Python package. It does not require KRE, a vector database, a hosted service or a provider API key for the offline demonstration. “Harness-agnostic” describes the core boundary; it does not mean every harness or model provider is supported.

| Path | Status | Verified boundary |
|---|---|---|
| Python core, CLI, inventory/research/bios | Working | Python 3.11+ package; macOS arm64 execution tested. Exact release verification lists the tested interpreter versions. |
| Deterministic Gate 0 and extensions | Working | Explicit registered operations and policy checks; third-party Python handlers are trusted code, not sandboxed plugins. Unknown operations decline. |
| Bundled fictional profiler | Working offline | Original Forge/Scribe fixtures; no real model or network required. |
| Existing local Ollama | Working, selected deployments tested | Loopback metadata and `/api/chat`; tested GGUF and Ollama-managed MLX/safetensors. Positive local artifact identity, supported metadata and native configuration checks remain required. |
| Optional OpenClaw/Codex isolated completion | Working in tested versions | OpenClaw 2026.11.1 with Codex 0.169.0-alpha.11.1; seven model IDs and 40 requested native configurations observed. Scoped administrator bridge, host-owned subscription authentication and operator-provided metadata snapshot required. |
| MCP stdio | Supported integration interface | An explicitly configured client launches the package; supported protocol fixtures and installation checks do not imply every live client discovers it automatically. |
| OpenClaw native governed routing | Preview-only | Complete pre-inference enforcement has not been established. No model reassignment or automatic provider dispatch follows a preview. |
| Hermes native governed routing | Preview-only | Compatibility research/contract fixtures do not prove complete native enforcement. No production Hermes inference adapter is claimed. |
| Other provider transports and execution backends | Unsupported or unverified | No universal OpenAI API-key transport or arbitrary provider compatibility is claimed. |
| Automatic live inventory discovery | Not implemented as universal onboarding | Ollama starts from an explicit installed tag; the host adapter consumes an operator-provided researched snapshot. The private campaign's catalog and workload orchestration are not ordinary installed features. |

The tested GPT native IDs are `gpt-6.1-sol`, `gpt-6.1-terra`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-terra` and `gpt-5.6-luna`. These are observed account-accessible models, not a guarantee of another account's catalog or entitlement. Low/Medium/High/Extra High/Max were researched for all seven; Ultra was researched for the five non-Luna models. Requested settings remain distinct from effective settings, which were not echoed. Do not apply this effort list to another model by name similarity.

For local Qwen models, thinking is a native boolean, not invented Low/Medium/High labels. Local model format labels are retained as metadata; GCB invokes the supported Ollama API rather than parsing, converting or downloading model weights. The tested MLX implementation's context behavior differs from GGUF, and selected request limits must not be represented as physical-memory limits.

Profiling uses POSIX process-ownership checks. macOS arm64 is the verified execution platform. Linux and other architectures are unverified until their CI/runtime checks actually run; Windows profiling fails closed while a supported native ownership probe is unavailable. A universal wheel tag does not establish every operating system's execution compatibility.

OpenClaw/Codex is optional. Users install and scope the bridge deliberately; it exposes administrator operations, not a broad agent-tool surface. Credentials remain host-managed. A newer SDK document does not prove an older installed runtime supports the same functions. Other versions require compatible contract checks; no automatic fallback to API-key billing is provided.

See [benchmark methodology](BENCHMARKS.md) and [known limitations](LIMITATIONS.md). Numeric local bounds, host software deadlines and cancellation signals have different guarantees. None should be advertised as a universal provider billing cap.
