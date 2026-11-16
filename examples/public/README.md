# Public examples and measured evidence

These examples contain no account state, agent assignment, Gateway configuration, operational journal, absolute user path or raw private transcript. Real results and fictional demonstrations are separate.

| File | Meaning |
|---|---|
| [synthetic-bio.json](synthetic-bio.json) | Fresh offline Forge demonstration. This fictional backend scripts responses; it is not a real model measurement. |
| [gpt-medium-bio.json](gpt-medium-bio.json) | Curated GPT-6.1 Sol Medium example with six observed fixtures. |
| [local-partial-bio.json](local-partial-bio.json) | Curated `qwen3.5:9b` example, including its failed planning fixture. |
| [gpt-observations.json](gpt-observations.json) | All 108 measured GPT observations, retained numeric usage and exact fixture outcomes. |
| [ollama-observations.json](ollama-observations.json) | Local pilot and partial Qwen3.6 coverage, including unknown and untouched work. |

The three example bios are explicitly labeled **public projections**, not importable runtime inventories or complete raw bio exports. They cannot grant execution authority. Generate a complete ordinary bio with the installed CLI:

```sh
gcb --db demo.sqlite profile demo
gcb --db demo.sqlite bio forge
```

Those commands are entirely offline. To check the measured GPT arithmetic without installing GCB or contacting a provider:

```sh
python3 examples/public/recompute.py
```

The check recomputes model/token totals, detects duplicate case/configuration rows, preserves the two reused samples, verifies cached-token inclusion and checks all 33 case-matched reasoning contrasts. It does not rerun inference or independently establish general answer quality. The [methodology and limits](../../docs/public/BENCHMARKS.md) explain the original experiment.

The published rows were projected through explicit field allowlists from retained development evidence. Model identifiers and local model digests describe tested artifacts; private request/session/account identifiers were omitted. Supplied inputs and deterministic scoring are the original bundled `gcb-small-2` fixtures. The private source history and operational records are intentionally not part of these examples.
