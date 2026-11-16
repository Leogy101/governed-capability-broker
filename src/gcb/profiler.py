"""Small durable profiler: one worker, bounded calls, no hidden retries."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import hashlib
import math
import os
from pathlib import Path
import queue
import statistics
import sqlite3
import threading
import time
import uuid

from .cases import CASES, CASE_BY_ID, SCORER_VERSION, SUITE_HASH, SUITE_VERSION, fixture_tool, score_text
from .inventory import Inventory
from .models import Backend, Model, Provider
from .profile_backend import FictionalBackend, NativeToolCall, ProfileResponse, RateLimited, UnknownCompletion
from .profiling import ProbeRequest
from .research import ResearchFact, SourceCitation
from .validation import (ValidationError, canonical, digest, identifier, integer, native_settings, object_fields)

_SLOT_LOCK = threading.Lock()
_INFLIGHT: set[threading.Thread] = set()
TERMINAL = frozenset({"observed","technical_error","not_applicable","resource_limited","unknown_completion"})
_HOST_TOKEN_FIELDS = ("uncached_input_tokens", "cache_read_tokens", "cache_write_tokens", "reported_total_tokens")
_HOST_NUMERIC_FIELDS = (*_HOST_TOKEN_FIELDS, "reported_cost_usd_unclassified")


def _host_usage(response):
    """Validate the installed host's lossy normalized usage without guessing billing.

    OpenClaw subtracts cached input from its inputTokens. A complete prompt total
    therefore requires all three input categories; cached tokens are never added
    to the host's separately reported total again. Native retries remain opaque.
    """
    if not isinstance(response.host_usage_json, str) or len(response.host_usage_json) > 4096:
        raise ValidationError("invalid_host_usage")
    try:
        host = json.loads(response.host_usage_json)
    except (ValueError, TypeError, RecursionError):
        raise ValidationError("invalid_host_usage") from None
    if not host:
        if host != {} or response.evidence_kind == "host_runtime":
            raise ValidationError("host_usage_required")
        return None
    object_fields(host, set(_HOST_NUMERIC_FIELDS) | {
        "provenance", "provider_request_count", "coverage", "finish_reason_known"})
    if (response.evidence_kind not in {"host_runtime", "transport_fixture"}
            or host["provenance"] != "openclaw_normalized"
            or host["provider_request_count"] is not None
            or host["coverage"] not in {"partial", "unknown"}
            or host["finish_reason_known"] is not False):
        raise ValidationError("unsupported_host_usage_semantics")
    for field in _HOST_TOKEN_FIELDS:
        if host[field] is not None:
            integer(host[field], field, 0, 10**9)
    if host["reported_cost_usd_unclassified"] is not None:
        host["reported_cost_usd_unclassified"] = _number(
            host["reported_cost_usd_unclassified"], "host_cost", 0, 10**6)
    input_parts = [host[field] for field in _HOST_TOKEN_FIELDS[:3]]
    complete_input = sum(input_parts) if all(value is not None for value in input_parts) else None
    if response.input_tokens != complete_input:
        raise ValidationError("host_prompt_input_attribution_mismatch")
    # The current helper exposes neither reasoning usage nor billing provenance.
    if (response.reasoning_tokens is not None or response.estimated_cost_usd is not None
            or response.billed_cost_usd is not None):
        raise ValidationError("unsupported_host_billing_or_reasoning_attribution")
    known = any(host[field] is not None for field in _HOST_NUMERIC_FIELDS) or response.output_tokens is not None
    if host["coverage"] != ("partial" if known else "unknown"):
        raise ValidationError("host_usage_coverage_mismatch")
    return host


def _aggregate_host_usage(summaries):
    hosts = [summary.get("host_usage") for summary in summaries]
    present = [host for host in hosts if host is not None]
    if not present:
        return None
    result = {"provenance": "openclaw_normalized", "provider_request_count": None,
              "coverage": "partial" if any(host["coverage"] == "partial" for host in present) else "unknown",
              "finish_reason_known": False, "profile_attempt_count": len(summaries),
              "attempts_with_host_usage": len(present)}
    for field in _HOST_NUMERIC_FIELDS:
        values = [host.get(field) if host is not None else None for host in hosts]
        result[field] = sum(values) if all(value is not None for value in values) else None
    return result


@dataclass(frozen=True)
class ProfileGrant:
    id: str
    plan_id: str
    plan_digest: str
    expires_at: float
    max_requests: int


def _number(value, name, low, high):
    if type(value) not in {int,float} or not math.isfinite(value) or not low <= value <= high:
        raise ValidationError("invalid_"+name)
    return float(value)


def _owner_alive(owner):
    if os.name != "posix":
        return True  # os.kill(pid, 0) is not a portable process-existence probe.
    try:
        pid=int(owner.split(":",1)[0])
        if pid<=0:
            return True
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except (ValueError,PermissionError,OSError):
        return True  # Unknown ownership is not permission to start another attempt.


class ProfileManager:
    def __init__(self, inventory: Inventory, backend=None, *, clock=time.time, allow_host_runtime=False):
        if not isinstance(inventory,Inventory):
            raise ValidationError("typed_inventory_required")
        self.inventory = inventory
        self.db = inventory.db
        self.backend = backend if backend is not None else FictionalBackend()
        self.clock = clock
        if type(allow_host_runtime) is not bool:
            raise ValidationError("boolean_host_runtime_authorization_required")
        self.allow_host_runtime = allow_host_runtime

    def _execution_locality_allowed(self, backend, body):
        # This opt-in belongs to the operator entry point, never a model argument.
        # Host policy still owns credentials/allowlists; the regular plan grant is
        # additionally required. It is not a general remote-provider bypass.
        return backend.locality == "local" or (
            self.allow_host_runtime and backend.locality == "remote"
            and backend.provider_id == "openai-host" and backend.api_family == "openclaw-isolated-codex"
            and body.get("evidence_kind") in {"host_runtime", "transport_fixture"})

    def _cancel_host_call(self, request):
        backend = self.inventory.backend(request.backend_id)
        if (self.allow_host_runtime and backend.locality == "remote"
                and backend.provider_id == "openai-host" and backend.api_family == "openclaw-isolated-codex"):
            cancel = getattr(self.backend, "cancel", None)
            if callable(cancel):
                try:
                    cancel()  # Host adapter only signals its worker; never waits here.
                except Exception:
                    pass  # Preserve uncertain completion and retained ownership.

    def research(self, model_id: str, *, refresh=False) -> dict:
        identifier(model_id,"model_id")
        row = self.db.execute("SELECT * FROM profile_metadata WHERE model_id=? ORDER BY rowid DESC LIMIT 1",(model_id,)).fetchone()
        if row and not refresh:
            return {"metadata_id":row["id"],"cached":True,**json.loads(row["body_json"])}
        body = self.backend.metadata(model_id)
        object_fields(body,{"metadata_version","provider","backend","model"},{"evidence_kind"})
        evidence_kind=body.get("evidence_kind","synthetic_fixture")
        if evidence_kind not in {"synthetic_fixture","transport_fixture","local_model","host_runtime"}:
            raise ValidationError("unsupported_metadata_evidence_kind")
        identifier(body["metadata_version"])
        provider,backend = Provider(**body["provider"]),Backend(**body["backend"])
        row = object_fields(body["model"],{"id","native_id","revision","context_window","max_output","modalities",
                                            "native_tools","features","reasoning","default_native","default_behavior","source"},
                            {"execution_limits","backend_snapshot","documentation_urls"})
        if row["id"] != model_id or backend.provider_id != provider.id:
            raise ValidationError("research_identity_mismatch")
        model = Model(row["id"],backend.id,row["native_id"],row["revision"])
        if not isinstance(row["features"],list) or any(x not in {"native_tools","error_recovery"} for x in row["features"]):
            raise ValidationError("invalid_backend_features")
        if ("native_tools" in row["features"]) != (row["native_tools"] is True) or ("error_recovery" in row["features"] and not row["native_tools"]):
            raise ValidationError("inconsistent_native_tool_metadata")
        if not isinstance(row["default_behavior"],str) or not 1 <= len(row["default_behavior"]) <= 512:
            raise ValidationError("documented_default_behavior_required")
        citation = SourceCitation(**row["source"])
        verification = "synthetic" if citation.url.startswith("fixture:") else "documented"
        facts = [ResearchFact.create(model_id,key,row[key],verification,(citation,))
                 for key in ("context_window","max_output","modalities","native_tools","reasoning")]
        default = native_settings(row["default_native"])
        if default not in {native_settings(value) for value in row["reasoning"]["native_configurations"]}:
            raise ValidationError("default_native_not_documented")
        self.inventory.add_provider(provider)
        self.inventory.add_backend(backend)
        self.inventory.add_model(model)
        for fact in facts:
            self.inventory.add_research(fact)
        configurations = [row["default_native"]]+[value for value in row["reasoning"]["native_configurations"] if native_settings(value)!=default]
        config_ids = [self.inventory.add_configuration(model_id,value).id for value in configurations]
        body = {**body,"configuration_ids":config_ids,"verification":verification}
        metadata_id = digest(body)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO profile_metadata VALUES (?,?,?,?)",
                            (metadata_id,model_id,canonical(body),str(self.clock())))
        return {"metadata_id":metadata_id,"cached":False,**body}

    def plan(self, model_id: str, *, contrast=True, max_requests=None,
             call_timeout_seconds=5, total_timeout_seconds=120, baseline_configuration_id=None) -> dict:
        if type(contrast) is not bool:
            raise ValidationError("boolean_contrast_required")
        timeout = _number(call_timeout_seconds,"call_timeout",0.01,60)
        total = _number(total_timeout_seconds,"total_timeout",0.01,3600)
        metadata = self.research(model_id)
        research_ids = tuple(sorted(fact.id for fact in self.inventory.research(model_id)))
        research_digest = digest(research_ids)
        available = metadata["configuration_ids"]
        if baseline_configuration_id is not None:
            identifier(baseline_configuration_id)
            if baseline_configuration_id not in available:
                raise ValidationError("baseline_configuration_not_researched")
            available = [baseline_configuration_id]+[value for value in available if value!=baseline_configuration_id]
        config_ids = available if contrast else available[:1]
        output_cap = metadata["model"]["max_output"] if "execution_limits" in metadata["model"] else 256
        selected = []
        for index,config_id in enumerate(config_ids):
            config = self.inventory.configuration(config_id)
            if self.inventory.configuration_reasons(config):
                raise ValidationError("resolved_research_required")
            for case in CASES:
                if index and not case.contrast:
                    continue
                cell_id = digest({"configuration":config_id,"case":case.hash,"scorer":SCORER_VERSION,
                                  "suite":SUITE_HASH,"research":research_digest})
                selected.append({"cell_id":cell_id,"configuration_id":config_id,"case_id":case.id})
        request_bound = sum(2 if cell["case_id"]=="recovery.native" else 1 for cell in selected)+2
        maximum = request_bound if max_requests is None else integer(max_requests,"max_requests",1,64)
        body = {"model_id":model_id,"metadata_id":metadata["metadata_id"],"metadata_version":metadata["metadata_version"],
                "research_digest":research_digest,"research_ids":research_ids,"configuration_ids":config_ids,"cells":selected,
                "suite_version":SUITE_VERSION,"suite_hash":SUITE_HASH,"scorer_version":SCORER_VERSION,
                "max_requests":maximum,"max_output_tokens":output_cap,"call_timeout_seconds":timeout,"total_timeout_seconds":total,
                "concurrency":1,"estimated_cost_usd":None,"billed_cost_usd":None,"evidence_kind":metadata.get("evidence_kind","synthetic_fixture")}
        plan_id = digest(body)
        features = set(metadata["model"]["features"])
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO profile_plans VALUES (?,?,?,?,?,?,?,?)",
                            (plan_id,model_id,canonical(body),"planned",str(self.clock()),"0","","0"))
            for position,cell in enumerate(selected):
                case = CASE_BY_ID[cell["case_id"]]
                applicable = set(case.requires) <= features
                observation = {} if applicable else {"status":"not_applicable","reason":"backend_capability_not_documented",
                                                      "category":case.category,"case_id":case.id,"correct":None}
                self.db.execute("INSERT OR IGNORE INTO profile_cells VALUES (?,?,?,?,?)",
                                (cell["cell_id"],cell["configuration_id"],case.id,"pending" if applicable else "not_applicable",canonical(observation)))
                self.db.execute("INSERT OR IGNORE INTO profile_plan_cells VALUES (?,?,?,?)",
                                (digest([plan_id,cell["cell_id"]]),plan_id,cell["cell_id"],str(position)))
        return {"plan_id":plan_id,**body,"status":self.status(plan_id)["status"]}

    def _plan(self, plan_id):
        identifier(plan_id)
        row=self.db.execute("SELECT * FROM profile_plans WHERE id=?",(plan_id,)).fetchone()
        if row is None:
            raise ValidationError("profile_plan_not_found")
        return row,json.loads(row["body_json"])

    def _continuation_parent_snapshot(self, parent_id):
        row,body=self._plan(parent_id)
        attempts=[dict(item) for item in self.db.execute(
            "SELECT * FROM profile_attempts WHERE plan_id=? ORDER BY rowid",(parent_id,))]
        unknown=[dict(cell) for cell in self._cells(parent_id) if cell["status"]=="unknown_completion"]
        return {"plan":dict(row),"attempts":attempts,"excluded_unknown_cells":unknown}

    def plan_untouched_continuation(self, parent_id: str, *, excluded_unknown_cell_ids,
                                    max_requests: int, total_timeout_seconds: float,
                                    prior_active_seconds: float, termination_evidence_ref: str) -> dict:
        """Explicit operator action after verified termination; never an automatic retry.

        A single linked child can execute only never-attempted cells. Parent rows,
        uncertain observations and expired grants remain immutable. No authority
        or wall-clock extension is created here; the child needs its own grant.
        """
        identifier(termination_evidence_ref,"termination_evidence_ref")
        if not isinstance(excluded_unknown_cell_ids,(tuple,list)) or not excluded_unknown_cell_ids:
            raise ValidationError("exact_unknown_exclusions_required")
        excluded=tuple(sorted(identifier(value) for value in excluded_unknown_cell_ids))
        if len(excluded)!=len(set(excluded)):
            raise ValidationError("exact_unknown_exclusions_required")
        maximum=integer(max_requests,"max_requests",1,64)
        total=_number(total_timeout_seconds,"total_timeout",0.01,3600)
        prior=_number(prior_active_seconds,"prior_active_seconds",0,3600)
        with _SLOT_LOCK:
            if any(thread.is_alive() for thread in _INFLIGHT):
                raise ValidationError("previous_attempt_still_running")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row,parent=self._plan(parent_id)
            worker=self.db.execute("SELECT * FROM profile_worker WHERE id='worker'").fetchone()
            if worker and worker["owner"] and _owner_alive(worker["owner"]):
                raise ValidationError("profile_worker_busy")
            snapshot=self._continuation_parent_snapshot(parent_id)
            actual=tuple(sorted(cell["id"] for cell in snapshot["excluded_unknown_cells"]))
            if ("continuation" in parent or row["status"]!="partial_unknown" or excluded!=actual
                    or any(item["status"]=="reserved" for item in snapshot["attempts"])):
                raise ValidationError("exact_stopped_parent_required")
            used=len(snapshot["attempts"])
            if (maximum>parent["max_requests"]-used or prior<float(row["elapsed_seconds"])
                    or total>parent["total_timeout_seconds"]-prior):
                raise ValidationError("continuation_exceeds_remaining_budget")
            selected=[]
            for cell in self._cells(parent_id):
                attempted=self.db.execute("SELECT 1 FROM profile_attempts WHERE cell_id=? LIMIT 1",(cell["id"],)).fetchone()
                if cell["status"]=="pending" and not attempted:
                    selected.append({"cell_id":cell["id"],"configuration_id":cell["configuration_id"],"case_id":cell["case_id"]})
            if not selected or maximum>sum(2 if c["case_id"]=="recovery.native" else 1 for c in selected):
                raise ValidationError("untouched_work_required")
            continuation={"parent_plan_id":parent_id,"parent_snapshot_sha256":digest(snapshot),
                          "excluded_unknown_cell_ids":list(excluded),"prior_attempts":used,
                          "prior_active_seconds":prior,"termination_evidence_ref":termination_evidence_ref}
            body={**parent,"cells":selected,"max_requests":maximum,"total_timeout_seconds":total,
                  "continuation":continuation}
            plan_id=digest(body)
            for other in self.db.execute("SELECT id,body_json FROM profile_plans"):
                link=json.loads(other["body_json"]).get("continuation",{})
                if link.get("parent_plan_id")==parent_id and other["id"]!=plan_id:
                    raise ValidationError("continuation_already_exists")
            self.db.execute("INSERT OR IGNORE INTO profile_plans VALUES (?,?,?,?,?,?,?,?)",
                            (plan_id,body["model_id"],canonical(body),"planned",str(self.clock()),"0","","0"))
            for position,cell in enumerate(selected):
                self.db.execute("INSERT OR IGNORE INTO profile_plan_cells VALUES (?,?,?,?)",
                                (digest([plan_id,cell["cell_id"]]),plan_id,cell["cell_id"],str(position)))
        return {"plan_id":plan_id,**body,"status":self.status(plan_id)["status"]}

    def _check_continuation(self, body):
        link=body.get("continuation")
        if link is None:
            return
        snapshot=self._continuation_parent_snapshot(link["parent_plan_id"])
        _,parent=self._plan(link["parent_plan_id"])
        if (digest(snapshot)!=link["parent_snapshot_sha256"]
                or body["max_requests"]+len(snapshot["attempts"])>parent["max_requests"]
                or body["total_timeout_seconds"]+link["prior_active_seconds"]>parent["total_timeout_seconds"]):
            raise ValidationError("continuation_parent_changed")

    def authorize(self, plan_id: str, *, ttl_seconds=3600) -> ProfileGrant:
        """Trusted host/operator call only, never a flag decoded from benchmark output."""
        _,body=self._plan(plan_id)
        ttl=_number(ttl_seconds,"grant_ttl",0.01,86400)
        grant=ProfileGrant(uuid.uuid4().hex,plan_id,digest(body),self.clock()+ttl,body["max_requests"])
        with self.db:
            self.db.execute("INSERT INTO profile_grants VALUES (?,?,?,?,?)",
                            (grant.id,plan_id,grant.plan_digest,str(grant.expires_at),str(grant.max_requests)))
        return grant

    def grant(self, grant_id: str) -> ProfileGrant:
        identifier(grant_id)
        row=self.db.execute("SELECT * FROM profile_grants WHERE id=?",(grant_id,)).fetchone()
        if row is None:
            raise ValidationError("profile_grant_not_found")
        return ProfileGrant(row["id"],row["plan_id"],row["plan_digest"],float(row["expires_at"]),int(row["max_requests"]))

    def _grant_valid(self, plan_id, body, grant):
        if not isinstance(grant,ProfileGrant) or self.grant(grant.id)!=grant or grant.plan_id!=plan_id or grant.plan_digest!=digest(body):
            raise ValidationError("trusted_matching_profile_grant_required")
        if grant.expires_at <= self.clock():
            raise ValidationError("profile_grant_expired")

    def _cells(self,plan_id):
        return self.db.execute("SELECT c.* FROM profile_plan_cells pc JOIN profile_cells c ON c.id=pc.cell_id WHERE pc.plan_id=? ORDER BY CAST(pc.position AS INTEGER)",(plan_id,)).fetchall()

    def status(self, plan_id: str) -> dict:
        row,body=self._plan(plan_id)
        counts=Counter(cell["status"] for cell in self._cells(plan_id))
        return {"plan_id":plan_id,"model_id":body["model_id"],"status":row["status"],"cells":dict(sorted(counts.items())),
                "total_cells":sum(counts.values()),"attempts":self.db.execute("SELECT count(*) FROM profile_attempts WHERE plan_id=?",(plan_id,)).fetchone()[0],
                "not_before":float(row["not_before"]),"error_code":row["error_code"] or None,"elapsed_seconds":float(row["elapsed_seconds"])}

    def cancel(self,plan_id: str) -> dict:
        row,_=self._plan(plan_id)
        if row["status"] not in {"complete","partial_unknown"}:
            with self.db:
                self.db.execute("UPDATE profile_plans SET status='cancelled' WHERE id=?",(plan_id,))
        return self.status(plan_id)

    def _state(self,plan_id,status,error="",not_before=0):
        with self.db:
            self.db.execute("UPDATE profile_plans SET status=?,error_code=?,not_before=? WHERE id=?",
                            (status,error,str(not_before),plan_id))

    def _claim(self,owner,lease_seconds):
        if os.name != "posix":
            raise ValidationError("profiling_requires_posix_worker_ownership")
        with _SLOT_LOCK:
            if any(thread.is_alive() for thread in _INFLIGHT):
                raise ValidationError("previous_attempt_still_running")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT * FROM profile_worker WHERE id='worker'").fetchone()
            if row and row["owner"]:
                own_finished_retained=(row["expires_at"]=="retained" and row["owner"].startswith(str(os.getpid())+":"))
                if not own_finished_retained and _owner_alive(row["owner"]):
                    raise ValidationError("profile_worker_busy")
            # A stale durable reservation might have completed externally. It is never replayed.
            self.db.execute("UPDATE profile_attempts SET status='unknown_completion',ended_at=?,summary_json=? WHERE status='reserved'",
                            (str(self.clock()),canonical({"error_code":"interrupted_reservation"})))
            self.db.execute("UPDATE profile_cells SET status='unknown_completion',observation_json=? WHERE status='running'",
                            (canonical({"status":"unknown_completion","correct":None,"reason":"interrupted_reservation"}),))
            self.db.execute("INSERT OR REPLACE INTO profile_worker VALUES ('worker',?,?)",(owner,str(self.clock()+lease_seconds)))

    def _reserve(self,plan_id,cell_id,body,grant,*,previous_attempt_id=None):
        self._grant_valid(plan_id,body,grant)
        current=tuple(sorted(fact.id for fact in self.inventory.research(body["model_id"])))
        if digest(current)!=body["research_digest"]:
            raise ValidationError("profile_research_changed")
        cell=self.db.execute("SELECT * FROM profile_cells WHERE id=?",(cell_id,)).fetchone()
        config=self.inventory.configuration(cell["configuration_id"])
        model=self.inventory.model(config.model_id)
        backend=self.inventory.backend(model.backend_id)
        limits=self.inventory.research_summary(model.id)
        if (backend.authorization!="allowed" or backend.availability!="ready" or not self._execution_locality_allowed(backend, body)
                or self.inventory.configuration_reasons(config)
                or body["max_output_tokens"]>limits["max_output"].value
                or len(CASE_BY_ID[cell["case_id"]].input_json.encode("utf-8"))+body["max_output_tokens"]>limits["context_window"].value):
            raise ValidationError("profile_target_no_longer_eligible")
        attempt_id=uuid.uuid4().hex
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._check_continuation(body)
            if "continuation" in body:
                prior=self.db.execute("SELECT * FROM profile_attempts WHERE cell_id=? ORDER BY rowid",(cell_id,)).fetchall()
                current_cell=self.db.execute("SELECT * FROM profile_cells WHERE id=?",(cell_id,)).fetchone()
                recovery=(previous_attempt_id is not None and current_cell["case_id"]=="recovery.native"
                          and current_cell["status"]=="running" and len(prior)==1
                          and prior[0]["id"]==previous_attempt_id and prior[0]["plan_id"]==plan_id
                          and prior[0]["status"]=="completed")
                if not recovery and (current_cell["status"]!="pending" or prior):
                    raise ValidationError("continuation_cell_already_attempted")
            if self._plan(plan_id)[0]["status"]=="cancelled":
                raise ValidationError("profile_cancelled")
            used=self.db.execute("SELECT count(*) FROM profile_attempts WHERE plan_id=?",(plan_id,)).fetchone()[0]
            if used>=min(body["max_requests"],grant.max_requests):
                raise ValidationError("profile_request_bound_exhausted")
            self.db.execute("INSERT INTO profile_attempts VALUES (?,?,?,?,?,?,?)",
                            (attempt_id,plan_id,cell_id,"reserved",str(self.clock()),"","{}"))
            self.db.execute("UPDATE profile_cells SET status='running' WHERE id=?",(cell_id,))
        return attempt_id

    def _release_retained(self,owner):
        if self.inventory.database==":memory:":
            return  # The next caller in this process can reclaim its own completed retained slot.
        try:
            # Cleanup must never recreate a database the owner has deliberately removed.
            uri=Path(self.inventory.database).resolve().as_uri()+"?mode=rw"
            with closing(sqlite3.connect(uri,uri=True,timeout=1)) as database:
                with database:
                    database.execute("UPDATE profile_worker SET owner='',expires_at='0' WHERE id='worker' AND owner=? AND expires_at='retained'",(owner,))
        except sqlite3.Error:
            pass  # Keep ownership conservatively; no request is made to recover a lock.

    def _call(self,request,case,timeout,plan_id,feedback,owner):
        results=queue.Queue(maxsize=1)
        deadline=time.monotonic()+timeout
        def work():
            try:
                result=(True,self.backend.complete(request,case,deadline=deadline,tool_feedback=feedback))
            except BaseException as exc:
                result=(False,exc)
            finally:
                # Finish all database cleanup before making completion visible to callers.
                self._release_retained(owner)
                with _SLOT_LOCK:
                    _INFLIGHT.discard(threading.current_thread())
            results.put(result)
        thread=threading.Thread(target=work,name="gcb-profile-attempt",daemon=True)
        with _SLOT_LOCK:
            if any(item.is_alive() for item in _INFLIGHT):
                raise UnknownCompletion("previous_attempt_still_running")
            _INFLIGHT.add(thread)
            thread.start()
        try:
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    self._cancel_host_call(request)
                    raise UnknownCompletion("call_timeout")
                if self._plan(plan_id)[0]["status"]=="cancelled":
                    self._cancel_host_call(request)
                    raise UnknownCompletion("cancelled_inflight")
                try:
                    ok,value=results.get(timeout=min(remaining,0.02))
                    if not ok:
                        if isinstance(value,(RateLimited,UnknownCompletion)):
                            raise value
                        if isinstance(value,TimeoutError):
                            raise UnknownCompletion("backend_timeout")
                        raise RuntimeError("backend_error")
                    return value
                except queue.Empty:
                    pass
        except (KeyboardInterrupt, SystemExit):
            self._cancel_host_call(request)
            backend = self.inventory.backend(request.backend_id)
            if (self.allow_host_runtime and backend.locality == "remote"
                    and backend.api_family == "openclaw-isolated-codex"):
                # Join only our worker's bounded cancel-RPC cleanup. This is not
                # evidence that the provider operation terminated.
                thread.join(timeout=6)
            raise

    def _response(self,response,request,elapsed,expected_kind="synthetic_fixture"):
        if not isinstance(response,ProfileResponse):
            raise ValidationError("invalid_backend_response")
        if (response.provider_id,response.backend_id,response.served_model,response.effective_native_json)!=(
                request.provider_id,request.backend_id,request.native_model_id,request.native_json):
            raise ValidationError("effective_identity_mismatch")
        if response.evidence_kind!=expected_kind or response.evidence_kind not in {"synthetic_fixture","transport_fixture","local_model","host_runtime"} or response.finish_reason not in {"stop","tool_calls","length"}:
            raise ValidationError("unsupported_response_semantics")
        if not isinstance(response.output,str) or len(response.output.encode("utf-8"))>32768:
            raise ValidationError("output_bound_exceeded")
        if not isinstance(response.native_calls,tuple) or len(response.native_calls)>1 or any(not isinstance(call,NativeToolCall) for call in response.native_calls):
            raise ValidationError("invalid_native_calls")
        if response.native_confirmation not in {"fixture_echo","request_only_not_echoed"}:
            raise ValidationError("unsupported_native_confirmation")
        if response.evidence_kind in {"local_model","host_runtime"} and response.native_confirmation!="request_only_not_echoed":
            raise ValidationError("unsupported_native_confirmation")
        metrics=json.loads(response.backend_metrics_json)
        object_fields(metrics,set(),{"total_duration","load_duration","prompt_eval_duration","eval_duration"})
        for key,value in metrics.items():
            integer(value,key,0,10**15)
        usage={}
        for field in ("input_tokens","output_tokens","reasoning_tokens"):
            value=getattr(response,field)
            usage[field]=None if value is None else integer(value,field,0,10**9)
        for field in ("estimated_cost_usd","billed_cost_usd"):
            value=getattr(response,field)
            usage[field]=None if value is None else _number(value,field,0,10**6)
        host_usage = _host_usage(response)
        return {"provider_id":response.provider_id,"backend_id":response.backend_id,"served_model":response.served_model,
                "effective_native_json":response.effective_native_json,"requested_native_json":request.native_json,
                "native_confirmation":response.native_confirmation,
                "effective_native_settings_known":response.native_confirmation=="fixture_echo",
                "backend_metrics_ns":metrics,"latency_ms":elapsed*1000,"usage":usage,
                "finish_reason":response.finish_reason,"output_sha256":hashlib.sha256(response.output.encode("utf-8")).hexdigest(),
                "native_call_count":len(response.native_calls),
                "native_calls":[{"name":"fixture_lookup" if call.name=="fixture_lookup" else "unsupported",
                                 "arguments_sha256":digest(json.loads(call.arguments_json))} for call in response.native_calls],
                "evidence_kind":response.evidence_kind,
                **({"host_usage":host_usage} if host_usage is not None else {})}

    def _finish_attempt(self,attempt_id,status,summary):
        with self.db:
            self.db.execute("UPDATE profile_attempts SET status=?,ended_at=?,summary_json=? WHERE id=?",
                            (status,str(self.clock()),canonical(summary),attempt_id))

    def _finish_cell(self,cell_id,status,observation):
        with self.db:
            self.db.execute("UPDATE profile_cells SET status=?,observation_json=? WHERE id=?",
                            (status,canonical({"status":status,**observation}),cell_id))

    def start(self,plan_id: str,grant: ProfileGrant,*,max_cells=None) -> dict:
        self._run(plan_id,grant,resume=False,max_cells=max_cells)
        return self.status(plan_id)

    def resume(self,plan_id: str,grant: ProfileGrant,*,max_cells=None) -> dict:
        self._run(plan_id,grant,resume=True,max_cells=max_cells)
        return self.status(plan_id)

    def _run(self,plan_id,grant,*,resume,max_cells):
        row,body=self._plan(plan_id)
        self._grant_valid(plan_id,body,grant)
        self._check_continuation(body)
        if max_cells is not None:
            integer(max_cells,"max_cells",1,64)
        if row["status"]=="complete":
            return self.status(plan_id)
        if row["status"]=="cancelled" and not resume:
            return self.status(plan_id)
        if float(row["not_before"])>self.clock():
            return self.status(plan_id)
        current=tuple(sorted(fact.id for fact in self.inventory.research(body["model_id"])))
        if digest(current)!=body["research_digest"] or body["suite_hash"]!=SUITE_HASH:
            raise ValidationError("profile_research_or_suite_changed")
        for config_id in body["configuration_ids"]:
            config=self.inventory.configuration(config_id)
            model=self.inventory.model(config.model_id)
            backend=self.inventory.backend(model.backend_id)
            if self.inventory.configuration_reasons(config) or backend.authorization!="allowed" or backend.availability!="ready" or not self._execution_locality_allowed(backend, body):
                raise ValidationError("profile_target_not_eligible")
        if hasattr(self.backend,"bind_metadata"):
            metadata_row=self.db.execute("SELECT body_json FROM profile_metadata WHERE id=?",(body["metadata_id"],)).fetchone()
            if metadata_row is None:
                raise ValidationError("profile_metadata_missing")
            self.backend.bind_metadata(json.loads(metadata_row[0]))
        owner=str(os.getpid())+":"+uuid.uuid4().hex
        remaining=body["total_timeout_seconds"]-float(row["elapsed_seconds"])
        if remaining<=0:
            self._state(plan_id,"budget_exhausted","total_time_bound")
            return self.status(plan_id)
        self._claim(owner,remaining+2)
        began=time.monotonic()
        job_deadline=began+remaining
        processed=0
        try:
            if any(cell["status"]=="unknown_completion" for cell in self._cells(plan_id)):
                self._state(plan_id,"partial_unknown","unknown_completion_requires_review")
                return self.status(plan_id)
            self._state(plan_id,"running")
            for cell in self._cells(plan_id):
                if cell["status"] in TERMINAL:
                    continue
                if self._plan(plan_id)[0]["status"]=="cancelled":
                    break
                if max_cells is not None and processed>=max_cells:
                    self._state(plan_id,"paused")
                    break
                if time.monotonic()>=job_deadline:
                    self._state(plan_id,"budget_exhausted","total_time_bound")
                    break
                case=CASE_BY_ID[cell["case_id"]]
                config=self.inventory.configuration(cell["configuration_id"])
                model=self.inventory.model(config.model_id)
                backend=self.inventory.backend(model.backend_id)
                request=ProbeRequest(config.id,backend.provider_id,backend.id,model.native_id,config.native_json,case.id,body["max_output_tokens"])
                observation={"case_id":case.id,"category":case.category,"case_hash":case.hash,"scorer_version":SCORER_VERSION,
                             "suite_version":SUITE_VERSION,"suite_hash":SUITE_HASH,"research_digest":body["research_digest"],"correct":None,
                             "configuration_id":config.id,"evidence_kind":body["evidence_kind"],"attempt_ids":[]}
                feedback=None
                for stage in range(2 if case.category=="error_recovery" else 1):
                    try:
                        attempt_id=self._reserve(plan_id,cell["id"],body,grant,
                                                 previous_attempt_id=observation["attempt_ids"][-1] if stage else None)
                    except ValidationError as exc:
                        if str(exc) in {"continuation_cell_already_attempted","continuation_parent_changed"}:
                            self._state(plan_id,"blocked",str(exc))
                            return self.status(plan_id)
                        self._finish_cell(cell["id"],"unknown_completion" if observation["attempt_ids"] else "pending",observation)
                        safe_codes={"profile_cancelled","profile_request_bound_exhausted","profile_grant_expired",
                                    "profile_research_changed","profile_target_no_longer_eligible",
                                    "continuation_cell_already_attempted","continuation_parent_changed"}
                        code=str(exc) if str(exc) in safe_codes else "profile_authorization_required"
                        state="partial_unknown" if observation["attempt_ids"] else (
                            "budget_exhausted" if code=="profile_request_bound_exhausted" else "blocked")
                        self._state(plan_id,state,code)
                        return self.status(plan_id)
                    observation["attempt_ids"].append(attempt_id)
                    call_started=time.monotonic()
                    try:
                        timeout=min(body["call_timeout_seconds"],job_deadline-time.monotonic(),grant.expires_at-self.clock())
                        if timeout<=0:
                            raise UnknownCompletion("deadline_elapsed_after_reservation")
                        response=self._call(request,case,timeout,plan_id,feedback,owner)
                        summary=self._response(response,request,time.monotonic()-call_started,body["evidence_kind"])
                        self._finish_attempt(attempt_id,"completed",summary)
                        if response.finish_reason=="length":
                            observation["evaluation"]={"criterion":"complete_response_required","outcome":"output_truncated"}
                            self._finish_cell(cell["id"],"resource_limited",observation)
                            break
                        if case.requires:
                            call=response.native_calls[0] if response.finish_reason=="tool_calls" and response.native_calls else None
                            if call is None:
                                observation["correct"]=False
                                observation["evaluation"]={"criterion":json.loads(case.expected_json),"outcome":"missing_native_call"}
                            else:
                                tool_result=fixture_tool(call.name,call.arguments_json)
                                observation["correct"]=tool_result=={"value":42}
                                observation["evaluation"]={"criterion":json.loads(case.expected_json),
                                                           "outcome":"native_fixture_match" if observation["correct"] else "native_fixture_mismatch",
                                                           "fixture_tool_result":tool_result}
                                if case.category=="error_recovery" and stage==0:
                                    if "error" not in tool_result:
                                        observation["correct"]=False
                                    else:
                                        feedback=tool_result
                                        continue
                        elif response.finish_reason!="stop" or response.native_calls:
                            observation["correct"]=False
                            observation["evaluation"]={"criterion":json.loads(case.expected_json),
                                                       "outcome":"unexpected_native_call_or_nonfinal_response"}
                        else:
                            observation["correct"]=score_text(case,response.output)
                            try:
                                actual=json.loads(response.output)
                                parsed=True
                            except (ValueError,TypeError,RecursionError):
                                actual=None
                                parsed=False
                            safe_values={case.expected_json,canonical({"fixture_wrong_answer":True}),canonical({"wrong":True})}
                            try:
                                safe_actual=actual if parsed and canonical(actual) in safe_values else None
                            except (ValueError,TypeError,RecursionError):
                                safe_actual=None
                            observation["evaluation"]={"criterion":json.loads(case.expected_json),
                                "outcome":"exact_match" if observation["correct"] else ("structured_mismatch" if parsed else "invalid_json"),
                                "allowlisted_fixture_output":safe_actual,"other_output_retained":False}
                        self._finish_cell(cell["id"],"observed",observation)
                        break
                    except RateLimited as exc:
                        self._finish_attempt(attempt_id,"rate_limited",{"error_code":"rate_limited","retry_after":exc.retry_after})
                        # After a completed recovery stage, replay would repeat a completed native call.
                        state="unknown_completion" if stage else "pending"
                        self._finish_cell(cell["id"],state,observation)
                        self._state(plan_id,"partial_unknown" if stage else "rate_limited","rate_limited",self.clock()+exc.retry_after)
                        return self.status(plan_id)
                    except UnknownCompletion:
                        self._finish_attempt(attempt_id,"unknown_completion",{"error_code":"uncertain_or_timed_out"})
                        self._finish_cell(cell["id"],"unknown_completion",observation)
                        self._state(plan_id,"partial_unknown","unknown_completion_requires_review")
                        return self.status(plan_id)
                    except Exception as exc:
                        code="effective_identity_mismatch" if isinstance(exc,ValidationError) and str(exc)=="effective_identity_mismatch" else "backend_or_response_error"
                        self._finish_attempt(attempt_id,"technical_error",{"error_code":code})
                        self._finish_cell(cell["id"],"technical_error",observation)
                        break
                    except BaseException:
                        self._cancel_host_call(request)
                        self._finish_attempt(attempt_id,"unknown_completion",{"error_code":"interrupted"})
                        self._finish_cell(cell["id"],"unknown_completion",observation)
                        self._state(plan_id,"partial_unknown","interrupted")
                        raise
                processed+=1
            else:
                self._state(plan_id,"complete")
        finally:
            with self.db:
                with _SLOT_LOCK:
                    pending=any(thread.is_alive() for thread in _INFLIGHT)
                if pending:
                    self.db.execute("UPDATE profile_worker SET expires_at='retained' WHERE id='worker' AND owner=?",(owner,))
                else:
                    self.db.execute("UPDATE profile_worker SET owner='',expires_at='0' WHERE id='worker' AND owner=?",(owner,))
                self.db.execute("UPDATE profile_plans SET elapsed_seconds=CAST(elapsed_seconds AS REAL)+? WHERE id=?",
                                (time.monotonic()-began,plan_id))
            # Close the race where the callback finished just before the retained marker committed.
            with _SLOT_LOCK:
                pending=any(thread.is_alive() for thread in _INFLIGHT)
            if not pending:
                with self.db:
                    self.db.execute("UPDATE profile_worker SET owner='',expires_at='0' WHERE id='worker' AND owner=? AND expires_at='retained'",(owner,))
        return self.status(plan_id)

    def evidence(self,plan_id: str) -> dict:
        _,body=self._plan(plan_id)
        cells=[{"cell_id":cell["id"],"configuration_id":cell["configuration_id"],"case_id":cell["case_id"],
                "status":cell["status"],"observation":json.loads(cell["observation_json"])} for cell in self._cells(plan_id)]
        attempts=[{**dict(row),"summary_json":json.loads(row["summary_json"])}
                  for row in self.db.execute("SELECT * FROM profile_attempts WHERE plan_id=? ORDER BY rowid",(plan_id,))]
        return {"plan":body,"status":self.status(plan_id),"cells":cells,"attempts":attempts}

    def bio(self,model_id: str) -> dict:
        model=self.inventory.model(model_id)
        backend=self.inventory.backend(model.backend_id)
        current_research_ids=tuple(sorted(fact.id for fact in self.inventory.research(model_id)))
        current_digest=digest(current_research_ids)
        configurations=[]
        for config in self.inventory.configurations():
            if config.model_id!=model_id:
                continue
            all_rows=self.db.execute("SELECT * FROM profile_cells WHERE configuration_id=? ORDER BY case_id,id",(config.id,)).fetchall()
            current_ids={digest({"configuration":config.id,"case":case.hash,"scorer":SCORER_VERSION,
                                 "suite":SUITE_HASH,"research":current_digest}) for case in CASES}
            rows=[]
            excluded=[]
            for row in all_rows:
                observation=json.loads(row["observation_json"])
                current=row["id"] in current_ids
                if row["status"]=="observed":
                    current=current and observation.get("suite_hash")==SUITE_HASH and observation.get("scorer_version")==SCORER_VERSION
                (rows if current else excluded).append(row)
            retained_ids={row["id"] for row in rows}
            categories={}
            for row in rows:
                case=CASE_BY_ID.get(row["case_id"])
                if case is None:
                    continue
                entry=categories.setdefault(case.category,{"observed":0,"correct":0,"technical_errors":0,"not_applicable":0,
                                                           "unknown_completion":0,"resource_limited":0,"pending":0,"case_ids":[]})
                entry["case_ids"].append(case.id)
                status=row["status"]
                if status=="observed":
                    entry["observed"]+=1
                    entry["correct"]+=int(json.loads(row["observation_json"])["correct"] is True)
                else:
                    key={"technical_error":"technical_errors","running":"unknown_completion"}.get(status,status)
                    entry[key]=entry.get(key,0)+1
            attempts=[row for row in self.db.execute("SELECT a.* FROM profile_attempts a JOIN profile_cells c ON c.id=a.cell_id WHERE c.configuration_id=? ORDER BY a.rowid",(config.id,)) if row["cell_id"] in retained_ids]
            summaries=[json.loads(row["summary_json"]) for row in attempts]
            latencies=[value["latency_ms"] for value in summaries if "latency_ms" in value]
            usage={}
            for field in ("input_tokens","output_tokens","reasoning_tokens","estimated_cost_usd","billed_cost_usd"):
                values=[value.get("usage",{}).get(field) for value in summaries]
                usage[field]=sum(values) if values and all(value is not None for value in values) else None
            strengths=[f"{key}: {value['correct']}/{value['observed']} exact fixture responses" for key,value in sorted(categories.items()) if value["observed"] and value["correct"]]
            weaknesses=[f"{key}: {value['observed']-value['correct']}/{value['observed']} fixture responses missed criteria" for key,value in sorted(categories.items()) if value["observed"]>value["correct"]]
            configurations.append({"configuration_id":config.id,"native":config.native,"research_ids":current_research_ids,
                                   "configuration_creation_research_ids":config.research_ids,
                                   "observation_cell_ids":[row["id"] for row in rows],
                                   "historical_excluded_cells":len(excluded),
                                   "historical_excluded_statuses":dict(Counter(row["status"] for row in excluded)),
                                   "categories":categories,"strengths":strengths,"weaknesses":weaknesses,
                                   "untested_categories":sorted({case.category for case in CASES}-{key for key,value in categories.items() if value["observed"]>0}),
                                   "unsupported_categories":sorted(key for key,value in categories.items() if value["not_applicable"]>0),
                                   "research_snapshots":sorted({json.loads(row["observation_json"]).get("research_digest","") for row in rows}-{ "" }),
                                   "suggested_fixture_workloads":[key for key,value in sorted(categories.items()) if value["correct"]>0],
                                   "observed_evidence_kinds":sorted({value["evidence_kind"] for value in summaries if "evidence_kind" in value}),
                                   "native_confirmations":sorted({value["native_confirmation"] for value in summaries if "native_confirmation" in value}),
                                   "attempt_count":len(attempts),"latency_ms":{"count":len(latencies),"min":min(latencies) if latencies else None,
                                   "max":max(latencies) if latencies else None,"median":statistics.median(latencies) if latencies else None},
                                   "usage":usage,"discoverable":True,
                                   **({"host_usage":_aggregate_host_usage(summaries)}
                                      if any("host_usage" in value for value in summaries) else {})})
        metadata=self.db.execute("SELECT body_json FROM profile_metadata WHERE model_id=? ORDER BY rowid DESC LIMIT 1",(model_id,)).fetchone()
        metadata_body=json.loads(metadata[0]) if metadata else None
        sources={canonical(asdict(source)):asdict(source) for fact in self.inventory.research(model_id) for source in fact.sources}
        return {"bio_version":"0.3","model":asdict(model),"backend":asdict(backend),"configurations":configurations,
                "generated_at":datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),
                "metadata_version":metadata_body["metadata_version"] if metadata_body else None,
                "documented_capabilities":{key:metadata_body["model"][key] for key in (
                    "context_window","max_output","modalities","native_tools","features","reasoning","default_native","default_behavior")}
                    if metadata_body else None,
                "research_sources":[sources[key] for key in sorted(sources)],
                "execution_limits":metadata_body["model"].get("execution_limits") if metadata_body else None,
                "backend_snapshot":metadata_body["model"].get("backend_snapshot") if metadata_body else None,
                "suite_version":SUITE_VERSION,"scorer_version":SCORER_VERSION,"evidence_kind":metadata_body.get("evidence_kind","synthetic_fixture") if metadata_body else "unknown",
                "uncertainty":"Usually one case per category/configuration; no statistical confidence or broad competence inferred.",
                "limitations":["Tiny deterministic fixtures, not universal qualification or broad model competence.",
                               "Partial coverage and unknown usage are explicit; local measurements do not establish cloud performance.",
                               "Ollama requested native settings are not echoed as effective settings; intrinsic output maximum remains unknown.",
                               "Native harness routing remains preview-only."] + (
                               ["Host-normalized usage does not establish complete provider-request accounting; native retries may be opaque.",
                                "Host-reported cost has no charge-versus-estimate provenance; actual billed cost remains unknown.",
                                "Isolated Codex completions have no model-callable tools; tool and recovery categories remain unmeasured."]
                               if backend.api_family == "openclaw-isolated-codex" else [])}


def run_profile_demo(inventory: Inventory) -> dict:
    from .models import RoutingPolicy, TaskRequirements
    from .routing import preview_route
    manager=ProfileManager(inventory)
    statuses=[]
    for model_id in ("forge","scribe"):
        plan=manager.plan(model_id)
        statuses.append(manager.start(plan["plan_id"],manager.authorize(plan["plan_id"])))
    return {"mode":"synthetic_profile_demo","profiles":statuses,
            "bios":[manager.bio(model_id) for model_id in ("forge","scribe")],
            "routing_previews":{category:asdict(preview_route(inventory,TaskRequirements(category=category),RoutingPolicy()))
                                for category in ("code_review","research")},
            "limitations":["Fictional outputs demonstrate the runner, not real model quality.",
                           "No live model, credentials, paid calls or native harness enforcement."]}
