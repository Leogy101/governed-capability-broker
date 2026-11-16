"""Small explicit CLI. JSON arguments are data; no expression or script evaluation."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

from .demo import run_demo
from .gate0 import GateContext, builtin_registry
from .inventory import Inventory
from .models import Backend, Model, Provider, RoutingPolicy, TaskRequirements
from .research import PROPERTIES, ResearchFact, SourceCitation
from .routing import preview_route
from .validation import ValidationError, canonical


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes invalid raw arguments; keep error output non-sensitive.
        self.exit(2, "gcb: invalid command arguments; use --help\n")


def parse_json(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65536:
        raise ValidationError("json_byte_limit")
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValidationError("duplicate_json_key")
            result[key] = item
        return result
    try:
        parsed = json.loads(value, object_pairs_hook=pairs)
        canonical(parsed)
        return parsed
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValidationError("invalid_json_input") from None


def parser():
    cli = _Parser(prog="gcb", description="Local inventory, deterministic operations, bounded profiles and routing previews.")
    cli.add_argument("--db", help="Explicit SQLite path (default gcb.sqlite3; demo defaults to memory)")
    commands = cli.add_subparsers(dest="command", required=True, parser_class=_Parser)
    measurement = commands.add_parser("measure", help="Offline usage accounting and quality scoring; no model or database access")
    measurement.add_argument("arguments", nargs=argparse.REMAINDER)
    commands.add_parser("init", help="Create an empty inventory; no discovery or network calls")
    commands.add_parser("demo", help="Run included synthetic research, routing and Gate 0 demo")
    commands.add_parser("doctor", help="Report local Python/SQLite capability only")
    commands.add_parser("models", help="List model identities and current research status")
    for name in ("provider", "backend", "model", "configuration"):
        command = commands.add_parser(name)
        command.add_argument("action", choices=["add"])
        command.add_argument("--record", required=True, help="JSON record; configuration needs model_id and native")
    research = commands.add_parser("research")
    research.add_argument("action", choices=["show", "add"])
    research.add_argument("--model", required=True)
    research.add_argument("--property", choices=sorted(PROPERTIES))
    research.add_argument("--value")
    research.add_argument("--verification", choices=["unverified", "documented", "verified", "synthetic", "unsupported"])
    research.add_argument("--source-url")
    research.add_argument("--source-title")
    research.add_argument("--checked-at")
    route = commands.add_parser("route", help="Preview only; never invokes an LLM")
    route.add_argument("--task", default="{}")
    route.add_argument("--policy", default="{}")
    route.add_argument("--operation")
    route.add_argument("--arguments", default="{}")
    route.add_argument("--permit-inventory", action="store_true", help="Trusted caller grant for inventory.describe")
    gate = commands.add_parser("gate", help="Execute one explicitly named deterministic operation")
    gate.add_argument("--operation", required=True)
    gate.add_argument("--input", required=True)
    gate.add_argument("--permit-inventory", action="store_true")
    profile=commands.add_parser("profile",help="Bounded fictional, local Ollama, or operator-approved host profiling")
    profile.add_argument("action",choices=["research","plan","start","status","cancel","resume","demo"])
    profile.add_argument("target",nargs="?",help="Model ID for research/plan, plan ID for other actions")
    profile.add_argument("--no-contrast",action="store_true")
    profile.add_argument("--authorize",action="store_true",help="Trusted operator grants this plan's bounded request scope")
    profile.add_argument("--grant",help="Previously issued profile grant ID")
    profile.add_argument("--max-cells",type=int,help="Pause after this many new cells")
    profile.add_argument("--max-requests",type=int)
    profile.add_argument("--call-timeout",type=float,default=5)
    profile.add_argument("--total-timeout",type=float,default=120)
    profile.add_argument("--backend",choices=["fictional","ollama","openclaw"],default="fictional")
    profile.add_argument("--host-snapshot",help="Reviewed nonsecret OpenClaw model/reasoning snapshot JSON")
    profile.add_argument("--approve-host-runtime",action="store_true",help="Explicit operator acceptance of subscription usage and incomplete native request accounting")
    profile.add_argument("--endpoint",help="Explicit http://127.0.0.1:PORT for Ollama")
    profile.add_argument("--native-model",help="Exact already-installed Ollama name:tag")
    profile.add_argument("--context-tokens",type=int,default=4096)
    profile.add_argument("--output-cap",type=int,default=512)
    profile.add_argument("--baseline-configuration",help="Exact researched configuration ID to run first")
    bio=commands.add_parser("bio",help="Show descriptive partial/full model bios")
    bio.add_argument("model")
    evidence=commands.add_parser("evidence",help="Inspect plan and scored evidence without raw answers/prompts")
    evidence.add_argument("plan")
    return cli


def _run(args):
    if args.command == "doctor":
        with closing(sqlite3.connect(":memory:")) as db:
            try:
                db.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(text)")
                fts5 = True
            except sqlite3.OperationalError:
                fts5 = False
        return {"python":sys.version.split()[0], "sqlite":sqlite3.sqlite_version, "fts5_available":fts5,
                "fts5_required_by_gcb":False, "runtime_dependencies":[], "network_probes":False}
    profile_demo=args.command=="profile" and args.action=="demo"
    database = args.db or (":memory:" if args.command in {"demo", "gate"} or profile_demo else "gcb.sqlite3")
    if database != ":memory:" and not Path(database).exists() and args.command not in {"init", "demo"} and not profile_demo:
        raise ValidationError("inventory_not_initialized")
    with Inventory(database) as inventory:
        if args.command == "init":
            return {"status":"initialized", "schema_version":2}
        if args.command == "demo":
            return run_demo(inventory)
        if args.command in {"profile","bio","evidence"}:
            from .profiler import ProfileManager,run_profile_demo
            backend=None
            if args.command=="profile" and args.backend=="ollama":
                from .ollama import OllamaBackend
                backend=OllamaBackend(args.endpoint,args.native_model,context_tokens=args.context_tokens,output_cap=args.output_cap)
            if args.command=="profile" and args.backend=="openclaw":
                from .openclaw import OpenClawBackend
                if not args.host_snapshot:
                    raise ValidationError("reviewed_host_snapshot_required")
                snapshot_path=Path(args.host_snapshot)
                if snapshot_path.stat().st_size>65536:
                    raise ValidationError("host_snapshot_byte_limit")
                backend=OpenClawBackend(parse_json(snapshot_path.read_text(encoding="utf-8")),output_hint=args.output_cap)
            manager=ProfileManager(inventory,backend,allow_host_runtime=(args.command=="profile" and args.backend=="openclaw" and args.approve_host_runtime))
            if args.command=="bio":
                return manager.bio(args.model)
            if args.command=="evidence":
                return manager.evidence(args.plan)
            if args.action=="demo":
                return run_profile_demo(inventory)
            if args.target is None:
                raise ValidationError("profile_target_required")
            if args.action=="research":
                return manager.research(args.target)
            if args.action=="plan":
                return manager.plan(args.target,contrast=not args.no_contrast,max_requests=args.max_requests,
                                    call_timeout_seconds=args.call_timeout,total_timeout_seconds=args.total_timeout,
                                    baseline_configuration_id=args.baseline_configuration)
            if args.action in {"status","cancel"}:
                return getattr(manager,args.action)(args.target)
            if args.authorize == bool(args.grant):
                raise ValidationError("one_trusted_grant_source_required")
            grant=manager.authorize(args.target) if args.authorize else manager.grant(args.grant)
            return {**getattr(manager,args.action)(args.target,grant,max_cells=args.max_cells),"grant_id":grant.id}
        if args.command == "models":
            return [{**asdict(model), "research":{key:value.status for key,value in inventory.research_summary(model.id).items()},
                     "configurations":[config.id for config in inventory.configurations() if config.model_id == model.id]}
                    for model in inventory.models()]
        if args.command in {"provider", "backend", "model", "configuration"}:
            record = parse_json(args.record)
            if not isinstance(record, dict):
                raise ValidationError("object_record_required")
            if args.command == "configuration":
                if set(record) != {"model_id", "native"}:
                    raise ValidationError("invalid_configuration_fields")
                return asdict(inventory.add_configuration(**record))
            kind = {"provider":Provider,"backend":Backend,"model":Model}[args.command]
            item = kind(**record)
            getattr(inventory, "add_" + args.command)(item)
            return asdict(item)
        if args.command == "research":
            if args.action == "add":
                if any(value is None for value in (args.property, args.value, args.verification, args.source_url,
                                                    args.source_title, args.checked_at)):
                    raise ValidationError("research_add_fields_required")
                fact = ResearchFact.create(args.model, args.property, parse_json(args.value), args.verification,
                                           (SourceCitation(args.source_url,args.source_title,args.checked_at),))
                return {"fact_id":inventory.add_research(fact)}
            return {"facts":[{"id":fact.id, **asdict(fact)} for fact in inventory.research(args.model)],
                    "resolved":{key:asdict(value) for key,value in inventory.research_summary(args.model).items()}}
        context = GateContext(frozenset({"inventory.read"}) if args.permit_inventory else frozenset(), inventory)
        if args.command == "gate":
            return asdict(builtin_registry().run(args.operation, parse_json(args.input), context))
        return asdict(preview_route(inventory, TaskRequirements(**parse_json(args.task)), RoutingPolicy(**parse_json(args.policy)),
                                    operation=args.operation, arguments=parse_json(args.arguments), gate_context=context))


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "measure":
        from .efficiency import main as measure_main
        return measure_main(arguments[1:])
    from .profile_backend import RateLimited, UnknownCompletion
    try:
        parsed = parser().parse_args(arguments)
        if parsed.command == "measure":
            # Measurement never opens the inventory, including with a global --db.
            from .efficiency import main as measure_main
            return measure_main(parsed.arguments)
        result = _run(parsed)
        print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))
        if isinstance(result, dict) and isinstance(result.get("status"),str) and result["status"] in {"denied", "no_eligible"}:
            return 3
        return 0
    except (ValidationError, TypeError, ValueError):
        print(json.dumps({"error":"invalid_input_or_ineligible_record"}), file=sys.stderr)
        return 2
    except RateLimited as exc:
        print(json.dumps({"error":"backend_rate_limited","retry_after":exc.retry_after}), file=sys.stderr)
        return 2
    except UnknownCompletion:
        print(json.dumps({"error":"backend_unavailable_or_uncertain"}), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error):
        print(json.dumps({"error":"inventory_io_error"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
