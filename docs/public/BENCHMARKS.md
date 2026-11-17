# What the measured profiles establish

GCB produced real local-Ollama profiles and subscription-backed GPT profiles through the tested OpenClaw/Codex path. These experiments establish adapter compatibility, durable observations and small descriptive capability bios. **They do not establish universal model qualification, general model superiority or guaranteed workflow savings.** Poor performance remains visible in the inventory.

The public evidence is a field-limited projection of retained development records. It omits account information, host configuration, private agent details, raw operational journals and unneeded transcripts. [Public data and offline checks](../../examples/public/README.md) reproduce the arithmetic without those records or any inference.

## GPT campaign

The November 2026 campaign tested seven accessible models and 40 researched native reasoning configurations through OpenClaw **2026.11.1**, Codex **0.169.0-alpha.11.1**, on macOS arm64. Access was through host-managed subscription authentication, not a direct API-key transport. This inventory belongs to the tested account/runtime combination; availability for another account is not promised.

The first configuration was Medium: six short fixtures covering static coding diagnosis, code review, supplied-source research, dependency planning, structured extraction and orchestration choice. Additional configurations received the same two coding/structured contrasts. All fixtures belong to `gcb-small-2`, scored by `exact-structured-1`. Native tool execution and recovery were unmeasured because the isolated interface exposed no real tool execution surface. Orchestration answers were plans, not executed tool workflows; research used supplied material, not the open web.

The campaign added 106 sequential host invocations and reused two matching Medium coding observations. No automatic retries or answer repairs were used. There was one observation per applicable model/setting/case; no repeatability sample or randomized order. The request used a 60-second best-effort deadline and an advisory 512-token hint. Neither establishes provider termination, an enforced native output ceiling or a spending cap.

| Model | Native reasoning configurations tested | Correct / observed | Input | Output | Cached input, included | Total | Observed latency sum |
|---|---|---:|---:|---:|---:|---:|---:|
| GPT-6.1 Sol | low, medium, high, xhigh, max, ultra | 16/16 | 74,961 | 545 | 27,264 | 75,506 | 69.92 s |
| GPT-6.1 Terra | low, medium, high, xhigh, max, ultra | 16/16 | 74,961 | 317 | 35,072 | 75,278 | 53.19 s |
| GPT-6 Astra | low, medium, high, xhigh, max, ultra | 16/16 | 74,961 | 438 | 8,832 | 75,399 | 63.96 s |
| GPT-6 Sol | low, medium, high, xhigh, max, ultra | 16/16 | 74,961 | 506 | 48,128 | 75,467 | 53.30 s |
| GPT-6 Luna | low, medium, high, xhigh, max | 14/14 | 65,596 | 375 | 20,992 | 65,971 | 43.73 s |
| GPT-5.6 Terra | low, medium, high, xhigh, max, ultra | 16/16 | 69,153 | 419 | 14,336 | 69,572 | 56.31 s |
| GPT-5.6 Luna | low, medium, high, xhigh, max | 14/14 | 60,514 | 362 | 31,232 | 60,876 | 47.12 s |

Totals include both reused observations: **495,107 input + 2,962 output = 498,069 host-reported tokens**, with 185,856 cached input already included. The 106 new invocations used **485,719 input + 2,923 output = 488,642 tokens**, including 183,808 cached input. Do not add cached tokens again. Luna models have fewer calls; the aggregate table is not a controlled model-efficiency ranking. Different host input counts across model families may reflect tokenizer/host-context differences that were not isolated.

All 108 responses matched their frozen criteria. All 33 additional-effort comparisons preserved both matched outcomes; higher reasoning added no measured correctness on these simple cases. Requested native effort was recorded, but effective effort was not independently verified. Equal scores do not establish equal overall capability. Unequal cache hits, fixed order, service load and reused historical samples confound one-sample latency comparisons. Planning was tested only at Medium.

New active profiling totaled **376.403 seconds**. The campaign elapsed interval was **1,545.484 seconds (25m45s)** including admission checks and pauses. First partial observations arrived in 2.66–5.95 seconds from each model's first new dispatch; Medium baselines were available in 35.25–130.19 seconds, reusing the two prior samples where applicable. Installation and initial authorization were outside these intervals. This supports useful bios in minutes under the tested conditions, not a universal onboarding guarantee. Observed latency includes transport/profiler overhead and is not pure generation time.

Host input/output/cache coverage is complete for these observations. Underlying provider requests/retries, reasoning-token counts, full host-augmented prompt bytes, actual charges and effective reasoning remain unknown. An unclassified host cost value of zero is not a billed-zero measurement. Subscription usage is not interchangeable with API pricing, and no cost-saving claim follows.

## GPT-6.2 follow-up

On November 17, 2026, GCB profiled GPT-6.2 Sol and GPT-6.2 Astra through OpenClaw **2026.11.1** and Codex **0.169.0-alpha.11.1** using host-managed ChatGPT subscription authentication on macOS arm64. Each Medium baseline used the six `gcb-small-2` text fixtures. Other tested settings used the same two coding and structured-extraction contrasts, scored by `exact-structured-1`. The jobs ran sequentially, with a 60-second best-effort deadline and an advisory 512-token output hint. Native-tool execution and recovery were inapplicable to the isolated interface.

| Model | Requested reasoning settings | Correct / scored / applicable | Host input tokens | Host output tokens | Cached input, included | Host total tokens | Observed latency sum |
|---|---|---:|---:|---:|---:|---:|---:|
| GPT-6.2 Sol | low, medium, high, xhigh, max, ultra | 16 / 16 / 16 | 80,113 | 336 | 14,464 | 80,449 | 64.60 s |
| GPT-6.2 Astra | low, medium, high, xhigh, max, ultra | 16 / 16 / 16 | 80,113 | 322 | 43,904 | 80,435 | 102.24 s |

