"""Repeatable synthetic inventory. No claims about real models or providers."""
from __future__ import annotations

from dataclasses import asdict

from .inventory import Inventory
from .models import Backend, Model, Provider, RoutingPolicy, TaskRequirements
from .profiling import SyntheticBackend, prepare_probe
from .research import ResearchFact, SourceCitation
from .routing import preview_route


def seed_demo(inventory: Inventory):
    inventory.add_provider(Provider("synthetic", "Synthetic fixture provider"))
    inventory.add_backend(Backend("synthetic-local-v1", "synthetic", "synthetic", "1", "local", "allowed", "ready"))
    inventory.add_model(Model("fixture", "synthetic-local-v1", "fixture-model", "1"))
    source = SourceCitation("fixture://gcb/offline-v1", "Included synthetic fixture specification", "2026-11-14T00:00:00Z")
    values = {"context_window":4096, "max_output":512, "modalities":["text"], "native_tools":False,
              "reasoning":{"semantics":"effort", "native_configurations":[
                  {"reasoning_effort":"low"}, {"reasoning_effort":"high"}]}}
    for property, value in values.items():
        inventory.add_research(ResearchFact.create("fixture", property, value, "synthetic", (source,)))
    return tuple(inventory.add_configuration("fixture", value) for value in values["reasoning"]["native_configurations"])


def run_demo(inventory: Inventory) -> dict:
    configurations = seed_demo(inventory)
    task, policy = TaskRequirements(), RoutingPolicy()
    decision = preview_route(inventory, task, policy)
    gate = preview_route(inventory, task, policy, operation="math.calculate",
                         arguments={"operator":"multiply", "operands":[6, 7]})
    request = prepare_probe(inventory, decision.selected_configuration_id, case_id="synthetic.identity")
    result = SyntheticBackend().complete(request)
    return {"mode":"offline_synthetic", "configurations":[asdict(config) for config in configurations],
            "route":asdict(decision), "gate0":asdict(gate), "synthetic_completion":asdict(result),
            "limitations":["No real model quality, cost or latency was measured.",
                           "Routing is a preview; no native harness enforcement is installed.",
                           "The full profiler and capability bios belong to G2."]}
