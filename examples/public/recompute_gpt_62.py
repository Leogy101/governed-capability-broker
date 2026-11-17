"""Verify the GPT-6.2 follow-up evidence arithmetic without inference."""

import json
import math
from pathlib import Path


MODELS = {"gpt-6.2-sol": "GPT-6.2 Sol", "gpt-6.2-astra": "GPT-6.2 Astra"}
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
BASELINE = {
    "coding.guard": "coding",
    "review.risks": "code_review",
    "research.source-pack": "research",
    "planning.dependencies": "planning",
    "structured.invoice": "structured_data",
    "orchestration.choice": "orchestration",
}
CONTRAST = ("coding.guard", "structured.invoice")
TOKEN_KEYS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "uncached_input_tokens", "reported_total_tokens",
)
ATTEMPT_STATUSES = {"completed", "technical_error", "rate_limited", "unknown_completion", "not_dispatched"}
SCORED_OUTCOMES = {"exact_match", "structured_mismatch", "invalid_json", "unexpected_native_call_or_nonfinal_response"}


def summarize(rows):
    attempted = [row for row in rows if row["status"] != "not_dispatched"]
    latencies = [row["latency_ms"] for row in attempted]
    return {
        "scheduled_cases": len(rows),
        "observed_cases": sum(row["correct"] is not None for row in rows),
        "correct_cases": sum(row["correct"] is True for row in rows),
        "attempted_cases": len(attempted),
        "completed_attempts": sum(row["status"] == "completed" for row in rows),
        "unknown_completion": sum(row["status"] == "unknown_completion" for row in rows),
        "usage": {key: sum(row[key] for row in attempted) if attempted and all(row[key] is not None for row in attempted) else None for key in TOKEN_KEYS},
        "observed_latency_seconds": sum(latencies) / 1000 if latencies and all(value is not None for value in latencies) else None,
    }


def verify(document):
    assert document["schema"] == "gcb.public-benchmark-observations/v1"
    assert document["suite_version"] == "gcb-small-2" and document["scorer_version"] == "exact-structured-1"
    assert document["suite_sha256"] == "27e17ed8bcab6d884d4388576e7dabd185c690d3d73435a4386b371698de7d48"
    rows = document["observations"]
    identities = {(row["model"], row["requested_reasoning"], row["case_id"]) for row in rows}
    assert len(identities) == len(rows), "Repeated case/configuration"
    assert {model["model"] for model in document["models"]} == set(MODELS)
    expected = set()
    for model in document["models"]:
        model_id = model["model"]
        efforts = model["requested_reasoning_settings"]
        assert "medium" in efforts and len(efforts) == len(set(efforts)) and set(efforts) <= set(EFFORTS)
        for effort in efforts:
            cases = BASELINE if effort == "medium" else CONTRAST
            expected.update((model_id, effort, case) for case in cases)
        selected = [row for row in rows if row["model"] == model_id]
        assert model["totals"] == summarize(selected), "Model totals do not reconcile"
    assert expected == identities, "Planned case coverage differs from the recorded observations"
    for row in rows:
        assert row["category"] == BASELINE[row["case_id"]]
        assert row["status"] in ATTEMPT_STATUSES
        assert all(row[key] is None for key in ("effective_reasoning", "reasoning_tokens", "billed_cost_usd", "provider_request_count"))
        assert all(row[key] is None or type(row[key]) is int and row[key] >= 0 for key in TOKEN_KEYS)
        latency = row["latency_ms"]
        assert latency is None or type(latency) in (float, int) and latency >= 0 and math.isfinite(latency)
        assert row["host_invocations_observed"] == int(row["status"] != "not_dispatched")
        if row["status"] != "completed":
            assert row["correct"] is None and latency is None and all(row[key] is None for key in TOKEN_KEYS)
        if row["correct"] is not None:
            assert type(row["correct"]) is bool and row["scoring_outcome"] in SCORED_OUTCOMES
            assert row["correct"] == (row["scoring_outcome"] == "exact_match")
        else:
            assert row["scoring_outcome"] in (None, "output_truncated")
        if all(row[key] is not None for key in ("input_tokens", "output_tokens", "reported_total_tokens")):
            assert row["reported_total_tokens"] == row["input_tokens"] + row["output_tokens"]
        input_keys = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens")
        if row["input_tokens"] is not None and all(row[key] is not None for key in input_keys):
            assert row["input_tokens"] == sum(row[key] for key in input_keys)
    totals = summarize(rows)
    assert document["totals"] == totals, "Combined totals do not reconcile"
    return {"checks": "passed", **totals}


if __name__ == "__main__":
    evidence = json.loads(Path(__file__).with_name("gpt-6.2-observations.json").read_text())
    print(json.dumps(verify(evidence), indent=2))