32 host invocations were recorded. All applicable cases were scored. Cached input is included in host input. Requested reasoning effort was recorded. Effective effort, provider request counts and billed cost were unknown. There was at most one sample per model, setting and case. Fixed order and unequal cache hits limit latency comparisons. See the [public observations and offline arithmetic check](../../examples/public/README.md).

## Four-model GPT comparison

Each model scored 16/16 on the same `gcb-small-2` cases with the `exact-structured-1` scorer. The matched set covers six cases at Medium and two cases each at Low, High, Extra High, Max, and Ultra.

| Model | Correct / observed | Host input tokens | Cached input, included | Host output tokens | Host total tokens | Summed observed latency |
|---|---:|---:|---:|---:|---:|---:|
| GPT-6.1 Sol | 16/16 | 74,961 | 27,264 | 545 | 75,506 | 69.92 s |
| GPT-6.2 Sol | 16/16 | 80,113 | 14,464 | 336 | 80,449 | 64.60 s |
| GPT-6 Astra | 16/16 | 74,961 | 8,832 | 438 | 75,399 | 63.96 s |
| GPT-6.2 Astra | 16/16 | 80,113 | 43,904 | 322 | 80,435 | 102.24 s |

| Comparison | Host input change | Host output change | Host total change | Summed latency change |
|---|---:|---:|---:|---:|
| GPT-6.2 Sol vs GPT-6.1 Sol | +5,152 (+6.9%) | −209 (−38.3%) | +4,943 (+6.5%) | −5.33 s (−7.6%) |
| GPT-6.2 Astra vs GPT-6 Astra | +5,152 (+6.9%) | −116 (−26.5%) | +5,036 (+6.7%) | +38.28 s (+59.8%) |

Both GPT-6.2 models returned fewer output tokens, and higher reported input raised their totals. Cached tokens are included in input. Summed latency includes transport and profiler overhead. Separate runs, fixed order, unequal cache hits, and one observation per case limit the timing comparison. The GPT-6.1 Sol results include one reused Medium coding observation. Effective reasoning effort and reasoning-token counts were unavailable. [Earlier GPT observations](../../examples/public/gpt-observations.json) · [GPT-6.2 follow-up observations](../../examples/public/gpt-6.2-observations.json)

## Local Ollama experiments

Local experiments used the existing Ollama API with exact installed tags, researched native thinking values and immutable model identities. No weight conversion or separate MLX inference service was introduced. The tested Qwen3.6 runtime was Ollama **0.40.15**; its GGUF and MLX-backed deployments used the same `/api/chat` adapter.

| Installed model | Tested native setting | Retained result | Important coverage limitation |
|---|---|---|---|
| `qwen3.5:9b` | `think:false` | 7/8 correct fixtures, 9 completed attempts; 1,609 input + 203 output = 1,812 tokens | Planning fixture failed; no broad certification |
| `qwen3.6:35b-a3b` | `think:false`, `think:true` | 8 scored fixtures correct, 2 historical unknown attempts | False coding/review remain unknown; complete historical usage cannot be calculated |
| `huihui_ai/Qwen3.6-abliterated:35b-a3b` | Measured `think:false`; true researched but unmeasured | 3/3 scored fixtures; 350 input + 49 output = 399 tokens | Seven untouched cells, including both thinking contrasts |

The Standard deployment was `safetensors`/`mlx`, quantization `nvfp4`; the Abliterated deployment was `gguf`/`ggml`, quantization `Q4_K_M`. Exact model digests and per-setting coverage are in [local observations](../../examples/public/ollama-observations.json). Format, quantization, cold/warm state and task coverage differ. Differences cannot be attributed solely to abliteration or used as a fair aggregate speed comparison.

Standard's single matched structured-extraction fixture was correct with both settings: 107 total tokens with false versus 223 with true, **108.41% more** with thinking. Ollama duration was 0.520 versus 1.629 seconds. This is one simple fixture; it is not a universal reasoning policy. Thinking tokens were not reported separately. A requested 4,096-token context does not establish 4K effective context or memory allocation for the tested MLX implementation.

The 9B pilot accumulated 18.237 seconds of active runner work; its observed request-to-completion interval was 183.421 seconds including deliberate cancellation/resumption and verification. A completed observation survived resumption exactly once. Separate reasoning usage, orchestrator usage and monetary charges were unknown. Local execution does not establish zero operating cost.

Only frozen harmless native fixture tools ran where supported. Generated code was never executed. Technical unknown completion is distinct from poor task performance and was not automatically replayed. The incomplete local coverage and negative planning result are intentionally retained.

## Reproduction and interpretation

Run `python3 examples/public/recompute.py` to independently verify the public GPT totals and matching-case accounting offline. Run `gcb --db demo.sqlite profile demo` to reproduce the fictional end-to-end profiling example, then `gcb --db demo.sqlite bio forge` to inspect its full generated bio. Fictional outcomes and timings must never replace measured provider evidence.

Reproducing real inference requires your own accessible model/runtime, explicit research and bounded authorization. Record exact model metadata, requested native settings, fixture/scorer versions, every attempt, complete returned usage and unknown fields. Preserve incomplete attempts; do not retry uncertain work merely to make a table complete. Use the ordinary onboarding walkthrough rather than private campaign scripts.

The bundled fixtures are intentionally small. The current results support a transparent experimental alpha and useful onboarding records. They are not a before/after test proving that GCB-guided task execution reduces tokens, time or money while preserving real-workflow quality. Native harness routing remains preview-only.
