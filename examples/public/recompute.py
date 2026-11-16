"""Recompute the published GPT table offline; no GCB install or network needed."""
from collections import Counter
import json
from pathlib import Path


def main():
    evidence = json.loads(Path(__file__).with_name("gpt-observations.json").read_text())
    rows = evidence["observations"]
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reported_total_tokens")
    assert len(rows) == 108
    identities = {(r["model"], r["requested_reasoning"], r["case_id"]) for r in rows}
    assert len(identities) == len(rows), "Repeated case/configuration"
    assert len({r["model"] for r in rows}) == 7
    assert len({(r["model"], r["requested_reasoning"]) for r in rows}) == 40
    assert Counter(r["historical_reused"] for r in rows) == {False: 106, True: 2}
    for row in rows:
        assert row["status"] == "completed" and row["correct"] is True
        assert row["host_invocations_observed"] == 1
        assert all(type(row[key]) is int and row[key] >= 0 for key in keys)
        assert row["input_tokens"] == row["uncached_input_tokens"] + row["cache_read_tokens"] + row["cache_write_tokens"]
        assert row["reported_total_tokens"] == row["input_tokens"] + row["output_tokens"]
        assert row["effective_reasoning"] is None
        assert row["billed_cost_usd"] is None and row["provider_request_count"] is None

    def totals(selected):
        return {key: sum(row[key] for row in selected) for key in keys}

    assert totals(rows) == evidence["totals_all_observations"]
    assert totals([r for r in rows if not r["historical_reused"]]) == evidence["totals_new_observations"]
    models = []
    for model in evidence["models"]:
        selected = [row for row in rows if row["model"] == model["model"]]
        assert len(selected) == model["observed_cases"] == model["correct_cases"]
        assert set(model["requested_reasoning_settings"]) == {r["requested_reasoning"] for r in selected}
        models.append({"model": model["model"], "correct": len(selected), **totals(selected),
                       "observed_latency_seconds": sum(row["latency_ms"] for row in selected) / 1000})
    by_key = {(r["model"], r["requested_reasoning"], r["case_id"]): r for r in rows}
    pairs = 0
    for model in evidence["models"]:
        for effort in model["requested_reasoning_settings"]:
            if effort == "medium":
                continue
            for case in evidence["methodology"]["contrast_cases"]:
                baseline = by_key[(model["model"], "medium", case)]
                contrast = by_key[(model["model"], effort, case)]
                assert baseline["correct"] is True and contrast["correct"] is True
            pairs += 1
    assert pairs == 33
    local = json.loads(Path(__file__).with_name("ollama-observations.json").read_text())
    pilot, standard, derivative = local["models"]
    assert pilot["model"] == "qwen3.5:9b"
    pilot_attempts = [a for case in pilot["cases"] for a in case["attempts"]]
    assert len(pilot_attempts) == pilot["completed_attempts"] == 9
    assert len(pilot["cases"]) == pilot["observed_cases"] == 8
    assert sum(case["correct"] is True for case in pilot["cases"]) == pilot["correct_cases"] == 7
    for key in ("input_tokens", "output_tokens"):
        assert sum(a["usage"][key] for a in pilot_attempts) == pilot["usage"][key]
    assert [c["case_id"] for c in pilot["cases"] if not c["correct"]] == ["planning.dependencies"]
    local_summaries = [{"model": pilot["model"], "observed": 8, "correct": 7,
                        "attempts": 9, "input_tokens": pilot["usage"]["input_tokens"],
                        "output_tokens": pilot["usage"]["output_tokens"]}]
    for model, expected in ((standard, (8, 8, 2, 0, 11)), (derivative, (3, 3, 0, 7, 3))):
        categories = [category for config in model["configurations"] for category in config["categories"].values()]
        values = tuple(sum(c[key] for c in categories) for key in ("observed", "correct", "unknown_completion", "pending"))
        attempts = sum(c["attempt_count"] for c in model["configurations"])
        assert (*values, attempts) == expected
        for config in model["configurations"]:
            assert config["effective_native"] is None
            assert config["usage"]["billed_cost_usd"] is None
            assert config["usage"]["reasoning_tokens"] is None
            if any(c["unknown_completion"] for c in config["categories"].values()):
                assert config["usage"]["input_tokens"] is None
                assert config["usage"]["output_tokens"] is None
        local_summaries.append({"model": model["model"], "observed": values[0], "correct": values[1],
                                "unknown": values[2], "pending": values[3], "attempts": attempts})
    print(json.dumps({"checks": "passed", "models": models, "total": totals(rows),
                      "new_calls_only": evidence["totals_new_observations"],
                      "matched_two_case_contrasts": pairs,
                      "local_profiles": local_summaries,
                      "billing_and_underlying_request_counts": "unknown"}, indent=2))


if __name__ == "__main__":
    main()
