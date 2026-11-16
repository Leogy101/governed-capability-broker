# Supported interfaces

GCB is an independent Python package with no runtime dependencies. The CLI and Python API share inventory, research, profiling, scoring and routing previews. Optional integrations do not replace those components. Task suitability means narrow observed fixture performance, not universal model certification.

## CLI

Run `gcb --help` for commands. Put `--db PATH` before the command. `gcb doctor` reports local Python/SQLite capabilities without network probes; `gcb demo` and `gcb profile demo` are offline demonstrations.

The profiler sequence is `profile research` → `profile plan` → explicitly authorized `profile start`. `profile status`, `profile cancel`, `profile resume`, `bio` and `evidence` preserve durable identities. A grant never expands the immutable plan. Completed work is reused; uncertain completion is not replayed. `gcb models` lists GCB's local inventory, not a live provider catalog.

`--backend ollama` uses an explicit existing loopback runtime and exact installed tag. `--backend openclaw` requires an operator-reviewed snapshot and the separately installed bridge; execution additionally requires `--approve-host-runtime`. No direct API-key transport is shipped. Research always precedes benchmarking. Bio/evidence/status reads and routing previews do not invoke a model.

`gcb route` returns an explainable **preview** and may decline all candidates. Remote candidates require explicit policy permission. This neither changes a harness assignment nor establishes complete pre-inference enforcement. `gcb measure` provides offline usage aggregation and deterministic task-quality scoring; it does not automatically collect another agent's entire execution trace.

## Python and deterministic Gate 0

`Inventory`, `TaskRequirements`, `RoutingPolicy` and `preview_route` are available from `gcb`. `ProfileManager` from `gcb.profiler` exposes research, planning, grants, lifecycle, evidence and bios. This is a trusted local programming interface, not an untrusted plugin sandbox.

Gate 0 runs only an explicitly named approved handler. Built-ins are `math.calculate`, `data.select` and permission-gated `inventory.describe`. Unknown operations decline; invalid arguments or insufficient permissions deny. There is no implicit model fallback. Generated benchmark code is text and never executed.

Trusted developers can add a bounded deterministic handler:

```python
from gcb.gate0 import Gate0Registry, HandlerSpec

def validate_text(arguments):
    if set(arguments) != {"text"}:
        raise ValueError("text_only")
    text = arguments["text"]
    if not isinstance(text, str) or len(text.encode("utf-8")) > 4096:
        raise ValueError("bounded_text_required")
    return text

registry = Gate0Registry()
registry.register(
    HandlerSpec("text.length", "1", validate_text,
                lambda text, context: {"characters": len(text)}),
    approved=True,
)
assert registry.run("text.length", {"text": "hello"}).value == {"characters": 5}
```

Registration executes trusted developer-supplied Python. The registry validates data, permissions and bounded JSON results, but does not sandbox a handler, prove its purity or cancel arbitrary Python code. Never load a handler from retrieved text or generated code. Supply `required_permissions` and a trusted `GateContext` for operations needing access beyond their arguments.

## Optional MCP stdio server

The owning MCP client launches the server explicitly. It listens on stdio, not a network port, and requires an initialized inventory plus a fixed allowlist of exact local tags:

```sh
gcb --db local-profile.db init
python -m gcb.mcp --db local-profile.db --model MODEL:TAG
```

Replace `MODEL:TAG` with an exact installed local tag. Repeat `--model` for up to 32 allowed tags. For this binding, use that same exact tag as the GCB inventory model ID when researching it; a separate CLI alias is a different entry. Startup validates the local binding without discovery or inference.

Default advertised tools are read-only: `gcb_models`, `gcb_status`, `gcb_bio`, `gcb_evidence` and `gcb_recommend`. Recommendations are local-only, allowlisted and always previews. The local process owns the inventory database; tool read-only annotations are not an operating-system read-only filesystem guarantee.

Only an operator's explicit `--allow-profiling` startup flag adds `gcb_research`, `gcb_plan`, `gcb_start` and `gcb_cancel`. This delegates bounded local Ollama profiling, not remote GPT usage. Limits are 45 seconds per call, 300 seconds per plan, at most 16 attempts and one cell per tool invocation. An exact native baseline can be chosen with its researched configuration ID; contrasts default off. Tool arguments cannot enlarge the fixed startup binding. Client approvals still apply; the flag does not bypass host approval policy.

The MCP host manages process lifetime. Disable GCB by removing only its MCP entry/stopping its process after requests settle; retain the inventory if its evidence is wanted. Removing the Python environment removes GCB without uninstalling Ollama or changing a harness. Cancellation and a closed connection do not prove server-side inference termination.

The private workload monitoring and account/schedule checks used in retained experiments are **not** features of this server or installed CLI. Do not advertise automatic protection of another agent's workloads.

## Optional OpenClaw/Codex bridge

The [bridge guide](../../integrations/openclaw-host/README.md) covers disabled installation, narrow configuration, metadata-only authorization, explicit bounded usage and rollback. It exposes three administrator RPCs and no agent-facing tools. Host authentication and model allowlists remain authoritative. Public snapshot/configuration examples are fictional templates; they neither discover current models nor authorize execution.

Successful isolated profiling was measured on the documented OpenClaw/Codex combination. Native OpenClaw and Hermes routing remains preview-only. Other providers/backends and automatic account/catalog discovery are unimplemented or unverified; consult the [compatibility matrix](COMPATIBILITY.md).

## Independent KRE context contract

The carried [context contract v0.1](../../contracts/context/0.1/README.md) lets a separate knowledge producer provide bounded cited evidence. GCB's `inspect_context` consumer validates the supported shape, identifiers, hashes, declared budgets, lifecycle/source filters, correlation and locality restrictions. Local-only context constrains a task to local routing.

GCB does not import, install or require KRE. The consumer cannot prove a producer's current authorization, retract text already disclosed or atomically govern an external host dispatch. Retrieved text is evidence, never executable instructions or a permission grant. Both projects remain independently installable; no mandatory third service is introduced.
