# Model onboarding with the installed CLI

Install with `python -m pip install .` inside a Python 3.11+ virtual environment, then run `gcb doctor`. No private campaign helper is needed. macOS arm64 is verified. Build tooling may need package-index access; the installed runtime does not.

## Existing local Ollama

GCB does not install Ollama, download models, convert weights or change a harness. An existing loopback runtime and installed model are prerequisites. Release verification uses fake HTTP responses and read-only live discovery only. The documented `start` command below would generate real inference after the operator authorizes it.

```sh
ollama list
```

Choose an exact installed tag. Change the example if it is not installed. Research registers the deployment and reads `/api/version`, `/api/tags` and `/api/show`, without inference.

```sh
MODEL_TAG='qwen3.5:9b'
gcb --db local.sqlite3 init
gcb --db local.sqlite3 profile research local-model --backend ollama \
  --endpoint http://127.0.0.1:11434 --native-model "$MODEL_TAG"
gcb --db local.sqlite3 models
```

Inspect native settings and the default. Boolean `think` stays boolean; GCB does not invent effort labels. GGUF and Ollama-managed MLX/safetensors are metadata formats, not separate runtimes. Missing thinking metadata or conflicting identity blocks execution.

Prepare the initial profile using the researched default:

```sh
gcb --db local.sqlite3 profile plan local-model --backend ollama \
  --endpoint http://127.0.0.1:11434 --native-model "$MODEL_TAG" \
  --no-contrast --max-requests 9 --call-timeout 60 --total-timeout 300 > plan.json
```

Six baseline cases plus supported tool/recovery fixtures need at most nine attempts; recovery uses two stages. Unsupported fixtures stay unmeasured. To choose another researched baseline, add `--baseline-configuration CONFIGURATION_ID` using the returned ID. Context/output defaults are 4096/512. MLX may use its intrinsic allocation despite `num_ctx`; this is not a memory cap.

Read `plan.json`, then substitute its `plan_id` below. Starting is the explicit operator authorization step. Use a safe resource/workload window.

```sh
gcb --db local.sqlite3 profile start PLAN_ID --backend ollama \
  --endpoint http://127.0.0.1:11434 --native-model "$MODEL_TAG" --authorize
gcb --db local.sqlite3 profile status PLAN_ID
gcb --db local.sqlite3 bio local-model
gcb --db local.sqlite3 evidence PLAN_ID
gcb --db local.sqlite3 route --task '{"category":"coding"}'
```

Weak results still receive bios. A route explains a choice without invoking Ollama. `no_eligible` names missing requirements rather than silently falling back.

## Pause, resume and remove

Add `--max-cells 1` to start for a partial bio and controlled pause. Save its returned `grant_id`. Resume with the same backend and `--grant GRANT_ID`, without also adding `--authorize`. Original limits remain in force.

```sh
gcb --db local.sqlite3 profile cancel PLAN_ID
gcb --db local.sqlite3 profile resume PLAN_ID --backend ollama \
  --endpoint http://127.0.0.1:11434 --native-model "$MODEL_TAG" --grant GRANT_ID
```

Cancellation/timeouts are best effort; disconnection may leave backend work running. Unknown completion blocks replay. Never delete a ledger or reload a plugin to make uncertain work repeat. GCB does not unload another application's model or kill a shared runner.

To remove GCB, stop its command, remove only its own MCP client entry if configured, then run `python -m pip uninstall governed-capability-broker`. SQLite files remain yours to archive/delete explicitly. Removing GCB does not uninstall Ollama or alter agents. The optional host plugin has a separate [disable/uninstall procedure](../../integrations/openclaw-host/README.md).

## Optional GPT path

The [OpenClaw/Codex setup](../../integrations/openclaw-host/README.md) requires an operator-installed bridge, scoped permissions and reviewed catalog snapshot. GCB uses the existing authenticated OpenClaw CLI, not an API-key transport. The operator must verify entitlement and capacity; catalog advertising is not proof of either. Public templates are not current authorization.
