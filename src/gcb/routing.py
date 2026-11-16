"""Deterministic eligibility previews; no dispatch or quality scores are implied."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import json
import math
from typing import Any

from .gate0 import Gate0Registry, GateContext, GateResult, builtin_registry
from .cases import CASE_BY_ID, SCORER_VERSION, SUITE_HASH, SUITE_VERSION
from .inventory import Inventory
from .models import RoutingPolicy, TaskRequirements
from .research import resolve_facts
from .validation import ValidationError, canonical, digest


@dataclass(frozen=True)
class CandidateDecision:
    configuration_id: str
    model_id: str
    backend_id: str
    eligible: bool
    reasons: tuple[str, ...]
    comparable_observed: int = 0
    comparable_correct: int = 0
    comparable_latency_ms: float | None = None
    latency_case_count: int = 0


@dataclass(frozen=True)
class RoutePreview:
    status: str
    selected_configuration_id: str | None
    candidates: tuple[CandidateDecision, ...]
    policy_id: str
    policy_revision: int
    gate0: GateResult
    enforcement: str = "preview_only"
    ranking: str = "eligible_configuration_id_ascending_no_quality_inference"


def preview_route(inventory: Inventory, task: TaskRequirements, policy: RoutingPolicy, *,
                  operation: str | None = None, arguments: Any = None,
                  gate_registry: Gate0Registry | None = None, gate_context: GateContext | None = None) -> RoutePreview:
    if not isinstance(inventory, Inventory) or not isinstance(task, TaskRequirements) or not isinstance(policy, RoutingPolicy):
        raise ValidationError("typed_routing_inputs_required")
    registry = gate_registry if gate_registry is not None else builtin_registry()
    if not isinstance(registry, Gate0Registry):
        raise ValidationError("typed_gate_registry_required")
    gate = registry.run(operation, arguments, gate_context)
    if gate.status in {"handled", "denied"}:
        return RoutePreview("gate0" if gate.status == "handled" else "denied", None, (),
                            policy.policy_id, policy.revision, gate)
    candidates = []
    models, backends, summaries, research_digests = {}, {}, {}, {}
    for config in inventory.configurations():
        if config.model_id not in models:
            model = inventory.model(config.model_id)
            models[model.id] = model
            facts = inventory.research(model.id)
            summaries[model.id] = resolve_facts(facts)
            research_digests[model.id] = digest(tuple(sorted(fact.id for fact in facts)))
        model = models[config.model_id]
        if model.backend_id not in backends:
            backends[model.backend_id] = inventory.backend(model.backend_id)
        backend = backends[model.backend_id]
        facts = summaries[model.id]
        reasons = []
        for name,fact in facts.items():
            if fact.status != "resolved":
                reasons.append(f"research_{name}_{fact.status}")
            elif policy.require_verified_research and "verified" not in fact.verifications:
                reasons.append(f"research_{name}_not_verified")
        if not reasons and config.native_json not in {canonical(value) for value in facts["reasoning"].value["native_configurations"]}:
            reasons.append("native_configuration_no_longer_resolved")
        if backend.authorization != "allowed":
            reasons.append("backend_not_authorized")
        if backend.availability != "ready":
            reasons.append("backend_not_ready")
        if backend.locality == "unknown":
            reasons.append("backend_locality_unknown")
        if backend.locality == "remote" and (task.local_only or not policy.allow_remote):
            reasons.append("remote_destination_denied")
        for allowed, actual, label in ((policy.allowed_providers, backend.provider_id, "provider"),
                                       (policy.allowed_backends, backend.id, "backend"),
                                       (policy.allowed_configurations, config.id, "configuration")):
            if allowed is not None and actual not in allowed:
                reasons.append(f"{label}_not_allowed")
        context_limit = facts["context_window"].value
        output_limit = facts["max_output"].value
        if type(context_limit) is not int:
            reasons.append("context_capacity_unknown")
        elif task.context_tokens + task.output_tokens > context_limit:
            reasons.append("context_capacity_exceeded")
        if type(output_limit) is not int:
            reasons.append("output_capacity_unknown")
        elif task.output_tokens > output_limit:
            reasons.append("output_capacity_exceeded")
        modalities = facts["modalities"].value
        if modalities is None or not set(task.modalities) <= set(modalities):
            reasons.append("required_modality_not_researched")
        if task.require_tools and facts["native_tools"].value is not True:
            reasons.append("native_tools_not_researched")
        candidates.append(CandidateDecision(config.id, model.id, backend.id, not reasons, tuple(reasons)))
    # Reuse is scoped to this call; state/research changes are read on the next preview.
    candidates.sort(key=lambda candidate: candidate.configuration_id)
    eligible = [candidate for candidate in candidates if candidate.eligible]
    observations = {}
    receipts = {}
    eligible_ids = {candidate.configuration_id:candidate for candidate in eligible}
    for row in inventory.db.execute("SELECT id,configuration_id,observation_json FROM profile_cells WHERE status='observed'"):
        candidate = eligible_ids.get(row["configuration_id"])
        if candidate is None:
            continue
        observation = json.loads(row["observation_json"])
        if observation.get("category") != task.category or observation.get("research_digest") != research_digests[candidate.model_id]:
            continue
        case=CASE_BY_ID.get(observation.get("case_id"))
        if (case is None or observation.get("case_hash")!=case.hash or observation.get("scorer_version")!=SCORER_VERSION
                or observation.get("suite_hash")!=SUITE_HASH or observation.get("suite_version")!=SUITE_VERSION):
            continue
        key = (observation.get("case_hash"),observation.get("scorer_version"))
        if not all(isinstance(value,str) and value for value in key) or type(observation.get("correct")) is not bool:
            continue
        observations.setdefault(candidate.configuration_id,{})[key] = observation["correct"]
        receipts.setdefault(candidate.configuration_id,{})[key] = (row["id"],observation)
    common = set.intersection(*(set(values) for values in observations.values())) if observations else set()
    ranking = "eligible_configuration_id_ascending_no_quality_inference"
    if common:
        from dataclasses import replace
        candidates = [replace(candidate,comparable_observed=len(common),
                              comparable_correct=sum(observations[candidate.configuration_id][key] for key in common))
                      if candidate.configuration_id in observations else candidate for candidate in candidates]
        eligible = [candidate for candidate in candidates if candidate.eligible]
        def quality(candidate):
            return (0 if candidate.comparable_observed else 1,
                    -Fraction(candidate.comparable_correct,candidate.comparable_observed or 1))
        # Real timing breaks a quality tie only when ALL its members have complete
        # local-model receipts for the same current cases. No synthetic speed ranking.
        groups = {}
        for candidate in eligible:
            groups.setdefault(quality(candidate),[]).append(candidate)
        latency_by_id = {}
        for tied in groups.values():
            if len(tied)<2 or not tied[0].comparable_observed:
                continue
            expected = {}
            valid = True
            for candidate in tied:
                for key in common:
                    cell_id,observation = receipts[candidate.configuration_id][key]
                    ids = observation.get("attempt_ids")
                    if (observation.get("evidence_kind")!="local_model" or not isinstance(ids,list) or not ids
                            or len(ids)>2 or any(type(item) is not str for item in ids) or len(set(ids))!=len(ids)):
                        valid = False
                        break
                    for attempt_id in ids:
                        if attempt_id in expected:
                            valid = False
                            break
                        expected[attempt_id] = (candidate.configuration_id,cell_id)
                if not valid:
                    break
            if not valid:
                continue
            totals = {candidate.configuration_id:0.0 for candidate in tied}
            found = set()
            ids = list(expected)
            for start in range(0,len(ids),200):
                chunk = ids[start:start+200]
                placeholders = ",".join("?" for _ in chunk)
                rows = inventory.db.execute("SELECT profile_attempts.id,cell_id,configuration_id,summary_json FROM profile_attempts "
                    "JOIN profile_cells ON profile_cells.id=profile_attempts.cell_id WHERE profile_attempts.id IN ("+placeholders+") "
                    "AND profile_attempts.status='completed'",chunk)
                for row in rows:
                    summary = json.loads(row["summary_json"])
                    latency = summary.get("latency_ms")
                    config_id,cell_id = expected[row["id"]]
                    candidate = eligible_ids[config_id]
                    config = inventory.configuration(config_id)
                    model = models[candidate.model_id]
                    if ((row["configuration_id"],row["cell_id"])!=(config_id,cell_id)
                            or summary.get("evidence_kind")!="local_model"
                            or summary.get("backend_id")!=candidate.backend_id
                            or summary.get("provider_id")!=backends[candidate.backend_id].provider_id
                            or summary.get("served_model")!=model.native_id
                            or summary.get("effective_native_json")!=config.native_json
                            or type(latency) not in {int,float} or not math.isfinite(latency) or not 0<=latency<=3600000):
                        valid = False
                        break
                    totals[config_id] += latency
                    found.add(row["id"])
                if not valid:
                    break
            if valid and found==set(expected):
                latency_by_id.update(totals)
        candidates = [replace(candidate,comparable_latency_ms=latency_by_id[candidate.configuration_id],latency_case_count=len(common))
                      if candidate.configuration_id in latency_by_id else candidate for candidate in candidates]
        eligible = [candidate for candidate in candidates if candidate.eligible]
        eligible.sort(key=lambda candidate:(*quality(candidate),
                      latency_by_id.get(candidate.configuration_id,0),candidate.configuration_id))
        ranking = ("comparable_category_fixture_fraction_then_complete_local_case_latency_then_stable_id_not_universal_quality"
                   if latency_by_id else "comparable_category_fixture_fraction_then_stable_id_not_universal_quality")
    return RoutePreview("selected" if eligible else "no_eligible", eligible[0].configuration_id if eligible else None,
                        tuple(candidates), policy.policy_id, policy.revision, gate,ranking=ranking)
