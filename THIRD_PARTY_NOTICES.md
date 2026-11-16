# Third-party attribution and dependencies

GCB's Python runtime dependency list is empty. The public source candidate contains original GCB implementation, original synthetic benchmark fixtures and the shared versioned context contract. No downloaded harness implementation, model weights or provider SDK is vendored. The original vector logo uses system font fallback and bundles no font or third-party artwork.

| Component | Relationship | License / reference | Distributed here |
|---|---|---|---|
| Python standard library | Required external interpreter | [PSF license](https://docs.python.org/3/license.html) | No interpreter/stdlib copy |
| SQLite | Supplied by Python's SQLite module | [Public domain](https://sqlite.org/copyright.html) | No SQLite implementation |
| setuptools | Build-time backend, not runtime dependency | [MIT](https://github.com/pypa/setuptools/blob/main/LICENSE) | Not vendored |
| PyPA build | Optional CI/build frontend | [MIT](https://github.com/pypa/build/blob/main/LICENSE) | Not vendored |
| Node.js | External optional bridge/test runtime | [Node license](https://github.com/nodejs/node/blob/main/LICENSE) | Not vendored |
| OpenClaw | Optional external host and public SDK interfaces | Installed 2026.11.1 declares MIT; [upstream](https://github.com/openclaw/openclaw) | No host implementation |
| Ollama | Optional external local execution service | [MIT upstream](https://github.com/ollama/ollama/blob/main/LICENSE) | No service or model weights |
| Codex / GPT services | Optional host-owned execution and access | Applicable provider/service terms | No binary, credential or entitlement |
| Qwen and derivative model weights | Independently installed models used for observations | Model-specific upstream licenses; not granted by GCB | No weights |

Public schemas and fixture pseudocode are authored project examples. References to providers, harnesses and model names describe interoperability, not endorsement. Dependencies used only for development/build retain their own licenses; users obtain them separately. Future vendoring or a new dependency requires a fresh attribution review.

GCB is licensed under [Apache-2.0](LICENSE), with copyright attribution to 2026 Brandon Gordon and GCB contributors in [NOTICE](NOTICE). See [licensing details](docs/public/LICENSING.md). GCB's license does not replace the separate terms governing external runtimes, services or model weights.
